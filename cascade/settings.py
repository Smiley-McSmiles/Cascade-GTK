"""Settings storage and validation.  Only the daemon writes settings.json; the GTK app, the web interface and the
CLI read and change settings through the daemon, so all three always see the same values."""

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import threading

from . import paths, schema, util

log = logging.getLogger("cascade.settings")

IFACE_RE = re.compile(r"^[A-Za-z0-9_.:@-]{0,32}$")
PBKDF2_ITER = 200000


def hash_secret(plain):
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, PBKDF2_ITER)
    return "pbkdf2_sha256$%d$%s$%s" % (PBKDF2_ITER, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def check_secret(plain, stored):
    try:
        algo, it, salt, dk = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        got = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), base64.b64decode(salt), int(it))
        return hmac.compare_digest(got, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


def coerce(spec, value):
    """Return the value converted to the setting's type, or raise ValueError with a readable message."""
    t = spec["type"]
    if t == "bool":
        if isinstance(value, str):
            v = value.strip().lower()
            if v in ("1", "true", "yes", "on", "enable", "enabled"):
                return True
            if v in ("0", "false", "no", "off", "disable", "disabled"):
                return False
            raise ValueError("expected on/off, got %r" % value)
        return bool(value)
    if t in ("int", "float"):
        try:
            v = int(float(value)) if t == "int" else float(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("expected a number, got %r" % (value,))
        lo, hi = spec.get("min"), spec.get("max")
        if lo is not None and v < lo:
            raise ValueError("must be at least %s" % lo)
        if hi is not None and v > hi:
            raise ValueError("must be at most %s" % hi)
        return v
    if t == "choice":
        allowed = [c[0] for c in spec["choices"]]
        if value not in allowed:
            raise ValueError("must be one of: %s" % ", ".join(allowed))
        return value
    if t in ("path", "file"):
        v = "" if value is None else str(value).strip()
        if v:
            v = os.path.abspath(os.path.expanduser(v))
        return v
    if t == "interface":
        v = "" if value is None else str(value).strip()
        if not IFACE_RE.match(v):
            raise ValueError("not a valid interface name: %r" % v)
        return v
    if t == "lines":
        if isinstance(value, str):
            value = value.splitlines()
        if not isinstance(value, (list, tuple)):
            raise ValueError("expected a list")
        return [str(x).strip() for x in value if str(x).strip()]
    if t == "schedule":
        v = str(value)
        if len(v) != schema.SCHEDULE_LEN or set(v) - set("012"):
            raise ValueError("the schedule must be %d characters of 0 (normal), 1 (slow) or 2 (paused)"
                             % schema.SCHEDULE_LEN)
        return v
    if t == "json":
        return value
    # str, password, secret
    return "" if value is None else str(value)


class Settings:
    def __init__(self, path=None):
        self.path = path or paths.settings_file()
        self.lock = threading.RLock()
        self.listeners = []
        self.values = {}
        self.rev = 0                # bumped on every change so clients know when to re-read
        raw = util.read_json(self.path, {}) or {}
        dirty = False
        for key, spec in schema.SPECS.items():
            if key in raw:
                try:
                    self.values[key] = coerce(spec, raw[key]) if spec["type"] != "secret" else str(raw[key])
                    continue
                except ValueError as e:
                    log.warning("ignoring invalid setting %s=%r (%s)", key, raw[key], e)
            self.values[key] = schema.default(key)
            if callable(spec["default"]):
                dirty = True      # remember generated defaults (the random listening port)
        if dirty or not os.path.exists(self.path):
            self.save()

    def save(self):
        with self.lock:
            util.write_json(self.path, self.values)

    def get(self, key):
        with self.lock:
            return self.values[key]

    def __getitem__(self, key):
        return self.get(key)

    def snapshot(self, masked=True):
        with self.lock:
            out = dict(self.values)
        if masked:
            for k, spec in schema.SPECS.items():
                if spec["type"] in ("password", "secret") and out.get(k):
                    out[k] = schema.MASK
        return out

    def check_password(self, key, plain):
        stored = self.get(key)
        return bool(stored) and check_secret(plain, stored)

    def update(self, changes, notify=True):
        """Validate and store {key: value}.  Returns (changed_keys, errors)."""
        changed, errors = [], {}
        with self.lock:
            for key, value in (changes or {}).items():
                spec = schema.SPECS.get(key)
                if spec is None:
                    errors[key] = "unknown setting"
                    continue
                if spec["type"] in ("password", "secret") and value == schema.MASK:
                    continue          # the masked placeholder came back unchanged
                try:
                    v = coerce(spec, value)
                except ValueError as e:
                    errors[key] = str(e)
                    continue
                if spec["type"] == "secret" and v:
                    v = hash_secret(v)
                if self.values.get(key) != v:
                    self.values[key] = v
                    changed.append(key)
            if changed:
                self.rev += 1
                self.save()
        if changed and notify:
            for cb in list(self.listeners):
                try:
                    cb(changed)
                except Exception:
                    log.exception("settings listener failed")
        return changed, errors

    def reset(self, keys=None):
        keys = keys or [k for k in schema.SPECS if k not in ("labels", "net.port")]
        changes = {}
        for k in keys:
            if k in schema.SPECS:
                changes[k] = schema.default(k)
        with self.lock:
            for k in keys:
                if schema.SPECS.get(k, {}).get("type") == "secret":
                    self.values[k] = ""
        return self.update({k: v for k, v in changes.items() if schema.SPECS[k]["type"] != "secret"})


class State:
    """Runtime state that survives restarts but is not a preference (all-time totals, the current random port...)."""

    def __init__(self, path=None):
        self.path = path or paths.state_file()
        self.lock = threading.RLock()
        self.data = util.read_json(self.path, {}) or {}

    def get(self, key, default=None):
        with self.lock:
            return self.data.get(key, default)

    def set(self, key, value, save=True):
        with self.lock:
            self.data[key] = value
            if save:
                self.save()

    def save(self):
        with self.lock:
            util.write_json(self.path, self.data)
