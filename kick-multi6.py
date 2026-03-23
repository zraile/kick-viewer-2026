import sys
import time
import math
import random
import datetime
import threading
import asyncio
import json
import os
import subprocess
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

CLIENT_TOKEN = "e1393935a959b4020a4491574f6490129f678acdaa92760471263db43487f823"
DOCKER_IMAGE = "multitor:latest"
CONTAINER_PREFIX = "multitor_"
PORTS_PER_CONTAINER = 6
CONNS_PER_PORT = 80
BASE_TOKEN_POOL_SIZE = 500
BASE_INITIAL_POOL_WAIT = 150
BASE_TOKEN_PRODUCERS = 60
TOKEN_POOL_SIZE = 500
INITIAL_POOL_WAIT = 150
TOKEN_PRODUCERS = 60
PONG_TIMEOUT = 90  # Reduced from 180s to drop dead connections faster and free slots sooner
STATE_FILE = "state.json"
LIST_FILE = "liste.txt"

# Token pool scaling: one scale unit per this many target viewers
TOKEN_SCALE_VIEWERS = 50

# Port blacklist duration (seconds) when a 403 is received
PORT_BLACKLIST_DURATION = 180

# Token producer backoff settings
BACKOFF_FAILURE_THRESHOLD = 5
BACKOFF_MULTIPLIER = 0.5
MAX_BACKOFF_SECONDS = 5.0

# Ping interval randomization range (seconds) — anti-block
MIN_PING_INTERVAL = 15
MAX_PING_INTERVAL = 25

# Client spawn delay range (seconds) — anti-block
MIN_SPAWN_DELAY = 1.5
MAX_SPAWN_DELAY = 4.0

# Auto-reconnect delay range (seconds)
MIN_RECONNECT_DELAY = 3
MAX_RECONNECT_DELAY = 8

# Per-batch cooldown added after each spawn batch in run_port_pool (seconds)
MIN_BATCH_COOLDOWN = 0.5
MAX_BATCH_COOLDOWN = 1.5

# Backoff delay before recording a ws error (seconds) — prevents failure cascade
MIN_ERROR_BACKOFF = 1.0
MAX_ERROR_BACKOFF = 3.0

# Gradual ramp-up: list of (threshold_seconds, fraction_of_target)
RAMP_STAGES = [
    (60, 0.20),
    (120, 0.40),
    (180, 0.60),
    (240, 0.80),
]
RAMP_FULL_FRACTION = 1.0

# Streamer live-status check interval (seconds)
STREAMER_CHECK_INTERVAL = 120

# Error log settings
MAX_ERROR_LOG = 15
MAX_ERRORS_DISPLAYED = 5

# Seconds to wait before the first streamer status check after startup
INITIAL_STATUS_CHECK_DELAY = 30

# Critical connection threshold (fraction of target below which emergency reconnect activates)
CRITICAL_CONN_FRACTION = 0.15

# Emergency reconnect batch size
EMERGENCY_BATCH_SIZE = 10
EMERGENCY_SPAWN_DELAY = 0.3

# 15+ realistic User-Agent strings for rotation
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

# --- Globals ---
stop = False
lock = threading.Lock()
token_queue = Queue()
token_hits = 0
token_misses = 0
containers = []
all_ports = []
port_blacklist = {}
port_blacklist_lock = threading.Lock()
streamers = []
error_log = []
error_log_lock = threading.Lock()


def add_error_log(msg):
    with error_log_lock:
        error_log.append(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {msg}")
        while len(error_log) > MAX_ERROR_LOG:
            error_log.pop(0)


# --- StreamerInfo class ---
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
        self.active = True
        self.assigned_ports = []
        self.is_live = True
        self.last_status_check = 0.0

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


# --- Token pool settings ---
def calculate_token_settings(total_target_viewers):
    global TOKEN_POOL_SIZE, INITIAL_POOL_WAIT, TOKEN_PRODUCERS
    scale = max(1, math.ceil(total_target_viewers / TOKEN_SCALE_VIEWERS))
    TOKEN_POOL_SIZE = BASE_TOKEN_POOL_SIZE + (scale - 1) * 50
    INITIAL_POOL_WAIT = min(300, BASE_INITIAL_POOL_WAIT + (scale - 1) * 10)
    TOKEN_PRODUCERS = BASE_TOKEN_PRODUCERS + (scale - 1) * 5


# --- Docker helpers ---
def run_cmd(cmd):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
        return result.returncode == 0, result.stdout.strip()
    except:
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


# --- Port management ---
def get_random_port(port_pool=None):
    pool = port_pool if port_pool else all_ports
    if not pool:
        return 9050
    now = time.time()
    with port_blacklist_lock:
        available = [p for p in pool if port_blacklist.get(p, 0) < now]
    return random.choice(available) if available else random.choice(pool)

def blacklist_port(port, duration=PORT_BLACKLIST_DURATION):
    with port_blacklist_lock:
        port_blacklist[port] = time.time() + duration

def get_proxy_dict(port=None, port_pool=None):
    p = port if port else get_random_port(port_pool)
    return {"http": f"socks5://127.0.0.1:{p}", "https": f"socks5://127.0.0.1:{p}"}


# --- Channel name cleaning ---
def clean_channel_name(name):
    if "kick.com/" in name:
        parts = name.split("kick.com/")
        ch = parts[1].split("/")[0].split("?")[0]
        return ch.lower()
    return name.lower()


# --- State persistence ---
def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except:
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
    except:
        pass


# --- liste.txt reading ---
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


# --- Duration parsing ---
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


# --- Channel info (per streamer) ---
def get_channel_info_for(streamer):
    try:
        s = tls_client.Session(client_identifier="chrome_120", random_tls_extension_order=True)
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


# --- Viewer count (per streamer) ---
def get_viewer_count_for(streamer):
    if not streamer.stream_id:
        return 0
    try:
        s = tls_client.Session(client_identifier="chrome_120", random_tls_extension_order=True)
        if all_ports:
            s.proxies = get_proxy_dict()
        s.headers.update({'Accept': 'application/json', 'User-Agent': get_random_ua()})
        response = s.get(f"https://kick.com/current-viewers?ids[]={streamer.stream_id}", timeout_seconds=15)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                streamer.viewers = data[0].get('viewers', 0)
                streamer.last_check = time.time()
    except:
        pass
    return streamer.viewers


# --- Token fetching ---
def fetch_token(port=None):
    try:
        s = tls_client.Session(client_identifier="chrome_120", random_tls_extension_order=True)
        if all_ports:
            s.proxies = get_proxy_dict(port)
        s.headers.update({
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'User-Agent': get_random_ua(),
        })
        s.get("https://kick.com", timeout_seconds=20)
        s.headers["X-CLIENT-TOKEN"] = CLIENT_TOKEN
        response = s.get('https://websockets.kick.com/viewer/v1/token', timeout_seconds=20)
        if response.status_code == 200:
            data = response.json()
            return data.get("data", {}).get("token")
        elif response.status_code == 403:
            if port:
                blacklist_port(port)
    except:
        pass
    return None


# --- Token producer with backoff ---
def token_producer():
    global stop
    consecutive_failures = 0
    while not stop:
        try:
            if token_queue.qsize() < TOKEN_POOL_SIZE:
                port = get_random_port()
                token = fetch_token(port)
                if token:
                    token_queue.put((token, port))
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures > BACKOFF_FAILURE_THRESHOLD:
                        backoff = min(MAX_BACKOFF_SECONDS, BACKOFF_MULTIPLIER * consecutive_failures)
                        time.sleep(backoff)
            else:
                time.sleep(0.05)
                consecutive_failures = 0
        except:
            consecutive_failures += 1
            time.sleep(0.1)

def get_token_from_pool():
    global token_hits, token_misses
    try:
        token, port = token_queue.get(timeout=0.1)
        with lock:
            token_hits += 1
        return token, port
    except Empty:
        with lock:
            token_misses += 1
        return None, None


# --- Port assignment (proportional by target viewers) ---
def assign_ports(streamer_list, available_ports):
    active = [s for s in streamer_list if s.active]
    if not active:
        return
    total_viewers = sum(s.target_viewers for s in active)
    if total_viewers == 0:
        return
    port_list = list(available_ports)
    random.shuffle(port_list)
    start = 0
    for i, s in enumerate(active):
        if i == len(active) - 1:
            s.assigned_ports = port_list[start:]
        else:
            count = max(1, int(len(port_list) * s.target_viewers / total_viewers))
            s.assigned_ports = port_list[start:start + count]
            start += count


# --- Gradual ramp-up ---
def get_ramp_target(streamer, port_target):
    elapsed = (datetime.datetime.now() - streamer.start_time).total_seconds()
    factor = RAMP_FULL_FRACTION
    for threshold, f in RAMP_STAGES:
        if elapsed < threshold:
            factor = f
            break
    return max(1, int(port_target * factor))


# --- Stats display ---
def show_stats():
    global stop
    # Header + separator + streamers + 2 separators + "Recent Errors:" header + MAX_ERRORS_DISPLAYED error lines
    n = 3 + len(streamers) + 2 + MAX_ERRORS_DISPLAYED
    print("\n" * n)
    os.system('cls' if os.name == 'nt' else 'clear')
    while not stop:
        try:
            for s in streamers:
                if time.time() - s.last_check >= 5:
                    get_viewer_count_for(s)

            lines = [
                f"\033[2K\r[+] Containers: \033[32m{len(containers)}\033[0m | Ports: \033[32m{len(all_ports)}\033[0m | TokenPool: \033[32m{token_queue.qsize()}\033[0m | Hits: \033[32m{token_hits}\033[0m | Miss: \033[31m{token_misses}\033[0m",
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

            # Error log section
            lines.append(f"\033[2K\r{'-'*80}")
            lines.append(f"\033[2K\r\033[36mRecent Errors/Logs:\033[0m")
            with error_log_lock:
                recent_errors = list(error_log[-MAX_ERRORS_DISPLAYED:])
            for i in range(MAX_ERRORS_DISPLAYED):
                if i < len(recent_errors):
                    lines.append(f"\033[2K\r  \033[31m{recent_errors[i]}\033[0m")
                else:
                    lines.append(f"\033[2K\r")

            print(f"\033[{n}A", end="")
            for line in lines:
                print(line)
            sys.stdout.flush()
            time.sleep(1)
        except:
            time.sleep(1)


# --- WebSocket handler ---
async def ws_handler(session, token, streamer):
    connected = False
    ws = None
    try:
        url = f"wss://websockets.kick.com/viewer/v1/connect?token={token}"
        ws = await session.ws_connect(url, timeout=aiohttp.ClientTimeout(total=30))
        with lock:
            streamer.connections += 1
        connected = True

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
                        break
                except asyncio.TimeoutError:
                    pass

                if time.time() - last_activity > PONG_TIMEOUT:
                    break
            except:
                break
    except Exception as e:
        await asyncio.sleep(random.uniform(MIN_ERROR_BACKOFF, MAX_ERROR_BACKOFF))
        with lock:
            streamer.ws_errors += 1
        add_error_log(f"{streamer.name}: WS error — {type(e).__name__}: {e}")
    finally:
        if ws and not ws.closed:
            try:
                await ws.close()
            except:
                pass
        if connected:
            with lock:
                streamer.connections = max(0, streamer.connections - 1)


# --- Port pool runner (per streamer per port) ---
async def run_port_pool(port, target_count, streamer):
    global stop
    try:
        connector = ProxyConnector.from_url(
            f'socks5://127.0.0.1:{port}',
            limit=target_count,
            limit_per_host=target_count,
        )
    except:
        return

    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            active_tasks = set()

            while not stop and not streamer.is_expired():
                done = {t for t in active_tasks if t.done()}
                active_tasks -= done

                # Pause new connections if streamer is offline
                if not streamer.is_live:
                    await asyncio.sleep(5.0)
                    continue

                ramp_target = get_ramp_target(streamer, target_count)
                slots = ramp_target - len(active_tasks)
                pool_size = token_queue.qsize()

                if pool_size < 20:
                    await asyncio.sleep(1.0)
                    continue

                if slots <= 0:
                    await asyncio.sleep(0.3)
                    continue

                # Emergency reconnect: connections critically low relative to target
                is_critical = (
                    streamer.target_viewers > 0 and
                    streamer.connections < streamer.target_viewers * CRITICAL_CONN_FRACTION
                )

                if is_critical:
                    batch_size = min(slots, EMERGENCY_BATCH_SIZE)
                    spawn_delay = EMERGENCY_SPAWN_DELAY
                else:
                    batch_size = (
                        min(slots, 4) if pool_size > 200 else
                        min(slots, 2) if pool_size > 50 else
                        min(slots, 1)
                    )
                    spawn_delay = random.uniform(MIN_SPAWN_DELAY, MAX_SPAWN_DELAY)

                for _ in range(batch_size):
                    if stop or streamer.is_expired():
                        break
                    token, _ = get_token_from_pool()
                    if not token:
                        break
                    with lock:
                        streamer.attempts += 1
                    task = asyncio.create_task(ws_handler(session, token, streamer))
                    active_tasks.add(task)
                    await asyncio.sleep(spawn_delay)

                await asyncio.sleep(random.uniform(MIN_BATCH_COOLDOWN, MAX_BATCH_COOLDOWN))

                # When no connections dropped, slow down the loop slightly
                if not done:
                    await asyncio.sleep(0.3)
    except:
        pass


def port_worker(port, count, streamer):
    while not stop and not streamer.is_expired():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_port_pool(port, count, streamer))
        except:
            pass
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except:
                pass
            loop.close()
        # Brief pause before restarting to avoid tight crash loops
        if not stop and not streamer.is_expired():
            time.sleep(random.uniform(MIN_RECONNECT_DELAY, MAX_RECONNECT_DELAY))


# --- Streamer live-status monitor ---
def streamer_status_monitor():
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
            # Temporarily reset stream_id so get_channel_info_for can detect offline state.
            # On error, it is restored to avoid disrupting active ws_handler tasks.
            s.stream_id = None
            try:
                get_channel_info_for(s)
            except Exception as e:
                add_error_log(f"{s.name}: Status check error — {e}")
                s.stream_id = prev_stream_id
                s.last_status_check = now
                continue
            s.last_status_check = time.time()
            if s.stream_id:
                if not prev_live:
                    add_error_log(f"{s.name}: Back ONLINE (Stream ID: {s.stream_id}), resuming connections")
                s.is_live = True
            else:
                if prev_live:
                    add_error_log(f"{s.name}: Went OFFLINE — pausing new connections")
                s.is_live = False


# --- Run all streamers ---
def run_all_streamers():
    global stop

    Thread(target=show_stats, daemon=True).start()
    Thread(target=streamer_status_monitor, daemon=True).start()

    threads = []
    for s in streamers:
        if not s.active:
            continue
        ports = s.assigned_ports
        if not ports:
            continue
        conns_per_port = max(1, min(CONNS_PER_PORT, math.ceil(s.target_viewers / len(ports))))
        for port in ports:
            if stop:
                break
            t = Thread(target=port_worker, args=(port, conns_per_port, s), daemon=True)
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
        print("\033[94m" + "="*50)
        print("   KICK VIEWER BOT - MULTI-TOR DOCKER EDITION   ")
        print("="*50 + "\033[0m\n")

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

        print(f"\n[*] Creating {num_containers} containers. This may take a moment...")
        for i in range(num_containers):
            container_base = base_port + (i * PORTS_PER_CONTAINER)
            print(f"\r[*] Progress: {i+1}/{num_containers} (Port {container_base})", end="", flush=True)
            if not create_container(i, container_base):
                print(f"\n\033[33m[!] Warning: Could not create container {i+1}. Check Docker resources.\033[0m")

        print(f"\n\n\033[92m[+] Created {len(containers)} containers with {len(all_ports)} Tor ports!\033[0m")

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

        # Assign ports proportionally
        assign_ports(streamers, all_ports)

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
