"""Network interfaces: what exists, whether it is up and has an address (the kill switch), and its gateway (the
NAT-PMP port forwarding of VPNs such as ProtonVPN).  Linux uses ioctl/procfs; elsewhere `ifconfig` is parsed."""

import fcntl
import ipaddress
import os
import re
import socket
import struct
import subprocess
import sys

SIOCGIFFLAGS = 0x8913
SIOCGIFADDR = 0x8915
IFF_UP = 0x1
IFF_RUNNING = 0x40
IFF_LOOPBACK = 0x8
IFF_POINTOPOINT = 0x10

LINUX = sys.platform.startswith("linux")


def _ioctl(name, req):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        return fcntl.ioctl(s.fileno(), req, struct.pack("256s", name.encode()[:15]))
    finally:
        s.close()


def _flags(name):
    try:
        return struct.unpack("H", _ioctl(name, SIOCGIFFLAGS)[16:18])[0]
    except OSError:
        return None


def _ipv4(name):
    try:
        return socket.inet_ntoa(_ioctl(name, SIOCGIFADDR)[20:24])
    except OSError:
        return None


def _ipv6_linux():
    out = {}
    try:
        with open("/proc/net/if_inet6") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) >= 6:
                    addr = str(ipaddress.IPv6Address(bytes.fromhex(parts[0])))
                    out.setdefault(parts[5], []).append(addr)
    except OSError:
        pass
    return out


def _ifconfig():
    """Fallback for the BSDs / macOS: {name: {"up": bool, "addresses": [...]}} parsed from ifconfig -a."""
    res = {}
    try:
        text = subprocess.run(["ifconfig", "-a"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return res
    cur = None
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9_.:-]+):\s+flags=\d+<([^>]*)>", line)
        if m:
            cur = m.group(1)
            res[cur] = {"up": "UP" in m.group(2).split(","), "addresses": [],
                        "loopback": "LOOPBACK" in m.group(2)}
            continue
        if cur:
            m = re.match(r"^\s+inet6?\s+([0-9a-fA-F:.]+)", line)
            if m:
                res[cur]["addresses"].append(m.group(1).split("%")[0])
    return res


def interfaces():
    """Every network device: [{"name", "up", "addresses", "loopback", "vpn"}], VPN-looking devices first."""
    out = []
    if LINUX:
        v6 = _ipv6_linux()
        try:
            names = [n for _, n in socket.if_nameindex()]
        except OSError:
            names = sorted(os.listdir("/sys/class/net")) if os.path.isdir("/sys/class/net") else []
        for n in names:
            fl = _flags(n) or 0
            addrs = [a for a in [_ipv4(n)] if a] + [a for a in v6.get(n, []) if not a.startswith("fe80")]
            out.append({"name": n, "up": bool(fl & IFF_UP), "addresses": addrs, "loopback": bool(fl & IFF_LOOPBACK),
                        "vpn": _looks_vpn(n, fl)})
    else:
        for n, info in sorted(_ifconfig().items()):
            out.append({"name": n, "up": info["up"], "addresses": [a for a in info["addresses"]
                                                                    if not a.startswith("fe80")],
                        "loopback": info["loopback"], "vpn": _looks_vpn(n, 0)})
    out.sort(key=lambda i: (i["loopback"], not i["vpn"], i["name"]))
    return out


def _looks_vpn(name, flags):
    return bool(flags & IFF_POINTOPOINT) or name.startswith(("tun", "tap", "wg", "ppp", "proton", "nordlynx",
                                                             "mullvad", "tailscale", "utun", "ipsec", "vpn"))


def status(name):
    """{"exists", "up", "addresses"} for one device; "usable" is what the kill switch looks at."""
    if not name:
        return {"name": "", "exists": True, "up": True, "addresses": [], "usable": True}
    for i in interfaces():
        if i["name"] == name:
            usable = i["up"] and bool(i["addresses"])
            return {"name": name, "exists": True, "up": i["up"], "addresses": i["addresses"], "usable": usable}
    return {"name": name, "exists": False, "up": False, "addresses": [], "usable": False}


def gateway(name):
    """IPv4 gateway of the routes through device `name` (Linux), or a guess for point-to-point tunnels."""
    if LINUX and name:
        try:
            with open("/proc/net/route") as fh:
                next(fh, None)
                for line in fh:
                    f = line.split()
                    if len(f) >= 3 and f[0] == name and f[2] != "00000000":
                        return socket.inet_ntoa(struct.pack("<I", int(f[2], 16)))
        except (OSError, ValueError):
            pass
    st = status(name) if name else None
    if st:
        for a in st["addresses"]:
            if ":" not in a:
                # point-to-point VPN devices often have no gateway route; the server is the .1 of the subnet
                return ".".join(a.split(".")[:3] + ["1"])
    return None


def first_ipv4(name):
    for a in status(name)["addresses"]:
        if ":" not in a:
            return a
    return None


def lan_addresses():
    """Addresses other devices can reach the web interface on (for showing its URL)."""
    out = []
    for i in interfaces():
        if i["loopback"] or not i["up"]:
            continue
        for a in i["addresses"]:
            if ":" not in a:
                out.append(a)
    return out
