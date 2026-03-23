VERSION = "2.1.0"

import sys
import time
import random
import datetime
import threading
import asyncio
import json
import os
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
DOCKER_IMAGE = "dperson/torproxy"
CONTAINER_PREFIX = "tor_viewer_"
CONNS_PER_CONTAINER = 300
TOKEN_POOL_SIZE = 500
INITIAL_POOL_WAIT = 150
TOKEN_PRODUCERS = 60
PONG_TIMEOUT = 180
STATE_FILE = "state_single.json"

PORT_BLACKLIST_DURATION = 120
BACKOFF_FAILURE_THRESHOLD = 5
BACKOFF_MULTIPLIER = 0.5
MAX_BACKOFF_SECONDS = 5.0
MIN_PING_INTERVAL = 15
MAX_PING_INTERVAL = 25
MIN_SPAWN_DELAY = 0.5
MAX_SPAWN_DELAY = 2.0

# Standby pool
STANDBY_MAX = 10
STANDBY_FILL_MIN_POOL = 50
STANDBY_TTL = 90

# Token freshness
TOKEN_MAX_AGE_SECONDS = 300

# Port health
PORT_HEALTH_INITIAL = 100
PORT_HEALTH_SUCCESS_BONUS = 10
PORT_HEALTH_FAIL_PENALTY = 10
PORT_HEALTH_403_PENALTY = 20
PORT_HEALTH_TIMEOUT_PENALTY = 8
PORT_HEALTH_COOLING_THRESHOLD = 30
PORT_HEALTH_DEAD_THRESHOLD = 10
PORT_HEALTH_FLOOR = 5
PORT_HEALTH_COOLING_RECOVERY = 5
PORT_COOLING_DURATION = 300
PORT_DEAD_DURATION = 600

PERIODIC_SAVE_INTERVAL = 300

# TLS fingerprint pool for rotation
TLS_CLIENT_IDENTIFIERS = [
    "chrome_119",
    "chrome_120",
    "chrome_121",
    "safari_17_2",
    "firefox_120",
]

# User-Agent pool
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

def get_random_ua():
    return random.choice(USER_AGENTS)

def get_random_tls_id():
    return random.choice(TLS_CLIENT_IDENTIFIERS)


# ── Logging setup ─────────────────────────────────────────────────────────────
os.makedirs("logs", exist_ok=True)
_log_handler = RotatingFileHandler(
    "logs/kick-single.log",
    maxBytes=10 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_log_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logger = logging.getLogger("kick-single")
logger.setLevel(logging.DEBUG)
logger.addHandler(_log_handler)


# ── Globals ───────────────────────────────────────────────────────────────────
channel = ""
channel_id = None
stream_id = None
stop = False
start_time = None
lock = threading.Lock()
connections = 0
attempts = 0
pings = 0
heartbeats = 0
viewers = 0
last_check = 0
ws_errors = 0
token_queue = Queue()
token_hits = 0
token_misses = 0
containers = []
container_ports = []
port_blacklist = {}
port_blacklist_lock = threading.Lock()
port_health_scores = {}
port_health_lock = threading.Lock()
port_cooling_until = {}
standby_tokens = Queue()


# ── Docker helpers ────────────────────────────────────────────────────────────
def run_cmd(cmd):
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
        return result.returncode == 0, result.stdout.strip()
    except Exception as exc:
        logger.debug(f"[CMD]     error running command: {exc}")
        return False, ""

def create_tor_container(index, socks_port):
    name = f"{CONTAINER_PREFIX}{index}"
    run_cmd(f"docker rm -f {name}")
    cmd = f"docker run -d --name {name} -p {socks_port}:9050 {DOCKER_IMAGE}"
    success, _ = run_cmd(cmd)
    if success:
        containers.append(name)
        container_ports.append(socks_port)
        return True
    return False

def cleanup_containers():
    for name in containers:
        run_cmd(f"docker rm -f {name}")


# ── Port health management ────────────────────────────────────────────────────
def get_port_health(port):
    with port_health_lock:
        return port_health_scores.get(port, PORT_HEALTH_INITIAL)

def update_port_health(port, delta):
    with port_health_lock:
        current = port_health_scores.get(port, PORT_HEALTH_INITIAL)
        new_score = max(PORT_HEALTH_FLOOR, min(100, current + delta))
        port_health_scores[port] = new_score
        if delta < 0 and new_score < PORT_HEALTH_COOLING_THRESHOLD:
            now = time.time()
            if port_cooling_until.get(port, 0) < now:
                duration = PORT_DEAD_DURATION if new_score < PORT_HEALTH_DEAD_THRESHOLD else PORT_COOLING_DURATION
                port_cooling_until[port] = now + duration
                logger.warning(f"[HEALTH]  port:{port} | score: {current}→{new_score} | cooling for {duration}s")

def is_port_cooling(port):
    with port_health_lock:
        return port_cooling_until.get(port, 0) >= time.time()

def get_random_healthy_port():
    if not container_ports:
        return None
    now = time.time()
    with port_health_lock:
        with port_blacklist_lock:
            available = [
                p for p in container_ports
                if port_cooling_until.get(p, 0) < now
                and port_blacklist.get(p, 0) < now
                and port_health_scores.get(p, PORT_HEALTH_INITIAL) >= PORT_HEALTH_COOLING_THRESHOLD
            ]
    return random.choice(available) if available else None

def get_random_port():
    if not container_ports:
        return 9050
    now = time.time()
    with port_health_lock:
        with port_blacklist_lock:
            available = [
                p for p in container_ports
                if port_blacklist.get(p, 0) < now
                and port_cooling_until.get(p, 0) < now
            ]
    return random.choice(available) if available else random.choice(container_ports)

def blacklist_port(port, duration=PORT_BLACKLIST_DURATION):
    with port_blacklist_lock:
        port_blacklist[port] = time.time() + duration

def get_proxy_dict(port=None):
    p = port or get_random_port()
    return {"http": f"socks5://127.0.0.1:{p}", "https": f"socks5://127.0.0.1:{p}"}


# ── Port health manager daemon ────────────────────────────────────────────────
def port_health_manager():
    global stop
    while not stop:
        time.sleep(60)
        now = time.time()
        with port_health_lock:
            for port, health in list(port_health_scores.items()):
                in_cooling = port_cooling_until.get(port, 0) >= now
                if in_cooling:
                    new_health = min(PORT_HEALTH_INITIAL, health + PORT_HEALTH_COOLING_RECOVERY)
                    if new_health != health:
                        port_health_scores[port] = new_health
                        logger.debug(f"[HEALTH]  port:{port} | cooling recovery: {health}→{new_health}")
                elif health < PORT_HEALTH_INITIAL:
                    port_health_scores[port] = min(PORT_HEALTH_INITIAL, health + 2)


# ── Channel helpers ───────────────────────────────────────────────────────────
def clean_channel_name(name):
    if "kick.com/" in name:
        parts = name.split("kick.com/")
        ch = parts[1].split("/")[0].split("?")[0]
        return ch.lower()
    return name.lower()

def get_channel_info(name):
    global channel_id, stream_id
    try:
        s = tls_client.Session(client_identifier=get_random_tls_id(), random_tls_extension_order=True)
        if container_ports:
            s.proxies = get_proxy_dict()
        s.headers.update({'Accept': 'application/json', 'User-Agent': get_random_ua()})
        response = s.get(f'https://kick.com/api/v2/channels/{name}', timeout_seconds=30)
        if response.status_code == 200:
            data = response.json()
            channel_id = data.get("id")
            if 'livestream' in data and data['livestream']:
                stream_id = data['livestream'].get('id')
            return channel_id
        elif response.status_code == 404:
            print(f"\033[31m[!] Channel '{name}' not found!\033[0m")
        else:
            print(f"\033[31m[!] API error for '{name}' (Code: {response.status_code})\033[0m")
    except Exception as e:
        print(f"Error: {e}")
    return None


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

def save_state():
    global channel, channel_id, stream_id, start_time
    data = {
        "channel": channel,
        "channel_id": channel_id,
        "stream_id": stream_id,
        "start_time": start_time.isoformat() if start_time else None,
    }
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as exc:
        logger.warning(f"[STATE]   failed to save state: {exc}")

def periodic_state_saver():
    global stop
    while not stop:
        time.sleep(PERIODIC_SAVE_INTERVAL)
        if not stop:
            save_state()


# ── Token fetch with TLS + UA rotation ───────────────────────────────────────
def fetch_token(port=None):
    try:
        s = tls_client.Session(client_identifier=get_random_tls_id(), random_tls_extension_order=True)
        if container_ports:
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
                update_port_health(port, -PORT_HEALTH_403_PENALTY)
            logger.warning(f"[TOKEN]   port:{port} | 403 Forbidden | port blacklisted")
    except Exception as exc:
        logger.debug(f"[TOKEN]   port:{port} | error: {exc}")
    return None


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
    global token_hits, token_misses
    now = time.time()
    while True:
        try:
            token, port, ts = token_queue.get(timeout=0.1)
            age = now - ts
            if age > TOKEN_MAX_AGE_SECONDS:
                logger.debug(f"[TOKEN]   stale token discarded | age: {age:.0f}s")
                continue
            with lock:
                token_hits += 1
            return token, port
        except Empty:
            with lock:
                token_misses += 1
            return None, None


# ── Standby token pool ────────────────────────────────────────────────────────
def get_standby_token():
    now = time.time()
    while True:
        try:
            item = standby_tokens.get_nowait()
            token, port, ts = item
            age = now - ts
            if age < STANDBY_TTL and not is_port_cooling(port):
                return item
            logger.debug(f"[STANDBY] discarded stale/cooling token | age: {age:.0f}s")
        except Empty:
            return None

def standby_producer_thread():
    global stop
    while not stop:
        try:
            needs_standby = standby_tokens.qsize() < STANDBY_MAX
            pool_healthy = token_queue.qsize() >= STANDBY_FILL_MIN_POOL
            if needs_standby and pool_healthy:
                port = get_random_healthy_port()
                if port:
                    token = fetch_token(port)
                    if token:
                        update_port_health(port, PORT_HEALTH_SUCCESS_BONUS)
                        standby_tokens.put((token, port, time.time()))
                        logger.debug(f"[STANDBY] produced token on port:{port} | pool: {standby_tokens.qsize()}/{STANDBY_MAX}")
                    else:
                        update_port_health(port, -PORT_HEALTH_FAIL_PENALTY)
                        time.sleep(2)
                else:
                    time.sleep(5)
            else:
                time.sleep(5)
        except Exception as exc:
            logger.debug(f"[STANDBY] producer error: {exc}")
            time.sleep(5)

def standby_health_checker_thread():
    global stop
    while not stop:
        time.sleep(30)
        now = time.time()
        fresh = []
        while True:
            try:
                item = standby_tokens.get_nowait()
                token, port, ts = item
                if now - ts < STANDBY_TTL and not is_port_cooling(port):
                    fresh.append(item)
            except Empty:
                break
        for item in fresh:
            standby_tokens.put(item)


# ── Viewer count ──────────────────────────────────────────────────────────────
def get_viewer_count():
    global viewers, last_check
    if not stream_id:
        return 0
    try:
        s = tls_client.Session(client_identifier=get_random_tls_id(), random_tls_extension_order=True)
        if container_ports:
            s.proxies = get_proxy_dict()
        s.headers.update({'Accept': 'application/json', 'User-Agent': get_random_ua()})
        response = s.get(f"https://kick.com/current-viewers?ids[]={stream_id}", timeout_seconds=15)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list) and len(data) > 0:
                viewers = data[0].get('viewers', 0)
                last_check = time.time()
    except Exception as exc:
        logger.debug(f"[VIEWER]  error fetching viewer count: {exc}")
    return viewers


# ── Stats display ─────────────────────────────────────────────────────────────
def show_stats():
    global stop
    print("\n\n\n\n\n\n\n")
    os.system('cls' if os.name == 'nt' else 'clear')
    while not stop:
        try:
            if time.time() - last_check >= 5:
                get_viewer_count()
            now = time.time()
            with port_health_lock:
                healthy_ports = sum(
                    1 for p in container_ports
                    if port_health_scores.get(p, PORT_HEALTH_INITIAL) >= PORT_HEALTH_COOLING_THRESHOLD
                    and port_cooling_until.get(p, 0) < now
                )
            with lock:
                elapsed = datetime.datetime.now() - start_time if start_time else datetime.timedelta(0)
                duration = f"{int(elapsed.total_seconds())}s"
            print("\033[7A", end="")
            print(f"\033[2K\r[+] Containers: \033[32m{len(containers)}\033[0m | Ports: \033[32m{len(container_ports)}\033[0m | Version: \033[94mv{VERSION}\033[0m")
            print(f"\033[2K\r[+] Port Health: \033[32m{healthy_ports}/{len(container_ports)}\033[0m good | Standby: \033[36m{standby_tokens.qsize()}/{STANDBY_MAX}\033[0m")
            print(f"\033[2K\r[+] Conn: \033[32m{connections}\033[0m | Attempts: \033[32m{attempts}\033[0m | Time: \033[32m{duration}\033[0m")
            print(f"\033[2K\r[+] Pings: \033[32m{pings}\033[0m | Heartbeats: \033[32m{heartbeats}\033[0m")
            print(f"\033[2K\r[+] Viewers: \033[32m{viewers}\033[0m | Stream: \033[32m{stream_id or 'N/A'}\033[0m")
            print(f"\033[2K\r[+] Errors: \033[31m{ws_errors}\033[0m")
            print(f"\033[2K\r[+] TokenPool: \033[32m{token_queue.qsize()}\033[0m | Hits: \033[32m{token_hits}\033[0m | Miss: \033[31m{token_misses}\033[0m")
            sys.stdout.flush()
            time.sleep(1)
        except Exception as exc:
            logger.debug(f"[STATS]   display error: {exc}")
            time.sleep(1)


# ── WebSocket handler ─────────────────────────────────────────────────────────
async def ws_handler(session, token, port, connect_timeout=30.0):
    global connections, pings, heartbeats, ws_errors, stop
    connected = False
    ws = None
    try:
        url = f"wss://websockets.kick.com/viewer/v1/connect?token={token}"
        ws = await session.ws_connect(
            url,
            timeout=aiohttp.ClientTimeout(total=connect_timeout, connect=connect_timeout),
        )
        update_port_health(port, PORT_HEALTH_SUCCESS_BONUS)
        with lock:
            connections += 1
        connected = True

        subscribe = {"event": "pusher:subscribe", "data": {"auth": "", "channel": f"channel.{channel_id}"}}
        await ws.send_str(json.dumps(subscribe))

        if stream_id:
            chatroom_sub = {"event": "pusher:subscribe", "data": {"auth": "", "channel": f"chatrooms.{channel_id}.v2"}}
            await ws.send_str(json.dumps(chatroom_sub))

        handshake = {"type": "channel_handshake", "data": {"message": {"channelId": channel_id}}}
        await ws.send_str(json.dumps(handshake))
        with lock:
            heartbeats += 1

        last_activity = time.time()
        last_ping = 0
        ping_interval = random.uniform(MIN_PING_INTERVAL, MAX_PING_INTERVAL)

        while not stop:
            try:
                now = time.time()
                if now - last_ping >= ping_interval:
                    await ws.send_str(json.dumps({"event": "pusher:ping", "data": {}}))
                    with lock:
                        pings += 1
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
                    update_port_health(port, -PORT_HEALTH_TIMEOUT_PENALTY)
                    break
            except Exception as inner_exc:
                logger.debug(f"[WS_DROP] port:{port} | reason: {inner_exc}")
                break
    except asyncio.TimeoutError:
        update_port_health(port, -PORT_HEALTH_TIMEOUT_PENALTY)
        with lock:
            ws_errors += 1
        logger.debug(f"[WS_DROP] port:{port} | reason: ConnectTimeout ({connect_timeout}s)")
    except Exception as e:
        err_str = str(e)
        if '403' in err_str:
            update_port_health(port, -PORT_HEALTH_403_PENALTY)
            blacklist_port(port)
        else:
            update_port_health(port, -PORT_HEALTH_FAIL_PENALTY)
        with lock:
            ws_errors += 1
        logger.debug(f"[ERROR]   port:{port} | {err_str[:120]}")
    finally:
        if ws and not ws.closed:
            try:
                await ws.close()
            except Exception:
                pass
        if connected:
            with lock:
                connections = max(0, connections - 1)


async def run_port_pool(port, target_count):
    global stop, attempts
    try:
        connector = ProxyConnector.from_url(f'socks5://127.0.0.1:{port}', limit=target_count, limit_per_host=target_count)
    except Exception as exc:
        logger.warning(f"[PORT]    port:{port} | failed to create connector: {exc}")
        return

    try:
        async with aiohttp.ClientSession(connector=connector) as session:
            active_tasks = set()

            while not stop:
                done = {t for t in active_tasks if t.done()}
                active_tasks -= done

                slots = target_count - len(active_tasks)
                pool_size = token_queue.qsize()

                if pool_size < 20:
                    await asyncio.sleep(0.5)
                    continue

                batch_size = min(slots, 25) if pool_size > 100 else min(slots, 15) if pool_size > 30 else min(slots, 5)

                for _ in range(batch_size):
                    if stop:
                        break
                    # Try standby pool first
                    standby = get_standby_token()
                    if standby:
                        s_token, s_port, _ = standby
                        with lock:
                            attempts += 1
                        task = asyncio.create_task(ws_handler(session, s_token, s_port, connect_timeout=2.0))
                    else:
                        token, tok_port = get_token_from_pool()
                        if not token:
                            break
                        with lock:
                            attempts += 1
                        task = asyncio.create_task(ws_handler(session, token, tok_port))
                    active_tasks.add(task)
                    await asyncio.sleep(0.02)

                await asyncio.sleep(0.15)
    except Exception as exc:
        logger.debug(f"[PORT]    port:{port} | pool error: {exc}")


def port_worker(port, count):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_port_pool(port, count))
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


def run(num_containers, channel_name):
    global channel, start_time, channel_id, stop
    channel = clean_channel_name(channel_name)
    start_time = datetime.datetime.now()

    print(f"[*] Getting channel info for: {channel}")
    channel_id = get_channel_info(channel)
    if channel_id:
        print(f"[+] Channel ID: {channel_id}")
    if stream_id:
        print(f"[+] Stream ID: {stream_id}")

    save_state()

    print("[*] Starting token producers...")
    for _ in range(TOKEN_PRODUCERS):
        Thread(target=token_producer, daemon=True).start()
    print("[*] Filling token pool...")
    while token_queue.qsize() < INITIAL_POOL_WAIT:
        time.sleep(0.3)
        print(f"\r[*] Tokens: {token_queue.qsize()}/{INITIAL_POOL_WAIT}", end="")
    print(f"\n[+] Token pool ready: {token_queue.qsize()}")

    Thread(target=periodic_state_saver, daemon=True).start()
    Thread(target=port_health_manager, daemon=True).start()
    Thread(target=standby_producer_thread, daemon=True).start()
    Thread(target=standby_health_checker_thread, daemon=True).start()
    Thread(target=show_stats, daemon=True).start()

    # Initialise port health scores
    with port_health_lock:
        for p in container_ports:
            port_health_scores[p] = PORT_HEALTH_INITIAL

    threads = []
    for port in container_ports:
        if stop:
            break
        t = Thread(target=port_worker, args=(port, CONNS_PER_CONTAINER), daemon=True)
        threads.append(t)
        t.start()
        time.sleep(0.05)

    while not stop:
        time.sleep(60)

    for t in threads:
        t.join(timeout=1)

if __name__ == "__main__":
    try:
        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"=== Kick Viewer Bot (Docker) v{VERSION} ===\n")

        if not FULL_SUPPORT:
            print("\033[31m[!] pip install aiohttp aiohttp-socks\033[0m")
            sys.exit(1)

        success, _ = run_cmd("docker --version")
        if not success:
            print("\033[31m[!] Docker not found! Install Docker Desktop.\033[0m")
            sys.exit(1)

        success, _ = run_cmd("docker ps")
        if not success:
            print("\033[31m[!] Docker is not running! Please start Docker Desktop.\033[0m")
            sys.exit(1)

        print("[*] Pulling Tor proxy image...")
        run_cmd(f"docker pull {DOCKER_IMAGE}")

        num_containers = int(input("Number of Tor containers (e.g. 10): ").strip() or "10")
        base_port = int(input("Base port (default 19050): ").strip() or "19050")

        print(f"\n[*] Creating {num_containers} Tor containers...")
        for i in range(num_containers):
            port = base_port + i
            print(f"\r[*] Creating container {i+1}/{num_containers} on port {port}...", end="")
            if create_tor_container(i, port):
                print(f" OK")
            else:
                print(f" FAILED")

        print(f"\n[+] Created {len(containers)} containers")
        print("[*] Waiting 30s for Tor to bootstrap...")
        time.sleep(30)

        channel_input = input("\nChannel: ").strip()
        if not channel_input:
            cleanup_containers()
            sys.exit(1)

        run(num_containers, channel_input)
    except KeyboardInterrupt:
        stop = True
        print("\n[*] Saving state and cleaning up containers...")
        save_state()
        cleanup_containers()
        print("Stopped.")
        sys.exit(0)
