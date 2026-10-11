"""A minimal NAT-PMP client (RFC 6886), used for VPN port forwarding.

VPN providers such as ProtonVPN hand out a forwarded port over NAT-PMP on the tunnel's gateway (10.2.0.1).  The
mapping only lives for 60 seconds, so it has to be renewed about every 45 seconds - exactly what
`natpmpc -a 1 0 udp 60 -g 10.2.0.1` does in a loop.  The public port it returns is the one peers must connect to,
so Cascade listens on it.
"""

import socket
import struct
import time

PORT = 5351
OP_EXTERNAL = 0
OP_UDP = 1
OP_TCP = 2

RESULTS = {
    0: "success", 1: "unsupported version", 2: "not authorized / refused", 3: "network failure",
    4: "out of resources", 5: "unsupported opcode",
}


class NatPmpError(Exception):
    pass


def _request(gateway, payload, expect_op, bind_ip=None, tries=4, first_timeout=0.25):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        if bind_ip:
            try:
                s.bind((bind_ip, 0))
            except OSError:
                pass
        timeout = first_timeout
        for _ in range(tries):
            s.settimeout(timeout)
            try:
                s.sendto(payload, (gateway, PORT))
                deadline = time.monotonic() + timeout
                while True:
                    data, addr = s.recvfrom(64)
                    if addr[0] == gateway and len(data) >= 8 and data[1] == 128 + expect_op:
                        return data
                    if time.monotonic() > deadline:
                        raise socket.timeout()
            except socket.timeout:
                timeout *= 2
                continue
            except OSError as e:
                raise NatPmpError("cannot reach %s: %s" % (gateway, e.strerror or e))
        raise NatPmpError("no answer from %s (is port forwarding enabled for this VPN server?)" % gateway)
    finally:
        s.close()


def external_address(gateway, bind_ip=None):
    data = _request(gateway, struct.pack("!BB", 0, OP_EXTERNAL), OP_EXTERNAL, bind_ip)
    if len(data) < 12:
        raise NatPmpError("short reply")
    _, _, result, _epoch = struct.unpack("!BBHI", data[:8])
    if result:
        raise NatPmpError(RESULTS.get(result, "error %d" % result))
    return socket.inet_ntoa(data[8:12])


def map_port(gateway, proto="udp", internal=0, external=1, lifetime=60, bind_ip=None):
    """Request a mapping; returns (public_port, internal_port, lifetime)."""
    op = OP_UDP if proto == "udp" else OP_TCP
    pkt = struct.pack("!BBHHHI", 0, op, 0, internal, external, lifetime)
    data = _request(gateway, pkt, op, bind_ip)
    if len(data) < 16:
        raise NatPmpError("short reply")
    _, _, result, _epoch, priv, pub, life = struct.unpack("!BBHIHHI", data[:16])
    if result:
        raise NatPmpError(RESULTS.get(result, "error %d" % result))
    return pub, priv, life


def forward(gateway, bind_ip=None, lifetime=60):
    """Map UDP and TCP the way VPN providers expect; returns the public port."""
    pub_udp, _, _ = map_port(gateway, "udp", 0, 1, lifetime, bind_ip)
    pub_tcp, _, _ = map_port(gateway, "tcp", 0, 1, lifetime, bind_ip)
    return pub_tcp or pub_udp
