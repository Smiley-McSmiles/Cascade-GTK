"""Talking to the Cascade service from the GTK main loop without ever blocking it.

Calls run one after another on a worker thread; results come back on the main loop via GLib.idle_add."""

import queue
import threading

from gi.repository import GLib

from .. import client, ipc


class AsyncRpc:
    def __init__(self):
        self.q = queue.Queue()
        self.c = None
        self.on_disconnect = None
        self.thread = threading.Thread(target=self._run, name="rpc", daemon=True)
        self.thread.start()

    def connect(self, done, failed):
        """Connect (starting the service when needed) on the worker thread."""
        self.q.put(("__connect__", None, done, failed))

    def call(self, method, params=None, done=None, failed=None):
        self.q.put((method, params or {}, done, failed))

    def call_sync(self, method, **params):
        """Blocking call for the rare places that need an answer right away (quit)."""
        c = self.c or ipc.Client()
        return c.call(method, **params)

    def _run(self):
        while True:
            method, params, done, failed = self.q.get()
            try:
                if method == "__connect__":
                    self.c = client.ensure_daemon()
                    result = True
                else:
                    if self.c is None:
                        raise ipc.DaemonUnavailable("not connected")
                    result = self.c.call(method, **params)
            except ipc.DaemonUnavailable as e:
                if self.on_disconnect and method != "__connect__":
                    GLib.idle_add(_once, self.on_disconnect, str(e))
                if failed:
                    GLib.idle_add(_once, failed, str(e))
                continue
            except Exception as e:  # RpcError and anything unexpected go to the caller as text
                if failed:
                    GLib.idle_add(_once, failed, str(e))
                else:
                    GLib.idle_add(_once, _print_error, method, str(e))
                continue
            if done:
                GLib.idle_add(_once, done, result)


def _once(fn, *args):
    fn(*args)
    return False        # never let a callback's return value make GLib call it again


def _print_error(method, msg):
    print("cascade: %s failed: %s" % (method, msg))
