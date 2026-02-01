# Kick Viewer Bot (Multi-Tor Docker)

A robust Kick.com viewbot using Docker and multiple Tor instances to bypass rate limits. This project has been updated with a user-friendly launcher and professional error handling to make it "plug-and-play."

## The New Easy Way (Recommended)

I've added a `run.bat` file to handle all the boring stuff for you. You no longer need to manually install dependencies or check if Docker is ready.

**Prerequisite:** Make sure **Docker Desktop** is installed and **WSL 2** is enabled in Docker settings (Settings > General > Use the WSL 2 based engine).

1.  **Start Docker Desktop** and wait for it to initialize.
2.  **Double-click run.bat**.
3.  The script will automatically:
    *   Verify your Python installation.
    *   Install or update all required libraries from requirements.txt.
    *   Confirm Docker is running.
    *   Build the multitor image if it's missing.
    *   Launch the bot directly.

## Enhanced Error Management

I've completely revamped the error handling. Instead of cryptic terminal errors, the bot now gives you clear, actionable feedback in plain English:

*   **Docker Check:** If Docker Desktop is closed, the bot will tell you exactly that instead of crashing.
*   **Kick API Validation:** It checks if the channel is live, if the username is correct, or if you're hitting Cloudflare blocks.
*   **Tor Readiness:** A live countdown shows you exactly how much time is left for Tor instances to bootstrap.
*   **Smart Cleanup:** When you stop the bot (Ctrl+C), it automatically wipes all Docker containers to keep your system clean.

---

## Requirements

- Python 3.8+
- Docker Desktop
- Windows 10/11

## Files

| File | Description | Tor per Container |
|------|-------------|-------------------|
| kick-single.py | Simple version, 1 Tor instance per container | 1 |
| kick-multi6.py | **Recommended** - Multi-Tor version, 6 Tor instances per container | 6 |
| kick-multi3.py | Lightweight version, 3 Tor instances per container | 3 |

## Usage (Manual)

If you prefer the old-school way:

```bash
python kick-multi6.py
```

1. Enter container count (e.g. 15)
2. Enter base port (default: 19050)
3. Wait for Tor bootstrap
4. Enter channel name

## Performance Comparison

| Version | For 90 Ports | RAM Usage | Stability |
|---------|--------------|-----------|-----------|
| kick-single.py | 90 containers | ~9 GB | Very stable |
| kick-multi6.py | 15 containers | ~2 GB | Stable |
| kick-multi3.py | 30 containers | ~3 GB | Stable |

## Stats Explained

- **Containers**: Running Docker containers.
- **Ports**: Total Tor SOCKS ports available.
- **Conn**: Active WebSocket connections (This is what increases viewers).
- **Attempts**: Total connection attempts made.
- **Viewers**: Actual viewer count reported by Kick API.
- **TokenPool**: Number of ready-to-use tokens in the pool.

## License

MIT

---

If you found this project useful, don't forget to give it a star.
