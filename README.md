# Kick Viewer Bot — Multi-Tor Docker Edition v2.0.0

A robust Kick.com viewbot using Docker and multiple Tor instances to bypass rate limits. Supports **multiple streamers simultaneously**, persistent session timers, auto-reconnect, and a 5-layer anti-block defence system.

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
| `kick-multi6.py` | **Recommended** — Multi-Tor, multi-streamer version with 5-layer anti-block | 6 |
| `kick-multi3.py` | Lightweight version, 3 Tor instances per container | 3 |
| `liste.txt` | Streamer list — one name or kick.com link per line | — |
| `state.json` | Auto-generated session state (persistent timers, excluded from git) | — |

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
6. The bot starts routing viewers to all streamers simultaneously.

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
[+] Containers: 10 | Ports: 60 | TokenPool: 487 | Hits: 1234 | Miss: 12 | NEWNYM: 5
[+] Port Health: 55/60 good | Version: v2.0.0
Streamer             Conn/Target     Standby  Viewers    Attempts   Errors   Remaining
────────────────────────────────────────────────────────────────────────────────────────
streamer1            48/50           8        52         312        3        6d 22:14:09
streamer2            97/100          10       104        621        7        29d 18:00:00
streamer3            29/30           6        31         189        1        01:44:22
```

| Column | Description |
|--------|-------------|
| **Conn/Target** | Active WebSocket connections vs. target |
| **Standby** | Pre-fetched tokens ready for instant replacement |
| **Viewers** | Actual viewer count from Kick API (updated every 5s) |
| **Attempts** | Total connection attempts |
| **Errors** | WebSocket errors encountered |
| **Remaining** | Time left before this streamer's session expires |
| **NEWNYM** | Total Tor circuit renewals triggered |
| **Port Health** | Number of ports in good health (not cooling/dead) |

---

## 5-Layer Anti-Block Defence System

### Layer 1 — Standby Health Check
Every 30 seconds, the standby pool is validated. Tokens older than 90 seconds or belonging to cooling ports are silently discarded and replaced.

### Layer 2 — Port Health Score
Each Tor port carries a health score (0–100):

| Event | Score Change |
|-------|-------------|
| Successful connection | +5 |
| Connection failure | −15 |
| 403 Forbidden | −30 |
| Activity timeout | −10 |

| Score Range | Status | Behaviour |
|-------------|--------|-----------|
| 70–100 | ✅ Healthy | Can produce standby tokens |
| 30–70 | ⚠️ Degraded | Active clients only |
| < 30 | 🚫 Cooling | 5-minute cooldown |
| < 10 | ☠️ Dead | 10-minute cooldown + Tor circuit renewal |

Ports passively recover (+2/min) once the cooldown expires.

### Layer 3 — Standby TTL (90 seconds)
All standby tokens expire after 90 seconds. Expired tokens are automatically refreshed using a different Tor circuit, ensuring the standby pool is always fresh.

### Layer 4 — Tor Circuit Renewal (NEWNYM)
When a port's health score drops below the dead threshold, a `SIGNAL NEWNYM` is sent via the Tor control port, assigning a completely new IP address. The health score is then reset to 50 to allow recovery.

### Layer 5 — Activation Verification
When a standby token is used to replace a dropped connection, the WebSocket connect timeout is reduced to **2 seconds**. If the connection does not activate within that window, the port health is penalised and the next available standby token is tried.

---

## Adaptive Client Refresh

- **Fast loop (0.3s):** Triggered when active connections are below the target. Dropped clients are detected and replaced within 300ms.
- **Slow loop (2.0s):** Used when the streamer is at full capacity — reduces CPU usage and avoids over-spawning.

---

## Anti-Block Features

- **TLS fingerprint rotation** — randomly cycles between `chrome_119`, `chrome_120`, `chrome_121`, `safari_17_2`, and `firefox_120` profiles for each token fetch and API call.
- **17 rotating User-Agents** — every request uses a different realistic UA string.
- **Token freshness enforcement** — tokens older than 5 minutes are automatically discarded from the pool.
- **Random connection delays** — `0.5–2.0s` between clients to avoid burst patterns.
- **Randomised ping intervals** — `15–25s` instead of a fixed interval.
- **Port blacklisting** — 403 responses block the port for 120 seconds.
- **Port health scoring** — see 5-layer system above.
- **Token producer backoff** — consecutive failures increase wait time up to 5s.

---

## Performance Comparison

| Feature | v1.x | v2.0.0 |
|---------|------|--------|
| Client refresh latency | ~2s | ~0.3s (adaptive) |
| Standby pool | ❌ | ✅ Up to 10 per streamer |
| Standby TTL validation | ❌ | ✅ 90s |
| Port health scoring | ❌ | ✅ 0–100 per port |
| Tor circuit renewal | ❌ | ✅ Auto NEWNYM |
| Token freshness check | ❌ | ✅ 5-min max age |
| TLS fingerprint rotation | ❌ | ✅ 5 profiles |
| Periodic state save | ❌ | ✅ Every 5 min |

---

## Auto-Reconnect

When a WebSocket connection drops, the adaptive loop (0.3s) detects it within 300ms. If a standby token is available, it is used immediately for near-instant replacement. Otherwise a fresh token is fetched from the main pool.

---

## Enhanced Error Management

- **Docker Check:** If Docker Desktop is closed, the bot reports it clearly.
- **Kick API Validation:** Reports if the channel is offline, not found, or hitting Cloudflare blocks.
- **Tor Readiness:** A live countdown shows bootstrap progress.
- **Smart Cleanup:** On Ctrl+C, state is saved and all Docker containers are removed automatically.
- **Crash Protection:** State is saved every 5 minutes by a background daemon thread.

---

## License

MIT

---

If you found this project useful, don't forget to give it a star.
