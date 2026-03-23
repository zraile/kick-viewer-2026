VERSION = "2.1.0"

import sys
import time
import math
import random
import datetime
import threading
import asyncio
import json
import os
import socket
import subprocess
import logging
from logging.handlers import RotatingFileHandler
from threading import Thread
from queue import Queue, Empty
import tls_client

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    import aiohttp
    from aiohttp_socks import ProxyConnector
    FULL_SUPPORT = True
except ImportError:
    FULL_SUPPORT = False

# ── Core settings ─────────────────────────────────────────────────────────────
CLIENT_TOKEN = os.environ.get("KICK_CLIENT_TOKEN", "e1393935a959b4020a4491574f6490129f678acdaa92760471263db43487f823")
DOCKER_IMAGE = "multitor:latest"
CONTAINER_PREFIX = "multitor_"
PORTS_PER_CONTAINER = 6
CONNS_PER_PORT = 120
BASE_TOKEN_POOL_SIZE = 500
BASE_INITIAL_POOL_WAIT = 150
BASE_TOKEN_PRODUCERS = 60
TOKEN_POOL_SIZE = 500
INITIAL_POOL_WAIT = 150
TOKEN_PRODUCERS = 60
PONG_TIMEOUT = 180
STATE_FILE = "state.json"
LIST_FILE = "liste.txt"

# Token pool scaling: one scale unit per this many target viewers
TOKEN_SCALE_VIEWERS = 50

# Port blacklist duration (seconds) when a 403 is received
PORT_BLACKLIST_DURATION = 120

# Token producer backoff settings
BACKOFF_FAILURE_THRESHOLD = 5
BACKOFF_MULTIPLIER = 0.5
MAX_BACKOFF_SECONDS = 5.0

# Ping interval randomization range (seconds) — anti-block
MIN_PING_INTERVAL = 15
MAX_PING_INTERVAL = 25

# Client spawn delay range (seconds) — anti-block
MIN_SPAWN_DELAY = 0.5
MAX_SPAWN_DELAY = 2.0

# Auto-reconnect delay range (seconds)
MIN_RECONNECT_DELAY = 1
MAX_RECONNECT_DELAY = 5

# ── v2.1.0 constants ──────────────────────────────────────────────────────────

# Standby pool
STANDBY_MAX_PER_STREAMER = 15
STANDBY_FILL_MIN_POOL = 50       # produce standby only when main pool has this many tokens
STANDBY_TTL = 90                 # max age (seconds) for a standby token

# Token freshness
TOKEN_MAX_AGE_SECONDS = 300      # tokens older than this are discarded from main pool

# Port health score system (0-100)
PORT_HEALTH_INITIAL = 100
PORT_HEALTH_SUCCESS_BONUS = 10
PORT_HEALTH_FAIL_PENALTY = 10
PORT_HEALTH_403_PENALTY = 20
PORT_HEALTH_TIMEOUT_PENALTY = 8
PORT_HEALTH_STANDBY_THRESHOLD = 40   # min health to produce standby tokens
PORT_HEALTH_COOLING_THRESHOLD = 30   # below this → cooling mode
PORT_HEALTH_DEAD_THRESHOLD = 10      # below this → dead (longer cooling + NEWNYM)
PORT_HEALTH_FLOOR = 5                # minimum health score (ports never fully die)
PORT_HEALTH_COOLING_RECOVERY = 5     # passive health gain every 60s while cooling
PORT_HEALTH_NEWNYM_RESET = 60        # health value after NEWNYM is sent

# Dynamic port-client limit
MAX_CLIENTS_PER_PORT_HARD_LIMIT = 5  # safety upper bound: max clients from one port

# Standby production retry delay when no healthy port is available (seconds)
STANDBY_PRODUCTION_RETRY_DELAY = 2

PORT_COOLING_DURATION = 300          # seconds for cooling mode
PORT_DEAD_DURATION = 600             # seconds for dead mode

# Periodic save interval
PERIODIC_SAVE_INTERVAL = 300         # every 5 minutes

# TLS fingerprint pool for rotation
TLS_CLIENT_IDENTIFIERS = [
    "chrome_119",
    "chrome_120",
    "chrome_121",
    "safari_17_2",
    "firefox_120",
]

# ── User-Agents ───────────────────────────────────────────────────────────────
USER_AGENTS = [
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

def get_random_ua():
    return random.choice(USER_AGENTS)

def get_random_tls_id():
    return random.choice(TLS_CLIENT_IDENTIFIERS)


# ── Logging setup ─────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
_log_handler = RotatingFileHandler(
    "logs/kick-viewer.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=3,
    encoding="utf-8",
)
_log_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger = logging.getLogger("kick-viewer")
logger.setLevel(logging.DEBUG)
logger.addHandler(_log_handler)


# ── Globals ───────────────────────────────────────────────────────────────────
stop = False
lock = threading.Lock()
token_queue = Queue()
token_hits = 0
token_misses = 0
total_newnyms = 0
containers = []
all_ports = []
port_blacklist = {}
port_blacklist_lock = threading.Lock()
port_health_scores = {}
port_health_lock = threading.Lock()

# Session cache for token fetching: {port: (session_object, last_used_timestamp)}
SESSION_CACHE: dict = {}
session_cache_lock = threading.Lock()
SESSION_CACHE_TTL = 300  # reuse sessions up to 5 minutes
port_cooling_until = {}
global_base_port = 19050
streamers = []


# ── StreamerInfo class ────────────────────────────────────────────────────────
class StreamerInfo:
    def __init__(self, name, target_viewers, duration_seconds, start_time=None):
        self.name = name
        self.target_viewers = target_viewers
        self.duration_seconds = duration_seconds
        self.start_time = start_time or datetime.datetime.now()
        self.channel_id = None
        self.stream_id = None
        self.connections = 0
        self.attempts = 0
        self.pings = 0
        self.heartbeats = 0
        self.viewers = 0
        self.last_check = 0
        self.ws_errors = 0
        self.pending_connections = 0
        self.active = True
        self.assigned_ports = []
        self.standby_tokens = Queue()   # pre-fetched (token, port, timestamp) tuples

    def total_claimed(self):
        return self.connections + self.pending_connections

    def time_remaining(self):
        elapsed = (datetime.datetime.now() - self.start_time).total_seconds()
        return max(0.0, self.duration_seconds - elapsed)

    def is_expired(self):
        return self.time_remaining() <= 0

    def format_remaining(self):
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


# ── Token pool settings ───────────────────────────────────────────────────────
def calculate_token_settings(total_target_viewers):
    global TOKEN_POOL_SIZE, INITIAL_POOL_WAIT, TOKEN_PRODUCERS
    scale = max(1, math.ceil(total_target_viewers / TOKEN_SCALE_VIEWERS))
    TOKEN_POOL_SIZE = BASE_TOKEN_POOL_SIZE + (scale - 1) * 50
    INITIAL_POOL_WAIT = min(300, BASE_INITIAL_POOL_WAIT + (scale - 1) * 10)
    TOKEN_PRODUCERS = BASE_TOKEN_PRODUCERS + (scale - 1) * 5


# ── Docker helpers ────────────────────────────────────────────────────────────
def run_cmd(cmd):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        return result.returncode == 0, result.stdout.strip()
    except Exception as exc:
        logger.debug(f"[CMD]     error running command: {exc}")
        return False, ""

def build_image():
    print("[*] Building multitor image...")
    success, _ = run_cmd("docker build -t multitor:latest -f Dockerfile.multitor .")
    return success

def create_container(index, base_port):
    name = f"{CONTAINER_PREFIX}{index}"
    run_cmd(f"docker rm -f {name}")
    port_mappings = " ".join([f"-p {base_port + i}:{9050 + i}" for i in range(PORTS_PER_CONTAINER)])
    cmd = f"docker run -d --name {name} {port_mappings} {DOCKER_IMAGE}"
    success, _ = run_cmd(cmd)
    if success:
        containers.append(name)
        for i in range(PORTS_PER_CONTAINER):
            all_ports.append(base_port + i)
        return True
    else:
        print(f"\n[DEBUG] Container {name} failed to start. Check Docker Desktop is running.")
    return False

def cleanup_containers():
    for name in containers:
        run_cmd(f"docker rm -f {name}")


# ── Port health management ────────────────────────────────────────────────────
def get_port_health(port):
    with port_health_lock:
        return port_health_scores.get(port, PORT_HEALTH_INITIAL)

def update_port_health(port, delta):
    """Update health score for a port and apply cooling if needed."""
    with port_health_lock:
        current = port_health_scores.get(port, PORT_HEALTH_INITIAL)
        new_score = max(PORT_HEALTH_FLOOR, min(100, current + delta))
        port_health_scores[port] = new_score
        old_status = "GOOD" if current >= PORT_HEALTH_COOLING_THRESHOLD else ("COOLING" if current >= PORT_HEALTH_DEAD_THRESHOLD else "DEAD")
        new_status = "GOOD" if new_score >= PORT_HEALTH_COOLING_THRESHOLD else ("COOLING" if new_score >= PORT_HEALTH_DEAD_THRESHOLD else "DEAD")
        if delta < 0 and new_score < PORT_HEALTH_COOLING_THRESHOLD:
            now = time.time()
            # Only set cooling if not already cooling (avoid repeated extension)
            if port_cooling_until.get(port, 0) < now:
                duration = PORT_DEAD_DURATION if new_score < PORT_HEALTH_DEAD_THRESHOLD else PORT_COOLING_DURATION
                port_cooling_until[port] = now + duration
                if old_status != new_status:
                    logger.warning(f"[HEALTH]  port:{port} | score: {current}→{new_score} | status: {old_status}→{new_status}")

def is_port_cooling(port):
    with port_health_lock:
        return port_cooling_until.get(port, 0) >= time.time()

def get_random_healthy_port(port_pool=None):
    """Return a random port that is not blacklisted and not in cooling."""
    pool = port_pool if port_pool else all_ports
    if not pool:
        return None
    now = time.time()
    with port_health_lock:
        with port_blacklist_lock:
            available = [
                p for p in pool
                if port_cooling_until.get(p, 0) < now
                and port_blacklist.get(p, 0) < now
                and port_health_scores.get(p, PORT_HEALTH_INITIAL) >= PORT_HEALTH_COOLING_THRESHOLD
            ]
    return random.choice(available) if available else None


# ── Port management ───────────────────────────────────────────────────────────
def get_random_port(port_pool=None):
    pool = port_pool if port_pool else all_ports
    if not pool:
        return 9050
    now = time.time()
    with port_health_lock:
        with port_blacklist_lock:
            available = [
                p for p in pool
                if port_blacklist.get(p, 0) < now
                and port_cooling_until.get(p, 0) < now
            ]
    return random.choice(available) if available else random.choice(pool)

def blacklist_port(port, duration=PORT_BLACKLIST_DURATION):
    with port_blacklist_lock:
        port_blacklist[port] = time.time() + duration

def get_proxy_dict(port=None, port_pool=None):
    p = port if port else get_random_port(port_pool)
    return {"http": f"socks5://127.0.0.1:{p}", "https": f"socks5://127.0.0.1:{p}"}


# ── Tor circuit renewal (NEWNYM) ──────────────────────────────────────────────
def send_newnym(port):
    """Send SIGNAL NEWNYM to the Tor control port for a given SOCKS port."""
    global total_newnyms
    try:
        port_idx = port - global_base_port
        if port_idx < 0:
            logger.error(f"[NEWNYM]  port:{port} | invalid port index (port_idx={port_idx})")
            return
        container_idx = port_idx // PORTS_PER_CONTAINER
        tor_idx = port_idx % PORTS_PER_CONTAINER
        container_name = f"{CONTAINER_PREFIX}{container_idx}"
        control_port = 9150 + tor_idx
        logger.info(f"[NEWNYM]  port:{port} | container:{container_name} | control_port:{control_port} | sending SIGNAL NEWNYM")

        # Primary method: docker exec + nc
        cmd = (
            f'docker exec {container_name} sh -c '
            f'"printf \'AUTHENTICATE\\r\\nSIGNAL NEWNYM\\r\\nQUIT\\r\\n\' '
            f'| nc 127.0.0.1 {control_port} -w 3"'
        )
        success, output = run_cmd(cmd)
        if success:
            with lock:
                total_newnyms += 1
            with port_health_lock:
                port_health_scores[port] = PORT_HEALTH_NEWNYM_RESET
                port_cooling_until[port] = 0  # clear cooling
            logger.info(f"[NEWNYM]  port:{port} | sent SIGNAL NEWNYM via docker exec | health reset to {PORT_HEALTH_NEWNYM_RESET}")
            return

        logger.warning(f"[NEWNYM]  port:{port} | docker exec failed (output: {output!r}), trying socket fallback")

        # Fallback method: Python socket directly to Tor control port
        try:
            with socket.create_connection(("127.0.0.1", control_port), timeout=5) as sock:
                sock.sendall(b"AUTHENTICATE\r\nSIGNAL NEWNYM\r\nQUIT\r\n")
                resp = sock.recv(1024).decode(errors="replace")
            if "250" in resp:
                with lock:
                    total_newnyms += 1
                with port_health_lock:
                    port_health_scores[port] = PORT_HEALTH_NEWNYM_RESET
                    port_cooling_until[port] = 0
                logger.info(f"[NEWNYM]  port:{port} | sent SIGNAL NEWNYM via socket | health reset to {PORT_HEALTH_NEWNYM_RESET}")
            else:
                logger.error(f"[NEWNYM]  port:{port} | socket fallback unexpected response: {resp!r}")
        except ConnectionRefusedError:
            logger.error(f"[NEWNYM]  port:{port} | socket fallback: connection refused on control_port:{control_port}")
        except socket.timeout:
            logger.error(f"[NEWNYM]  port:{port} | socket fallback: timeout connecting to control_port:{control_port}")
        except Exception as exc:
            logger.error(f"[NEWNYM]  port:{port} | socket fallback error: {exc}")
    except Exception as exc:
        logger.error(f"[NEWNYM]  port:{port} | unexpected error: {exc}")


# ── Port health manager (background daemon) ───────────────────────────────────
def port_health_manager():
    """
    Layer 4: Monitors port health scores, triggers Tor NEWNYM for dead ports,
    and passively restores health for recovering ports.
    """
    global stop
    while not stop:
        time.sleep(60)
        now = time.time()
        dead_ports = []
        with port_health_lock:
            for port, health in list(port_health_scores.items()):
                in_cooling = port_cooling_until.get(port, 0) >= now
                if in_cooling:
                    # Time-based recovery: cooling ports slowly regain health every 60s
                    new_health = min(PORT_HEALTH_INITIAL, health + PORT_HEALTH_COOLING_RECOVERY)
                    if new_health != health:
                        port_health_scores[port] = new_health
                        logger.debug(f"[HEALTH]  port:{port} | cooling recovery: {health}→{new_health}")
                elif health < PORT_HEALTH_INITIAL:
                    # Normal passive recovery when not in cooling
                    port_health_scores[port] = min(PORT_HEALTH_INITIAL, health + 2)

                # Dead port: send NEWNYM and reset health
                current_health = port_health_scores[port]
                if current_health <= PORT_HEALTH_DEAD_THRESHOLD and port_cooling_until.get(port, 0) < now:
                    port_cooling_until[port] = now + PORT_DEAD_DURATION
                    dead_ports.append(port)
                    logger.warning(f"[HEALTH]  port:{port} | score: {current_health} | status: DEAD | triggering NEWNYM")

        for port in dead_ports:
            Thread(target=send_newnym, args=(port,), daemon=True).start()


# ── Channel name cleaning ─────────────────────────────────────────────────────
def clean_channel_name(name):
    if "kick.com/" in name:
        parts = name.split("kick.com/")
        ch = parts[1].split("/")[0].split("?")[0]
        return ch.lower()
    return name.lower()


# ── State persistence ─────────────────────────────────────────────────────────
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except Exception as exc:
        logger.debug(f"[STATE]   failed to load state: {exc}")
        return {}

def save_state(streamer_list):
    data = {"streamers": {}}
    for s in streamer_list:
        data["streamers"][s.name] = {
            "start_time": s.start_time.isoformat(),
            "duration_seconds": s.duration_seconds,
            "target_viewers": s.target_viewers,
        }
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        logger.warning(f"[STATE]   failed to save state: {exc}")

def periodic_state_saver():
    """Daemon thread: saves state every PERIODIC_SAVE_INTERVAL seconds."""
    global stop
    while not stop:
        time.sleep(PERIODIC_SAVE_INTERVAL)
        if not stop:
            save_state(streamers)


# ── liste.txt reading ─────────────────────────────────────────────────────────
def read_liste():
    if not os.path.exists(LIST_FILE):
        print(f"\033[31m[!] ERROR: '{LIST_FILE}' not found!\033[0m")
        print(f"    Please create a '{LIST_FILE}' file with one streamer name or link per line.")
        return []
    entries = []
    with open(LIST_FILE, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            entries.append(line)
    if not entries:
        print(f"\033[31m[!] ERROR: '{LIST_FILE}' is empty or has no valid entries.\033[0m")
    return entries


# Hot-reload interval (seconds): re-read liste.txt every 60 minutes
LISTE_RELOAD_INTERVAL = 3600


def liste_hot_reloader():
    """
    Daemon thread: every LISTE_RELOAD_INTERVAL seconds, re-reads liste.txt.
    - Streamers removed from the file are deactivated.
    - New streamers present in state.json are resumed automatically.
    - New streamers not in state.json are logged; restart to configure them.
    """
    global stop
    while not stop:
        for _ in range(LISTE_RELOAD_INTERVAL):
            if stop:
                return
            time.sleep(1)

        logger.info(f"[RELOAD]  hot-reload: re-reading {LIST_FILE}")
        try:
            entries = read_liste()
        except Exception as exc:
            logger.warning(f"[RELOAD]  failed to read {LIST_FILE}: {exc}")
            continue

        current_names = {s.name for s in streamers}
        new_names = {clean_channel_name(e) for e in entries}

        # Deactivate streamers removed from liste.txt
        for streamer in streamers:
            if streamer.name not in new_names and streamer.active:
                streamer.active = False
                logger.info(f"[RELOAD]  {streamer.name} removed from {LIST_FILE} — deactivated")

        # Add new streamers found in liste.txt
        state = load_state()
        state_streamers = state.get("streamers", {})
        added = 0
        for entry in entries:
            name = clean_channel_name(entry)
            if name in current_names:
                continue
            if name in state_streamers:
                s_data = state_streamers[name]
                start_time_dt = datetime.datetime.fromisoformat(s_data["start_time"])
                duration_secs = s_data["duration_seconds"]
                target_viewers = s_data["target_viewers"]
                si = StreamerInfo(name, target_viewers, duration_secs, start_time_dt)
                if si.is_expired():
                    logger.info(f"[RELOAD]  {name}: found in state.json but expired — skipping")
                    continue
                streamers.append(si)
                Thread(target=standby_producer, args=(si,), daemon=True).start()
                Thread(target=standby_health_checker, args=(si,), daemon=True).start()
                get_channel_info_for(si)
                logger.info(
                    f"[RELOAD]  {name}: resumed from state.json"
                    f" | target: {target_viewers} | remaining: {si.format_remaining()}"
                )
                added += 1
            else:
                logger.info(
                    f"[RELOAD]  {name}: new streamer found in {LIST_FILE} but not in state.json"
                    f" — restart the bot to configure viewer count and duration"
                )

        if added:
            logger.info(f"[RELOAD]  hot-reload complete: {added} new streamer(s) added")
        else:
            logger.info(f"[RELOAD]  hot-reload complete: no new streamers added")


# ── Duration parsing ──────────────────────────────────────────────────────────
_DURATION_UNITS = {'w': 604800, 'd': 86400, 'h': 3600, 'm': 60, 's': 1}

def parse_duration(text):
    text = text.strip().lower()
    total = 0
    i = 0
    while i < len(text):
        num = ''
        while i < len(text) and text[i].isdigit():
            num += text[i]
            i += 1
        if not num:
            i += 1
            continue
        unit = text[i] if i < len(text) else 's'
        i += 1
        total += int(num) * _DURATION_UNITS.get(unit, 1)
    return total if total > 0 else None


# ── Channel info (per streamer) ───────────────────────────────────────────────
def get_channel_info_for(streamer):
    try:
        s = tls_client.Session(client_identifier=get_random_tls_id(), random_tls_extension_order=True)
        if all_ports:
            s.proxies = get_proxy_dict()
        s.headers.update({'Accept': 'application/json', 'User-Agent': get_random_ua()})
        response = s.get(f'https://kick.com/api/v2/channels/{streamer.name}', timeout_seconds=30)
        if response.status_code == 200:
            data = response.json()
            streamer.channel_id = data.get("id")
            if 'livestream' in data and data['livestream']:
                streamer.stream_id = data['livestream'].get('id')
            else:
                print(f"\033[33m[!] {streamer.name} is OFFLINE. Bot will run but viewers might not increase.\033[0m")
            return streamer.channel_id
        elif response.status_code == 404:
            print(f"\033[31m[!] Channel '{streamer.name}' not found! Please check the username.\033[0m")
        elif response.status_code == 403:
            print(f"\033[31m[!] Blocked by Kick API (403) for '{streamer.name}'. Try different proxy.\033[0m")
        else:
            print(f"\033[31m[!] Unexpected API response for '{streamer.name}' (Code: {response.status_code})\033[0m")
    except Exception as e:
        print(f"\033[31m[!] Connection error for '{streamer.name}': {e}\033[0m")
    return None


# ── Viewer count (per streamer) ───────────────────────────────────────────────
def get_viewer_count_for(streamer):
    if not streamer.stream_id:
        return 0
    try:
        s = tls_client.Session(client_identifier=get_random_tls_id(), random_tls_extension_order=True)
        if all_ports:
            s.proxies = get_proxy_dict()
        s.headers.update({'Accept': 'application/json', 'User-Agent': get_random_ua()})
        response = s.get(f"https://kick.com/current-viewers?ids[]={streamer.stream_id}", timeout_seconds=15)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                streamer.viewers = data[0].get('viewers', 0)
                streamer.last_check = time.time()
    except Exception as exc:
        logger.debug(f"[VIEWER]  [{streamer.name}] error fetching viewer count: {exc}")
    return streamer.viewers


# ── Token fetching ────────────────────────────────────────────────────────────
def fetch_token(port=None):
    try:
        now = time.time()
        session_key = port if port else 0
        s = None

        # Session cache: reuse existing session if less than SESSION_CACHE_TTL old
        with session_cache_lock:
            cached = SESSION_CACHE.get(session_key)
            if cached:
                s_obj, last_used = cached
                if now - last_used < SESSION_CACHE_TTL:
                    s = s_obj
                else:
                    del SESSION_CACHE[session_key]

        if s is None:
            s = tls_client.Session(client_identifier=get_random_tls_id(), random_tls_extension_order=True)
            if all_ports:
                s.proxies = get_proxy_dict(port)
            s.headers.update({
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'User-Agent': get_random_ua(),
            })
            s.get("https://kick.com", timeout_seconds=20)
            with session_cache_lock:
                SESSION_CACHE[session_key] = (s, now)

        s.headers["X-CLIENT-TOKEN"] = CLIENT_TOKEN
        response = s.get('https://websockets.kick.com/viewer/v1/token', timeout_seconds=20)
        if response.status_code == 200:
            data = response.json()
            token = data.get("data", {}).get("token")
            if token:
                logger.debug(f"[TOKEN]   port:{port} | fetched token | pool: {token_queue.qsize()}")
                # Update session last used time
                with session_cache_lock:
                    SESSION_CACHE[session_key] = (s, time.time())
                return token
        elif response.status_code == 403:
            if port:
                blacklist_port(port)
                update_port_health(port, -PORT_HEALTH_403_PENALTY)
            # Invalidate cached session on 403
            with session_cache_lock:
                SESSION_CACHE.pop(session_key, None)
            logger.warning(f"[TOKEN]   port:{port} | 403 Forbidden | health penalized -{PORT_HEALTH_403_PENALTY}")
        else:
            logger.warning(f"[TOKEN]   port:{port} | HTTP {response.status_code}")
    except Exception as exc:
        logger.debug(f"[TOKEN]   port:{port} | error: {exc}")
        with session_cache_lock:
            SESSION_CACHE.pop(session_key, None)
    return None


# ── Token producer with backoff ───────────────────────────────────────────────
def token_producer():
    global stop
    consecutive_failures = 0
    while not stop:
        try:
            if token_queue.qsize() < TOKEN_POOL_SIZE:
                port = get_random_port()
                token = fetch_token(port)
                if token:
                    token_queue.put((token, port, time.time()))
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures > BACKOFF_FAILURE_THRESHOLD:
                        backoff = min(MAX_BACKOFF_SECONDS, BACKOFF_MULTIPLIER * consecutive_failures)
                        time.sleep(backoff)
            else:
                time.sleep(0.05)
                consecutive_failures = 0
        except Exception as exc:
            consecutive_failures += 1
            logger.debug(f"[TOKEN]   producer error: {exc}")
            time.sleep(0.1)

def get_token_from_pool():
    """
    Get a fresh token from the main pool.
    Discards tokens older than TOKEN_MAX_AGE_SECONDS.
    Returns (token, port) or (None, None).
    """
    global token_hits, token_misses
    now = time.time()
    while True:
        try:
            token, port, ts = token_queue.get(timeout=0.1)
            age = now - ts
            if age > TOKEN_MAX_AGE_SECONDS:
                logger.debug(f"[TOKEN]   port:{port} | stale token discarded | age: {age:.0f}s | pool: {token_queue.qsize()}")
                continue
            with lock:
                token_hits += 1
            return token, port
        except Empty:
            with lock:
                token_misses += 1
            return None, None


# ── Standby token pool ────────────────────────────────────────────────────────
def get_standby_token(streamer):
    """
    Layer 3/5: Get a fresh standby token from the streamer's standby pool.
    Discards tokens older than STANDBY_TTL or from cooling ports.
    Returns (token, port, timestamp) or None.
    """
    now = time.time()
    while True:
        try:
            item = streamer.standby_tokens.get_nowait()
            token, port, ts = item
            age = now - ts
            if age < STANDBY_TTL and not is_port_cooling(port):
                pool_size = streamer.standby_tokens.qsize()
                logger.debug(f"[STANDBY] [{streamer.name}] consumed token from port:{port} | age: {age:.0f}s | standby_pool: {pool_size}/{STANDBY_MAX_PER_STREAMER}")
                return item
            # Stale or port in cooling — discard
            logger.debug(f"[STANDBY] [{streamer.name}] discarded stale/cooling token from port:{port} | age: {age:.0f}s")
        except Empty:
            return None

def standby_producer(streamer):
    """
    Produces standby tokens for a streamer when the main pool has sufficient
    tokens and the standby queue needs filling.
    """
    global stop
    while not stop and streamer.active and not streamer.is_expired():
        try:
            needs_standby = streamer.standby_tokens.qsize() < STANDBY_MAX_PER_STREAMER
            pool_healthy = token_queue.qsize() >= STANDBY_FILL_MIN_POOL
            if needs_standby and pool_healthy:
                port = get_random_healthy_port()
                if port and get_port_health(port) >= PORT_HEALTH_STANDBY_THRESHOLD:
                    token = fetch_token(port)
                    if token:
                        update_port_health(port, PORT_HEALTH_SUCCESS_BONUS)
                        streamer.standby_tokens.put((token, port, time.time()))
                        pool_size = streamer.standby_tokens.qsize()
                        logger.info(f"[STANDBY] [{streamer.name}] produced token on port:{port} | standby_pool: {pool_size}/{STANDBY_MAX_PER_STREAMER}")
                    else:
                        update_port_health(port, -PORT_HEALTH_FAIL_PENALTY)
                        time.sleep(STANDBY_PRODUCTION_RETRY_DELAY)
                else:
                    time.sleep(5)
            else:
                time.sleep(5)
        except Exception as exc:
            logger.debug(f"[STANDBY] [{streamer.name}] producer error: {exc}")
            time.sleep(5)

def standby_health_checker(streamer):
    """
    Layer 1: Every 30s, drain the standby queue and discard tokens that are
    older than STANDBY_TTL or whose port is in cooling mode.
    """
    global stop
    while not stop and streamer.active:
        time.sleep(30)
        now = time.time()
        fresh = []
        while True:
            try:
                item = streamer.standby_tokens.get_nowait()
                token, port, ts = item
                if now - ts < STANDBY_TTL and not is_port_cooling(port):
                    fresh.append(item)
                # else discard stale/cooling-port token
            except Empty:
                break
        for item in fresh:
            streamer.standby_tokens.put(item)


# ── Stats display ─────────────────────────────────────────────────────────────
def show_stats():
    global stop
    n = 4 + len(streamers)
    print("\n" * n)
    os.system('cls' if os.name == 'nt' else 'clear')
    last_log_time = 0
    while not stop:
        try:
            for s in streamers:
                if time.time() - s.last_check >= 5:
                    get_viewer_count_for(s)

            # Count healthy ports
            now = time.time()
            with port_health_lock:
                healthy_ports = sum(
                    1 for p in all_ports
                    if port_health_scores.get(p, PORT_HEALTH_INITIAL) >= PORT_HEALTH_COOLING_THRESHOLD
                    and port_cooling_until.get(p, 0) < now
                )

            lines = [
                f"\033[2K\r[+] Containers: \033[32m{len(containers)}\033[0m | Ports: \033[32m{len(all_ports)}\033[0m"
                f" | TokenPool: \033[32m{token_queue.qsize()}\033[0m"
                f" | Hits: \033[32m{token_hits}\033[0m | Miss: \033[31m{token_misses}\033[0m"
                f" | NEWNYM: \033[36m{total_newnyms}\033[0m",
                f"\033[2K\r[+] Port Health: \033[32m{healthy_ports}/{len(all_ports)}\033[0m good"
                f" | Version: \033[94mv{VERSION}\033[0m",
                f"\033[2K\r{'Streamer':<20} {'Conn/Target':<15} {'Standby':<8} {'Viewers':<10} {'Attempts':<10} {'Errors':<8} {'Remaining':<15}",
                f"\033[2K\r{'-'*88}",
            ]
            for s in streamers:
                if s.active:
                    conn_str = f"{s.connections}/{s.target_viewers}"
                    remaining = s.format_remaining()
                    standby_count = s.standby_tokens.qsize()
                    lines.append(
                        f"\033[2K\r{s.name:<20} \033[32m{conn_str:<15}\033[0m "
                        f"\033[36m{standby_count:<8}\033[0m"
                        f"\033[32m{s.viewers:<10}\033[0m \033[32m{s.attempts:<10}\033[0m "
                        f"\033[31m{s.ws_errors:<8}\033[0m \033[33m{remaining:<15}\033[0m"
                    )
                else:
                    lines.append(f"\033[2K\r{s.name:<20} \033[31mEXPIRED\033[0m")

            print(f"\033[{n}A", end="")
            for line in lines:
                print(line)
            sys.stdout.flush()

            # Periodic summary log every 30 seconds
            if now - last_log_time >= 30:
                total_conn = sum(s.connections for s in streamers if s.active)
                logger.info(
                    f"[STATE]   periodic stats | port_health: {healthy_ports}/{len(all_ports)} good"
                    f" | token_pool: {token_queue.qsize()} | streamers: {len(streamers)} | total_conn: {total_conn}"
                    f" | newnyms: {total_newnyms}"
                )
                for s in streamers:
                    if s.active:
                        logger.info(
                            f"[STATE]   [{s.name}] conn: {s.connections}/{s.target_viewers}"
                            f" | standby: {s.standby_tokens.qsize()}/{STANDBY_MAX_PER_STREAMER}"
                            f" | errors: {s.ws_errors} | viewers: {s.viewers}"
                        )
                last_log_time = now

            time.sleep(1)
        except Exception as exc:
            logger.debug(f"[STATS]   display error: {exc}")
            time.sleep(1)


# ── WebSocket handler ─────────────────────────────────────────────────────────
async def ws_handler(session, token, streamer, port, connect_timeout=30.0):
    """
    Handles a single WebSocket viewer connection.

    port            — SOCKS proxy port used (for health score updates)
    connect_timeout — seconds to wait for WS connect (2s for standby activation
                      verification, 30s for normal connections)
    """
    connected = False
    ws = None
    try:
        url = f"wss://websockets.kick.com/viewer/v1/connect?token={token}"
        ws = await session.ws_connect(
            url,
            timeout=aiohttp.ClientTimeout(total=connect_timeout, connect=connect_timeout),
        )
        # Layer 2/5: successful connect → boost port health
        update_port_health(port, PORT_HEALTH_SUCCESS_BONUS)
        with lock:
            streamer.connections += 1
            streamer.pending_connections = max(0, streamer.pending_connections - 1)
        connected = True
        logger.debug(f"[WS_CONN] [{streamer.name}] port:{port} | connected | conn: {streamer.connections}/{streamer.target_viewers}")

        subscribe = {"event": "pusher:subscribe", "data": {"auth": "", "channel": f"channel.{streamer.channel_id}"}}
        await ws.send_str(json.dumps(subscribe))

        if streamer.stream_id:
            chatroom_sub = {"event": "pusher:subscribe", "data": {"auth": "", "channel": f"chatrooms.{streamer.channel_id}.v2"}}
            await ws.send_str(json.dumps(chatroom_sub))

        handshake = {"type": "channel_handshake", "data": {"message": {"channelId": streamer.channel_id}}}
        await ws.send_str(json.dumps(handshake))
        with lock:
            streamer.heartbeats += 1

        last_activity = time.time()
        last_ping = 0
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
                        logger.debug(f"[WS_DROP] [{streamer.name}] port:{port} | reason: {msg.type.name}")
                        break
                except asyncio.TimeoutError:
                    pass

                if time.time() - last_activity > PONG_TIMEOUT:
                    # Layer 2: timeout → penalise port health
                    update_port_health(port, -PORT_HEALTH_TIMEOUT_PENALTY)
                    logger.debug(f"[WS_DROP] [{streamer.name}] port:{port} | reason: PongTimeout ({PONG_TIMEOUT}s)")
                    break
            except Exception as inner_exc:
                logger.debug(f"[WS_DROP] [{streamer.name}] port:{port} | reason: {inner_exc}")
                break
    except asyncio.TimeoutError:
        # Layer 5: activation verification failed (2s timeout on standby tokens)
        update_port_health(port, -PORT_HEALTH_TIMEOUT_PENALTY)
        with lock:
            streamer.ws_errors += 1
            streamer.pending_connections = max(0, streamer.pending_connections - 1)
        logger.debug(f"[WS_DROP] [{streamer.name}] port:{port} | reason: ConnectTimeout ({connect_timeout}s)")
    except Exception as e:
        err_str = str(e)
        if '403' in err_str:
            update_port_health(port, -PORT_HEALTH_403_PENALTY)
            blacklist_port(port)
            logger.warning(f"[ERROR]   [{streamer.name}] port:{port} | 403 Forbidden | health: -{PORT_HEALTH_403_PENALTY} | action: cooling")
        else:
            update_port_health(port, -PORT_HEALTH_FAIL_PENALTY)
            logger.debug(f"[ERROR]   [{streamer.name}] port:{port} | {err_str[:120]}")
        with lock:
            streamer.ws_errors += 1
            streamer.pending_connections = max(0, streamer.pending_connections - 1)
    finally:
        if ws and not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass
        if connected:
            with lock:
                streamer.connections = max(0, streamer.connections - 1)


# ── Port pool runner (per port, serves all streamers) ─────────────────────────
async def run_port_pool(port, streamer_list):
    global stop
    try:
        connector = ProxyConnector.from_url(
            f'socks5://127.0.0.1:{port}',
            limit=CONNS_PER_PORT,
            limit_per_host=CONNS_PER_PORT,
        )
    except Exception as exc:
        logger.warning(f"[PORT]    port:{port} | failed to create connector: {exc}")
        return

    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            active_tasks = {}  # {streamer_name: set of tasks}

            while not stop:
                if all(s.is_expired() for s in streamer_list if s.active):
                    break

                # Token pool check — wait for minimum supply
                pool_size = token_queue.qsize()
                if pool_size < 10:
                    await asyncio.sleep(1.0)
                    continue

                spawned_any = False
                for streamer in streamer_list:
                    if stop:
                        break
                    if not streamer.active or streamer.is_expired():
                        continue
                    if not streamer.channel_id:
                        continue

                    # Clean finished tasks
                    streamer_tasks = active_tasks.get(streamer.name, set())
                    streamer_tasks = {t for t in streamer_tasks if not t.done()}
                    active_tasks[streamer.name] = streamer_tasks

                    # Dynamic clients per port: ceil(target / available_ports), capped by hard limit
                    available_ports = max(1, len([p for p in all_ports if not is_port_cooling(p)]))
                    dynamic_max = min(
                        MAX_CLIENTS_PER_PORT_HARD_LIMIT,
                        max(2, math.ceil(streamer.target_viewers / available_ports))
                    )

                    # Atomic reservation under lock
                    reserved = 0
                    with lock:
                        claimed = streamer.connections + streamer.pending_connections
                        available = streamer.target_viewers - claimed
                        if available > 0:
                            can_add = max(0, dynamic_max - len(streamer_tasks))
                            to_send = min(available, can_add)
                            if to_send > 0:
                                streamer.pending_connections += to_send
                                reserved = to_send

                    if reserved <= 0:
                        continue

                    actually_sent = 0
                    for _ in range(reserved):
                        if stop or streamer.is_expired():
                            break

                        # Layer B: try standby pool first for instant replacement
                        standby = get_standby_token(streamer)
                        if standby:
                            s_token, s_port, _ = standby
                            with lock:
                                streamer.attempts += 1
                            # Layer 5: activation verification — 2s connect timeout
                            task = asyncio.create_task(
                                ws_handler(session, s_token, streamer, s_port, connect_timeout=2.0)
                            )
                            streamer_tasks.add(task)
                            active_tasks[streamer.name] = streamer_tasks
                            spawned_any = True
                            actually_sent += 1
                        else:
                            # Normal path: get token from main pool
                            token, tok_port = get_token_from_pool()
                            if not token:
                                break
                            with lock:
                                streamer.attempts += 1
                            task = asyncio.create_task(
                                ws_handler(session, token, streamer, tok_port)
                            )
                            streamer_tasks.add(task)
                            active_tasks[streamer.name] = streamer_tasks
                            spawned_any = True
                            actually_sent += 1
                            if reserved > 1:
                                await asyncio.sleep(random.uniform(MIN_SPAWN_DELAY, MAX_SPAWN_DELAY))

                    # Return unreserved slots
                    not_sent = reserved - actually_sent
                    if not_sent > 0:
                        with lock:
                            streamer.pending_connections = max(0, streamer.pending_connections - not_sent)

                # Layer A1: adaptive sleep — fast when below target, slow when at target
                all_at_target = all(
                    s.connections + s.pending_connections >= s.target_viewers
                    for s in streamer_list
                    if s.active and not s.is_expired() and s.channel_id
                )
                if spawned_any or not all_at_target:
                    await asyncio.sleep(0.3)
                else:
                    await asyncio.sleep(2.0)
    except Exception as exc:
        logger.debug(f"[PORT]    port:{port} | pool error: {exc}")


def port_worker(port, streamer_list):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_port_pool(port, streamer_list))
    except Exception as exc:
        logger.debug(f"[WORKER]  port:{port} | worker error: {exc}")
    finally:
        try:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception as exc:
            logger.debug(f"[WORKER]  port:{port} | cleanup error: {exc}")
        loop.close()


# ── Run all streamers ─────────────────────────────────────────────────────────
def run_all_streamers():
    global stop

    Thread(target=show_stats, daemon=True).start()
    Thread(target=periodic_state_saver, daemon=True).start()
    Thread(target=port_health_manager, daemon=True).start()
    Thread(target=liste_hot_reloader, daemon=True).start()

    for streamer in streamers:
        Thread(target=standby_producer, args=(streamer,), daemon=True).start()
        Thread(target=standby_health_checker, args=(streamer,), daemon=True).start()

    threads = []
    for port in all_ports:
        if stop:
            break
        t = Thread(target=port_worker, args=(port, streamers), daemon=True)
        threads.append(t)
        t.start()
        time.sleep(0.02)

    while not stop:
        if all(s.is_expired() for s in streamers if s.active):
            print("\n\033[33m[*] All streamers have expired. Shutting down...\033[0m")
            stop = True
            break
        time.sleep(60)

    for t in threads:
        t.join(timeout=1)


if __name__ == "__main__":
    try:
        os.system('cls' if os.name == 'nt' else 'clear')
        print("\033[94m" + "="*55)
        print(f"   KICK VIEWER BOT - MULTI-TOR DOCKER EDITION v{VERSION}   ")
        print("="*55 + "\033[0m\n")

        if not FULL_SUPPORT:
            print("\n\033[31m[!] Missing required libraries! Please run 'run.bat' or install manually:\033[0m")
            print("    pip install aiohttp aiohttp-socks tls-client")
            sys.exit(1)

        # Read streamer list from liste.txt
        entries = read_liste()
        if not entries:
            sys.exit(1)

        print(f"[*] {LIST_FILE} read. {len(entries)} streamer(s) found.\n")

        # Load existing state
        state = load_state()
        state_streamers = state.get("streamers", {})

        # Build streamer list — resume from state or prompt for new ones
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
                print(f"[*] {name}: Resuming — {si.format_remaining()} remaining, {target_viewers} target viewers.")
                streamers.append(si)
            else:
                # New streamer — ask for viewer count and duration
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
                    dur_input = input(f"Duration for {name} (e.g. 7d, 2h, 30m, 1w): ").strip()
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

        # Docker setup
        print("\n[*] Checking Docker status...")
        success, _ = run_cmd("docker --version")
        if not success:
            print("\n\033[31m[!] ERROR: Docker not found!\033[0m")
            print("    Please make sure Docker Desktop is installed and added to PATH.")
            input("\nPress any key to exit...")
            sys.exit(1)

        success, _ = run_cmd("docker ps")
        if not success:
            print("\n\033[31m[!] ERROR: Docker is not running!\033[0m")
            print("    Please start Docker Desktop and try again once it's ready.")
            input("\nPress any key to exit...")
            sys.exit(1)

        if not build_image():
            print("\n\033[31m[!] ERROR: Failed to build Docker image (multitor).\033[0m")
            print("    Check if Dockerfile.multitor exists and you have internet connection.")
            sys.exit(1)

        print("\n\033[92m[+] Docker is Ready!\033[0m")
        num_containers = int(input("\nNumber of Containers (Recommended 10): ").strip() or "10")
        base_port = int(input("Starting Base Port (Default 19050): ").strip() or "19050")

        # Store globally so send_newnym() can calculate container/tor indices
        global_base_port = base_port

        print(f"\n[*] Creating {num_containers} containers. This may take a moment...")
        for i in range(num_containers):
            container_base = base_port + (i * PORTS_PER_CONTAINER)
            print(f"\r[*] Progress: {i+1}/{num_containers} (Port {container_base})", end="", flush=True)
            if not create_container(i, container_base):
                print(f"\n\033[33m[!] Warning: Could not create container {i+1}. Check Docker resources.\033[0m")

        print(f"\n\n\033[92m[+] Created {len(containers)} containers with {len(all_ports)} Tor ports!\033[0m")

        # Initialise port health scores
        with port_health_lock:
            for p in all_ports:
                port_health_scores[p] = PORT_HEALTH_INITIAL

        print("[*] Waiting 45s for Tor instances to bootstrap...")
        for i in range(45, 0, -1):
            print(f"\rTime Remaining: {i}s... ", end="", flush=True)
            time.sleep(1)
        print("\n")

        # Get channel info for each streamer
        for s in streamers:
            print(f"[*] Getting channel info for: {s.name}")
            get_channel_info_for(s)
            if s.channel_id:
                print(f"[+] {s.name}: Channel ID={s.channel_id} | Stream ID={s.stream_id or 'N/A'}")

        # Start token producers
        print("[*] Starting token producers...")
        for _ in range(TOKEN_PRODUCERS):
            Thread(target=token_producer, daemon=True).start()
        print("[*] Filling token pool...")

        start_fill = time.time()
        while token_queue.qsize() < INITIAL_POOL_WAIT:
            time.sleep(0.3)
            print(f"\r[*] Tokens: {token_queue.qsize()}/{INITIAL_POOL_WAIT}", end="")
            if time.time() - start_fill > 30 and token_queue.qsize() < 10:
                print(f"\n[DEBUG] WARNING: Token generation is very slow. Only {token_queue.qsize()} tokens in 30s.")
                print("[DEBUG] This usually means SOCKS proxies are not responding.")
                print("[DEBUG] Check: 1) Docker containers running 2) Ports not blocked 3) Tor bootstrap complete")

        print(f"\n[+] Token pool ready: {token_queue.qsize()}")

        # Persist state before starting
        save_state(streamers)

        run_all_streamers()

    except KeyboardInterrupt:
        stop = True
        print("\n\n\033[33m[*] Shutting down, please wait. Saving state and removing containers...\033[0m")
        save_state(streamers)
        cleanup_containers()
        print("\033[92m[+] Cleanup complete. Goodbye!\033[0m")
        sys.exit(0)
    except Exception as e:
        print(f"\n\033[31m[!] UNEXPECTED ERROR: {e}\033[0m")
        cleanup_containers()
        sys.exit(1)
