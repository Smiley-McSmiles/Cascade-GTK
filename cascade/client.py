"""Connecting to the Cascade service: the local socket (starting the service on demand) or a remote web interface."""

import os
import subprocess
import sys
import time

from . import ipc, paths


def spawn_daemon():
    """Start the service detached from this process (it outlives the GTK window and the CLI)."""
    env = dict(os.environ)
    parent = os.path.dirname(paths.package_dir())
    env["PYTHONPATH"] = parent + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    return subprocess.Popen([sys.executable, "-m", "cascade", "daemon", "--quiet"], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            start_new_session=True, cwd="/")


def ensure_daemon(timeout=30):
    """Return a connected local client, starting the service when it is not running."""
    c = ipc.Client()
    if c.alive():
        return c
    proc = spawn_daemon()
    deadline = time.time() + timeout
    while time.time() < deadline:
        if c.alive():
            return c
        if proc.poll() is not None:
            err = (proc.stderr.read() or b"").decode("utf-8", "replace").strip() if proc.stderr else ""
            if c.alive():          # another client started it at the same moment
                return c
            raise ipc.DaemonUnavailable("the Cascade service failed to start%s" % (": " + err.splitlines()[-1]
                                                                                  if err else ""))
        time.sleep(0.15)
    raise ipc.DaemonUnavailable("the Cascade service did not start within %d seconds (see %s)"
                                % (timeout, paths.log_file()))


def connect(url=None, password=None, autostart=True, insecure=False):
    url = url or os.environ.get("CASCADE_URL")
    if url:
        return ipc.HttpClient(url, password if password is not None else os.environ.get("CASCADE_PASSWORD"),
                              insecure=insecure)
    if autostart:
        return ensure_daemon()
    return ipc.Client()
