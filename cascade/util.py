"""Small helpers shared by the daemon, the CLI and the GTK app (the web UI has the same formatters in app.js)."""

import json
import os
import tempfile
import time

UNITS = ("B", "kB", "MB", "GB", "TB", "PB")


def fmt_size(n):
    """Decimal units, like GLib.format_size() (what GNOME apps show)."""
    if n is None or n < 0:
        return "-"
    if n < 1000:
        return "%d B" % n
    n = float(n)
    for unit in UNITS[1:]:
        n /= 1000.0
        if n < 999.95 or unit == UNITS[-1]:
            return "%.1f %s" % (n, unit)


def fmt_speed(bps):
    if not bps or bps <= 0:
        return "0 B/s"
    return fmt_size(bps) + "/s"


def fmt_duration(seconds, short=False):
    """3 d 4 h / 2 h 5 min / 45 s.  None or negative means unknown."""
    if seconds is None or seconds < 0:
        return "∞"
    s = int(seconds)
    if s < 60:
        return "%d s" % s
    m, s = divmod(s, 60)
    if m < 60:
        return "%d min" % m if short or not s else "%d min %d s" % (m, s)
    h, m = divmod(m, 60)
    if h < 24:
        return "%d h" % h if short or not m else "%d h %d min" % (h, m)
    d, h = divmod(h, 24)
    if d < 365:
        return "%d d" % d if short or not h else "%d d %d h" % (d, h)
    y, d = divmod(d, 365)
    return "%d y %d d" % (y, d)


def fmt_time(ts):
    if not ts or ts <= 0:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def fmt_ratio(r):
    if r is None or r < 0:
        return "∞"
    return "%.2f" % r


STATE_LABELS = {
    "downloading": "Downloading",
    "seeding": "Seeding",
    "completed": "Completed",
    "paused": "Paused",
    "queued": "Queued",
    "checking": "Checking",
    "metadata": "Fetching metadata",
    "moving": "Moving",
    "error": "Error",
    "stopped": "Stopped",
}

FILTERS = [
    ("all", "All Torrents", "view-list-symbolic"),
    ("downloading", "Downloading", "folder-download-symbolic"),
    ("seeding", "Seeding", "send-to-symbolic"),
    ("completed", "Completed", "object-select-symbolic"),
    ("paused", "Paused", "media-playback-pause-symbolic"),
    ("queued", "Queued", "view-list-ordered-symbolic"),
    ("active", "Active", "network-transmit-receive-symbolic"),
    ("checking", "Checking", "view-refresh-symbolic"),
    ("error", "Errors", "dialog-warning-symbolic"),
]


def is_magnet(s):
    return isinstance(s, str) and s.strip().lower().startswith("magnet:?")


def is_url(s):
    return isinstance(s, str) and s.strip().lower().startswith(("http://", "https://"))


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_atomic(path, data, mode=0o600):
    """Write bytes/str to path via a temp file + rename, so a crash never leaves a half-written file."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    if isinstance(data, str):
        data = data.encode("utf-8")
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(path, obj, mode=0o600):
    write_atomic(path, json.dumps(obj, indent=2, sort_keys=True) + "\n", mode)


def free_space(path):
    """Free bytes on the filesystem that holds path (walks up to an existing parent)."""
    p = os.path.abspath(os.path.expanduser(path or "/"))
    while p and not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    try:
        st = os.statvfs(p)
        return st.f_bavail * st.f_frsize
    except OSError:
        return -1
