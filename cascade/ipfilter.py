"""IP blocklists: P2P ("name:1.2.3.0-1.2.3.255"), eMule DAT ("001.002.003.000 - 001.002.003.255 , 000 , name"),
CIDR ("1.2.3.0/24"), plain ranges and single addresses; plain text, gzip or zip."""

import gzip
import io
import ipaddress
import re
import urllib.request
import zipfile

MAX_BYTES = 200 * 1024 * 1024

_RANGE = re.compile(r"([0-9a-fA-F:.]+)\s*-\s*([0-9a-fA-F:.]+)")


def _norm(ip):
    ip = ip.strip()
    if "." in ip and ":" not in ip:
        ip = ".".join(str(int(p)) for p in ip.split("."))      # DAT pads with zeros: 001.002.003.000
    return str(ipaddress.ip_address(ip))


def parse_line(line):
    """(start, end) or None."""
    line = line.strip()
    if not line or line.startswith(("#", "//", ";")):
        return None
    # DAT: "start - end , level , description"   (level > 127 means allowed)
    if line.count(",") >= 2:
        rng, level = line.split(",")[0], line.split(",")[1]
        try:
            if int(level.strip() or 0) > 127:
                return None
        except ValueError:
            pass
        m = _RANGE.search(rng)
        if m:
            return _norm(m.group(1)), _norm(m.group(2))
    # P2P: "description:start-end" (the description may itself contain colons)
    m = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})\s*-\s*(\d{1,3}(?:\.\d{1,3}){3})\s*$", line)
    if m:
        return _norm(m.group(1)), _norm(m.group(2))
    m = _RANGE.fullmatch(line)
    if m:
        return _norm(m.group(1)), _norm(m.group(2))
    if "/" in line:
        net = ipaddress.ip_network(line.split()[0], strict=False)
        return str(net.network_address), str(net.broadcast_address)
    return _norm(line.split()[0]), _norm(line.split()[0])


def decode(data):
    """Unpack gzip/zip, return text."""
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    elif data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            if not names:
                raise ValueError("the zip file is empty")
            data = z.read(names[0])
    return data.decode("utf-8", "replace")


def parse(text):
    """Returns (ranges, bad_line_count)."""
    ranges, bad = [], 0
    for line in text.splitlines():
        try:
            r = parse_line(line)
        except ValueError:
            bad += 1
            continue
        if r:
            ranges.append(r)
    return ranges, bad


def fetch(source, timeout=60):
    """Read a blocklist from a URL or a file; returns raw bytes."""
    if source.lower().startswith(("http://", "https://")):
        req = urllib.request.Request(source, headers={"User-Agent": "Cascade"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(MAX_BYTES + 1)
    else:
        with open(source, "rb") as fh:
            data = fh.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("the blocklist is larger than 200 MB")
    return data
