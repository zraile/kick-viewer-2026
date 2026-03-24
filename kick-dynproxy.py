"""
Kick Viewer Bot — Multi-Source Dynamic Proxy Edition
=====================================================
This is the **new** proxy backend: all TOR / Docker dependencies have been
removed and replaced by a live, multi-source proxy scraper (proxy_fetcher.py).

Key differences from kick-multi6.py
-------------------------------------
* No Docker, no Tor containers, no local SOCKS ports.
* On startup the bot downloads proxy lists from several GitHub repos that are
  updated every 5-30 minutes, validates each proxy concurrently, and builds a
  healthy pool in ~60-90 seconds.
* The pool is refreshed automatically every 3 hours in the background.
* Every WebSocket connection uses its own randomly chosen proxy from the pool.
  Failed proxies are removed from the pool immediately.
* All other logic (token freshness, ramp-up, stats display, streamer list,
  state persistence, live-status monitor) is unchanged from kick-multi6.py.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import math
import os
import random
import ssl
import sys
import threading
import time
from queue import Empty, Queue
from threading import Thread
from typing import List, Optional

import tls_client

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    import aiohttp
    from aiohttp_socks import ProxyConnector, ProxyConnectionError, ProxyTimeoutError
    FULL_SUPPORT = True
except ImportError:
    FULL_SUPPORT = False
    ProxyTimeoutError = Exception  # type: ignore[misc,assignment]
    ProxyConnectionError = Exception  # type: ignore[misc,assignment]

from proxy_fetcher import proxy_fetcher  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CLIENT_TOKEN = "e1393935a959b4020a4491574f6490129f678acdaa92760471263db43487f823"

# Connections each individual async-worker manages simultaneously
CONNS_PER_WORKER: int = 20

# Total async-worker threads spawned per streamer (scales with target viewers)
WORKERS_PER_STREAMER: int = 5      # default; overridden in assign_workers()

BASE_TOKEN_POOL_SIZE: int = 500
BASE_INITIAL_POOL_WAIT: int = 150
BASE_TOKEN_PRODUCERS: int = 80
TOKEN_POOL_SIZE: int = 500
INITIAL_POOL_WAIT: int = 150
TOKEN_PRODUCERS: int = 80
PONG_TIMEOUT: int = 90

STATE_FILE: str = "state.json"
LIST_FILE: str = "liste.txt"

TOKEN_SCALE_VIEWERS: int = 50

TOKEN_TTL_SECONDS: int = 120

# Per-connection pre-connect jitter (applied inside ws_handler before the
# WebSocket handshake).  Tune via env vars to spread individual handshakes.
MIN_PRECONNECT_JITTER: float = float(os.getenv("MIN_PRECONNECT_JITTER", "0.1"))
MAX_PRECONNECT_JITTER: float = float(os.getenv("MAX_PRECONNECT_JITTER", "1.5"))

ACCEPT_LANGUAGES: List[str] = [
    "en-US,en;q=0.9",
    "en-GB,en;q=0.9",
    "en-US,en;q=0.8",
    "en-CA,en;q=0.9",
    "en-AU,en;q=0.9",
    "en-US,en;q=0.8,tr;q=0.6",
]

BACKOFF_FAILURE_THRESHOLD: int = 3
BACKOFF_MULTIPLIER: float = 0.5
MAX_BACKOFF_SECONDS: float = 2.0

MIN_PING_INTERVAL: int = 15
MAX_PING_INTERVAL: int = 25

# ---------------------------------------------------------------------------
# Connection ramp-up / jitter
# ---------------------------------------------------------------------------
# Randomised delay injected between consecutive WebSocket connection attempts
# inside run_proxy_worker_pool.  Raising these values slows the ramp-up,
# which reduces the risk of Cloudflare rate-limiting (HTTP 429) or proxy bans
# when using fast datacenter proxies.
#
# Override at runtime via environment variables, e.g.:
#   set MIN_CONNECTION_DELAY=1.0   (Windows)
#   export MIN_CONNECTION_DELAY=1.0  (Linux/macOS)
MIN_CONNECTION_DELAY: float = float(os.getenv("MIN_CONNECTION_DELAY", "1.5"))
MAX_CONNECTION_DELAY: float = float(os.getenv("MAX_CONNECTION_DELAY", "4.0"))

# Delay injected between successful token-fetch calls in token_producer.
# A small pause here prevents hammering the Kick token endpoint and avoids
# triggering Cloudflare's per-IP rate limits on the token API.
# Override via:  export TOKEN_FETCH_DELAY=0.2
TOKEN_FETCH_DELAY: float = float(os.getenv("TOKEN_FETCH_DELAY", "0.1"))

MIN_RECONNECT_DELAY: int = 3
MAX_RECONNECT_DELAY: int = 8

MIN_BATCH_COOLDOWN: float = 0.5
MAX_BATCH_COOLDOWN: float = 1.5

MIN_ERROR_BACKOFF: float = 1.0
MAX_ERROR_BACKOFF: float = 3.0

RAMP_STAGES = [
    (60, 0.20),
    (120, 0.40),
    (180, 0.60),
    (240, 0.80),
]
RAMP_FULL_FRACTION: float = 1.0

STREAMER_CHECK_INTERVAL: int = 120
INITIAL_STATUS_CHECK_DELAY: int = 30

MAX_ERROR_LOG: int = 15
MAX_ERRORS_DISPLAYED: int = 8
DEBUG_LOG_FILE: str = "error_debug.log"

CRITICAL_CONN_FRACTION: float = 0.15
EMERGENCY_BATCH_SIZE: int = 10
EMERGENCY_SPAWN_DELAY: float = 0.3

# Warn when the live proxy pool drops below this
MIN_PROXY_POOL_WARN: int = 10

USER_AGENTS: List[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Android 14; Mobile; rv:121.0) Gecko/121.0 Firefox/121.0",
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 OPR/106.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
]


def get_random_ua() -> str:
    return random.choice(USER_AGENTS)


# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
stop: bool = False
lock = threading.Lock()
token_queue: Queue = Queue()
token_hits: int = 0
token_misses: int = 0
streamers: List["StreamerInfo"] = []
error_log: List[str] = []
error_log_lock = threading.Lock()
debug_log_lock = threading.Lock()


def write_debug_log(entry: str) -> None:
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with debug_log_lock:
        try:
            with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as fh:
                fh.write(f"[{ts}] {entry}\n")
        except Exception:
            pass


_ERROR_COLORS = {
    "[CF-BLOCK]":       "\033[35m",
    "[TOKEN-INVALID]":  "\033[31m",
    "[PROXY-TIMEOUT]":  "\033[33m",
    "[WS-ERROR]":       "\033[31m",
    "[STATUS]":         "\033[36m",
}
_DEFAULT_ERROR_COLOR = "\033[31m"


def _error_color(msg: str) -> str:
    for prefix, color in _ERROR_COLORS.items():
        if prefix in msg:
            return color
    return _DEFAULT_ERROR_COLOR


def add_error_log(msg: str) -> None:
    with error_log_lock:
        error_log.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")
        while len(error_log) > MAX_ERROR_LOG:
            error_log.pop(0)


# ---------------------------------------------------------------------------
# Chrome-like TLS context
# ---------------------------------------------------------------------------
def _build_chrome_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    chrome_ciphers = (
        "TLS_AES_128_GCM_SHA256:"
        "TLS_AES_256_GCM_SHA384:"
        "TLS_CHACHA20_POLY1305_SHA256:"
        "ECDHE-ECDSA-AES128-GCM-SHA256:"
        "ECDHE-RSA-AES128-GCM-SHA256:"
        "ECDHE-ECDSA-AES256-GCM-SHA384:"
        "ECDHE-RSA-AES256-GCM-SHA384:"
        "ECDHE-ECDSA-CHACHA20-POLY1305:"
        "ECDHE-RSA-CHACHA20-POLY1305:"
        "ECDHE-RSA-AES128-SHA:"
        "ECDHE-RSA-AES256-SHA:"
        "AES128-GCM-SHA256:"
        "AES256-GCM-SHA384:"
        "AES128-SHA:"
        "AES256-SHA"
    )
    try:
        ctx.set_ciphers(chrome_ciphers)
    except ssl.SSLError:
        add_error_log("TLS fingerprint spoofing unavailable on this platform")
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


_chrome_ssl_ctx = _build_chrome_ssl_context()


# ---------------------------------------------------------------------------
# StreamerInfo
# ---------------------------------------------------------------------------
class StreamerInfo:
    def __init__(
        self,
        name: str,
        target_viewers: int,
        duration_seconds: int,
        start_time: Optional[datetime.datetime] = None,
    ) -> None:
        self.name = name
        self.target_viewers = target_viewers
        self.duration_seconds = duration_seconds
        self.start_time = start_time or datetime.datetime.now()
        self.channel_id: Optional[int] = None
        self.stream_id: Optional[int] = None
        self.connections: int = 0
        self.attempts: int = 0
        self.pings: int = 0
        self.heartbeats: int = 0
        self.viewers: int = 0
        self.last_check: float = 0.0
        self.ws_errors: int = 0
        self.active: bool = True
        self.num_workers: int = 0    # assigned in assign_workers()
        self.is_live: bool = True
        self.last_status_check: float = 0.0

    def time_remaining(self) -> float:
        elapsed = (datetime.datetime.now() - self.start_time).total_seconds()
        return max(0.0, self.duration_seconds - elapsed)

    def is_expired(self) -> bool:
        return self.time_remaining() <= 0

    def format_remaining(self) -> str:
        secs = int(self.time_remaining())
        if secs <= 0:
            return "EXPIRED"
        days = secs // 86400
        hours = (secs % 86400) // 3600
        mins = (secs % 3600) // 60
        s = secs % 60
        if days > 0:
            return f"{days}d {hours:02d}:{mins:02d}:{s:02d}"
        return f"{hours:02d}:{mins:02d}:{s:02d}"


# ---------------------------------------------------------------------------
# Token pool
# ---------------------------------------------------------------------------
def calculate_token_settings(total_target_viewers: int) -> None:
    global TOKEN_POOL_SIZE, INITIAL_POOL_WAIT, TOKEN_PRODUCERS
    scale = max(1, math.ceil(total_target_viewers / TOKEN_SCALE_VIEWERS))
    TOKEN_POOL_SIZE = BASE_TOKEN_POOL_SIZE + (scale - 1) * 50
    INITIAL_POOL_WAIT = min(300, BASE_INITIAL_POOL_WAIT + (scale - 1) * 10)
    TOKEN_PRODUCERS = BASE_TOKEN_PRODUCERS + (scale - 1) * 5


def fetch_token(proxy_url: Optional[str] = None) -> Optional[str]:
    """Fetch a fresh Kick viewer token, optionally routing through *proxy_url*."""
    try:
        s = tls_client.Session(
            client_identifier="chrome_120", random_tls_extension_order=True
        )
        if proxy_url:
            s.proxies = {"http": proxy_url, "https": proxy_url}
        s.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "User-Agent": get_random_ua(),
        })
        s.get("https://kick.com", timeout_seconds=20)
        s.headers["X-CLIENT-TOKEN"] = CLIENT_TOKEN
        response = s.get(
            "https://websockets.kick.com/viewer/v1/token", timeout_seconds=20
        )
        if response.status_code == 200:
            data = response.json()
            return data.get("data", {}).get("token")
        if response.status_code == 403 and proxy_url:
            proxy_fetcher.mark_bad(proxy_url)
    except Exception:
        pass
    return None


def token_producer() -> None:
    global stop
    consecutive_failures = 0
    while not stop:
        try:
            if token_queue.qsize() < TOKEN_POOL_SIZE:
                proxy = proxy_fetcher.get()
                token = fetch_token(proxy)
                if token:
                    token_queue.put((token, proxy, time.time()))
                    consecutive_failures = 0
                    # Small delay between successful fetches to avoid
                    # rate-limiting (HTTP 429) on the Kick token endpoint.
                    time.sleep(TOKEN_FETCH_DELAY)
                else:
                    consecutive_failures += 1
                    if consecutive_failures > BACKOFF_FAILURE_THRESHOLD:
                        backoff = min(
                            MAX_BACKOFF_SECONDS,
                            BACKOFF_MULTIPLIER * consecutive_failures,
                        )
                        time.sleep(backoff)
            else:
                time.sleep(0.05)
                consecutive_failures = 0
        except Exception:
            consecutive_failures += 1
            time.sleep(0.1)


def get_token_from_pool():
    global token_hits, token_misses
    now = time.time()
    for _ in range(200):
        try:
            token, proxy, ts = token_queue.get(timeout=0.1)
            if now - ts <= TOKEN_TTL_SECONDS:
                with lock:
                    token_hits += 1
                return token, proxy
        except Empty:
            break
    with lock:
        token_misses += 1
    return None, None


# ---------------------------------------------------------------------------
# Worker assignment
# ---------------------------------------------------------------------------
def assign_workers(streamer_list: List[StreamerInfo]) -> None:
    """
    Calculate how many async-worker threads each streamer gets, proportional
    to its target viewer count.  Each worker manages up to CONNS_PER_WORKER
    simultaneous WebSocket connections.
    """
    for s in streamer_list:
        if s.active:
            s.num_workers = max(1, math.ceil(s.target_viewers / CONNS_PER_WORKER))


# ---------------------------------------------------------------------------
# Gradual ramp-up
# ---------------------------------------------------------------------------
def get_ramp_target(streamer: StreamerInfo, target: int) -> int:
    elapsed = (datetime.datetime.now() - streamer.start_time).total_seconds()
    factor = RAMP_FULL_FRACTION
    for threshold, f in RAMP_STAGES:
        if elapsed < threshold:
            factor = f
            break
    return max(1, int(target * factor))


# ---------------------------------------------------------------------------
# Channel / viewer helpers
# ---------------------------------------------------------------------------
def clean_channel_name(name: str) -> str:
    # Use proper URL parsing to extract the channel slug from a kick.com URL.
    # Accepts exact "kick.com" or valid subdomains (e.g. "www.kick.com").
    try:
        from urllib.parse import urlparse
        parsed = urlparse(name if "://" in name else f"https://{name}")
        hostname = parsed.hostname or ""
        if hostname == "kick.com" or hostname.endswith(".kick.com"):
            path = parsed.path.strip("/")
            # Take the first path segment as the channel name
            ch = path.split("/")[0].split("?")[0]
            if ch:
                return ch.lower()
    except Exception:
        pass
    return name.lower()


def get_channel_info_for(streamer: StreamerInfo) -> Optional[int]:
    try:
        s = tls_client.Session(
            client_identifier="chrome_120", random_tls_extension_order=True
        )
        proxy = proxy_fetcher.get()
        if proxy:
            s.proxies = {"http": proxy, "https": proxy}
        s.headers.update({
            "Accept": "application/json",
            "User-Agent": get_random_ua(),
        })
        response = s.get(
            f"https://kick.com/api/v2/channels/{streamer.name}", timeout_seconds=30
        )
        if response.status_code == 200:
            data = response.json()
            streamer.channel_id = data.get("id")
            if data.get("livestream"):
                streamer.stream_id = data["livestream"].get("id")
            else:
                print(
                    f"\033[33m[!] {streamer.name} is OFFLINE. "
                    "Bot will run but viewers might not increase.\033[0m"
                )
            return streamer.channel_id
        elif response.status_code == 404:
            print(
                f"\033[31m[!] Channel '{streamer.name}' not found! "
                "Please check the username.\033[0m"
            )
        elif response.status_code == 403:
            print(
                f"\033[31m[!] Blocked by Kick API (403) for '{streamer.name}'. "
                "Trying next proxy…\033[0m"
            )
            if proxy:
                proxy_fetcher.mark_bad(proxy)
        else:
            print(
                f"\033[31m[!] Unexpected API response for '{streamer.name}' "
                f"(Code: {response.status_code})\033[0m"
            )
    except Exception as exc:
        print(f"\033[31m[!] Connection error for '{streamer.name}': {exc}\033[0m")
    return None


def get_viewer_count_for(streamer: StreamerInfo) -> int:
    if not streamer.stream_id:
        return 0
    try:
        s = tls_client.Session(
            client_identifier="chrome_120", random_tls_extension_order=True
        )
        proxy = proxy_fetcher.get()
        if proxy:
            s.proxies = {"http": proxy, "https": proxy}
        s.headers.update({
            "Accept": "application/json",
            "User-Agent": get_random_ua(),
        })
        response = s.get(
            f"https://kick.com/current-viewers?ids[]={streamer.stream_id}",
            timeout_seconds=15,
        )
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and data:
                streamer.viewers = data[0].get("viewers", 0)
                streamer.last_check = time.time()
    except Exception:
        pass
    return streamer.viewers


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(streamer_list: List[StreamerInfo]) -> None:
    data: dict = {"streamers": {}}
    for s in streamer_list:
        data["streamers"][s.name] = {
            "start_time": s.start_time.isoformat(),
            "duration_seconds": s.duration_seconds,
            "target_viewers": s.target_viewers,
        }
    try:
        with open(STATE_FILE, "w") as fh:
            json.dump(data, fh, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# liste.txt
# ---------------------------------------------------------------------------
def read_liste() -> List[str]:
    if not os.path.exists(LIST_FILE):
        print(f"\033[31m[!] ERROR: '{LIST_FILE}' not found!\033[0m")
        print(f"    Create a '{LIST_FILE}' with one streamer name or link per line.")
        return []
    entries: List[str] = []
    with open(LIST_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            entries.append(line)
    if not entries:
        print(f"\033[31m[!] ERROR: '{LIST_FILE}' is empty or has no valid entries.\033[0m")
    return entries


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------
_DURATION_UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text: str) -> Optional[int]:
    text = text.strip().lower()
    total = 0
    i = 0
    while i < len(text):
        num = ""
        while i < len(text) and text[i].isdigit():
            num += text[i]
            i += 1
        if not num:
            i += 1
            continue
        unit = text[i] if i < len(text) else "s"
        i += 1
        total += int(num) * _DURATION_UNITS.get(unit, 1)
    return total if total > 0 else None


# ---------------------------------------------------------------------------
# Stats display
# ---------------------------------------------------------------------------
def show_stats() -> None:
    global stop
    n = 3 + len(streamers) + 3 + MAX_ERRORS_DISPLAYED
    print("\n" * n)
    os.system("cls" if os.name == "nt" else "clear")
    while not stop:
        try:
            for s in streamers:
                if time.time() - s.last_check >= 5:
                    get_viewer_count_for(s)

            pstats = proxy_fetcher.stats()
            pool_sz = pstats["pool_size"]

            lines = [
                f"\033[2K\r[+] ProxyPool: \033[{'32' if pool_sz >= MIN_PROXY_POOL_WARN else '31'}m{pool_sz}\033[0m"
                f" | Fetched: \033[32m{pstats['fetch_count']}\033[0m"
                f" | Validated: \033[32m{pstats['valid_count']}\033[0m"
                f" | TokenPool: \033[32m{token_queue.qsize()}\033[0m"
                f" | Hits: \033[32m{token_hits}\033[0m | Miss: \033[31m{token_misses}\033[0m",
                f"\033[2K\r{'Streamer':<20} {'Conn/Target':<15} {'Viewers':<10} {'Attempts':<10} {'Errors':<8} {'Live':<6} {'Remaining':<15}",
                f"\033[2K\r{'-'*80}",
            ]
            for s in streamers:
                if s.active:
                    conn_str = f"{s.connections}/{s.target_viewers}"
                    remaining = s.format_remaining()
                    live_str = "\033[32mYES\033[0m" if s.is_live else "\033[31mNO \033[0m"
                    lines.append(
                        f"\033[2K\r{s.name:<20} \033[32m{conn_str:<15}\033[0m "
                        f"\033[32m{s.viewers:<10}\033[0m \033[32m{s.attempts:<10}\033[0m "
                        f"\033[31m{s.ws_errors:<8}\033[0m {live_str:<14}\033[33m{remaining:<15}\033[0m"
                    )
                else:
                    lines.append(f"\033[2K\r{s.name:<20} \033[31mEXPIRED\033[0m")

            lines.append(f"\033[2K\r{'-'*80}")
            lines.append(
                f"\033[2K\r\033[36mRecent Errors:\033[0m  "
                f"\033[35m[CF-BLOCK]\033[0m Cloudflare  "
                f"\033[31m[TOKEN-INVALID]\033[0m Kick token  "
                f"\033[33m[PROXY-TIMEOUT]\033[0m Proxy  "
                f"\033[31m[WS-ERROR]\033[0m Other"
            )
            lines.append(f"\033[2K\r{'-'*80}")
            with error_log_lock:
                recent_errors = list(error_log[-MAX_ERRORS_DISPLAYED:])
            for i in range(MAX_ERRORS_DISPLAYED):
                if i < len(recent_errors):
                    color = _error_color(recent_errors[i])
                    lines.append(f"\033[2K\r  {color}{recent_errors[i]}\033[0m")
                else:
                    lines.append(f"\033[2K\r")

            print(f"\033[{n}A", end="")
            for line in lines:
                print(line)
            sys.stdout.flush()
            time.sleep(1)
        except Exception:
            time.sleep(1)


# ---------------------------------------------------------------------------
# WebSocket handler  (one connection, one proxy)
# ---------------------------------------------------------------------------
async def ws_handler(
    session: "aiohttp.ClientSession",
    token: str,
    streamer: StreamerInfo,
    proxy_url: Optional[str] = None,
) -> None:
    connected = False
    ws = None
    try:
        url = f"wss://websockets.kick.com/viewer/v1/connect?token={token}"
        ua = get_random_ua()
        headers = {
            "Origin": "https://kick.com",
            "User-Agent": ua,
            "Accept-Language": random.choice(ACCEPT_LANGUAGES),
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Sec-WebSocket-Extensions": "permessage-deflate; client_max_window_bits",
        }
        await asyncio.sleep(random.uniform(MIN_PRECONNECT_JITTER, MAX_PRECONNECT_JITTER))
        ws = await session.ws_connect(
            url,
            headers=headers,
            ssl=_chrome_ssl_ctx,
            timeout=aiohttp.ClientTimeout(total=30),
        )
        with lock:
            streamer.connections += 1
        connected = True

        subscribe = {
            "event": "pusher:subscribe",
            "data": {"auth": "", "channel": f"channel.{streamer.channel_id}"},
        }
        await ws.send_str(json.dumps(subscribe))

        if streamer.stream_id:
            chatroom_sub = {
                "event": "pusher:subscribe",
                "data": {"auth": "", "channel": f"chatrooms.{streamer.channel_id}.v2"},
            }
            await ws.send_str(json.dumps(chatroom_sub))

        handshake = {
            "type": "channel_handshake",
            "data": {"message": {"channelId": streamer.channel_id}},
        }
        await ws.send_str(json.dumps(handshake))
        with lock:
            streamer.heartbeats += 1

        last_activity = time.time()
        last_ping = 0.0
        ping_interval = random.uniform(MIN_PING_INTERVAL, MAX_PING_INTERVAL)

        while not stop and not streamer.is_expired():
            try:
                now = time.time()
                if now - last_ping >= ping_interval:
                    await ws.send_str(json.dumps({"event": "pusher:ping", "data": {}}))
                    with lock:
                        streamer.pings += 1
                    last_ping = now
                    ping_interval = random.uniform(MIN_PING_INTERVAL, MAX_PING_INTERVAL)

                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        last_activity = time.time()
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                except asyncio.TimeoutError:
                    pass

                if time.time() - last_activity > PONG_TIMEOUT:
                    break
            except Exception:
                break

    except aiohttp.WSServerHandshakeError as exc:
        if exc.status == 403:
            resp_headers = dict(getattr(exc, "headers", None) or {})
            cf_ray = resp_headers.get("cf-ray", resp_headers.get("Cf-Ray", ""))
            server = resp_headers.get("server", resp_headers.get("Server", ""))
            body = str(getattr(exc, "message", ""))

            category = "[CF-BLOCK]" if cf_ray else "[TOKEN-INVALID]"
            write_debug_log(
                f"{category} streamer={streamer.name} proxy={proxy_url} "
                f"cf-ray={cf_ray!r} server={server!r} "
                f"headers={resp_headers!r} body={body!r}"
            )

            if category == "[CF-BLOCK]":
                if proxy_url:
                    proxy_fetcher.mark_bad(proxy_url)
                    add_error_log(
                        f"[CF-BLOCK] {streamer.name}: proxy removed (cf-ray={cf_ray})"
                    )
                else:
                    add_error_log(f"[CF-BLOCK] {streamer.name}: 403 Cloudflare block")
            else:
                add_error_log(
                    f"[TOKEN-INVALID] {streamer.name}: dead token on proxy {proxy_url}, "
                    "fresh token will be used next attempt"
                )
        else:
            await asyncio.sleep(random.uniform(MIN_ERROR_BACKOFF, MAX_ERROR_BACKOFF))
            with lock:
                streamer.ws_errors += 1
            add_error_log(
                f"[WS-ERROR] {streamer.name}: WSServerHandshakeError "
                f"{exc.status}: {exc.message}"
            )

    except (ProxyTimeoutError, ProxyConnectionError) as exc:
        if proxy_url:
            proxy_fetcher.mark_bad(proxy_url)
        add_error_log(
            f"[PROXY-TIMEOUT] {streamer.name}: proxy {proxy_url} — "
            f"{type(exc).__name__}: {exc}"
        )

    except Exception as exc:
        await asyncio.sleep(random.uniform(MIN_ERROR_BACKOFF, MAX_ERROR_BACKOFF))
        with lock:
            streamer.ws_errors += 1
        add_error_log(f"[WS-ERROR] {streamer.name}: {type(exc).__name__}: {exc}")

    finally:
        if ws and not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass
        if connected:
            with lock:
                streamer.connections = max(0, streamer.connections - 1)


# ---------------------------------------------------------------------------
# Per-connection wrapper  (creates its own session + proxy connector)
# ---------------------------------------------------------------------------
async def _single_proxy_conn(token: str, streamer: StreamerInfo, proxy_url: str) -> None:
    """
    Establish exactly one WebSocket connection via *proxy_url*.
    A fresh aiohttp session (with a dedicated ProxyConnector) is created and
    destroyed for each connection so that failed proxies do not pollute other
    active connections.
    """
    try:
        connector = ProxyConnector.from_url(
            proxy_url,
            ssl=False,
            rdns=True,
            limit=1,
            force_close=True,
        )
    except Exception:
        return

    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            await ws_handler(session, token, streamer, proxy_url=proxy_url)
    except (ProxyConnectionError, ProxyTimeoutError) as exc:
        proxy_fetcher.mark_bad(proxy_url)
        add_error_log(
            f"[PROXY-TIMEOUT] {streamer.name}: {type(exc).__name__}: {exc}"
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Async worker pool  (one event loop → many concurrent connections)
# ---------------------------------------------------------------------------
async def run_proxy_worker_pool(
    worker_id: int,
    target_conn_count: int,
    streamer: StreamerInfo,
) -> None:
    """
    Maintain up to *target_conn_count* simultaneous WebSocket connections for
    *streamer*, each routed through a different proxy from the pool.
    """
    global stop
    active_tasks: set = set()

    while not stop and not streamer.is_expired():
        done = {t for t in active_tasks if t.done()}
        active_tasks -= done

        # Wait while the proxy pool is critically low
        pool_size = proxy_fetcher.pool_size()
        if pool_size < MIN_PROXY_POOL_WARN:
            await asyncio.sleep(5.0)
            continue

        # Pause new connections while streamer is offline
        if not streamer.is_live:
            await asyncio.sleep(5.0)
            continue

        ramp_target = get_ramp_target(streamer, target_conn_count)
        slots = ramp_target - len(active_tasks)

        if token_queue.qsize() < 20:
            await asyncio.sleep(1.0)
            continue

        if slots <= 0:
            await asyncio.sleep(0.3)
            continue

        is_critical = (
            streamer.target_viewers > 0
            and streamer.connections < streamer.target_viewers * CRITICAL_CONN_FRACTION
        )

        if is_critical:
            batch_size = min(slots, EMERGENCY_BATCH_SIZE)
            spawn_delay = EMERGENCY_SPAWN_DELAY
        else:
            batch_size = (
                min(slots, 4) if token_queue.qsize() > 200
                else min(slots, 2) if token_queue.qsize() > 50
                else min(slots, 1)
            )
            spawn_delay = random.uniform(MIN_CONNECTION_DELAY, MAX_CONNECTION_DELAY)

        for _ in range(batch_size):
            if stop or streamer.is_expired():
                break
            proxy = proxy_fetcher.get()
            if not proxy:
                break
            token, _ = get_token_from_pool()
            if not token:
                break
            with lock:
                streamer.attempts += 1
            task = asyncio.create_task(
                _single_proxy_conn(token, streamer, proxy)
            )
            active_tasks.add(task)
            await asyncio.sleep(spawn_delay)

        await asyncio.sleep(random.uniform(MIN_BATCH_COOLDOWN, MAX_BATCH_COOLDOWN))
        if not done:
            await asyncio.sleep(0.3)


def proxy_worker(worker_id: int, target_conn_count: int, streamer: StreamerInfo) -> None:
    """Thread entry point for one async worker."""
    while not stop and not streamer.is_expired():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                run_proxy_worker_pool(worker_id, target_conn_count, streamer)
            )
        except Exception:
            pass
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
        if not stop and not streamer.is_expired():
            time.sleep(random.uniform(MIN_RECONNECT_DELAY, MAX_RECONNECT_DELAY))


# ---------------------------------------------------------------------------
# Streamer live-status monitor
# ---------------------------------------------------------------------------
def streamer_status_monitor() -> None:
    global stop
    while not stop:
        time.sleep(INITIAL_STATUS_CHECK_DELAY)
        for s in streamers:
            if stop or s.is_expired():
                continue
            now = time.time()
            if now - s.last_status_check < STREAMER_CHECK_INTERVAL:
                continue
            prev_live = s.is_live
            prev_stream_id = s.stream_id
            s.stream_id = None
            try:
                get_channel_info_for(s)
            except Exception as exc:
                add_error_log(f"[STATUS] {s.name}: Status check error — {exc}")
                s.stream_id = prev_stream_id
                s.last_status_check = now
                continue
            s.last_status_check = time.time()
            if s.stream_id:
                if not prev_live:
                    add_error_log(
                        f"[STATUS] {s.name}: Back ONLINE (Stream ID: {s.stream_id}), "
                        "resuming connections"
                    )
                s.is_live = True
            else:
                if prev_live:
                    add_error_log(
                        f"[STATUS] {s.name}: Went OFFLINE — pausing new connections"
                    )
                s.is_live = False


# ---------------------------------------------------------------------------
# Run all streamers
# ---------------------------------------------------------------------------
def run_all_streamers() -> None:
    global stop

    Thread(target=show_stats, daemon=True).start()
    Thread(target=streamer_status_monitor, daemon=True).start()

    threads: List[Thread] = []
    for s in streamers:
        if not s.active:
            continue
        num_workers = s.num_workers
        conns_per_worker = max(1, math.ceil(s.target_viewers / max(1, num_workers)))
        for w_id in range(num_workers):
            if stop:
                break
            t = Thread(
                target=proxy_worker,
                args=(w_id, conns_per_worker, s),
                daemon=True,
            )
            threads.append(t)
            t.start()
            time.sleep(0.02)

    while not stop:
        if all(s.is_expired() for s in streamers if s.active):
            print("\n\033[33m[*] All streamers have expired. Shutting down…\033[0m")
            stop = True
            break
        time.sleep(60)

    for t in threads:
        t.join(timeout=1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    try:
        os.system("cls" if os.name == "nt" else "clear")
        print("\033[94m" + "=" * 55)
        print("   KICK VIEWER BOT — DYNAMIC PROXY EDITION   ")
        print("=" * 55 + "\033[0m\n")

        if not FULL_SUPPORT:
            print(
                "\n\033[31m[!] Missing required libraries! "
                "Please install them first:\033[0m"
            )
            print("    pip install aiohttp aiohttp-socks tls-client")
            sys.exit(1)

        # ------------------------------------------------------------------
        # 1. Read streamer list
        # ------------------------------------------------------------------
        entries = read_liste()
        if not entries:
            sys.exit(1)

        print(f"[*] {LIST_FILE} read. {len(entries)} streamer(s) found.\n")

        # Load existing state
        state = load_state()
        state_streamers = state.get("streamers", {})

        for entry in entries:
            name = clean_channel_name(entry)
            if name in state_streamers:
                s_data = state_streamers[name]
                start_time = datetime.datetime.fromisoformat(s_data["start_time"])
                duration_secs = s_data["duration_seconds"]
                target_viewers = s_data["target_viewers"]
                si = StreamerInfo(name, target_viewers, duration_secs, start_time)
                if si.is_expired():
                    print(f"[!] {name}: Duration expired, skipping.")
                    continue
                print(
                    f"[*] {name}: Resuming — {si.format_remaining()} remaining, "
                    f"{target_viewers} target viewers."
                )
                streamers.append(si)
            else:
                while True:
                    try:
                        v = input(f"\nHow many viewers for {name}? : ").strip()
                        target_viewers = int(v)
                        if target_viewers > 0:
                            break
                        print("[!] Please enter a positive number.")
                    except ValueError:
                        print("[!] Please enter a valid number.")

                while True:
                    dur_input = input(
                        f"Duration for {name} (e.g. 7d, 2h, 30m, 1w): "
                    ).strip()
                    duration_secs = parse_duration(dur_input)
                    if duration_secs:
                        break
                    print("[!] Invalid duration format. Examples: 7d, 2h, 30m, 1w, 45m")

                si = StreamerInfo(name, target_viewers, duration_secs)
                streamers.append(si)

        if not streamers:
            print("\033[31m[!] No active streamers to run. Exiting.\033[0m")
            sys.exit(1)

        total_target = sum(s.target_viewers for s in streamers)
        calculate_token_settings(total_target)

        # ------------------------------------------------------------------
        # 2. Start proxy fetcher and wait for initial pool
        # ------------------------------------------------------------------
        print("\n[*] Starting proxy fetcher…")
        print(
            "[*] Downloading proxy lists from multiple sources "
            "(Proxifly, ProxyScraper, TheSpeedX, vakhov)…"
        )
        proxy_fetcher.start()

        print("[*] Validating proxies — this may take 60-90 seconds…")
        start_wait = time.time()
        while True:
            ready = proxy_fetcher.wait_ready(timeout=10)
            elapsed = int(time.time() - start_wait)
            pool_now = proxy_fetcher.pool_size()
            if ready:
                break
            print(
                f"\r[*] Proxy pool: {pool_now} healthy  |  elapsed {elapsed}s…",
                end="",
                flush=True,
            )
            if elapsed > 180:
                print(
                    "\n\033[33m[!] Proxy validation is taking longer than expected. "
                    f"Pool so far: {pool_now}. Continuing anyway…\033[0m"
                )
                break

        pool_now = proxy_fetcher.pool_size()
        if pool_now == 0:
            print(
                "\n\033[31m[!] ERROR: No healthy proxies found. "
                "Check your internet connection and that the proxy source URLs "
                "are reachable.\033[0m"
            )
            sys.exit(1)

        print(f"\n\033[92m[+] Proxy pool ready: {pool_now} healthy proxies\033[0m")

        # ------------------------------------------------------------------
        # 3. Get channel info for each streamer
        # ------------------------------------------------------------------
        for s in streamers:
            print(f"[*] Getting channel info for: {s.name}")
            get_channel_info_for(s)
            if s.channel_id:
                print(
                    f"[+] {s.name}: Channel ID={s.channel_id} "
                    f"| Stream ID={s.stream_id or 'N/A'}"
                )

        # ------------------------------------------------------------------
        # 4. Assign async workers proportionally
        # ------------------------------------------------------------------
        assign_workers(streamers)
        for s in streamers:
            print(
                f"[*] {s.name}: {s.num_workers} worker thread(s) × "
                f"{math.ceil(s.target_viewers / s.num_workers)} connections each"
            )

        # ------------------------------------------------------------------
        # 5. Start token producers
        # ------------------------------------------------------------------
        print("[*] Starting token producers…")
        for _ in range(TOKEN_PRODUCERS):
            Thread(target=token_producer, daemon=True).start()

        print("[*] Filling token pool…")
        start_fill = time.time()
        while token_queue.qsize() < INITIAL_POOL_WAIT:
            time.sleep(0.3)
            print(f"\r[*] Tokens: {token_queue.qsize()}/{INITIAL_POOL_WAIT}", end="")
            if time.time() - start_fill > 60 and token_queue.qsize() < 10:
                print(
                    f"\n[DEBUG] WARNING: Token generation very slow. "
                    f"Only {token_queue.qsize()} tokens in 60s. "
                    "Check if proxy pool is healthy."
                )
                break

        print(f"\n[+] Token pool ready: {token_queue.qsize()}")

        # ------------------------------------------------------------------
        # 6. Persist state and start
        # ------------------------------------------------------------------
        save_state(streamers)
        run_all_streamers()

    except KeyboardInterrupt:
        stop = True
        print(
            "\n\n\033[33m[*] Shutting down, please wait. Saving state…\033[0m"
        )
        save_state(streamers)
        proxy_fetcher.stop()
        print("\033[92m[+] Goodbye!\033[0m")
        sys.exit(0)
    except Exception as exc:
        print(f"\n\033[31m[!] UNEXPECTED ERROR: {exc}\033[0m")
        sys.exit(1)
