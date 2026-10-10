#!/usr/bin/env bash
# ==============================================================================
# Cascade Multi-Platform Packaging Script (the same options as VPNMan's package.sh)
# Builds packages for Debian/Ubuntu (.deb), Fedora/RHEL/openSUSE (.rpm), Arch Linux (.pkg.tar.xz), Void Linux
# (.xbps), a portable source tarball (.tar.gz), and recipes for Alpine (APKBUILD) and OpenBSD (port), plus
# dist/SHA256SUMS.  No Flatpak or AppImage: libtorrent's Python bindings come from the distribution, and the
# network-interface kill switch needs the host's real network devices - the tarball's install.sh is the portable
# route (it also installs for one user without root: ./install.sh --prefix ~/.local).
#
#   ./package.sh                     build everything this machine can build (= --all); a missing tool only skips
#                                    that package, the rest still build, and the summary says why
#   ./package.sh --deb --arch        selected packages (the plain names work too: ./package.sh deb arch)
#   ./package.sh --container --rpm   build inside a Fedora container (podman/docker) - works from any distro
#   ./package.sh --help
# ==============================================================================
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$ROOT"
VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' cascade/__init__.py)
NAME=cascade
APPID=io.github.smiley_mcsmiles.Cascade
DIST=$ROOT/dist
BUILD=$ROOT/build
SRCNAME=$NAME-$VERSION
URL=https://github.com/Smiley-McSmiles/Cascade-GTK
DESC="BitTorrent client with a GTK4/libadwaita app, a command line and a web interface"

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m[SUCCESS]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
have() { command -v "$1" >/dev/null 2>&1; }
# A target that cannot be built on this machine (tool missing) exits 3: "skipped", not "failed".
skip() { warn "$*"; exit 3; }

# Files that make up the program, shared by every package type.
SRC_ITEMS=(cascade data packaging tools install.sh package.sh README.md LICENSE tests)

stage() {  # stage <destdir> <init>
    local dest=$1 init=$2
    rm -rf "$dest"
    mkdir -p "$dest"
    DESTDIR=$dest "$ROOT/install.sh" --prefix /usr --init "$init" --no-post >/dev/null
    find "$dest" -name '*.pyc' -delete
    find "$dest" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
}

# Scriptlet bodies shared by deb/rpm/arch: refresh the icon cache and the desktop database (which registers the
# magnet: and .torrent handler), and drop stale compiled caches from an older version.
POST_COMMON='find /usr/lib/cascade -name __pycache__ -type d -exec rm -rf {} + >/dev/null 2>&1 || true
# a copy installed by install.sh into /usr/local comes first in PATH and would keep running the old version
if [ -e /usr/local/lib/cascade/cascade/__init__.py ]; then
    rm -rf /usr/local/lib/cascade /usr/local/share/cascade
    rm -f /usr/local/bin/cascade /usr/local/bin/cascaded /usr/local/bin/cascade-gtk
fi
# GTK only validates icon-theme.cache against the mtime of hicolor/ itself, so new icons stay invisible until rebuilt
for d in /usr/share/icons/hicolor /usr/local/share/icons/hicolor; do
    [ -d "$d" ] || continue
    for t in gtk4-update-icon-cache gtk-update-icon-cache; do
        if command -v $t >/dev/null 2>&1; then $t -q -f -t "$d" >/dev/null 2>&1 && break; fi
    done
done
if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database -q /usr/share/applications >/dev/null 2>&1 || :; fi
if [ -d /run/systemd/system ]; then systemctl daemon-reload >/dev/null 2>&1 || :; fi'

POSTRM_COMMON='for d in /usr/share/icons/hicolor /usr/local/share/icons/hicolor; do
    [ -d "$d" ] || continue
    for t in gtk4-update-icon-cache gtk-update-icon-cache; do
        if command -v $t >/dev/null 2>&1; then $t -q -f -t "$d" >/dev/null 2>&1 && break; fi
    done
done
if command -v update-desktop-database >/dev/null 2>&1; then update-desktop-database -q /usr/share/applications >/dev/null 2>&1 || :; fi'

build_tar() {
    [ -n "${PKG_TAR_DONE:-}" ] && [ -f "$DIST/$SRCNAME.tar.gz" ] && return 0
    log "tarball"
    mkdir -p "$DIST" "$BUILD"
    local t=$BUILD/$SRCNAME
    rm -rf "$t"
    mkdir -p "$t"
    for i in "${SRC_ITEMS[@]}"; do [ -e "$i" ] && cp -R "$i" "$t/"; done
    find "$t" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
    # normalise modes so the archive installs correctly whatever the packager's umask was
    chmod -R u+rwX,go+rX,go-w "$t"
    chmod 755 "$t/install.sh" "$t/package.sh"
    tar -C "$BUILD" -czf "$DIST/$SRCNAME.tar.gz" "$SRCNAME"
    log "  -> dist/$SRCNAME.tar.gz"
}

build_deb() {
    have python3 || skip "python3 not found, cannot build the deb"
    log "deb"
    local root=$BUILD/deb/${NAME}_$VERSION
    stage "$root" systemd
    mkdir -p "$root/DEBIAN" "$root/usr/share/doc/$NAME"
    cp LICENSE "$root/usr/share/doc/$NAME/copyright"
    gzip -9n "$root/usr/share/man/man1/cascade.1"
    local size
    size=$(du -sk "$root" | cut -f1)
    cat > "$root/DEBIAN/control" <<CTL
Package: $NAME
Version: $VERSION
Section: net
Priority: optional
Architecture: all
Installed-Size: $size
Depends: python3 (>= 3.9), python3-libtorrent (>= 2.0), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.5)
Recommends: libnotify-bin, desktop-file-utils
Maintainer: Cascade contributors <noreply@example.invalid>
Homepage: $URL
Description: $DESC
 Cascade is a libtorrent client for GNOME: expanding torrent cards instead of
 pop-up windows, a weekly speed schedule, network interface binding with a
 kill switch for VPNs, VPN port forwarding (NAT-PMP), queueing, seeding
 limits, labels, blocklists and torrent creation. The command line and the
 one-click web interface share the same torrents and settings.
CTL
    printf '#!/bin/sh\nset -e\nif [ "$1" = configure ]; then\n%s\nfi\nexit 0\n' "$POST_COMMON" > "$root/DEBIAN/postinst"
    printf '#!/bin/sh\nset -e\nif [ "$1" = remove ] || [ "$1" = purge ]; then\n%s\nfi\nexit 0\n' "$POSTRM_COMMON" > "$root/DEBIAN/postrm"
    chmod 755 "$root/DEBIAN/postinst" "$root/DEBIAN/postrm"
    mkdir -p "$DIST"
    # works on any distribution: packaging/mkdeb.py writes the ar/tar structure itself
    python3 "$ROOT/packaging/mkdeb.py" "$root" "$DIST/${NAME}_${VERSION}_all.deb"
    log "  -> dist/${NAME}_${VERSION}_all.deb"
}

recipe_rpm() {
    mkdir -p "$DIST/recipes"
    cat > "$DIST/recipes/$NAME.spec" <<SPEC
# Do not clamp file mtimes to the changelog date: with identical mtimes and same-size edits Python keeps trusting
# stale __pycache__ files and the upgraded program silently keeps running the old code.
%global clamp_mtime_to_source_date_epoch 0
%global source_date_epoch_from_changelog 0

Name:           $NAME
Version:        $VERSION
Release:        1%{?dist}
Summary:        $DESC
License:        MIT
URL:            $URL
Source0:        $SRCNAME.tar.gz
BuildArch:      noarch
Requires:       python3 >= 3.9, python3-libtorrent >= 2.0, python3-gobject, gtk4, libadwaita >= 1.5
Recommends:     libnotify, desktop-file-utils

%description
Cascade is a libtorrent client for GNOME with expanding torrent cards, a weekly speed schedule, network
interface binding with a kill switch, VPN port forwarding (NAT-PMP), queueing, seeding limits, labels,
blocklists and torrent creation. The command line and the web interface share the same torrents and settings.

%prep
%autosetup -n $SRCNAME

%install
DESTDIR=%{buildroot} ./install.sh --prefix /usr --init systemd --no-post

%post
$(printf '%s' "$POST_COMMON")

%postun
$(printf '%s' "$POSTRM_COMMON")

%files
%license LICENSE
/usr/bin/cascade
/usr/bin/cascaded
/usr/bin/cascade-gtk
/usr/lib/cascade
/usr/lib/systemd/user/cascaded.service
/usr/lib/systemd/system/cascaded@.service
/usr/share/cascade
/usr/share/applications/$APPID.desktop
/usr/share/metainfo/$APPID.metainfo.xml
/usr/share/icons/hicolor/*/apps/$APPID*
/usr/share/pixmaps/$APPID.svg
/usr/share/man/man1/cascade.1*
/usr/share/bash-completion/completions/cascade
/usr/share/zsh/site-functions/_cascade
/usr/share/fish/vendor_completions.d/cascade.fish

%changelog
* Sat Oct 10 2026 Cascade contributors <noreply@example.invalid> - $VERSION-1
- Packaged Cascade $VERSION
SPEC
}

build_rpm() {
    recipe_rpm
    have rpmbuild || skip "rpmbuild not found (dnf/apt install rpm-build); spec written to dist/recipes/$NAME.spec"
    log "rpm"
    build_tar
    local top=$BUILD/rpm
    rm -rf "$top"
    mkdir -p "$top"/{SOURCES,SPECS,BUILD,RPMS,SRPMS}
    cp "$DIST/$SRCNAME.tar.gz" "$top/SOURCES/"
    cp "$DIST/recipes/$NAME.spec" "$top/SPECS/"
    if ! rpmbuild --define "_topdir $top" -bb "$top/SPECS/$NAME.spec" >"$top/rpmbuild.log" 2>&1; then
        tail -n 40 "$top/rpmbuild.log" >&2
        echo "rpmbuild failed (full log: $top/rpmbuild.log)" >&2
        exit 1
    fi
    cp "$top"/RPMS/*/*.rpm "$DIST/"
    log "  -> dist/$(cd "$DIST" && ls -- *"$VERSION"*.rpm)"
}

recipe_arch() {
    mkdir -p "$DIST/recipes/arch"
    cat > "$DIST/recipes/arch/PKGBUILD" <<PKG
# Maintainer: Cascade contributors
pkgname=$NAME
pkgver=$VERSION
pkgrel=1
pkgdesc="$DESC"
arch=('any')
url="$URL"
license=('MIT')
depends=('python' 'libtorrent-rasterbar' 'python-gobject' 'gtk4' 'libadwaita')
optdepends=('libnotify: notifications while the window is closed')
source=("\$pkgname-\$pkgver.tar.gz")
sha256sums=('SKIP')

package() {
  cd "\$srcdir/\$pkgname-\$pkgver"
  DESTDIR="\$pkgdir" ./install.sh --prefix /usr --init systemd --no-post
  install -Dm644 LICENSE "\$pkgdir/usr/share/licenses/\$pkgname/LICENSE"
}
PKG
}

build_arch() {
    # packaging/mkarch.py writes the package itself, so this works on ANY distribution: no makepkg, no pacman.
    # The PKGBUILD is still generated for people who build it the Arch way or submit it to the AUR.
    recipe_arch
    have python3 || skip "python3 not found, cannot build the Arch package"
    log "arch"
    local root=$BUILD/arch/${NAME}-$VERSION
    stage "$root" systemd
    mkdir -p "$BUILD/arch" "$DIST" "$root/usr/share/licenses/$NAME"
    cp LICENSE "$root/usr/share/licenses/$NAME/LICENSE"
    printf 'post_install() {\n%s\n}\npost_upgrade() { post_install; }\npost_remove() {\n%s\n}\n' \
        "$POST_COMMON" "$POSTRM_COMMON" > "$BUILD/arch/cascade.install"
    python3 "$ROOT/packaging/mkarch.py" "$root" "$DIST/$NAME-$VERSION-1-any.pkg.tar.xz" \
        --name "$NAME" --version "$VERSION" --desc "$DESC" --url "$URL" --packager "Cascade contributors" \
        --depend python --depend libtorrent-rasterbar --depend python-gobject --depend gtk4 --depend libadwaita \
        --optdepend 'libnotify: notifications while the window is closed' \
        --install "$BUILD/arch/cascade.install" >/dev/null
    log "  -> dist/$NAME-$VERSION-1-any.pkg.tar.xz   (install with: sudo pacman -U ...)"
}

recipe_void() {
    mkdir -p "$DIST/recipes/void"
    cat > "$DIST/recipes/void/template" <<TPL
# Template file for 'cascade' (copy to srcpkgs/cascade/template in void-packages)
pkgname=$NAME
version=$VERSION
revision=1
depends="python3 python3-libtorrent-rasterbar python3-gobject gtk4 libadwaita"
short_desc="$DESC"
maintainer="Cascade contributors <noreply@example.invalid>"
license="MIT"
homepage="$URL"
distfiles="\${homepage}/archive/refs/tags/v\${version}.tar.gz"
checksum=SKIP
do_build() { :; }
do_install() {
	DESTDIR=\${DESTDIR} ./install.sh --prefix /usr --init runit --no-post
	vlicense LICENSE
}
TPL
}

build_void() {
    recipe_void
    have xbps-create || skip "xbps-create not found (it ships with Void's xbps); template written to dist/recipes/void/template - copy it to void-packages/srcpkgs/$NAME/"
    log "void"
    local root=$BUILD/void/${NAME}-$VERSION
    stage "$root" runit
    mkdir -p "$DIST"
    (cd "$DIST" && xbps-create -A noarch -n "${NAME}-${VERSION}_1" -s "$DESC" \
        -D "python3>=3.9 python3-libtorrent-rasterbar python3-gobject gtk4 libadwaita" -H "$URL" -l MIT \
        -m "Cascade contributors <noreply@example.invalid>" -t "net" "$root" >/dev/null)
    log "  -> dist/${NAME}-${VERSION}_1.noarch.xbps   (install: sudo xbps-install -R dist $NAME)"
}

build_alpine() {
    recipe_alpine
    skip "an Alpine .apk can only be built with abuild on Alpine; APKBUILD written to dist/recipes/alpine/ (run: abuild -r there)"
}

build_openbsd() {
    recipe_openbsd
    skip "an OpenBSD package is built from a port; the port was written to dist/recipes/openbsd/ (see README)"
}

recipe_alpine() {
    mkdir -p "$DIST/recipes/alpine"
    cat > "$DIST/recipes/alpine/APKBUILD" <<APK
# Maintainer: Cascade contributors <noreply@example.invalid>
pkgname=$NAME
pkgver=$VERSION
pkgrel=0
pkgdesc="$DESC"
url="$URL"
arch="noarch"
license="MIT"
depends="python3 py3-libtorrent-rasterbar py3-gobject3 gtk4.0 libadwaita"
source="\$pkgname-\$pkgver.tar.gz"
builddir="\$srcdir/\$pkgname-\$pkgver"
options="!check"

package() {
	DESTDIR="\$pkgdir" ./install.sh --prefix /usr --init openrc --no-post
	install -Dm644 LICENSE "\$pkgdir"/usr/share/licenses/\$pkgname/LICENSE
}
APK
}

recipe_openbsd() {
    mkdir -p "$DIST/recipes/openbsd/pkg"
    cat > "$DIST/recipes/openbsd/Makefile" <<MK
COMMENT =	$DESC
DISTNAME =	$NAME-$VERSION
CATEGORIES =	net
HOMEPAGE =	$URL
MAINTAINER =	Cascade contributors <noreply@example.invalid>
# MIT
PERMIT_PACKAGE =	Yes
MODULES =	lang/python
MODPY_PYBUILD =	No
RUN_DEPENDS =	net/libtorrent-rasterbar,-python \\
		x11/gnome/libadwaita \\
		x11/py-gobject3
NO_BUILD =	Yes
NO_TEST =	Yes

do-install:
	cd \${WRKSRC} && DESTDIR=\${DESTDIR} ./install.sh --prefix \${PREFIX} --init none --no-post

.include <bsd.port.mk>
MK
    echo "$DESC" > "$DIST/recipes/openbsd/pkg/DESCR"
}

build_recipes() { recipe_rpm; recipe_arch; recipe_void; recipe_alpine; recipe_openbsd; log "recipes -> dist/recipes/"; }

show_help() {
    cat <<HELP
Cascade Multi-Platform Packaging Tool (version $VERSION)
Usage: ./package.sh [options]

Options:
  --all         Build every package this machine can build (the default)
  --tar         Portable source tarball (.tar.gz) - unpack and run: sudo ./install.sh (or --prefix ~/.local)
  --deb         Debian / Ubuntu / Mint package (.deb)
  --rpm         Fedora / RHEL / openSUSE package (.rpm) - needs rpmbuild (or use --container)
  --arch        Arch Linux package (.pkg.tar.xz) and PKGBUILD - needs only python3
  --void        Void Linux package (.xbps) and template - needs xbps-create
  --alpine      Alpine APKBUILD (build it with abuild on Alpine)
  --openbsd     OpenBSD port recipe
  --recipes     Only the recipes (spec, PKGBUILD, template, APKBUILD, port) in dist/recipes/
  --container   Build inside a Fedora container (podman/docker), e.g. --container --rpm
  --clean       Remove dist/ and build/
  --help, -h    Show this message

The plain names work too (./package.sh deb arch). Every build also writes dist/SHA256SUMS.
Not built: Flatpak and AppImage - libtorrent's Python bindings come from your distribution and the interface kill
switch needs the host's network devices; the tarball with install.sh is the portable way.
Output: $DIST/
HELP
}

# dist/SHA256SUMS for this version's release files (old versions left in dist/ are not listed)
checksums() {
    local files=() f
    for f in "$DIST"/*"$VERSION"*; do
        case "$f" in
            *.tar.gz|*.deb|*.rpm|*.pkg.tar.*|*.xbps) [ -f "$f" ] && files+=("$(basename "$f")") ;;
        esac
    done
    [ ${#files[@]} -gt 0 ] || return 0
    if have sha256sum; then
        (cd "$DIST" && sha256sum "${files[@]}") > "$DIST/SHA256SUMS.tmp"
    elif have shasum; then
        (cd "$DIST" && shasum -a 256 "${files[@]}") > "$DIST/SHA256SUMS.tmp"
    elif have sha256; then                     # BSD
        (cd "$DIST" && sha256 -r "${files[@]}" | awk '{print $1 "  " $2}') > "$DIST/SHA256SUMS.tmp"
    else
        warn "no sha256sum, shasum or sha256: dist/SHA256SUMS not written"
        return 0
    fi
    mv "$DIST/SHA256SUMS.tmp" "$DIST/SHA256SUMS"
    log "checksums -> dist/SHA256SUMS (${#files[@]} files)"
}

summary() {
    checksums
    local f
    echo
    ok "Packaging complete! Artifacts in $DIST/:"
    for f in "$DIST"/*"$VERSION"* "$DIST/SHA256SUMS"; do
        [ -f "$f" ] && printf '    %s\n' "$(basename "$f")"
    done
    [ -d "$DIST/recipes" ] && printf '    recipes/  (%s)\n' "$(cd "$DIST/recipes" && ls | tr '\n' ' ' | sed 's/ $//')"
    return 0
}

# --deb style options and the plain names (deb) both work.
targets=()
container=0
for a in "$@"; do
    case "$a" in
        -h|--help|help) show_help; exit 0 ;;
        --container) container=1 ;;
        --*) targets+=("${a#--}") ;;
        *) targets+=("$a") ;;
    esac
done
[ ${#targets[@]} -eq 0 ] && targets=(all)

# --container: build inside a Fedora container (podman or docker), so the rpm builds from any distro
if [ "$container" -eq 1 ]; then
    engine=$(command -v podman || command -v docker || true)
    [ -n "$engine" ] || { echo "--container needs podman or docker" >&2; exit 1; }
    image=${PKG_IMAGE:-registry.fedoraproject.org/fedora:latest}
    [ "${targets[*]}" = all ] && targets=(rpm)
    log "building [${targets[*]}] in $image with $(basename "$engine")"
    "$engine" run --rm -v "$ROOT:/src:Z" -w /src "$image" bash -c \
        "dnf -y -q install rpm-build python3 tar gzip findutils sed systemd-rpm-macros >/dev/null && \
         cp -r /src /tmp/build-src && cd /tmp/build-src && rm -rf dist build && ./package.sh ${targets[*]} && \
         mkdir -p /src/dist && cp dist/*.rpm dist/*.tar.gz dist/*.deb /src/dist/ 2>/dev/null; true"
    summary
    exit 0
fi

if [ "${targets[0]}" = all ]; then
    # Every target runs in its own process: one that cannot be built here (missing tool) or that fails must not stop
    # the others, and a failure is reported at the end instead of silently skipping everything after it.
    built=(); skipped=(); failed=()
    build_tar || { warn "tarball failed"; failed+=(tar); }
    export PKG_TAR_DONE=1 PKG_CHILD=1
    for t in deb rpm arch void alpine openbsd recipes; do
        set +e
        bash "$ROOT/package.sh" "$t"
        rc=$?
        set -e
        case $rc in
            0) built+=("$t") ;;
            3) skipped+=("$t") ;;
            *) failed+=("$t") ;;
        esac
    done
    log "built:   tar ${built[*]:-}"
    [ ${#skipped[@]} -eq 0 ] || log "skipped: ${skipped[*]}  (recipes for these are in dist/recipes/)"
    if [ ${#failed[@]} -gt 0 ]; then
        echo "FAILED:  ${failed[*]}" >&2
        exit 1
    fi
    summary
    exit 0
fi

for t in "${targets[@]}"; do
    case "$t" in
        tar) build_tar ;;
        deb) build_deb ;;
        rpm) build_rpm ;;
        arch) build_arch ;;
        void) build_void ;;
        alpine) build_alpine ;;
        openbsd) build_openbsd ;;
        recipes) build_recipes ;;
        clean) rm -rf "$DIST" "$BUILD"; log "removed dist/ and build/" ;;
        *) echo "unknown target: $t (see ./package.sh --help)" >&2; exit 2 ;;
    esac
done
if [ -z "${PKG_CHILD:-}" ] && [ "${targets[*]}" != clean ]; then summary; fi
