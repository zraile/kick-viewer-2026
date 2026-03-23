# Kick Viewer Bot — Multi-Tor Docker Edition v2.1.0

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
| `logs/kick-viewer.log` | Real-time rotating log file (auto-created, excluded from git) | — |

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

## Live Log System (`logs/kick-viewer.log`)

Every important event is written to `logs/kick-viewer.log` (rotating, max 10 MB, 3 backups). This replaces the need to guess what's happening from the terminal table alone.

Example log lines:

```
[2026-03-21 03:15:22] [WARNING] [ERROR]   [vleonn9] port:9052 | 403 Forbidden | health: -20 | action: cooling
[2026-03-21 03:15:23] [INFO]    [STANDBY] [efemeric3] produced token on port:9054 | standby_pool: 3/15
[2026-03-21 03:15:24] [DEBUG]   [WS_DROP] [uknowsg] port:9058 | reason: ConnectionClosed
[2026-03-21 03:15:25] [DEBUG]   [WS_CONN] [zumibox] port:9060 | connected | conn: 15/20
[2026-03-21 03:15:30] [INFO]    [NEWNYM]  port:9052 | sent SIGNAL NEWNYM via docker exec | health reset to 60
[2026-03-21 03:15:35] [DEBUG]   [TOKEN]   port:9054 | fetched token | pool: 352
[2026-03-21 03:15:40] [WARNING] [HEALTH]  port:9052 | score: 30→15 | status: COOLING→DEAD
[2026-03-21 03:15:45] [INFO]    [STATE]   periodic stats | port_health: 54/60 good | token_pool: 481 | streamers: 3 | total_conn: 177
```

The `logs/` directory is excluded from git (`.gitignore`).

---

## Stats Display

```
[+] Containers: 10 | Ports: 60 | TokenPool: 487 | Hits: 1234 | Miss: 12 | NEWNYM: 5
[+] Port Health: 55/60 good | Version: v2.1.0
Streamer             Conn/Target     Standby  Viewers    Attempts   Errors   Remaining
────────────────────────────────────────────────────────────────────────────────────────
streamer1            48/50           8        52         312        3        6d 22:14:09
streamer2            97/100          12       104        621        7        29d 18:00:00
streamer3            29/30           6        31         189        1        01:44:22
```

| Column | Description |
|--------|-------------|
| **Conn/Target** | Active WebSocket connections vs. target |
| **Standby** | Pre-fetched tokens ready for instant replacement (up to 15 per streamer) |
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
Each Tor port carries a health score (5–100, minimum floor is 5 — ports never fully die):

| Event | Score Change |
|-------|-------------|
| Successful connection | +10 |
| Connection failure | −10 |
| 403 Forbidden | −20 |
| Activity timeout | −8 |

| Score Range | Status | Behaviour |
|-------------|--------|-----------|
| 40–100 | ✅ Healthy | Can produce standby tokens |
| 30–40 | ⚠️ Degraded | Active clients only (no standby) |
| < 30 | 🚫 Cooling | 5-minute cooldown + passive +5 health every 60s |
| < 10 | ☠️ Dead | 10-minute cooldown + Tor circuit renewal (NEWNYM) |

Ports in cooling mode recover **+5 health every 60 seconds** (passive recovery). After NEWNYM, the port health resets to 60.

### Layer 3 — Standby TTL (90 seconds)
All standby tokens expire after 90 seconds. Expired tokens are automatically refreshed using a different Tor circuit, ensuring the standby pool is always fresh.

### Layer 4 — Tor Circuit Renewal (NEWNYM)
When a port's health score drops below the dead threshold, a `SIGNAL NEWNYM` is sent via the Tor control port (primary: `docker exec` + `nc`; fallback: Python `socket`), assigning a completely new IP address. The health score is then reset to **60** to allow recovery. All NEWNYM attempts and errors are logged in detail.

### Layer 5 — Activation Verification
When a standby token is used to replace a dropped connection, the WebSocket connect timeout is reduced to **2 seconds**. If the connection does not activate within that window, the port health is penalised and the next available standby token is tried.

---

## Dynamic Port-Client Limit (v2.1.0)

Instead of a fixed 2 clients per port, the bot dynamically calculates how many clients each port should spawn for a given streamer:

```
max_per_port = min(MAX_CLIENTS_PER_PORT_HARD_LIMIT, max(2, ceil(target_viewers / available_ports)))
```

- `MAX_CLIENTS_PER_PORT_HARD_LIMIT = 5` — safety upper bound
- Allows high-target streamers (e.g. 100 viewers) to reach their target even with fewer healthy ports

---

## Token Session Cache (v2.1.0)

`fetch_token()` caches the `tls_client.Session` object per port for up to **5 minutes**. The initial `kick.com` landing-page request (which sets cookies) is only made once per session, reducing TLS handshake overhead and improving token generation speed by 2–3×.

---

## Adaptive Client Refresh

- **Fast loop (0.3s):** Triggered when active connections are below the target. Dropped clients are detected and replaced within 300ms.
- **Slow loop (2.0s):** Used when the streamer is at full capacity — reduces CPU usage and avoids over-spawning.

---

## Anti-Block Features

- **TLS fingerprint rotation** — randomly cycles between `chrome_119`, `chrome_120`, `chrome_121`, `safari_17_2`, and `firefox_120` profiles for each token fetch and API call.
- **17 rotating User-Agents** — every request uses a different realistic UA string.
- **Token freshness enforcement** — tokens older than 5 minutes are automatically discarded from the pool.
- **Token session cache** — session/cookie reuse reduces redundant TLS handshakes (2–3× faster).
- **Random connection delays** — `0.5–2.0s` between clients to avoid burst patterns.
- **Randomised ping intervals** — `15–25s` instead of a fixed interval.
- **Port blacklisting** — 403 responses block the port for 120 seconds.
- **Port health scoring** — see 5-layer system above.
- **Token producer backoff** — consecutive failures increase wait time up to 5s.

---

## Performance Comparison

| Feature | v1.x | v2.0.0 | v2.1.0 |
|---------|------|--------|--------|
| Client refresh latency | ~2s | ~0.3s (adaptive) | ~0.3s (adaptive) |
| Standby pool | ❌ | ✅ Up to 10/streamer | ✅ Up to 15/streamer |
| Standby threshold | — | health ≥ 70 | health ≥ 40 |
| Standby TTL validation | ❌ | ✅ 90s | ✅ 90s |
| Port health scoring | ❌ | ✅ 0–100 | ✅ 5–100 (floor=5) |
| Port health penalties | — | −15/−30/−10 | −10/−20/−8 (gentler) |
| Cooling recovery | passive +2/min | passive +2/min | +5/min during cooling |
| Tor circuit renewal | ❌ | ✅ Auto NEWNYM | ✅ NEWNYM + socket fallback |
| NEWNYM health reset | — | 50 | 60 |
| Token freshness check | ❌ | ✅ 5-min max age | ✅ 5-min max age |
| Token session cache | ❌ | ❌ | ✅ 5-min reuse (2–3× faster) |
| TLS fingerprint rotation | ❌ | ✅ 5 profiles | ✅ 5 profiles |
| Dynamic port-client limit | ❌ | ❌ | ✅ up to 5/port |
| Periodic state save | ❌ | ✅ Every 5 min | ✅ Every 5 min |
| Live log file | ❌ | ❌ | ✅ logs/kick-viewer.log |

---

## v2.1.0 Key Parameters

| Constant | Value | Description |
|----------|-------|-------------|
| `STANDBY_MAX_PER_STREAMER` | 15 | Maximum standby tokens per streamer |
| `PORT_HEALTH_SUCCESS_BONUS` | +10 | Health gain per successful connection |
| `PORT_HEALTH_FAIL_PENALTY` | −10 | Health loss per general failure |
| `PORT_HEALTH_403_PENALTY` | −20 | Health loss per 403 Forbidden |
| `PORT_HEALTH_TIMEOUT_PENALTY` | −8 | Health loss per activity timeout |
| `PORT_HEALTH_STANDBY_THRESHOLD` | 40 | Min health to produce standby tokens |
| `PORT_HEALTH_FLOOR` | 5 | Minimum health score (never reaches 0) |
| `PORT_HEALTH_COOLING_RECOVERY` | +5 | Passive health gain every 60s during cooling |
| `PORT_HEALTH_NEWNYM_RESET` | 60 | Health value after NEWNYM is sent |
| `MAX_CLIENTS_PER_PORT_HARD_LIMIT` | 5 | Max clients per port (hard safety cap) |
| `SESSION_CACHE_TTL` | 300s | Session cache lifetime (5 minutes) |

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
- **Live Log:** All errors, NEWNYM events, standby activity, and port health changes are written to `logs/kick-viewer.log`.

---

## License

MIT

---

If you found this project useful, don't forget to give it a star.

