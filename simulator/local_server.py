"""A tiny stand-in for the Firebase Realtime Database REST API, plus the dashboard files.

Use it when you have no Firebase project yet, or at a venue without internet:
    python simulator/local_server.py
then open http://localhost:8787/dashboard/ and point the ESP32 (FIREBASE_URL) or
simulate.py (--db) at http://<laptop-ip>:8787.

Supports what VialGuard uses: GET / PUT / PATCH (incl. multi-path keys) / DELETE on
any path ending in .json, and orderBy="$key"&limitToLast=N on GET.
"""
import argparse
import json
import mimetypes
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = {}
LOCK = threading.Lock()


def split(path):
    return [p for p in unquote(path).strip("/").split("/") if p]


def get_node(parts):
    node = DB
    for p in parts:
        if not isinstance(node, dict) or p not in node:
            return None
        node = node[p]
    return node


def set_node(parts, value):
    if not parts:
        DB.clear()
        if isinstance(value, dict):
            DB.update(value)
        return
    node = DB
    for p in parts[:-1]:
        if not isinstance(node.get(p), dict):
            node[p] = {}
        node = node[p]
    if value is None:
        node.pop(parts[-1], None)
    else:
        node[parts[-1]] = value


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"null")

    def db_parts(self):
        u = urlparse(self.path)
        if not u.path.endswith(".json"):
            return None, None
        return split(u.path[:-5]), parse_qs(u.query)

    def do_GET(self):
        parts, q = self.db_parts()
        if parts is None:
            return self.static()
        with LOCK:
            node = get_node(parts)
            if isinstance(node, dict) and "limitToLast" in q:
                keys = sorted(node)[-int(q["limitToLast"][0]):]
                node = {k: node[k] for k in keys}
            self.reply(200, node)

    def do_PUT(self):
        parts, _ = self.db_parts()
        if parts is None:
            return self.reply(404, {"error": "not found"})
        value = self.body()
        with LOCK:
            set_node(parts, value)
        self.reply(200, value)

    def do_PATCH(self):
        parts, _ = self.db_parts()
        if parts is None:
            return self.reply(404, {"error": "not found"})
        value = self.body()
        if not isinstance(value, dict):
            return self.reply(400, {"error": "PATCH body must be an object"})
        with LOCK:
            for k, v in value.items():
                set_node(parts + split(k), v)
        self.reply(200, value)

    def do_DELETE(self):
        parts, _ = self.db_parts()
        with LOCK:
            set_node(parts or [], None)
        self.reply(200, None)

    def static(self):
        path = unquote(urlparse(self.path).path)
        if path in ("", "/"):
            path = "/dashboard/"
        if path.endswith("/"):
            path += "index.html"
        full = os.path.normpath(os.path.join(ROOT_DIR, path.lstrip("/")))
        if not full.startswith(os.path.join(ROOT_DIR, "dashboard")) or not os.path.isfile(full):
            return self.reply(404, {"error": "not found"})
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(full)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class DualStackServer(ThreadingHTTPServer):
    """Listens on IPv6 and IPv4 at once. Otherwise "localhost" stalls ~2 s per request on Windows."""
    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def serve(port):
    try:
        srv = DualStackServer(("::", port), Handler)
    except OSError:
        srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    serve(a.port)
    print(f"VialGuard local server on http://localhost:{a.port}  (dashboard: http://localhost:{a.port}/dashboard/)")
    print("Press Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
