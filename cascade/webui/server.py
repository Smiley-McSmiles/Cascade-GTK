"""The web interface: a small HTTP(S) server inside the daemon.

It serves the single-page app in static/ and exposes the daemon's RPC methods at POST /api/rpc, so the browser
sees exactly what the GTK app and the CLI see.  Sign-in uses the web interface password (stored hashed); the
token travels in an Authorization header, never a cookie, so other sites cannot make requests on your behalf.
"""

import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import secrets
import socket
import ssl
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import __version__, netif, paths

log = logging.getLogger("cascade.webui")

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
MAX_BODY = 80 * 1024 * 1024
TOKEN_TTL = 30 * 24 * 3600
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, webui):
        self.webui = webui
        if ":" in addr[0]:
            self.address_family = socket.AF_INET6
        super().__init__(addr, handler)

    def server_bind(self):
        if self.address_family == socket.AF_INET6:
            try:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                pass
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    server_version = "Cascade/" + __version__
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.client_address[0], fmt % args)

    # ------------------------------------------------------------ helpers
    @property
    def webui(self):
        return self.server.webui

    def _headers(self, code, ctype, length, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                         "connect-src 'self'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _json(self, code, obj):
        body = json.dumps(obj, default=str).encode()
        self._headers(code, "application/json; charset=utf-8", len(body), {"Cache-Control": "no-store"})
        self.wfile.write(body)

    def _is_local(self):
        try:
            ip = ipaddress.ip_address(self.client_address[0])
            if ip.version == 6 and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
            return ip.is_loopback
        except ValueError:
            return False

    def _host_is_local(self):
        host = (self.headers.get("Host") or "").strip()
        if host.startswith("["):
            host = host[:host.find("]") + 1]
        else:
            host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return host.lower() in LOCAL_HOSTS

    def _local_bypass(self):
        # the Host check stops DNS-rebinding pages from using the password-free local access
        return self.webui.settings["webui.local_no_auth"] and self._is_local() and self._host_is_local()

    def _authorized(self):
        if self._local_bypass():
            return True
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Bearer "):
            return False
        return self.webui.check_token(auth[7:].strip())

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("request too large")
        raw = self.rfile.read(n) if n else b""
        return json.loads(raw.decode() or "{}")

    # ------------------------------------------------------------ routes
    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/ping":
            return self._json(200, {"ok": True, "result": {
                "version": __version__, "auth_required": not self._local_bypass(),
                "password_set": bool(self.webui.settings["webui.password"])}})
        if path in ("/", "/index.html", "/add"):
            return self._static("index.html")
        if path == "/manifest.webmanifest":
            return self._static("manifest.webmanifest", "application/manifest+json")
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        self._json(404, {"ok": False, "error": "not found"})

    def do_HEAD(self):
        self.do_GET()

    def _static(self, rel, ctype=None):
        rel = os.path.normpath(rel).lstrip("/")
        full = os.path.join(STATIC, rel)
        if rel.startswith("..") or not os.path.isfile(full):
            return self._json(404, {"ok": False, "error": "not found"})
        with open(full, "rb") as fh:
            data = fh.read()
        ctype = ctype or mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        etag = '"%x-%x"' % (int(os.stat(full).st_mtime), len(data))
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._headers(200, ctype, len(data), {"Cache-Control": "no-cache", "ETag": etag})
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        origin = self.headers.get("Origin")
        if origin:
            o = urllib.parse.urlsplit(origin)
            if o.netloc and o.netloc != self.headers.get("Host"):
                return self._json(403, {"ok": False, "error": "cross-origin request refused"})
        try:
            body = self._body()
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"ok": False, "error": "bad request"})
        if path == "/api/login":
            return self._login(body)
        if path == "/api/logout":
            auth = self.headers.get("Authorization") or ""
            self.webui.revoke(auth[7:].strip())
            return self._json(200, {"ok": True, "result": True})
        if path == "/api/rpc":
            if not self._authorized():
                return self._json(401, {"ok": False, "error": "not signed in"})
            method = body.get("method", "")
            try:
                result = self.webui.api.dispatch(method, body.get("params") or {}, "web")
                return self._json(200, {"ok": True, "result": result})
            except Exception as e:
                if not str(e):
                    log.exception("rpc %s", method)
                return self._json(200, {"ok": False, "error": str(e) or e.__class__.__name__})
        self._json(404, {"ok": False, "error": "not found"})

    def _login(self, body):
        ip = self.client_address[0]
        if self._local_bypass():
            return self._json(200, {"ok": True, "result": {"token": self.webui.new_token()}})
        if self.webui.throttled(ip):
            return self._json(429, {"ok": False, "error": "too many attempts, wait a minute"})
        if not self.webui.settings["webui.password"]:
            return self._json(403, {"ok": False, "error": "no password is set: set one in Cascade's preferences "
                                                          "(Remote → Password) or with: cascade webui password"})
        pw = str(body.get("password", ""))
        if self.webui.settings.check_password("webui.password", pw):
            return self._json(200, {"ok": True, "result": {"token": self.webui.new_token()}})
        self.webui.failed(ip)
        time.sleep(0.8)
        return self._json(401, {"ok": False, "error": "wrong password"})


class WebUI:
    def __init__(self, engine, settings, api=None):
        self.engine = engine
        self.settings = settings
        self.api = api
        self.server = None
        self.thread = None
        self.error = None
        self.bound = None
        self.tokens = {}
        self.attempts = {}
        self.lock = threading.Lock()

    # ------------------------------------------------------------ tokens
    def new_token(self):
        t = secrets.token_urlsafe(32)
        with self.lock:
            self.tokens[t] = time.time() + TOKEN_TTL
        return t

    def check_token(self, t):
        now = time.time()
        with self.lock:
            for k in [k for k, exp in self.tokens.items() if exp < now]:
                del self.tokens[k]
            return any(hmac.compare_digest(t, k) for k in self.tokens)

    def revoke(self, t):
        with self.lock:
            self.tokens.pop(t, None)

    def throttled(self, ip):
        now = time.time()
        with self.lock:
            recent = [x for x in self.attempts.get(ip, []) if now - x < 60]
            self.attempts[ip] = recent
            return len(recent) >= 10

    def failed(self, ip):
        with self.lock:
            self.attempts.setdefault(ip, []).append(time.time())

    # ------------------------------------------------------------ lifecycle
    def on_settings_changed(self, keys):
        if "webui.password" in keys:
            with self.lock:
                self.tokens.clear()            # a new password signs every browser out
        if any(k.startswith("webui.") and k not in ("webui.password", "webui.local_no_auth") for k in keys):
            self.apply()

    def apply(self):
        self.stop()
        if self.settings["webui.enabled"]:
            self.start()

    def start(self):
        s = self.settings
        host, port = s["webui.host"] or "0.0.0.0", s["webui.port"]
        self.error = None
        try:
            srv = _Server((host, port), Handler, self)
            if s["webui.https"]:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                ctx.load_cert_chain(s["webui.cert"], s["webui.key"] or None)
                srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        except (OSError, ssl.SSLError, ValueError) as e:
            self.error = "cannot start on %s:%d: %s" % (host, port, getattr(e, "strerror", None) or e)
            self.engine.event("error", "webui", "Web interface %s" % self.error)
            return False
        self.server = srv
        self.bound = (host, port)
        self.thread = threading.Thread(target=srv.serve_forever, name="webui", daemon=True)
        self.thread.start()
        self.engine.event("info", "webui", "Web interface running at %s" % ", ".join(self.urls()))
        return True

    def stop(self):
        if self.server:
            try:
                self.server.shutdown()
                self.server.server_close()
            except OSError:
                pass
            self.server = None
            self.bound = None

    def urls(self):
        s = self.settings
        scheme = "https" if s["webui.https"] else "http"
        host, port = s["webui.host"] or "0.0.0.0", s["webui.port"]
        if host in ("0.0.0.0", "::", ""):
            hosts = ["localhost"] + netif.lan_addresses()
        else:
            hosts = [host]
        out = []
        for h in hosts:
            out.append("%s://%s:%d/" % (scheme, "[%s]" % h if ":" in h else h, port))
        return out

    def status(self):
        return {"enabled": self.settings["webui.enabled"], "running": self.server is not None,
                "urls": self.urls() if self.server else [], "error": self.error,
                "password_set": bool(self.settings["webui.password"]), "socket": paths.socket_path()}
