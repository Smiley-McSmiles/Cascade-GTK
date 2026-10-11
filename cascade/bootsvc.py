"""Starting the Cascade service when the computer starts, before anyone logs in, on every init system.

    systemd   a user unit + "lingering" for the account (no root needed)
    runit     /etc/sv/cascaded-USER linked into the service directory (Void, Artix, ...)
    openrc    /etc/init.d/cascaded.USER added to the default runlevel (Alpine, Gentoo, Artix)
    freebsd   /usr/local/etc/rc.d/cascaded with cascaded_enable / cascaded_user in rc.conf
    openbsd   /etc/rc.d/cascaded enabled with rcctl, running as the user

The service always runs as the user who owns the torrents.  Everything but systemd needs root: the app asks for
the password with pkexec, `cascade boot enable` uses sudo or doas.
"""

import os
import pwd
import shlex
import shutil
import subprocess
import sys

from . import paths, util


class BootError(Exception):
    pass


def detect():
    p = sys.platform
    if p.startswith(("freebsd", "dragonfly")):
        return "freebsd"
    if p.startswith("openbsd"):
        return "openbsd"
    if os.path.isdir("/run/systemd/system"):
        return "systemd"
    try:
        with open("/proc/1/comm") as fh:
            if fh.read().strip() == "runit":
                return "runit"
    except OSError:
        pass
    if os.path.isdir("/run/runit") or (shutil.which("sv") and os.path.isdir("/etc/sv")):
        return "runit"
    if os.path.isdir("/run/openrc") or os.path.exists("/sbin/openrc-run"):
        return "openrc"
    return None


def daemon_command():
    """Absolute command line that runs the service (the installed wrapper, or this Python + source tree)."""
    exe = shutil.which("cascaded")
    if exe:
        return shlex.quote(os.path.realpath(exe) if os.path.islink(exe) else exe) + " --quiet"
    parent = os.path.dirname(paths.package_dir())
    return "%s PYTHONPATH=%s %s -m cascade daemon --quiet" % (shutil.which("env") or "/usr/bin/env",
                                                              shlex.quote(parent), shlex.quote(sys.executable))


def cascade_command():
    """argv that runs the `cascade` CLI as root (pkexec/sudo drop the environment)."""
    exe = shutil.which("cascade")
    if exe:
        return [exe]
    return [shutil.which("env") or "/usr/bin/env", "PYTHONPATH=" + os.path.dirname(paths.package_dir()),
            sys.executable, "-m", "cascade"]


def _run(*cmd, timeout=30):
    try:
        r = subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stderr or r.stdout).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)


def _must(*cmd):
    rc, out = _run(*cmd)
    if rc:
        raise BootError("%s failed: %s" % (" ".join(cmd), out or "exit %d" % rc))
    return out


def _write(path, text, mode):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    util.write_atomic(path, text, mode)


def _home(user):
    try:
        return pwd.getpwnam(user).pw_dir
    except KeyError:
        raise BootError("no such user: %s" % user)


# ---------------------------------------------------------------- service definitions
SYSTEMD_USER_UNIT = """[Unit]
Description=Cascade BitTorrent service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={cmd}
Restart=on-failure
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=default.target
"""

RUNIT_RUN = """#!/bin/sh
# Cascade for {user} (written by `cascade boot enable`)
export HOME={home} USER={user} LOGNAME={user}
cd "$HOME" || exit 1
exec chpst -u {user} {cmd} 2>&1
"""

RUNIT_LOG = """#!/bin/sh
exec logger -t cascaded
"""

OPENRC = """#!/sbin/openrc-run
# Cascade for {user} (written by `cascade boot enable`)
name="Cascade"
description="Cascade BitTorrent service for {user}"
command="/bin/sh"
command_args="-c 'exec {cmd_sh}'"
command_user="{user}"
command_background=true
pidfile="/run/${{RC_SVCNAME}}.pid"
retry="TERM/30/KILL/5"

depend() {{
    need net
    after firewall
}}

start_pre() {{
    export HOME="{home}" USER="{user}" LOGNAME="{user}"
}}
"""

FREEBSD = """#!/bin/sh
# Cascade BitTorrent service (written by `cascade boot enable`)
# PROVIDE: cascaded
# REQUIRE: LOGIN NETWORKING
# KEYWORD: shutdown
. /etc/rc.subr

name=cascaded
rcvar=cascaded_enable
load_rc_config $name
: ${{cascaded_enable:=NO}}
: ${{cascaded_user:=}}

pidfile="/var/run/${{name}}.pid"
procname="/usr/sbin/daemon"
command="/usr/sbin/daemon"
command_args="-r -P ${{pidfile}} -u ${{cascaded_user}} /bin/sh -c 'exec {cmd_sh}'"
start_precmd="cascaded_prestart"

cascaded_prestart()
{{
    [ -n "${{cascaded_user}}" ] || {{ echo "set cascaded_user in /etc/rc.conf"; return 1; }}
    export HOME=$(pw usershow "${{cascaded_user}}" | cut -d: -f9)
}}

run_rc_command "$1"
"""

OPENBSD = """#!/bin/ksh
# Cascade BitTorrent service (written by `cascade boot enable`; the user is set with: rcctl set cascaded user NAME)
daemon="/bin/sh"
daemon_flags="-c 'exec {cmd_sh}'"

. /etc/rc.d/rc.subr

pexp=".* -m cascade daemon --quiet.*|.*/cascaded --quiet.*"
rc_bg=YES
rc_reload=NO

rc_cmd $1
"""


def _sh(cmd):
    return cmd.replace("'", "'\\''")


def _runit_dirs():
    sv = "/etc/sv" if os.path.isdir("/etc/sv") or not os.path.isdir("/etc/runit/sv") else "/etc/runit/sv"
    for d in ("/var/service", "/etc/service", "/service", "/etc/runit/runsvdir/default"):
        if os.path.isdir(d):
            return sv, d
    raise BootError("no runit service directory found (/var/service, /etc/service)")


# ---------------------------------------------------------------- root operations
def enable_system(user, init=None):
    """As root: install and enable the service for `user`."""
    init = init or detect()
    home = _home(user)
    cmd = daemon_command()
    if init == "systemd":
        unit = "/etc/systemd/system/cascaded@.service"
        if _run("systemctl", "cat", "cascaded@%s.service" % user)[0]:
            _write(unit, SYSTEMD_USER_UNIT.replace("Type=simple", "Type=simple\nUser=%i").replace(
                "WantedBy=default.target", "WantedBy=multi-user.target").format(cmd=cmd), 0o644)
            _must("systemctl", "daemon-reload")
        _must("systemctl", "enable", "cascaded@%s.service" % user)
    elif init == "runit":
        sv, svc = _runit_dirs()
        d = os.path.join(sv, "cascaded-" + user)
        _write(os.path.join(d, "run"), RUNIT_RUN.format(user=user, home=shlex.quote(home), cmd=cmd), 0o755)
        _write(os.path.join(d, "log", "run"), RUNIT_LOG, 0o755)
        link = os.path.join(svc, "cascaded-" + user)
        if not os.path.lexists(link):
            os.symlink(d, link)
    elif init == "openrc":
        path = "/etc/init.d/cascaded.%s" % user
        _write(path, OPENRC.format(user=user, home=home, cmd_sh=_sh(cmd)), 0o755)
        _must("rc-update", "add", "cascaded.%s" % user, "default")
    elif init == "freebsd":
        _write("/usr/local/etc/rc.d/cascaded", FREEBSD.format(cmd_sh=_sh(cmd)), 0o555)
        _must("sysrc", "cascaded_enable=YES")
        _must("sysrc", "cascaded_user=%s" % user)
    elif init == "openbsd":
        _write("/etc/rc.d/cascaded", OPENBSD.format(cmd_sh=_sh(cmd)), 0o555)
        _must("rcctl", "set", "cascaded", "user", user)
        _must("rcctl", "enable", "cascaded")
    else:
        raise BootError("no supported init system found (systemd, runit, OpenRC, FreeBSD or OpenBSD rc)")
    return init


def disable_system(user, init=None):
    init = init or detect()
    if init == "systemd":
        _run("systemctl", "disable", "cascaded@%s.service" % user)
    elif init == "runit":
        sv, svc = _runit_dirs()
        link = os.path.join(svc, "cascaded-" + user)
        if os.path.islink(link):
            os.unlink(link)          # runsvdir stops the service; the definition stays for later
    elif init == "openrc":
        _run("rc-update", "del", "cascaded.%s" % user, "default")
    elif init == "freebsd":
        _run("sysrc", "cascaded_enable=NO")
    elif init == "openbsd":
        _run("rcctl", "disable", "cascaded")
    else:
        raise BootError("no supported init system found")
    return init


def status_system(user, init=None):
    init = init or detect()
    if init == "systemd":
        return _run("systemctl", "is-enabled", "cascaded@%s.service" % user)[1] == "enabled"
    if init == "runit":
        try:
            return os.path.lexists(os.path.join(_runit_dirs()[1], "cascaded-" + user))
        except BootError:
            return False
    if init == "openrc":
        return "cascaded.%s" % user in _run("rc-update", "show", "default")[1]
    if init == "freebsd":
        return _run("sysrc", "-n", "cascaded_enable")[1].upper() == "YES"
    if init == "openbsd":
        return _run("rcctl", "get", "cascaded", "status")[0] == 0
    return False


# ---------------------------------------------------------------- systemd without root
def running_unit():
    """(scope, unit) of the systemd service we run in, e.g. ("system", "cascaded@cascade.service"), or None."""
    try:
        with open("/proc/self/cgroup") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    for line in lines:
        path = line.split(":", 2)[-1]
        parts = [p for p in path.split("/") if p]
        units = [p for p in parts if p.endswith(".service")]
        if not units or units[-1].startswith("user@"):
            continue
        return ("user" if any(p.startswith("user@") for p in parts) else "system"), units[-1]
    return None


def _user_env():
    """Environment that reaches our own user manager even from a service without a login session."""
    env = dict(os.environ)
    run = env.get("XDG_RUNTIME_DIR") or "/run/user/%d" % os.getuid()
    env["XDG_RUNTIME_DIR"] = run
    if not env.get("DBUS_SESSION_BUS_ADDRESS") and os.path.exists(os.path.join(run, "bus")):
        env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=" + os.path.join(run, "bus")
    return env


def _user_bus():
    return os.path.exists(os.path.join(_user_env()["XDG_RUNTIME_DIR"], "systemd", "private")) or \
        os.path.exists(os.path.join(_user_env()["XDG_RUNTIME_DIR"], "bus"))


def _systemctl_user(*args, must=False):
    cmd = ("systemctl", "--user") + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=_user_env())
        rc, out = r.returncode, (r.stderr or r.stdout).strip()
    except (OSError, subprocess.SubprocessError) as e:
        rc, out = 1, str(e)
    if rc and must:
        raise BootError("%s failed: %s" % (" ".join(cmd), out or "exit %d" % rc))
    return rc, out


def enable_user_systemd(state):
    user = pwd.getpwuid(os.getuid()).pw_name
    if _systemctl_user("cat", "cascaded.service")[0]:
        # source checkout or a user install without the packaged unit: write our own
        unit = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                            "systemd", "user", "cascaded.service")
        _write(unit, SYSTEMD_USER_UNIT.format(cmd=daemon_command()), 0o644)
        _systemctl_user("daemon-reload")
    _systemctl_user("enable", "cascaded.service", must=True)
    if _run("loginctl", "show-user", user, "-p", "Linger")[1] != "Linger=yes":
        _must("loginctl", "enable-linger", user)
        state.set("linger_by_cascade", True)


def disable_user_systemd(state):
    user = pwd.getpwuid(os.getuid()).pw_name
    _systemctl_user("disable", "cascaded.service")
    if state.get("linger_by_cascade"):
        _run("loginctl", "disable-linger", user)     # only undo what we turned on ourselves
        state.set("linger_by_cascade", False)


# ---------------------------------------------------------------- what the service itself calls
def status(state=None):
    """Does Cascade start at boot right now?  True/False, or None when that cannot be told."""
    init = detect()
    if init == "systemd":
        unit = running_unit()
        if unit and unit[0] == "system":
            return _run("systemctl", "is-enabled", unit[1])[1] == "enabled"
        user = pwd.getpwuid(os.getuid()).pw_name
        if status_system(user, init):
            return True
        if not _user_bus():
            return None
        linger = _run("loginctl", "show-user", user, "-p", "Linger")[1] == "Linger=yes"
        return linger and _systemctl_user("is-enabled", "cascaded.service")[1] == "enabled"
    if init is None:
        return None
    return status_system(pwd.getpwuid(os.getuid()).pw_name, init)


def _needs_root(init, action, user, why=""):
    return BootError("%sthis needs root on %s: run  sudo cascade boot %s --user %s"
                     % (why + "; " if why else "", init, action, user))


def apply(enabled, state):
    """Called by the service (running as the user) when the setting changes.  Raises BootError."""
    init = detect()
    if init is None:
        raise BootError("no supported init system found (systemd, runit, OpenRC, FreeBSD or OpenBSD rc)")
    user = pwd.getpwuid(os.getuid()).pw_name
    action = "enable" if enabled else "disable"
    if status(state) is enabled:
        return init                              # already the way it should be (e.g. a headless system service)
    if os.getuid() == 0:
        (enable_system if enabled else disable_system)(user, init)
        return init
    if init == "systemd":
        unit = running_unit()
        if unit and unit[0] == "system":
            # we run as a system service (cascaded@USER.service or the admin's own unit): that unit decides,
            # and changing it needs root unless polkit allows it without asking
            rc, out = _run("systemctl", "--no-ask-password", action, unit[1])
            if rc:
                raise BootError("Cascade runs as the system service %s; to change this run  sudo systemctl %s %s"
                                % (unit[1], action, unit[1]))
            return init
        if _user_bus():
            (enable_user_systemd if enabled else disable_user_systemd)(state)
            return init
        # no user manager for this account (a system account without a login): use the system template
    pk = shutil.which("pkexec")
    if not pk or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise _needs_root(init, action, user)    # pkexec needs a desktop to ask for the password
    # pkexec shows the desktop's password prompt; it can take a while for the user to answer
    rc, out = _run(pk, *cascade_command(), "boot", action, "--user", user, timeout=300)
    if rc:
        why = "the password prompt was dismissed" if rc in (126, 127) else (out or "exit %d" % rc)
        raise _needs_root(init, action, user, why)
    return init
