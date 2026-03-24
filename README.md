# Kick Viewer Bot (Dynamic Proxy Edition)

A robust Kick.com viewer bot that uses a **multi-source dynamic proxy pool** to bypass rate limits. Supports **multiple streamers simultaneously**, persistent session timers, auto-reconnect, and anti-block improvements.

No Docker or TOR required.

## The Easy Way (Recommended)

1. **Edit `liste.txt`** — add streamer names or kick.com links, one per line.
2. **Double-click `run.bat`**.
3. The script will automatically:
   - Verify your Python installation.
   - Install or update all required libraries from `requirements.txt`.
   - Download and validate fresh proxies from multiple public sources.
   - Launch the bot.

---

## Requirements

- Python 3.8+
- Windows 10/11 (or any OS with Python)
- Internet connection (proxies are fetched automatically)

## Files

| File | Description |
|------|-------------|
| `kick-dynproxy.py` | **Main bot** — multi-streamer, dynamic proxy edition |
| `proxy_fetcher.py` | Background proxy downloader and validator |
| `liste.txt` | Streamer list — one name or kick.com link per line |
| `state.json` | Auto-generated session state (persistent timers) |

---

## `liste.txt` — Streamer List

Add one streamer name or kick.com link per line. Lines starting with `#` and blank lines are ignored.

```
# Each line is a streamer name or kick.com link
# Blank lines and lines starting with # are ignored
streamer1
https://kick.com/streamer2
streamer3
```

---

## Usage (Manual)

```bash
python kick-dynproxy.py
```

### Startup Flow

1. The bot downloads and validates fresh proxies from multiple public sources (~60-90s).
2. The bot reads `liste.txt` and shows how many streamers were found.
3. For **new** streamers (not in `state.json`), it asks:
   - How many viewers to assign
   - How long to run (supports `7d`, `2h`, `30m`, `1w`, `45m`, etc.)
4. For **existing** streamers (already in `state.json` with time remaining), it resumes automatically — no prompts needed.
5. The bot starts routing viewers to all streamers proportionally via the dynamic proxy pool.

### Example Session

```
[*] liste.txt read. 3 streamer(s) found.

How many viewers for streamer1? : 50
Duration for streamer1 (e.g. 7d, 2h, 30m, 1w): 7d

How many viewers for streamer2? : 100
Duration for streamer2 (e.g. 7d, 2h, 30m, 1w): 30d

How many viewers for streamer3? : 30
Duration for streamer3 (e.g. 7d, 2h, 30m, 1w): 2h
```

---

## Duration Format

| Input | Meaning |
|-------|---------|
| `7d` | 7 days |
| `1w` | 1 week (= 7 days) |
| `30d` | 30 days |
| `2h` | 2 hours |
| `45m` | 45 minutes |
| `1d12h` | 1 day and 12 hours |

---

## Proxy System

Proxies are fetched automatically from multiple actively-maintained public sources:

- **Proxifly** — refreshed every 5 minutes, ~3,000-4,000 proxies
- **ProxyScraper** — refreshed every 30 minutes
- **TheSpeedX SOCKS5 list** — large list, frequently updated
- **vakhov fresh-proxy-list** — SOCKS5 subset, updated every 5-20 minutes

Only proxies that pass a live validation check (latency ≤ 6 seconds) are used. The pool refreshes automatically every 3 hours. Failed proxies are removed from the pool immediately.

---

## Persistent Timer (`state.json`)

The bot saves each streamer's start time, duration, and target viewer count to `state.json`. When you restart the bot:

- **Time remaining:** The streamer resumes automatically with the remaining duration.
- **Expired:** The streamer is skipped (removed from the session).
- **New streamer added to `liste.txt`:** You are prompted for viewer count and duration.

`state.json` is excluded from git (`.gitignore`).

---

## Stats Display

```
[+] Proxy Pool: 842 | TokenPool: 487 | Hits: 1234 | Miss: 12
Streamer             Conn/Target     Viewers    Attempts   Errors   Remaining
--------------------------------------------------------------------------------
streamer1            48/50           52         312        3        6d 22:14:09
streamer2            97/100          104        621        7        29d 18:00:00
streamer3            29/30           31         189        1        01:44:22
```

- **Conn/Target**: Active WebSocket connections vs. target.
- **Viewers**: Actual viewer count from Kick API (updated every 5s).
- **Attempts**: Total connection attempts.
- **Errors**: WebSocket errors encountered.
- **Remaining**: Time left before this streamer's session expires.

---

## Anti-Block Features

- **17 rotating User-Agents** — every token fetch and WebSocket connection uses a different realistic UA string.
- **Random connection delays** — `0.5–2.0s` between clients to avoid burst patterns.
- **Randomized ping intervals** — `15–25s` instead of a fixed 20s.
- **Proxy rotation** — every WebSocket connection uses its own randomly chosen proxy.
- **Bad proxy eviction** — proxies that fail in production are immediately removed from the pool.
- **Gradual ramp-up** — connections scale up over the first 4 minutes.
- **Token producer backoff** — consecutive failures increase wait time automatically.

---

## Auto-Reconnect

When a WebSocket connection drops, the worker immediately spawns a new connection with a fresh token and a different proxy (with a random reconnect delay). Viewer counts are maintained continuously.

---

## License

MIT

---

If you found this project useful, don't forget to give it a star.
