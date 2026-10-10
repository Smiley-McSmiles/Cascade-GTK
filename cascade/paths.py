"""Filesystem locations.  Cascade runs as the user who owns the torrents, so everything follows the XDG base
directory spec.  Every location can be overridden through the environment, which is what the test-suite and
portable/headless setups use (CASCADE_HOME moves all of them under one directory)."""

import os
import re


def _home():
    return os.path.expanduser("~")


def _xdg(var, fallback):
    base = os.environ.get("CASCADE_HOME")
    if base:
        return os.path.join(base, {"XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data",
                                   "XDG_STATE_HOME": "state"}.get(var, "other"))
    v = os.environ.get(var)
    return os.path.join(v, "cascade") if v and os.path.isabs(v) else os.path.join(_home(), fallback, "cascade")


def config_dir():
    return os.environ.get("CASCADE_CONFIG_DIR") or _xdg("XDG_CONFIG_HOME", ".config")


def data_dir():
    return os.environ.get("CASCADE_DATA_DIR") or _xdg("XDG_DATA_HOME", ".local/share")


def state_dir():
    return os.environ.get("CASCADE_STATE_DIR") or _xdg("XDG_STATE_HOME", ".local/state")


def run_dir():
    if os.environ.get("CASCADE_RUN_DIR"):
        return os.environ["CASCADE_RUN_DIR"]
    if os.environ.get("CASCADE_HOME"):
        return os.path.join(os.environ["CASCADE_HOME"], "run")
    rt = os.environ.get("XDG_RUNTIME_DIR")
    if rt and os.path.isdir(rt):
        return os.path.join(rt, "cascade")
    return "/tmp/cascade-%d" % os.getuid()


def socket_path():
    """Where the daemon listens.  Clients also look in /tmp/cascade-UID: a daemon started at boot by the
    cascaded@USER system unit has no XDG_RUNTIME_DIR, while the user's terminal session does."""
    if os.environ.get("CASCADE_SOCKET"):
        return os.environ["CASCADE_SOCKET"]
    primary = os.path.join(run_dir(), "cascade.sock")
    fallback = "/tmp/cascade-%d/cascade.sock" % os.getuid()
    if not os.path.exists(primary) and primary != fallback and os.path.exists(fallback):
        return fallback
    return primary


def server_socket_path():
    return os.environ.get("CASCADE_SOCKET") or os.path.join(run_dir(), "cascade.sock")


def lock_file():
    return os.path.join(run_dir(), "daemon.lock")


def log_file():
    return os.environ.get("CASCADE_LOG_FILE") or os.path.join(state_dir(), "cascade.log")


def settings_file():
    return os.path.join(config_dir(), "settings.json")


def state_file():
    return os.path.join(data_dir(), "state.json")


def torrents_dir():
    return os.path.join(data_dir(), "torrents")


def meta_file():
    return os.path.join(data_dir(), "torrents.json")


def session_file():
    return os.path.join(data_dir(), "session.state")


def blocklist_cache():
    return os.path.join(data_dir(), "blocklist.cache")


def autostart_file():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(_home(), ".config")
    return os.path.join(base, "autostart", "io.github.smiley_mcsmiles.Cascade.Daemon.desktop")


def package_dir():
    return os.path.dirname(os.path.abspath(__file__))


def share_dir():
    """Installed data (icons) - set by the installed wrapper; falls back to the source tree's data/."""
    d = os.environ.get("CASCADE_SHARE_DIR")
    if d and os.path.isdir(d):
        return d
    src = os.path.join(os.path.dirname(package_dir()), "data")
    return src if os.path.isdir(src) else ""


def default_download_dir():
    """The user's XDG download directory (~/Downloads unless user-dirs.dirs says otherwise)."""
    home = _home()
    cfg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    try:
        with open(os.path.join(cfg, "user-dirs.dirs"), encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r'\s*XDG_DOWNLOAD_DIR\s*=\s*"(.*)"', line)
                if m:
                    p = m.group(1).replace("$HOME", home)
                    if os.path.isabs(p):
                        return p
    except OSError:
        pass
    return os.path.join(home, "Downloads")


def ensure_private_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    if os.stat(path).st_uid != os.getuid():
        # e.g. somebody else created /tmp/cascade-UID first: never put our socket in a directory they control
        raise RuntimeError("%s belongs to another user; remove it or set CASCADE_RUN_DIR" % path)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path
