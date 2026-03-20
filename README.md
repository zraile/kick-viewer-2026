# Kick Viewer Bot (Multi-Tor Docker)

A robust Kick.com viewbot using Docker and multiple Tor instances to bypass rate limits. Now supports **multiple streamers simultaneously**, persistent session timers, auto-reconnect, and anti-block improvements.

## The Easy Way (Recommended)

**Prerequisite:** Make sure **Docker Desktop** is installed and **WSL 2** is enabled in Docker settings (Settings > General > Use the WSL 2 based engine).

1. **Start Docker Desktop** and wait for it to initialize.
2. **Edit `liste.txt`** — add streamer names or kick.com links, one per line.
3. **Double-click `run.bat`**.
4. The script will automatically:
   - Verify your Python installation.
   - Install or update all required libraries from `requirements.txt`.
   - Confirm Docker is running.
   - Build the multitor image if it's missing.
   - Launch the bot directly.

---

## Requirements

- Python 3.8+
- Docker Desktop
- Windows 10/11

## Files

| File | Description | Tor per Container |
|------|-------------|-------------------|
| `kick-single.py` | Simple version, 1 Tor instance per container | 1 |
| `kick-multi6.py` | **Recommended** — Multi-Tor, multi-streamer version, 6 Tor instances per container | 6 |
| `kick-multi3.py` | Lightweight version, 3 Tor instances per container | 3 |
| `liste.txt` | Streamer list — one name or kick.com link per line | — |
| `state.json` | Auto-generated session state (persistent timers) | — |

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
python kick-multi6.py
```

### Startup Flow

1. The bot reads `liste.txt` and shows how many streamers were found.
2. For **new** streamers (not in `state.json`), it asks:
   - How many viewers to assign
   - How long to run (supports `7d`, `2h`, `30m`, `1w`, `45m`, etc.)
3. For **existing** streamers (already in `state.json` with time remaining), it resumes automatically — no prompts needed.
4. Enter container count and base port.
5. Wait for Tor bootstrap (~45s).
6. The bot starts routing viewers to all streamers proportionally.

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

## Persistent Timer (`state.json`)

The bot saves each streamer's start time, duration, and target viewer count to `state.json`. When you restart the bot:

- **Time remaining:** The streamer resumes automatically with the remaining duration.
- **Expired:** The streamer is skipped (removed from the session).
- **New streamer added to `liste.txt`:** You are prompted for viewer count and duration.

`state.json` is excluded from git (`.gitignore`).

---

## Stats Display

```
[+] Containers: 10 | Ports: 60 | TokenPool: 487 | Hits: 1234 | Miss: 12
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
- **Port blacklisting** — ports that return 403 are blocked for 120 seconds; the bot automatically picks a different port.
- **Gradual ramp-up** — connections scale up over the first 2 minutes (25% → 50% → 75% → 100%).
- **Token producer backoff** — consecutive failures increase wait time up to 5s.

---

## Auto-Reconnect

When a WebSocket connection drops, the port pool loop immediately detects the completed task and spawns a new connection with a fresh token (with a 1–5s random reconnect delay). Viewer counts are maintained continuously.

---

## Enhanced Error Management

- **Docker Check:** If Docker Desktop is closed, the bot reports it clearly.
- **Kick API Validation:** Reports if the channel is offline, not found, or hitting Cloudflare blocks.
- **Tor Readiness:** A live countdown shows bootstrap progress.
- **Smart Cleanup:** On Ctrl+C, state is saved and all Docker containers are removed automatically.

---

## License

MIT

---

If you found this project useful, don't forget to give it a star.
