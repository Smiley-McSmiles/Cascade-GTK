#!/bin/sh
# Cascade installer - POSIX sh, runs on any Linux distribution (and the BSDs).
#
#   sudo ./install.sh                    install to /usr/local for every user
#   ./install.sh --prefix ~/.local       install for yourself only, no root needed
#   sudo ./install.sh --install-deps     install Python, libtorrent, GTK 4 and libadwaita first
#   DESTDIR=/tmp/stage ./install.sh --prefix /usr --init systemd --no-post    stage files for a package
#   sudo ./install.sh --uninstall
#   ./install.sh --check                 only check this machine (Python, libtorrent, GTK/libadwaita)
set -eu

umask 022
PATH="${PATH:-/usr/bin:/bin}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/sbin"
export PATH

PREFIX=/usr/local
INIT=auto
DO_POST=1
UNINSTALL=0
DEPS=0
CHECK=0
SYSLINKS=1
WARNINGS=""
SRC=$(cd "$(dirname "$0")" && pwd)
DESTDIR=${DESTDIR:-}
APPID=io.github.smiley_mcsmiles.Cascade

usage() {
    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
    cat <<USAGE

Options:
  --prefix DIR        installation prefix (default: /usr/local; ~/.local installs for the current user)
  --init SYSTEM       service files to install: auto | systemd | openrc | runit | all-linux | none
                      (Cascade runs as your user and the app starts it by itself; these are for headless use)
  --no-post           do not refresh icon/desktop caches
  --uninstall         remove Cascade (your torrents and settings in ~/.config and ~/.local are kept)
  --install-deps      install the dependencies with the native package manager first
                      (apt, dnf, pacman, xbps, apk, zypper, pkg, pkg_add)
  --check             only report what is missing on this machine; install nothing
  --no-system-links   do not copy the launcher/icons into /usr/share when installing under another prefix
  -h, --help          show this help
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --prefix) PREFIX=$2; shift ;;
        --prefix=*) PREFIX=${1#*=} ;;
        --init) INIT=$2; shift ;;
        --init=*) INIT=${1#*=} ;;
        --no-post) DO_POST=0 ;;
        --uninstall) UNINSTALL=1 ;;
        --install-deps) DEPS=1 ;;
        --check) CHECK=1 ;;
        --no-system-links) SYSLINKS=0 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

say() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
warn() {
    printf 'warning: %s\n' "$*" >&2
    WARNINGS="$WARNINGS
  - $*"
}

case "$PREFIX" in "~"*) PREFIX="$HOME${PREFIX#\~}" ;; esac
OS=$(uname -s)
LIBDIR=$PREFIX/lib/cascade
SHAREDIR=$PREFIX/share
BINDIR=$PREFIX/bin
MANDIR=$SHAREDIR/man/man1
[ "$OS" = OpenBSD ] && MANDIR=$PREFIX/man/man1
D=$DESTDIR
SYSTEM_SHARE=${CASCADE_SYSTEM_SHARE:-/usr/share}
USER_INSTALL=0
case "$PREFIX" in "$HOME"/*) USER_INSTALL=1 ;; esac

if [ -z "$DESTDIR" ] && [ "$CHECK" -eq 0 ] && [ "$(id -u)" -ne 0 ]; then
    if [ "$DEPS" -eq 1 ]; then die "--install-deps needs root (sudo sh $0 --install-deps)"; fi
    mkdir -p "$PREFIX" 2>/dev/null || true
    [ -w "$PREFIX" ] || die "no write access to $PREFIX - run as root (sudo sh $0) or install for yourself: sh $0 --prefix ~/.local"
fi

pm_name() {
    for pm in apt-get dnf pacman xbps-install apk zypper pkg_add pkg; do
        if have "$pm"; then echo "$pm"; return; fi
    done
    echo none
}

deps_hint() {
    case "$(pm_name)" in
        apt-get) echo "apt install python3-libtorrent python3-gi gir1.2-gtk-4.0 gir1.2-adw-1" ;;
        dnf) echo "dnf install rb_libtorrent-python3 python3-gobject gtk4 libadwaita" ;;
        pacman) echo "pacman -S libtorrent-rasterbar python-gobject gtk4 libadwaita" ;;
        xbps-install) echo "xbps-install -S python3-libtorrent-rasterbar python3-gobject gtk4 libadwaita" ;;
        apk) echo "apk add py3-libtorrent-rasterbar py3-gobject3 gtk4.0 libadwaita" ;;
        zypper) echo "zypper install python3-libtorrent-rasterbar python3-gobject typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1" ;;
        pkg) echo "pkg install py311-libtorrent-rasterbar py311-gobject3 gtk4 libadwaita" ;;
        pkg_add) echo "pkg_add py3-libtorrent-rasterbar py3-gobject3 gtk4 libadwaita" ;;
        *) echo "install the Python bindings of libtorrent (pip install libtorrent), PyGObject, GTK 4 and libadwaita" ;;
    esac
}

install_deps() {
    say "Installing dependencies with $(pm_name)"
    case "$(pm_name)" in
        apt-get) apt-get update || warn "apt-get update failed"
                 REQ="python3 python3-libtorrent python3-gi gir1.2-gtk-4.0 gir1.2-adw-1"
                 OPT="desktop-file-utils libgtk-4-bin libnotify-bin" ;;
        dnf) REQ="python3 rb_libtorrent-python3 python3-gobject gtk4 libadwaita"; OPT="desktop-file-utils libnotify" ;;
        pacman) REQ="python libtorrent-rasterbar python-gobject gtk4 libadwaita"; OPT="desktop-file-utils libnotify" ;;
        xbps-install) REQ="python3 python3-libtorrent-rasterbar python3-gobject gtk4 libadwaita"
                      OPT="desktop-file-utils gtk-update-icon-cache libnotify" ;;
        apk) REQ="python3 py3-libtorrent-rasterbar py3-gobject3 gtk4.0 libadwaita"; OPT="desktop-file-utils libnotify" ;;
        zypper) REQ="python3 python3-libtorrent-rasterbar python3-gobject typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1"
                OPT="desktop-file-utils libnotify-tools" ;;
        pkg) REQ="python3 py311-libtorrent-rasterbar py311-gobject3 gtk4 libadwaita"; OPT="desktop-file-utils" ;;
        pkg_add) REQ="python3 py3-libtorrent-rasterbar py3-gobject3 gtk4 libadwaita"; OPT="" ;;
        *) die "no supported package manager found; $(deps_hint)" ;;
    esac
    # shellcheck disable=SC2086
    install_pkgs $REQ || warn "could not install all required packages ($REQ)"
    for p in $OPT; do install_pkgs "$p" >/dev/null 2>&1 || warn "optional package not installed: $p"; done
}

install_pkgs() {
    case "$(pm_name)" in
        apt-get) apt-get install -y "$@" ;;
        dnf) dnf install -y "$@" ;;
        pacman) pacman -S --needed --noconfirm "$@" ;;
        xbps-install) xbps-install -y "$@" ;;
        apk) apk add "$@" ;;
        zypper) zypper --non-interactive install "$@" ;;
        pkg_add) pkg_add "$@" ;;
        pkg) pkg install -y "$@" ;;
        *) return 1 ;;
    esac
}

libtorrent_pkg() {
    case "$(pm_name)" in
        apt-get) echo python3-libtorrent ;;
        dnf) echo rb_libtorrent-python3 ;;
        pacman) echo libtorrent-rasterbar ;;
        xbps-install) echo python3-libtorrent-rasterbar ;;
        apk) echo py3-libtorrent-rasterbar ;;
        zypper) echo python3-libtorrent-rasterbar ;;
        pkg) echo py311-libtorrent-rasterbar ;;
        pkg_add) echo py3-libtorrent-rasterbar ;;
        *) return 1 ;;
    esac
}

# Cascade cannot start without libtorrent: an interactive install offers to fetch it right away
offer_libtorrent() {
    [ -z "$DESTDIR" ] && [ "$(id -u)" -eq 0 ] && [ -t 0 ] && [ -t 1 ] || return 1
    pkg=$(libtorrent_pkg) || return 1
    printf '\nThe Python bindings of libtorrent are missing and Cascade cannot run without them.\n'
    printf 'Install them now with %s (%s)? [Y/n] ' "$(pm_name)" "$pkg"
    ans=""
    read -r ans || ans=n
    case "$ans" in
        ""|y|Y|yes|Yes|YES) ;;
        *) return 1 ;;
    esac
    [ "$(pm_name)" = apt-get ] && { apt-get update >/dev/null 2>&1 || true; }
    install_pkgs "$pkg" || return 1
    PY=$(find_python) && "$PY" -c 'import libtorrent' 2>/dev/null
}

find_python() {   # the first Python 3.9+ that can import libtorrent (falls back to any Python 3.9+)
    first=""
    for c in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
        have "$c" || continue
        "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null || continue
        [ -n "$first" ] || first=$(command -v "$c")
        if "$c" -c 'import libtorrent' 2>/dev/null; then command -v "$c"; return 0; fi
    done
    [ -n "$first" ] && { echo "$first"; return 0; }
    return 1
}

preflight() {
    PY=$(find_python) || die "Python 3.9 or newer is required. $(deps_hint)"
    say "Python: $PY ($("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])'))"
    if LTV=$("$PY" -c 'import libtorrent; print(libtorrent.__version__)' 2>/dev/null); then
        case "$LTV" in
            2.*) say "libtorrent: $LTV" ;;
            *) warn "libtorrent $LTV is old: Cascade needs libtorrent 2.0 or newer" ;;
        esac
    elif [ "$CHECK" -eq 0 ] && offer_libtorrent; then
        say "libtorrent: $("$PY" -c 'import libtorrent; print(libtorrent.__version__)')"
    else
        warn "the Python bindings of libtorrent are missing - Cascade cannot run without them. Install with: $(deps_hint)   (or rerun with --install-deps, or: pip install libtorrent)"
    fi
    if "$PY" -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')" 2>/dev/null; then
        if "$PY" -c "from gi.repository import Adw; import sys; sys.exit(0 if (Adw.MAJOR_VERSION, Adw.MINOR_VERSION) >= (1, 5) else 1)" 2>/dev/null; then
            say "GTK app: OK (PyGObject, GTK 4, libadwaita >= 1.5)"
        else
            warn "libadwaita is older than 1.5 - the GTK app needs 1.5+. The command line, service and web interface work regardless."
        fi
    else
        warn "GTK app dependencies missing (PyGObject, GTK 4, libadwaita). The command line, service and web interface still work. Install with: $(deps_hint)"
    fi
}

wrapper() {  # wrapper <path> <cascade args...>
    out=$1; shift
    mkdir -p "$(dirname "$out")"
    cat > "$out" <<WRAP
#!/bin/sh
# generated by Cascade install.sh
# Desktop launchers can run us with a different PATH than a terminal (pyenv, conda, ~/.local/bin ...), so pick a
# Python by what it can import instead of by name.
NEED_GI=0
case "$*" in gui*) NEED_GI=1 ;; esac
usable() {
    [ -x "\$1" ] || return 1
    "\$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null || return 1
    "\$1" -c 'import libtorrent' 2>/dev/null || return 1
    [ "\$NEED_GI" -eq 0 ] && return 0
    "\$1" -c "import gi; gi.require_version('Gtk','4.0'); gi.require_version('Adw','1')" 2>/dev/null
}
PY=\${CASCADE_PYTHON:-}
if [ -z "\$PY" ]; then
    FIRST=
    for c in "${PY:-}" /usr/bin/python3 /usr/local/bin/python3 /bin/python3 /usr/bin/python3.13 /usr/bin/python3.12 \\
             /usr/bin/python3.11 /usr/bin/python3.10 /usr/bin/python3.9 python3 python3.13 python3.12 python3.11 \\
             python3.10 python3.9 python; do
        [ -n "\$c" ] || continue
        case "\$c" in /*) ;; *) c=\$(command -v "\$c" 2>/dev/null) || continue ;; esac
        [ -x "\$c" ] || continue
        [ -n "\$FIRST" ] || FIRST=\$c
        if usable "\$c"; then PY=\$c; break; fi
    done
    PY=\${PY:-\$FIRST}
fi
[ -n "\$PY" ] || { echo "cascade: no Python 3 interpreter found (install python3)" >&2; exit 127; }
# Never write bytecode into the (root-owned) install tree; caches are validated by mtime and size only
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$LIBDIR\${PYTHONPATH:+:\$PYTHONPATH}"
export CASCADE_SHARE_DIR="$SHAREDIR/cascade"
exec "\$PY" -m cascade $* "\$@"
WRAP
    chmod 755 "$out"
}

render() {  # render <template> <dest> <mode>
    mkdir -p "$(dirname "$D$2")"
    sed "s|@BINDIR@|$BINDIR|g" "$1" > "$D$2"
    chmod "$3" "$D$2"
}

install_init() {
    case "$1" in
        systemd)
            if [ "$USER_INSTALL" -eq 1 ]; then
                render "$SRC/data/init/cascaded.service" "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/cascaded.service" 644
            else
                udir=/usr/lib/systemd; [ -z "$DESTDIR" ] && [ "$PREFIX" != /usr ] && udir=/etc/systemd
                render "$SRC/data/init/cascaded.service" "$udir/user/cascaded.service" 644
                render "$SRC/data/init/cascaded@.service" "$udir/system/cascaded@.service" 644
            fi ;;
        openrc) if [ "$USER_INSTALL" -eq 0 ]; then render "$SRC/data/init/cascaded.openrc" /etc/init.d/cascaded 755; fi ;;
        runit)
            render "$SRC/data/init/runit/run" "$SHAREDIR/cascade/runit/run" 755
            render "$SRC/data/init/runit/log/run" "$SHAREDIR/cascade/runit/log/run" 755 ;;
        none) ;;
        *) die "unknown init system: $1" ;;
    esac
}

detect_init() {
    if [ -d /run/systemd/system ]; then echo systemd
    elif [ "$(cat /proc/1/comm 2>/dev/null)" = runit ] || [ -d /run/runit ]; then echo runit
    elif [ -d /run/openrc ] || [ -x /sbin/openrc-run ]; then echo openrc
    else echo none
    fi
}

refresh_caches() {
    for theme in "$SHAREDIR/icons/hicolor" "$SYSTEM_SHARE/icons/hicolor"; do
        [ -d "$theme" ] || continue
        for t in gtk4-update-icon-cache gtk-update-icon-cache; do
            if have "$t"; then "$t" -q -f -t "$theme" 2>/dev/null && break; fi
        done
    done
    for d in "$SHAREDIR/applications" "$SYSTEM_SHARE/applications"; do
        if have update-desktop-database && [ -d "$d" ]; then update-desktop-database -q "$d" 2>/dev/null || true; fi
    done
    return 0
}

# Desktops look up launchers in $XDG_DATA_DIRS; sessions that set it themselves often leave /usr/local/share out,
# and the launcher and the magnet-link handler then silently never show up.  A plain copy in /usr/share fixes that.
link_system_dirs() {
    [ "$SYSLINKS" -eq 1 ] && [ "$USER_INSTALL" -eq 0 ] && [ "$OS" = Linux ] && [ "$SHAREDIR" != "$SYSTEM_SHARE" ] || return 0
    [ -d "$SYSTEM_SHARE" ] && [ -w "$SYSTEM_SHARE" ] || return 0
    for rel in "applications/$APPID.desktop" "metainfo/$APPID.metainfo.xml"; do
        mkdir -p "$(dirname "$SYSTEM_SHARE/$rel")" && cp "$SHAREDIR/$rel" "$SYSTEM_SHARE/$rel" 2>/dev/null || true
    done
    for f in "$SHAREDIR"/icons/hicolor/*/apps/"$APPID"*; do
        [ -f "$f" ] || continue
        rel=${f#"$SHAREDIR"/}
        mkdir -p "$(dirname "$SYSTEM_SHARE/$rel")" && cp "$f" "$SYSTEM_SHARE/$rel" 2>/dev/null || true
    done
    say "Copied the launcher and icons into $SYSTEM_SHARE (visible to every desktop session)"
}

uninstall() {
    say "Removing Cascade from $PREFIX"
    rm -rf "$D$LIBDIR" "$D$SHAREDIR/cascade"
    rm -f "$D$BINDIR/cascade" "$D$BINDIR/cascaded" "$D$BINDIR/cascade-gtk"
    rm -f "$D$SHAREDIR/applications/$APPID.desktop" "$D$SHAREDIR/metainfo/$APPID.metainfo.xml" \
          "$D$SHAREDIR/pixmaps/$APPID.svg" "$D$MANDIR/cascade.1" "$D$SHAREDIR/bash-completion/completions/cascade" \
          "$D$SHAREDIR/zsh/site-functions/_cascade" "$D$SHAREDIR/fish/vendor_completions.d/cascade.fish"
    rm -f "$D$SHAREDIR"/icons/hicolor/*/apps/"$APPID"*
    if [ -z "$DESTDIR" ] && [ "$USER_INSTALL" -eq 0 ] && [ "$SHAREDIR" != "$SYSTEM_SHARE" ]; then
        rm -f "$SYSTEM_SHARE/applications/$APPID.desktop" "$SYSTEM_SHARE/metainfo/$APPID.metainfo.xml" \
              "$SYSTEM_SHARE"/icons/hicolor/*/apps/"$APPID"*
    fi
    rm -f "$D/etc/systemd/user/cascaded.service" "$D/etc/systemd/system/cascaded@.service" \
          "$D/usr/lib/systemd/user/cascaded.service" "$D/usr/lib/systemd/system/cascaded@.service" "$D/etc/init.d/cascaded"
    [ "$USER_INSTALL" -eq 1 ] && rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/cascaded.service"
    [ -z "$DESTDIR" ] && refresh_caches
    say "Removed. Your torrents and settings are kept in ~/.config/cascade and ~/.local/share/cascade."
    exit 0
}

if [ "$UNINSTALL" -eq 1 ]; then uninstall; fi
if [ "$CHECK" -eq 1 ]; then
    say "Checking this machine ($OS)"
    preflight
    if [ -z "$WARNINGS" ]; then say "Everything looks good."; else printf '\nTo fix:%s\n' "$WARNINGS"; fi
    exit 0
fi
if [ "$DEPS" -eq 1 ]; then install_deps; fi
[ -d "$SRC/cascade" ] && [ -d "$SRC/data" ] || die "run install.sh from the extracted Cascade directory (cascade/ and data/ must be next to it)"
PY=""
if [ -z "$DESTDIR" ]; then preflight; fi

say "Installing Cascade to $D$PREFIX"
rm -rf "$D$LIBDIR"
mkdir -p "$D$LIBDIR"
cp -R "$SRC/cascade" "$D$LIBDIR/cascade"
find "$D$LIBDIR" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

wrapper "$D$BINDIR/cascade"
wrapper "$D$BINDIR/cascaded" daemon
wrapper "$D$BINDIR/cascade-gtk" gui

mkdir -p "$D$SHAREDIR/applications" "$D$SHAREDIR/metainfo" "$D$MANDIR" "$D$SHAREDIR/pixmaps" "$D$SHAREDIR/cascade"
sed "s|^Exec=.*|Exec=$BINDIR/cascade-gtk %U|" "$SRC/data/$APPID.desktop" > "$D$SHAREDIR/applications/$APPID.desktop"
cp "$SRC/data/$APPID.metainfo.xml" "$D$SHAREDIR/metainfo/"
for d in scalable symbolic 48x48 64x64 128x128 256x256; do
    mkdir -p "$D$SHAREDIR/icons/hicolor/$d/apps"
    cp "$SRC/data/icons/hicolor/$d/apps/"* "$D$SHAREDIR/icons/hicolor/$d/apps/"
done
cp "$SRC/data/icons/hicolor/scalable/apps/$APPID.svg" "$D$SHAREDIR/pixmaps/"
# a private, cache-free copy: the app falls back to it when the system icon cache is stale
cp -R "$SRC/data/icons" "$D$SHAREDIR/cascade/"
cp "$SRC/data/cascade.1" "$D$MANDIR/cascade.1"
mkdir -p "$D$SHAREDIR/bash-completion/completions" "$D$SHAREDIR/zsh/site-functions" "$D$SHAREDIR/fish/vendor_completions.d"
cp "$SRC/data/completions/cascade.bash" "$D$SHAREDIR/bash-completion/completions/cascade"
cp "$SRC/data/completions/_cascade" "$D$SHAREDIR/zsh/site-functions/_cascade"
cp "$SRC/data/completions/cascade.fish" "$D$SHAREDIR/fish/vendor_completions.d/cascade.fish"

# cp keeps the source modes: a tarball unpacked with a strict umask must still install world-readable files
chmod -R go+rX "$D$LIBDIR" "$D$SHAREDIR/cascade"
find "$D$SHAREDIR" \( -name "$APPID*" -o -name 'cascade.1' -o -name 'cascade' -o -name '_cascade' -o -name 'cascade.fish' \) \
    -type f -exec chmod 644 {} + 2>/dev/null || true
chmod 755 "$D$BINDIR/cascade" "$D$BINDIR/cascaded" "$D$BINDIR/cascade-gtk"
if [ -z "$DESTDIR" ] && [ -n "$PY" ]; then
    "$PY" -m compileall -q --invalidation-mode checked-hash "$D$LIBDIR" >/dev/null 2>&1 || true
fi

if [ "$INIT" = auto ]; then INIT=$(detect_init); fi
case "$INIT" in
    all-linux) install_init systemd; install_init openrc; install_init runit ;;
    *) install_init "$INIT" ;;
esac
if [ "$INIT" != none ]; then say "Service files installed for: $INIT"; fi

if [ -z "$DESTDIR" ] && [ "$DO_POST" -eq 1 ]; then
    link_system_dirs
    refresh_caches
    if have desktop-file-validate; then
        desktop-file-validate "$SHAREDIR/applications/$APPID.desktop" >&2 2>&1 || warn "the launcher failed desktop-file-validate"
    fi
    case ":${XDG_DATA_DIRS:-/usr/local/share:/usr/share}:" in
        *":$SHAREDIR:"*) ;;
        *) [ "$USER_INSTALL" -eq 1 ] || warn "$SHAREDIR is not in XDG_DATA_DIRS; if Cascade is missing from your menu, reinstall with --prefix /usr" ;;
    esac
    case ":$PATH:" in
        *":$BINDIR:"*) ;;
        *) warn "$BINDIR is not in your PATH; add it to use the cascade command" ;;
    esac
fi

if [ -n "$WARNINGS" ]; then
    printf '\nCascade is installed, with warnings:%s\n' "$WARNINGS"
else
    printf '\nCascade is installed.\n'
fi
cat <<DONE

  App:  cascade-gtk (or "Cascade" in your application menu)     Command line:  cascade --help
  Add a torrent:  cascade add 'magnet:?xt=...'     Web interface:  cascade webui on
  Headless:  systemctl --user enable --now cascaded    (or at boot: sudo systemctl enable --now cascaded@USER)
DONE
