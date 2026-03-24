"""
Dynamic Proxy Fetcher & Validator
==================================
Downloads the proxy list from the Proxifly free-proxy-list repository,
deduplicates entries, validates each proxy against a lightweight endpoint, and
maintains a thread-safe pool of healthy proxies that the rest of the bot can
consume at any time.

Usage::

    from proxy_fetcher import proxy_fetcher

    proxy_fetcher.start()            # kick off background fetch + validate
    proxy_fetcher.wait_ready(120)    # block until MIN_POOL_SIZE proxies ready

    url = proxy_fetcher.get()        # "socks5://1.2.3.4:1080" | None
    proxy_fetcher.mark_bad(url)      # remove a proxy that failed in production
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from typing import List, Optional, Set

try:
    import aiohttp
    from aiohttp_socks import ProxyConnector
    _AIOHTTP_OK = True
except ImportError:
    _AIOHTTP_OK = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Proxy source URLs (raw TXT – one "ip:port" or "scheme://ip:port" per line)
# ---------------------------------------------------------------------------
PROXY_SOURCES: List[str] = [
    # Proxifly – refreshed every 5 minutes, ~3,000-4,000 proxies, all protocols
    "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt",
]

# Lightweight URL used to validate that a proxy can reach the internet.
# Google's generate_204 returns an empty 204 response – fast and stable.
PROXY_VALIDATION_URL: str = "http://www.google.com/generate_204"

# Per-proxy timeout during the validation phase (seconds)
PROXY_VALIDATION_TIMEOUT: float = 8.0

# Discard proxies slower than this (round-trip seconds during validation)
PROXY_MAX_LATENCY: float = 6.0

# Maximum concurrent proxy-validation coroutines in a single refresh cycle
PROXY_CHECK_CONCURRENCY: int = 200

# Minimum healthy-proxy count before wait_ready() unblocks
MIN_POOL_SIZE: int = 30

# Seconds between automatic refresh cycles (3 hours by default)
PROXY_REFRESH_INTERVAL: int = 3 * 3600

# Timeout for downloading each individual proxy source (seconds)
SOURCE_FETCH_TIMEOUT: float = 25.0

# Protocol prefix assigned to bare "ip:port" lines that have no scheme
DEFAULT_SCHEME: str = "socks5"


class ProxyFetcher:
    """
    Background service that keeps a pool of healthy proxies up to date.

    Thread-safe: ``get()`` and ``mark_bad()`` may be called from any thread.
    """

    def __init__(self) -> None:
        self._pool: List[str] = []
        self._bad: Set[str] = set()
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._fetch_count: int = 0   # total proxies downloaded across all refreshes
        self._valid_count: int = 0   # total proxies that passed validation

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Spawn the background refresh daemon."""
        if not _AIOHTTP_OK:
            logger.error("proxy_fetcher: aiohttp / aiohttp-socks not available – proxy pool will remain empty.")
            return
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="proxy-fetcher"
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal the background thread to exit."""
        self._stop.set()

    def wait_ready(self, timeout: float = 120.0) -> bool:
        """
        Block until at least ``MIN_POOL_SIZE`` healthy proxies are available,
        or until *timeout* seconds elapse.  Returns ``True`` on success.
        """
        return self._ready.wait(timeout=timeout)

    def get(self) -> Optional[str]:
        """
        Return a random healthy proxy URL (e.g. ``"socks5://1.2.3.4:1080"``),
        or ``None`` when the pool is empty.
        """
        with self._lock:
            available = [p for p in self._pool if p not in self._bad]
        if not available:
            return None
        return random.choice(available)

    def mark_bad(self, proxy: Optional[str]) -> None:
        """
        Remove *proxy* from the active pool.  Call this when a proxy fails
        during production use so subsequent ``get()`` calls skip it.
        """
        if not proxy:
            return
        with self._lock:
            self._bad.add(proxy)
            try:
                self._pool.remove(proxy)
            except ValueError:
                pass

    def pool_size(self) -> int:
        """Return the number of currently available (non-bad) proxies."""
        with self._lock:
            return len([p for p in self._pool if p not in self._bad])

    def stats(self) -> dict:
        """Return a snapshot of fetcher statistics."""
        return {
            "pool_size": self.pool_size(),
            "fetch_count": self._fetch_count,
            "valid_count": self._valid_count,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        """Run refresh cycles in the background thread until stop() is called."""
        while not self._stop.is_set():
            try:
                asyncio.run(self._refresh())
            except Exception as exc:
                logger.warning("proxy_fetcher: refresh cycle error – %s", exc)
            if self._stop.is_set():
                break
            # Wait PROXY_REFRESH_INTERVAL before the next cycle, but wake up
            # immediately if stop() is called.
            self._stop.wait(timeout=PROXY_REFRESH_INTERVAL)

    async def _refresh(self) -> None:
        """Fetch → deduplicate → validate → update pool (one full cycle)."""
        logger.info("proxy_fetcher: refresh started (sources=%d)", len(PROXY_SOURCES))
        raw_lines = await self._fetch_all()
        parsed = _parse_proxies(raw_lines)
        self._fetch_count = len(parsed)
        logger.info("proxy_fetcher: %d unique proxies downloaded", len(parsed))

        if not parsed:
            logger.warning("proxy_fetcher: no proxies downloaded – check source URLs")
            return

        healthy = await _validate_batch(parsed)
        self._valid_count = len(healthy)
        logger.info("proxy_fetcher: %d proxies passed validation", len(healthy))

        with self._lock:
            self._pool = healthy
            self._bad.clear()

        if len(healthy) >= MIN_POOL_SIZE:
            self._ready.set()
        else:
            logger.warning(
                "proxy_fetcher: only %d healthy proxies (need %d) – "
                "validation endpoint may be unreachable",
                len(healthy), MIN_POOL_SIZE,
            )

    async def _fetch_all(self) -> List[str]:
        """Download proxy lists from all sources concurrently."""
        lines: List[str] = []
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Each source uses a per-request timeout (SOURCE_FETCH_TIMEOUT) that is
            # shorter than the session timeout so a slow source does not block others.
            tasks = [_fetch_source(session, url) for url in PROXY_SOURCES]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, list):
                lines.extend(result)
        return lines


# ---------------------------------------------------------------------------
# Module-level async helpers
# ---------------------------------------------------------------------------

async def _fetch_source(session: "aiohttp.ClientSession", url: str) -> List[str]:
    """Download one source URL and return its non-empty lines."""
    try:
        per_source_timeout = aiohttp.ClientTimeout(total=SOURCE_FETCH_TIMEOUT)
        async with session.get(url, timeout=per_source_timeout) as resp:
            if resp.status == 200:
                text = await resp.text(errors="replace")
                return [ln.strip() for ln in text.splitlines() if ln.strip()]
    except Exception as exc:
        logger.debug("proxy_fetcher: failed to fetch %s – %s", url, exc)
    return []


def _parse_proxies(lines: List[str]) -> List[str]:
    """
    Normalise proxy strings to ``scheme://ip:port`` and deduplicate.

    Input lines may be any of::

        1.2.3.4:1080
        socks5://1.2.3.4:1080
        http://1.2.3.4:8080
        1.1.1.1:8080 US-N-S +    (e.g. Spys.me – extra metadata is stripped)
    """
    seen: Set[str] = set()
    result: List[str] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Strip out extra country codes/metadata some lists provide (e.g. Spys.me)
        parts = line.split()
        if not parts:
            continue
        line = parts[0]

        if "://" not in line:
            line = f"{DEFAULT_SCHEME}://{line}"
        if line not in seen:
            seen.add(line)
            result.append(line)
    return result


async def _check_proxy(proxy_url: str, semaphore: asyncio.Semaphore) -> Optional[str]:
    """
    Return *proxy_url* if it can successfully reach PROXY_VALIDATION_URL within
    PROXY_VALIDATION_TIMEOUT seconds and PROXY_MAX_LATENCY response time.
    Returns ``None`` on any failure.
    """
    async with semaphore:
        try:
            connector = ProxyConnector.from_url(
                proxy_url,
                ssl=False,
                rdns=True,
                limit=1,
                force_close=True,
            )
            t0 = time.monotonic()
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=PROXY_VALIDATION_TIMEOUT),
            ) as session:
                async with session.get(PROXY_VALIDATION_URL) as resp:
                    # Any HTTP response (200, 204, 301…) means the proxy works
                    _ = await resp.read()
                    latency = time.monotonic() - t0
                    if latency <= PROXY_MAX_LATENCY:
                        return proxy_url
        except Exception:
            pass
        return None


async def _validate_batch(proxies: List[str]) -> List[str]:
    """Validate all proxies concurrently, returning those that pass."""
    semaphore = asyncio.Semaphore(PROXY_CHECK_CONCURRENCY)
    tasks = [_check_proxy(p, semaphore) for p in proxies]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, str)]


# ---------------------------------------------------------------------------
# Singleton instance used by the rest of the bot
# ---------------------------------------------------------------------------
proxy_fetcher = ProxyFetcher()
