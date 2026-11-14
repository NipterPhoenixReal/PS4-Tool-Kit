#!/usr/bin/env python3
"""PS4 Toolkit: an HTML/CSS/JS interface served by a small local Python backend.

Run it with `python ps4_toolkit.py`. It starts a server that only listens on
127.0.0.1 and opens the interface in your default browser. Closing the tab or
pressing the Quit button shuts everything down.

Options:
  --app         open an Edge/Chrome app-style window instead of a normal tab
  --no-browser  just start the server and print the address
"""
import codecs
import collections
import copy
import hmac
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

# ------------------------------------------------------------------ paths
APP_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "PS4Toolkit"
PAYLOAD_DIR = DATA_DIR / "payloads"
SETTINGS_PATH = DATA_DIR / "settings.json"

STATIC = {
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}
MAX_JSON = 1024 * 1024
MAX_UPLOAD = 64 * 1024 * 1024
CLOSE_GRACE = 10       # seconds to wait after the page closes before quitting (covers a refresh)
IDLE_LIMIT = 15 * 60   # quit if no page has talked to us for this long (browser was killed)

# The app icon. Drop your own icon.png / icon.svg / icon.ico into the settings folder
# or next to this script and it is used for the header logo and the browser tab.
ICON_TYPES = {
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
}
DEFAULT_ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4a90ff"/><stop offset="1" stop-color="#2f5fe0"/></linearGradient></defs>
<rect width="64" height="64" rx="15" fill="url(#g)"/>
<path fill="#fff" d="M20 23h24a9 9 0 0 1 8.9 7.7l1.9 12.2c.5 3.3-2 6.1-5.3 6.1-1.8 0-3.1-.8-4.3-2L42 43.8c-.8-.8-1.8-1.3-3-1.3H25c-1.2 0-2.2.5-3 1.3l-3.2 3.2c-1.2 1.2-2.5 2-4.3 2-3.3 0-5.8-2.8-5.3-6.1l1.9-12.2A9 9 0 0 1 20 23z"/>
<path fill="#2f5fe0" d="M21 29.5h2v2.5h2.5v2H23v2.5h-2V34h-2.5v-2H21z"/>
<circle cx="41" cy="30.5" r="1.9" fill="#2f5fe0"/><circle cx="45.5" cy="34.5" r="1.9" fill="#2f5fe0"/>
</svg>"""


def find_icon():
    here = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
    for folder in (DATA_DIR, here, APP_DIR):
        for ext in (".png", ".svg", ".ico", ".webp", ".jpg", ".jpeg"):
            path = folder / f"icon{ext}"
            try:
                if path.is_file() and path.stat().st_size <= 2 * 1024 * 1024:
                    return path.read_bytes(), ICON_TYPES[ext]
            except OSError:
                continue
    return DEFAULT_ICON.encode("utf-8"), "image/svg+xml"

# ---------------------------------------------------------------- protocol
NOTIFY_PORT = 744
PAYLOAD_PORT = 9090
KLOG_PORT = 3232
FTP_PORT = 2121
MAGIC = 0xFFAABBCC
CMD_NOTIFY = 0xBDDD0004
CMD_REBOOT = 0xBDDD0001  # from memory, not verified against the ps4debug source

SERVICES = [
    (744, True), (2121, True), (3232, True),
    (9090, False), (9020, False), (9021, False),  # payload ports: only probed when asked
]


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- network
def recv_exact(sock, size):
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("PS4 closed the connection")
        data += chunk
    return data


def send_notification(ip, message, port=NOTIFY_PORT):
    text = message.encode("utf-8") + b"\0"
    body = struct.pack("<II", 222, len(text))
    header = struct.pack("<III", MAGIC, CMD_NOTIFY, 8)

    with socket.create_connection((ip, port), timeout=5) as sock:
        sock.sendall(header + body + text)
        status = struct.unpack("<I", recv_exact(sock, 4))[0]

    if status != 0x80000000:
        raise RuntimeError(f"PS4 error: {hex(status)}")


def send_reboot(ip, port=NOTIFY_PORT):
    """Ask the PS4 to reboot. Returns a short description of what happened."""
    header = struct.pack("<III", MAGIC, CMD_REBOOT, 0)
    with socket.create_connection((ip, port), timeout=5) as sock:
        sock.sendall(header)
        sock.settimeout(3)
        try:
            reply = sock.recv(4)
        except socket.timeout:
            return "Console is now going to reboot"
        except OSError:
            return "Console is now going to reboot"
    if not reply:
        return "Console is now going to reboot"
    if len(reply) == 4:
        status = struct.unpack("<I", reply)[0]
        if status != 0x80000000:
            raise RuntimeError(f"PS4 error: {hex(status)}")
    return "Console is now going to reboot"


def send_payload(ip, port, path, progress=None):
    size = os.path.getsize(path)
    if size == 0:
        raise ValueError("The selected file is empty")
    sent = 0
    with socket.create_connection((ip, port), timeout=10) as sock, open(path, "rb") as f:
        while True:
            chunk = f.read(64 * 1024)
            if not chunk:
                break
            sock.sendall(chunk)
            sent += len(chunk)
            if progress:
                progress(sent, size)
        sock.shutdown(socket.SHUT_WR)
    return size


def ping(ip, timeout_ms=1000):
    """True if the host answers an ICMP echo. ip must already be validated."""
    if os.name == "nt":
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
        flags = subprocess.CREATE_NO_WINDOW
    else:
        cmd = ["ping", "-c", "1", "-W", "1", ip]
        flags = 0
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=5, creationflags=flags).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    # Windows returns 0 even for "host unreachable", so look for a real reply.
    return b"TTL=" in out.upper()


def check_port(ip, port, timeout=1.5):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def stream_klog(ip, port, stop, on_connect, on_text):
    """Read the kernel log stream until stop is set. Returns the reason it ended."""
    try:
        sock = socket.create_connection((ip, port), timeout=5)
    except OSError as e:
        return f"Could not connect: {friendly(e)}"
    on_connect()
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        sock.settimeout(0.5)
        while not stop.is_set():
            try:
                data = sock.recv(8192)
            except socket.timeout:
                continue
            if not data:
                return "Connection closed by the PS4"
            on_text(decoder.decode(data))
        return "Stopped"
    except OSError as e:
        return f"Connection lost: {friendly(e)}"
    finally:
        sock.close()


def friendly(error):
    if isinstance(error, (socket.timeout, TimeoutError)):
        return "timed out, is the PS4 on and the service running?"
    if isinstance(error, ConnectionRefusedError):
        return "connection refused, is the service running on the PS4?"
    return str(error)


def valid_ip(value):
    try:
        return str(ipaddress.ip_address(str(value).strip()))
    except ValueError:
        raise ApiError(f"'{value}' is not a valid IP address")


def valid_port(value):
    try:
        port = int(value)
    except (TypeError, ValueError):
        raise ApiError("Port must be a number between 1 and 65535")
    if not 1 <= port <= 65535:
        raise ApiError("Port must be a number between 1 and 65535")
    return port


# --------------------------------------------------------------- settings
DEFAULTS = {
    "ip": "",
    "port": PAYLOAD_PORT,
    "klog_port": KLOG_PORT,
    "last_file": "",
    "recent_ips": [],
    "templates": ["Hello from my PC", "Payload loaded", "Test notification"],
    "notify_after": False,
    "last_tab": "notify",
    "notify_history": [],
}


def clean_setting(key, value):
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if not isinstance(value, bool):
            raise ValueError
        return value
    if isinstance(default, int):
        if isinstance(value, bool):
            raise ValueError
        return valid_port(value)
    if isinstance(default, str):
        if not isinstance(value, str):
            raise ValueError
        return value.strip()[:300]
    if key == "recent_ips":
        if not isinstance(value, list):
            raise ValueError
        return [valid_ip(v) for v in value][:6]
    if key == "notify_history":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError
        return [v.strip()[:500] for v in value if v.strip()][:10]
    if key == "templates":
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ValueError
        return [v.strip()[:500] for v in value if v.strip()][:20]
    raise ValueError


class Settings:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.data = copy.deepcopy(DEFAULTS)
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                for key in DEFAULTS:
                    if key in loaded:
                        try:
                            self.data[key] = clean_setting(key, loaded[key])
                        except (ValueError, ApiError):
                            pass
        except (OSError, ValueError):
            pass

    def get(self):
        with self.lock:
            return copy.deepcopy(self.data)

    def update(self, patch):
        if not isinstance(patch, dict):
            raise ApiError("Expected a JSON object")
        with self.lock:
            for key, value in patch.items():
                if key not in DEFAULTS:
                    continue
                try:
                    self.data[key] = clean_setting(key, value)
                except (ValueError, ApiError):
                    raise ApiError(f"Invalid value for '{key}'")
            self._save()
            return copy.deepcopy(self.data)

    def remember_ip(self, ip):
        with self.lock:
            recent = self.data["recent_ips"]
            if ip in recent:
                recent.remove(ip)
            recent.insert(0, ip)
            del recent[6:]
            self._save()

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except OSError:
            pass


# ------------------------------------------------------------ payload jobs
JOBS = {}
JOBS_LOCK = threading.Lock()


def start_payload_job(ip, port, path, notify, settings):
    job_id = uuid.uuid4().hex[:12]
    job = {
        "state": "running", "sent": 0, "total": path.stat().st_size,
        "message": "Connecting...", "note": None, "ok": None,
    }
    with JOBS_LOCK:
        for old in [k for k, v in JOBS.items() if v["state"] != "running" and len(JOBS) > 20]:
            JOBS.pop(old, None)
        JOBS[job_id] = job

    def progress(sent, total):
        job["sent"], job["total"] = sent, total

    def work():
        try:
            size = send_payload(ip, port, path, progress)
        except Exception as e:
            job.update(state="done", ok=False, message=f"Payload failed: {friendly(e)}")
            return
        settings.remember_ip(ip)
        note = None
        if notify:
            time.sleep(2)  # give the payload a moment to start
            try:
                send_notification(ip, f"Payload sent: {path.name}")
                note = {"ok": True, "message": "Notification sent to the PS4"}
            except Exception as e:
                note = {"ok": False, "message": f"Could not send the notification: {friendly(e)}"}
        job.update(state="done", ok=True, message=f"Payload sent ({size:,} bytes)", note=note)

    threading.Thread(target=work, daemon=True).start()
    return job_id


# -------------------------------------------------------------------- klog
class KlogSession:
    def __init__(self):
        self.lock = threading.Lock()
        self.lines = collections.deque(maxlen=5000)
        self.next_id = 0
        self.state = "idle"
        self.message = ""
        self.stop_event = None
        self.gen = 0
        self.partial = ""

    def start(self, ip, port):
        with self.lock:
            if self.state in ("connecting", "connected"):
                raise ApiError("Klog is already running", 409)
            self.gen += 1
            gen = self.gen
            self.lines.clear()
            self.partial = ""
            self.state = "connecting"
            self.message = f"Connecting to {ip}:{port}..."
            self.stop_event = stop = threading.Event()
            start_id = self.next_id

        def on_connect():
            with self.lock:
                if self.gen == gen:
                    self.state = "connected"
                    self.message = f"Connected to {ip}:{port}"

        def on_text(text):
            with self.lock:
                if self.gen != gen:
                    return
                self.partial += text
                *done, self.partial = self.partial.split("\n")
                for line in done:
                    self.lines.append((self.next_id, line.rstrip("\r")))
                    self.next_id += 1

        def work():
            reason = stream_klog(ip, port, stop, on_connect, on_text)
            with self.lock:
                if self.gen == gen:
                    self.state = "ended"
                    self.message = reason

        threading.Thread(target=work, daemon=True).start()
        return start_id

    def stop(self):
        with self.lock:
            if self.stop_event:
                self.stop_event.set()

    def read(self, since):
        with self.lock:
            lines = [text for i, text in self.lines if i >= since]
            return {"lines": lines, "next": self.next_id, "state": self.state, "message": self.message}


# ----------------------------------------------------------------- library
def clean_filename(name):
    base = os.path.basename(unquote(name or "").replace("\\", "/"))
    base = re.sub(r"[^\w.\- ()]", "_", base).strip(" .")[:100]
    return base or "payload.bin"


def library_path(name):
    if not name or name != os.path.basename(name) or name.startswith("."):
        raise ApiError("Invalid payload name")
    path = PAYLOAD_DIR / name
    if not path.is_file():
        raise ApiError("Payload not found. It may have been deleted.", 404)
    return path


def list_payloads():
    PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in PAYLOAD_DIR.iterdir():
        if p.is_file():
            st = p.stat()
            items.append({"name": p.name, "size": st.st_size, "modified": int(st.st_mtime)})
    items.sort(key=lambda i: i["modified"], reverse=True)
    return items


# --------------------------------------------------------------- http layer
class Handler(BaseHTTPRequestHandler):
    server_version = "PS4Toolkit"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    # --- plumbing
    def send_bytes(self, status, body, content_type, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, payload):
        self.send_bytes(status, json.dumps(payload).encode("utf-8"), "application/json")

    def json_body(self):
        raw = self.body
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            raise ApiError("Invalid JSON")
        if not isinstance(data, dict):
            raise ApiError("Expected a JSON object")
        return data

    def origin_ok(self):
        port = self.server.port
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "") not in allowed_hosts:
            return False  # blocks DNS rebinding
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in allowed_hosts}

    def route(self, method):
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        self.body = b""
        try:
            if not self.origin_ok():
                self.close_connection = True
                raise ApiError("Forbidden", 403)
            if method == "GET" and path == "/":
                html = (APP_DIR / "index.html").read_text(encoding="utf-8")
                html = html.replace("__TOKEN__", self.server.token)
                return self.send_bytes(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if method == "GET" and path == "/icon":
                data, ctype = find_icon()
                return self.send_bytes(
                    200, data, ctype,
                    {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"},
                )
            if method == "GET" and path in STATIC:
                name, ctype = STATIC[path]
                return self.send_bytes(200, (APP_DIR / name).read_bytes(), ctype)
            if not path.startswith("/api/"):
                self.close_connection = True
                raise ApiError("Not found", 404)
            if not hmac.compare_digest(self.headers.get("X-Token", ""), self.server.token):
                self.close_connection = True
                raise ApiError("Forbidden", 403)
            self.server.last_seen = time.time()
            if path != "/api/closing":
                self.server.closing_at = None  # a page is alive (for example after a refresh)
            self.read_request_body(method, path)
            handler = ROUTES.get((method, path))
            if not handler:
                raise ApiError("Not found", 404)
            self.send_json(200, handler(self, query))
        except ApiError as e:
            self.send_json(e.status, {"error": str(e)})
        except Exception as e:  # keep the server alive whatever happens
            self.close_connection = True
            self.send_json(500, {"error": f"{type(e).__name__}: {e}"})

    def read_request_body(self, method, path):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        limit = MAX_UPLOAD if (method, path) == ("POST", "/api/payloads") else MAX_JSON
        if length > limit:
            self.close_connection = True  # the body is left unread, so drop the connection
            raise ApiError("That is too large", 413)
        self.body = self.rfile.read(length) if length else b""

    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_DELETE(self):
        self.route("DELETE")

    # --- api
    def api_ping(self, q):
        return {"ok": True}

    def api_closing(self, q):
        self.server.closing_at = time.time()
        return {"ok": True}

    def api_quit(self, q):
        self.server.quit_event.set()
        return {"ok": True}

    def api_settings_get(self, q):
        return self.server.settings.get()

    def api_settings_post(self, q):
        return self.server.settings.update(self.json_body())

    def api_status(self, q):
        ip = q.get("ip", [""])[0].strip()
        try:
            ip = valid_ip(ip)
        except ApiError:
            return {"valid": False, "online": False}
        return {"valid": True, "online": ping(ip)}

    def api_notify(self, q):
        body = self.json_body()
        ip = valid_ip(body.get("ip", ""))
        message = str(body.get("message", "")).strip()
        if not message:
            raise ApiError("Enter a message first")
        if len(message) > 1000:
            raise ApiError("That message is too long")
        try:
            send_notification(ip, message)
        except Exception as e:
            raise ApiError(f"Notification failed: {friendly(e)}", 502)
        self.server.settings.remember_ip(ip)
        return {"message": "Notification sent successfully!"}

    def api_reboot(self, q):
        ip = valid_ip(self.json_body().get("ip", ""))
        try:
            message = send_reboot(ip)
        except Exception as e:
            raise ApiError(f"Reboot failed: {friendly(e)}", 502)
        self.server.settings.remember_ip(ip)
        return {"message": message}

    def api_ports(self, q):
        body = self.json_body()
        ip = valid_ip(body.get("ip", ""))
        include = bool(body.get("include_payload"))
        results = {}

        def probe(port):
            results[str(port)] = check_port(ip, port)

        threads = []
        for port, always in SERVICES:
            if always or include:
                t = threading.Thread(target=probe, args=(port,), daemon=True)
                t.start()
                threads.append(t)
            else:
                results[str(port)] = None
        for t in threads:
            t.join(timeout=4)
        return {"results": results}

    def api_ftp(self, q):
        ip = valid_ip(self.json_body().get("ip", ""))
        url = f"ftp://{ip}:{FTP_PORT}"
        try:
            if hasattr(os, "startfile"):
                os.startfile(url)
            else:
                webbrowser.open(url)
        except OSError as e:
            raise ApiError(f"Could not open FTP: {e}", 500)
        return {"message": f"Opening {url}"}

    def api_payloads_list(self, q):
        return {"payloads": list_payloads()}

    def api_payloads_upload(self, q):
        name = clean_filename(self.headers.get("X-Filename", ""))
        data = self.body
        if not data:
            raise ApiError("That file is empty")
        PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)
        tmp = PAYLOAD_DIR / f".upload-{uuid.uuid4().hex}"
        tmp.write_bytes(data)
        os.replace(tmp, PAYLOAD_DIR / name)
        return {"name": name, "payloads": list_payloads()}

    def api_payloads_delete(self, q):
        library_path(q.get("name", [""])[0]).unlink()
        return {"payloads": list_payloads()}

    def api_payload_send(self, q):
        body = self.json_body()
        ip = valid_ip(body.get("ip", ""))
        port = valid_port(body.get("port"))
        path = library_path(str(body.get("name", "")))
        job = start_payload_job(ip, port, path, bool(body.get("notify_after")), self.server.settings)
        return {"job": job}

    def api_job(self, q):
        with JOBS_LOCK:
            job = JOBS.get(q.get("id", [""])[0])
        if not job:
            raise ApiError("Unknown job", 404)
        return dict(job)

    def api_klog_start(self, q):
        body = self.json_body()
        ip = valid_ip(body.get("ip", ""))
        port = valid_port(body.get("port"))
        return {"next": self.server.klog.start(ip, port)}

    def api_klog_stop(self, q):
        self.server.klog.stop()
        return {"ok": True}

    def api_klog_read(self, q):
        try:
            since = int(q.get("since", ["0"])[0])
        except ValueError:
            since = 0
        return self.server.klog.read(since)


ROUTES = {
    ("GET", "/api/ping"): Handler.api_ping,
    ("POST", "/api/closing"): Handler.api_closing,
    ("POST", "/api/quit"): Handler.api_quit,
    ("GET", "/api/settings"): Handler.api_settings_get,
    ("POST", "/api/settings"): Handler.api_settings_post,
    ("GET", "/api/status"): Handler.api_status,
    ("POST", "/api/notify"): Handler.api_notify,
    ("POST", "/api/reboot"): Handler.api_reboot,
    ("POST", "/api/ports"): Handler.api_ports,
    ("POST", "/api/ftp"): Handler.api_ftp,
    ("GET", "/api/payloads"): Handler.api_payloads_list,
    ("POST", "/api/payloads"): Handler.api_payloads_upload,
    ("DELETE", "/api/payloads"): Handler.api_payloads_delete,
    ("POST", "/api/payload/send"): Handler.api_payload_send,
    ("GET", "/api/job"): Handler.api_job,
    ("POST", "/api/klog/start"): Handler.api_klog_start,
    ("POST", "/api/klog/stop"): Handler.api_klog_stop,
    ("GET", "/api/klog"): Handler.api_klog_read,
}


# ----------------------------------------------------------------- launch
def find_app_browser():
    candidates = []
    if os.name == "nt":
        for env in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
            base = os.environ.get(env)
            if base:
                candidates.append(Path(base) / "Microsoft/Edge/Application/msedge.exe")
                candidates.append(Path(base) / "Google/Chrome/Application/chrome.exe")
    else:
        for name in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
    return next((c for c in candidates if c.exists()), None)


def launch_ui(url, app_mode=False):
    """Open the UI in the default browser. With app_mode, try an Edge/Chrome app window first.

    Returns the browser process in app mode, otherwise None."""
    if app_mode:
        exe = find_app_browser()
        if exe:
            try:
                return subprocess.Popen([
                    str(exe), f"--app={url}", f"--user-data-dir={DATA_DIR / 'browser'}",
                    "--window-size=780,920", "--no-first-run", "--no-default-browser-check",
                ])
            except OSError:
                pass
    webbrowser.open(url)
    return None


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    app_mode = "--app" in argv
    open_browser = "--no-browser" not in argv

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PAYLOAD_DIR.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.port = server.server_address[1]
    server.token = secrets.token_urlsafe(24)
    server.settings = Settings(SETTINGS_PATH)
    server.klog = KlogSession()
    server.last_seen = time.time()
    server.closing_at = None
    server.quit_event = threading.Event()
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{server.port}/"
    print(f"PS4 Toolkit running at {url}  (close the browser tab, use the Quit button, or press Ctrl+C)", flush=True)
    proc = launch_ui(url, app_mode) if open_browser else None

    try:
        window_closed = False
        if proc:
            try:
                proc.wait(timeout=3)  # exits at once if the browser handed off to another process
            except subprocess.TimeoutExpired:
                proc.wait()
                window_closed = True
        while not window_closed and not server.quit_event.is_set():
            time.sleep(0.5)
            now = time.time()
            if server.closing_at and now - server.closing_at > CLOSE_GRACE:
                break  # the tab was closed and nothing came back
            if now - server.last_seen > IDLE_LIMIT:
                break  # nobody has talked to us for a long time
    except KeyboardInterrupt:
        pass
    finally:
        server.klog.stop()
        server.shutdown()


if __name__ == "__main__":
    main()
