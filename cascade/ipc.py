"""Line-delimited JSON-RPC between the daemon and its clients.

Locally the GTK app and the CLI use a Unix socket (only the owner can open it).  Remotely the CLI can use the web
interface's HTTP API with the same methods (HttpClient).  A request is {"id", "method", "params"}; a reply is
{"id", "ok": true, "result"} or {"id", "ok": false, "error"}.
"""

import json
import os
import socket
import socketserver
import ssl
import threading
import urllib.error
import urllib.request

from . import paths


class DaemonUnavailable(Exception):
    pass


class RpcError(Exception):
    pass


class Client:
    """Keeps one connection open and reconnects transparently; safe to share between threads."""

    def __init__(self, path=None, timeout=60):
        self.path = path or paths.socket_path()
        self.timeout = timeout
        self._sock = None
        self._buf = b""
        self._n = 0
        self._lock = threading.Lock()

    def _connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.path)
        except FileNotFoundError:
            s.close()
            raise DaemonUnavailable("the Cascade service is not running (no socket at %s)" % self.path)
        except OSError as e:
            s.close()
            raise DaemonUnavailable("cannot reach the Cascade service: %s" % (e.strerror or e))
        self._sock, self._buf = s, b""

    def close(self):
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
            self._sock = None

    def call(self, method, **params):
        with self._lock:
            for attempt in (0, 1):
                if self._sock is None:
                    self._connect()
                self._n += 1
                req = (json.dumps({"id": self._n, "method": method, "params": params}) + "\n").encode()
                try:
                    self._sock.sendall(req)
                    while b"\n" not in self._buf:
                        chunk = self._sock.recv(1 << 20)
                        if not chunk:
                            raise ConnectionResetError("closed")
                        self._buf += chunk
                except (OSError, ConnectionResetError) as e:
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                    self._sock = None
                    if attempt == 0 and not isinstance(e, socket.timeout):
                        continue      # the daemon restarted: reconnect once
                    raise DaemonUnavailable("the Cascade service did not answer: %s" % e)
                line, self._buf = self._buf.split(b"\n", 1)
                break
        try:
            resp = json.loads(line.decode())
        except ValueError:
            raise DaemonUnavailable("garbled reply from the Cascade service")
        if not resp.get("ok"):
            raise RpcError(resp.get("error", "unknown error"))
        return resp.get("result")

    def alive(self):
        try:
            self.call("ping")
            return True
        except (DaemonUnavailable, RpcError):
            return False


class HttpClient:
    """The same interface over the web interface's HTTP API (for `cascade --url http://host:8150`)."""

    def __init__(self, url, password=None, timeout=60, insecure=False):
        self.url = url.rstrip("/")
        self.password = password
        self.timeout = timeout
        self.token = None
        self.ctx = None
        if insecure:
            self.ctx = ssl.create_default_context()
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE

    def _post(self, path, body, auth=True):
        headers = {"Content-Type": "application/json"}
        if auth and self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode())
            except ValueError:
                raise DaemonUnavailable("HTTP %d from %s" % (e.code, self.url))
        except (urllib.error.URLError, OSError) as e:
            raise DaemonUnavailable("cannot reach %s: %s" % (self.url, getattr(e, "reason", e)))

    def login(self):
        r = self._post("/api/login", {"password": self.password or ""}, auth=False)
        if not r.get("ok"):
            raise RpcError(r.get("error", "login failed"))
        self.token = r["result"]["token"]

    def call(self, method, **params):
        if self.token is None:
            self.login()
        r = self._post("/api/rpc", {"method": method, "params": params})
        if not r.get("ok") and r.get("error") == "not signed in":
            self.login()
            r = self._post("/api/rpc", {"method": method, "params": params})
        if not r.get("ok"):
            raise RpcError(r.get("error", "unknown error"))
        return r.get("result")

    def alive(self):
        try:
            self.call("ping")
            return True
        except (DaemonUnavailable, RpcError):
            return False

    def close(self):
        pass


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        while True:
            try:
                line = self.rfile.readline(64 * 1024 * 1024)
            except OSError:
                return
            if not line:
                return
            rid = None
            try:
                req = json.loads(line.decode())
                rid = req.get("id")
                result = self.server.dispatch(req.get("method", ""), req.get("params") or {}, "local")
                resp = {"id": rid, "ok": True, "result": result}
            except Exception as e:  # every error goes back to the client as text
                resp = {"id": rid, "ok": False, "error": str(e) or e.__class__.__name__}
            try:
                self.wfile.write((json.dumps(resp, default=str) + "\n").encode())
            except OSError:
                return


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, path, dispatch):
        self.dispatch = dispatch
        d = os.path.dirname(path)
        paths.ensure_private_dir(d)
        if os.path.exists(path):
            os.unlink(path)
        old = os.umask(0o177)
        try:
            super().__init__(path, _Handler)
        finally:
            os.umask(old)
        os.chmod(path, 0o600)
        self.path = path

    def serve_in_thread(self):
        t = threading.Thread(target=self.serve_forever, name="rpc", daemon=True)
        t.start()
        return t

    def close(self):
        self.shutdown()
        self.server_close()
        try:
            os.unlink(self.path)
        except OSError:
            pass
