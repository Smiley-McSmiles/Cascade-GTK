"""cascaded: the background service that owns the libtorrent session.

The GTK app starts it on demand (and it keeps running after the window closes when "Keep running in the
background" is on), `cascade daemon` runs it in the foreground, and the systemd user unit runs it at login.
Exactly one runs per user; the lock in $XDG_RUNTIME_DIR/cascade enforces that.
"""

import fcntl
import logging
import logging.handlers
import os
import shlex
import shutil
import signal
import sys
import threading

from . import __version__, paths, util

log = logging.getLogger("cascade")

LEVELS = {"error": logging.ERROR, "warning": logging.WARNING, "info": logging.INFO, "debug": logging.DEBUG}


class AlreadyRunning(Exception):
    pass


def setup_logging(level="info", foreground=True):
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    os.makedirs(os.path.dirname(paths.log_file()), exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(paths.log_file(), maxBytes=2 * 1024 * 1024, backupCount=2)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if foreground:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
    root.setLevel(LEVELS.get(level, logging.INFO))


def launcher_command():
    """How the login autostart entry should start the daemon."""
    for exe in ("cascaded",):
        p = shutil.which(exe)
        if p:
            return shlex.quote(p)
    pkg_parent = os.path.dirname(paths.package_dir())
    return "%s PYTHONPATH=%s %s -m cascade daemon" % (shutil.which("env") or "/usr/bin/env", shlex.quote(pkg_parent),
                                                     shlex.quote(sys.executable))


def write_autostart(enabled):
    f = paths.autostart_file()
    if enabled:
        util.write_atomic(f, "[Desktop Entry]\nType=Application\nName=Cascade service\n"
                             "Comment=Keeps your torrents transferring in the background\n"
                             "Exec=%s\nIcon=io.github.smiley_mcsmiles.Cascade\nTerminal=false\nNoDisplay=true\n"
                             "X-GNOME-Autostart-enabled=true\n" % launcher_command(), 0o644)
    else:
        try:
            os.unlink(f)
        except OSError:
            pass


def acquire_lock():
    paths.ensure_private_dir(paths.run_dir())
    fh = open(paths.lock_file(), "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise AlreadyRunning("Cascade is already running (lock %s)" % paths.lock_file())
    fh.seek(0)
    fh.truncate()
    fh.write("%d\n" % os.getpid())
    fh.flush()
    return fh


def run(foreground=True):
    for d in (paths.config_dir(), paths.data_dir(), paths.state_dir()):
        paths.ensure_private_dir(d)
    lock = acquire_lock()

    from .settings import Settings, State
    settings = Settings()
    setup_logging(settings["log.level"], foreground)
    if not foreground:
        # started in the background: once logging works, stray output (warnings, tracebacks) belongs in the log,
        # not in a pipe that closes when the app that started us exits
        out = os.open(paths.log_file(), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.dup2(out, 1)
        os.dup2(out, 2)
        os.close(out)
    log.info("Cascade %s starting (pid %d)", __version__, os.getpid())

    from . import ipc
    from .api import Api
    from .engine import Engine
    from .webui.server import WebUI

    stop = threading.Event()
    state = State()
    engine = Engine(settings, state)
    webui = WebUI(engine, settings)
    api = Api(engine, settings, webui, stop)
    webui.api = api

    def on_change(keys):
        if "log.level" in keys:
            logging.getLogger().setLevel(LEVELS.get(settings["log.level"], logging.INFO))
        if "app.start_at_boot" in keys:
            on = settings["app.start_at_boot"]

            def work():
                from . import bootsvc
                try:
                    init = bootsvc.apply(on, state)
                except bootsvc.BootError as e:
                    engine.event("error", "autostart", "Cannot change starting at boot: %s" % e)
                    settings.update({"app.start_at_boot": not on}, notify=False)
                    return
                engine.event("info", "autostart", "Cascade %s when the computer starts (%s)"
                             % ("now starts" if on else "no longer starts", init))
                if on and settings["app.autostart"]:
                    settings.update({"app.autostart": False})      # one way of starting is enough

            threading.Thread(target=work, name="boot", daemon=True).start()
        if "app.autostart" in keys:
            try:
                write_autostart(settings["app.autostart"])
            except OSError as e:
                log.error("could not change the login autostart entry: %s", e)

    settings.listeners += [engine.on_settings_changed, webui.on_settings_changed, on_change]
    server = ipc.Server(paths.server_socket_path(), api.dispatch)
    server.serve_in_thread()
    webui.apply()

    def handle(signum, _frame):
        log.info("signal %d received", signum)
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, handle)
    log.info("ready on %s", paths.server_socket_path())
    try:
        while not stop.wait(1.0):
            try:
                engine.tick()
            except Exception:
                log.exception("periodic work failed")
    finally:
        server.close()
        webui.stop()
        engine.shutdown()
        lock.close()
        log.info("stopped")
    return 0
