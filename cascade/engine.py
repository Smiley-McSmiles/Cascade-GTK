"""The BitTorrent engine: one libtorrent session plus everything Cascade adds around it - persistence, the network
interface kill switch, port modes (fixed, random, VPN NAT-PMP, port file), the speed schedule, seeding limits, the
watch folder, blocklists, .part files, first/last piece priority, labels, hooks and torrent creation.

All clients (GTK, web, CLI) reach this object through the RPC API in api.py, so they share one list of torrents and
one set of settings.
"""

import base64
import collections
import logging
import os
import random
import shlex
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request

import libtorrent as lt

from . import __version__, ipfilter, natpmp, netif, paths, scheduler, util

log = logging.getLogger("cascade.engine")

ALERT_MASK = (lt.alert_category.error | lt.alert_category.status | lt.alert_category.storage
              | lt.alert_category.port_mapping | lt.alert_category.file_progress
              | lt.alert_category.performance_warning)

Q_PIECES = 8
Q_NAME = 64
Q_SAVE_PATH = 128
Q_ALL = 0x7FFFFFFF
STATES = lt.torrent_status.states
TF = lt.torrent_flags
UTP_SOCKET = 0x20000
INT_MAX = 2 ** 31 - 1
MAX_TORRENT_BYTES = 50 * 1024 * 1024

STATS_KEYS = ("dht.dht_nodes", "peer.num_peers_connected", "net.has_incoming_connections", "net.recv_payload_bytes",
              "net.sent_payload_bytes", "net.recv_bytes", "net.sent_bytes", "peer.num_peers_half_open",
              "ses.num_downloading_torrents", "ses.num_seeding_torrents", "ses.num_queued_download_torrents")


class EngineError(Exception):
    pass


def id_of_hashes(ih):
    return str(ih.v1) if ih.has_v1() else str(ih.v2)


def kib(v):
    """KiB/s setting -> libtorrent bytes/s (0 = unlimited), clamped to what libtorrent accepts."""
    return 0 if not v or v <= 0 else min(int(v) * 1024, INT_MAX)


def strip_part(path):
    return path[:-5] if path.endswith(".part") else path


ERROR_TEXTS = {
    "asio.netdb": {1: "host not found", 2: "host not found (try again later)", 3: "no address for this host",
                   4: "name lookup failed"},
    "asio.misc": {1: "already open", 2: "connection closed", 3: "element not found"},
    "asio.addrinfo": {1: "service not found", 2: "socket type not supported"},
}


def _err_text(e):
    """libtorrent hands tracker errors over as {"value", "category"}; make them readable."""
    if not e:
        return ""
    if isinstance(e, dict):
        v = e.get("value", 0)
        cat = e.get("category", "")
        if not v:
            return ""
        if cat == "system":
            return os.strerror(v)
        if cat == "http":
            return "HTTP error %d" % v
        text = ERROR_TEXTS.get(cat, {}).get(v)
        if text:
            return text
        return "%s error %s" % (cat, v)
    return str(e)


class Engine:
    def __init__(self, settings, state):
        self.settings = settings
        self.state = state
        self.lt_version = lt.__version__
        self.lock = threading.RLock()
        self.handles = {}                     # id -> torrent_handle
        self.meta = util.read_json(paths.meta_file(), {}) or {}
        self.events = collections.deque(maxlen=400)
        self.event_seq = 0
        self.stats = {}
        self.history = collections.deque(maxlen=600)          # (time, down, up) every second, 10 minutes
        self.listen_info = {"ok": [], "failed": {}, "external_port": None, "external_ip": None}
        self.pause_reasons = set()
        self.alt_manual = state.get("alt_manual")
        self.sched_mode = scheduler.NORMAL
        self.iface = {"name": "", "usable": True}
        self.natpmp = {"ok": None, "port": None, "gateway": None, "error": None, "time": None}
        self.ipfilter_info = {"rules": 0, "updated": state.get("ipfilter_updated"), "error": None, "loading": False}
        self.jobs = {}
        self.last_gui_poll = 0
        self.session_start = time.time()
        self.alltime_base = {"down": state.get("alltime_down", 0), "up": state.get("alltime_up", 0)}
        self.pending_resume = 0
        self.resume_cv = threading.Condition()
        self.running = True
        self._port_file_mtime = None
        self._natpmp_wake = threading.Event()
        self._applied = {}
        self._tick_n = 0
        self._last_rates = (0, 0)

        for d in (paths.data_dir(), paths.torrents_dir()):
            paths.ensure_private_dir(d)
        self.port = self._initial_port()
        self.sched_mode = self._schedule_mode()
        pack = self._pack()
        self.ses = lt.session(pack)
        self._applied = dict(pack)
        self._load_session_state()
        self._apply_peer_classes()
        self._load_torrents()
        self._check_interface(initial=True)
        self._apply_pause()
        self.alert_thread = threading.Thread(target=self._alert_loop, name="alerts", daemon=True)
        self.alert_thread.start()
        self.natpmp_thread = threading.Thread(target=self._natpmp_loop, name="natpmp", daemon=True)
        self.natpmp_thread.start()
        if settings["ipfilter.enabled"]:
            threading.Thread(target=self._ipfilter_load, args=(False,), name="ipfilter", daemon=True).start()
        self.event("info", "started", "Cascade %s started (libtorrent %s), listening on port %d"
                   % (__version__, lt.__version__, self.port))

    # ------------------------------------------------------------------ settings -> libtorrent
    def _schedule_mode(self):
        s = self.settings
        return scheduler.mode_at(s["sched.grid"]) if s["sched.enabled"] else scheduler.NORMAL

    def alt_active(self):
        if self.alt_manual is not None:
            return bool(self.alt_manual)
        return self.sched_mode == scheduler.SLOW

    def _listen_string(self):
        s = self.settings
        iface = s["net.interface"]
        if iface:
            return "%s:%d" % (iface, self.port)
        out = "0.0.0.0:%d" % self.port
        if s["net.ipv6"]:
            out += ",[::]:%d" % self.port
        return out

    def _pack(self):
        s = self.settings
        alt = self.alt_active()
        enc = {
            "prefer": (lt.enc_policy.pe_enabled, lt.enc_level.pe_both, True),
            "require": (lt.enc_policy.pe_forced, lt.enc_level.pe_rc4, True),
            "allow": (lt.enc_policy.pe_enabled, lt.enc_level.pe_both, False),
            "disable": (lt.enc_policy.pe_disabled, lt.enc_level.pe_both, False),
        }[s["net.encryption"]]
        ptype = s["proxy.type"]
        has_auth = bool(s["proxy.username"])
        proxy_type = {"none": 0, "socks4": 1, "socks5": 3 if has_auth else 2, "http": 5 if has_auth else 4,
                      "i2p": 6}[ptype]
        q = s["queue.enabled"]
        slow = kib(s["queue.slow_rate"])
        pack = {
            "alert_mask": ALERT_MASK,
            "user_agent": "Cascade/%s libtorrent/%s" % (__version__, lt.__version__),
            "listen_interfaces": self._listen_string(),
            "outgoing_interfaces": s["net.interface"],
            "listen_system_port_fallback": False,
            "max_retry_port_bind": 0,
            "enable_dht": s["net.dht"],
            "enable_lsd": s["net.lsd"],
            "enable_upnp": s["net.upnp"],
            "enable_natpmp": s["net.upnp"],
            "enable_incoming_utp": s["net.utp"],
            "enable_outgoing_utp": s["net.utp"],
            "in_enc_policy": int(enc[0]),
            "out_enc_policy": int(enc[0]),
            "allowed_enc_level": int(enc[1]),
            "prefer_rc4": enc[2],
            "anonymous_mode": s["net.anonymous"],
            "proxy_type": proxy_type,
            "proxy_hostname": s["proxy.host"] if ptype not in ("none", "i2p") else "",
            "proxy_port": s["proxy.port"] if ptype not in ("none", "i2p") else 0,
            "proxy_username": s["proxy.username"],
            "proxy_password": s["proxy.password"],
            "proxy_peer_connections": s["proxy.peers"],
            "proxy_tracker_connections": s["proxy.trackers"],
            "proxy_hostnames": s["proxy.hostnames"],
            "i2p_hostname": s["proxy.host"] if ptype == "i2p" else "",
            "i2p_port": s["proxy.port"] if ptype == "i2p" else 0,
            "download_rate_limit": kib(s["alt.download"] if alt else s["speed.download"]),
            "upload_rate_limit": kib(s["alt.upload"] if alt else s["speed.upload"]),
            "rate_limit_ip_overhead": s["speed.overhead"],
            "connections_limit": s["conn.max"],
            "unchoke_slots_limit": s["conn.slots"],
            "active_downloads": s["queue.max_downloads"] if q else -1,
            "active_seeds": s["queue.max_seeds"] if q else -1,
            "active_limit": s["queue.max_active"] if q else -1,
            "dont_count_slow_torrents": s["queue.ignore_slow"],
            "inactive_down_rate": slow,
            "inactive_up_rate": slow,
            "announce_to_all_trackers": s["adv.announce_all_trackers"],
            "announce_to_all_tiers": s["adv.announce_all_tiers"],
            "validate_https_trackers": s["adv.validate_https"],
            "disk_io_read_mode": 0 if s["adv.os_cache"] else 2,
            "disk_io_write_mode": 0 if s["adv.os_cache"] else 2,
            "aio_threads": s["adv.aio_threads"],
            "hashing_threads": s["adv.hashing_threads"],
            "file_pool_size": s["adv.file_pool"],
            "checking_mem_usage": s["adv.checking_mem"] * 64,
            "outgoing_port": s["adv.outgoing_port"],
            "num_outgoing_ports": s["adv.outgoing_ports"] if s["adv.outgoing_port"] else 0,
            "max_peerlist_size": s["adv.max_peerlist"],
            "connection_speed": s["adv.connection_speed"],
            "peer_dscp": s["adv.dscp"],
            "mixed_mode_algorithm": 0 if s["adv.mixed_mode"] == "prefer_tcp" else 1,
            "choking_algorithm": 2 if s["adv.choking"] == "rate_based" else 0,
            "seed_choking_algorithm": {"round_robin": 0, "fastest_upload": 1, "anti_leech": 2}[s["adv.seed_choking"]],
            "dht_bootstrap_nodes": s["adv.dht_bootstrap"],
            "apply_ip_filter_to_trackers": s["ipfilter.trackers"],
            "enable_ip_notifier": True,
        }
        known = lt.default_settings()
        return {k: v for k, v in pack.items() if k in known}

    def _apply_pack(self, force_listen=False):
        pack = self._pack()
        diff = {k: v for k, v in pack.items() if self._applied.get(k) != v}
        if force_listen:
            diff["listen_interfaces"] = pack["listen_interfaces"]
            diff["outgoing_interfaces"] = pack["outgoing_interfaces"]
        if diff:
            log.debug("applying %s", sorted(diff))
            self.ses.apply_settings(diff)
            self._applied.update(diff)

    def _apply_peer_classes(self):
        """libtorrent never rate-limits LAN peers by default; "apply limits to the local network" puts every
        address in the global class instead."""
        f = lt.ip_filter()
        if self.settings["speed.limit_lan"]:
            g = 1 << self.ses.global_peer_class_id
            f.add_rule("0.0.0.0", "255.255.255.255", g)
            f.add_rule("::", "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff", g)
            self.ses.set_peer_class_filter(f)
        else:
            g = 1 << self.ses.global_peer_class_id
            loc = g | (1 << self.ses.local_peer_class_id)
            f.add_rule("0.0.0.0", "255.255.255.255", g)
            f.add_rule("::", "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff", g)
            for a, b in (("10.0.0.0", "10.255.255.255"), ("172.16.0.0", "172.31.255.255"),
                         ("192.168.0.0", "192.168.255.255"), ("169.254.0.0", "169.254.255.255"),
                         ("127.0.0.0", "127.255.255.255"), ("fe80::", "febf:ffff:ffff:ffff:ffff:ffff:ffff:ffff"),
                         ("fc00::", "fdff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"), ("::1", "::1")):
                f.add_rule(a, b, loc)
            self.ses.set_peer_class_filter(f)

    def on_settings_changed(self, keys):
        """Called by the settings store after a change from any client."""
        keys = set(keys)
        s = self.settings
        if keys & {"net.port_mode", "net.port", "net.random_min", "net.random_max", "net.port_file"}:
            self._update_port_for_mode(keys)
        if keys & {"sched.enabled", "sched.grid"}:
            self.alt_manual = None
            self.state.set("alt_manual", None)
            self.sched_mode = self._schedule_mode()
        if "speed.limit_lan" in keys:
            self._apply_peer_classes()
        if keys & {"net.interface", "net.killswitch"}:
            self._check_interface(force=True)
        self._apply_pack(force_listen=bool(keys & {"net.interface", "net.ipv6"}))
        if keys & {"conn.max_per_torrent", "conn.slots_per_torrent", "net.pex"}:
            for tid, h in self._items():
                m = self.meta.get(tid, {})
                if "max_connections" not in m:
                    h.set_max_connections(s["conn.max_per_torrent"])
                if "max_uploads" not in m:
                    h.set_max_uploads(s["conn.slots_per_torrent"])
                if s["net.pex"]:
                    h.unset_flags(TF.disable_pex)
                else:
                    h.set_flags(TF.disable_pex)
        if keys & {"queue.enabled"} and s["queue.enabled"]:
            # torrents that were simply running become managed again; user-paused ones stay paused
            for _, h in self._items():
                st = h.status(0)
                if not st.paused:
                    h.set_flags(TF.auto_managed)
        if keys & {"ipfilter.enabled", "ipfilter.source", "ipfilter.trackers"}:
            if s["ipfilter.enabled"]:
                threading.Thread(target=self._ipfilter_load, args=("ipfilter.source" in keys,), daemon=True).start()
            else:
                self.ses.set_ip_filter(lt.ip_filter())
                self.ipfilter_info.update(rules=0, error=None)
        if keys & {"net.port_mode", "net.natpmp_gateway", "net.interface"}:
            self._natpmp_wake.set()
        self._apply_pause()

    # ------------------------------------------------------------------ ports
    def _initial_port(self):
        s = self.settings
        mode = s["net.port_mode"]
        if mode == "random":
            p = self._random_port()
            self.state.set("random_port", p)
            return p
        if mode == "natpmp":
            return self.state.get("natpmp_port") or s["net.port"]
        if mode == "file":
            return self._read_port_file() or s["net.port"]
        return s["net.port"]

    def _random_port(self):
        lo, hi = sorted((self.settings["net.random_min"], self.settings["net.random_max"]))
        return random.randint(lo, hi)

    def _read_port_file(self):
        p = self.settings["net.port_file"]
        if not p:
            return None
        try:
            self._port_file_mtime = os.stat(p).st_mtime
            with open(p) as fh:
                txt = fh.read(64).strip()
            port = int("".join(ch for ch in txt.split()[0] if ch.isdigit()) or 0) if txt else 0
            return port if 0 < port < 65536 else None
        except (OSError, ValueError, IndexError):
            return None

    def _set_port(self, port, why):
        if port and port != self.port:
            old, self.port = self.port, port
            self._apply_pack()
            self.event("info", "port", "Listening port changed from %d to %d (%s)" % (old, port, why))

    def _update_port_for_mode(self, keys):
        s = self.settings
        mode = s["net.port_mode"]
        if mode == "fixed":
            self._set_port(s["net.port"], "fixed port")
        elif mode == "random":
            lo, hi = sorted((s["net.random_min"], s["net.random_max"]))
            if "net.port_mode" in keys or not lo <= self.port <= hi:
                p = self._random_port()
                self.state.set("random_port", p)
                self._set_port(p, "random port")
        elif mode == "file":
            p = self._read_port_file()
            if p:
                self._set_port(p, "port file")
        elif mode == "natpmp":
            self._natpmp_wake.set()

    def randomize_port(self):
        s = self.settings
        if s["net.port_mode"] == "fixed":
            p = random.randint(49152, 65000)
            s.update({"net.port": p})       # the settings listener applies it
            return p
        p = self._random_port()
        self.state.set("random_port", p)
        self._set_port(p, "random port")
        return p

    def _natpmp_loop(self):
        while self.running:
            s = self.settings
            if s["net.port_mode"] != "natpmp" or not self.iface.get("usable", True):
                self._natpmp_wake.wait(10)
                self._natpmp_wake.clear()
                continue
            iface = s["net.interface"]
            gw = s["net.natpmp_gateway"] or netif.gateway(iface)
            if not gw:
                self.natpmp.update(ok=False, error="no gateway: set the VPN gateway address")
                self._natpmp_wake.wait(15)
                self._natpmp_wake.clear()
                continue
            try:
                port = natpmp.forward(gw, netif.first_ipv4(iface) if iface else None)
                self.natpmp.update(ok=True, port=port, gateway=gw, error=None, time=time.time())
                if port:
                    self.state.set("natpmp_port", port)
                    self._set_port(port, "VPN port forwarding")
                wait = 45
            except natpmp.NatPmpError as e:
                if self.natpmp.get("ok") is not False or self.natpmp.get("error") != str(e):
                    self.event("warning", "port", "VPN port forwarding via %s failed: %s" % (gw, e))
                self.natpmp.update(ok=False, gateway=gw, error=str(e))
                wait = 15
            self._natpmp_wake.wait(wait)
            self._natpmp_wake.clear()

    # ------------------------------------------------------------------ pause reasons (kill switch, schedule)
    def _apply_pause(self):
        reasons = set()
        s = self.settings
        if s["net.interface"] and s["net.killswitch"] and not self.iface.get("usable", True):
            reasons.add("killswitch")
        if self.sched_mode == scheduler.PAUSED:
            reasons.add("schedule")
        if reasons != self.pause_reasons:
            self.pause_reasons = reasons
        if reasons and not self.ses.is_paused():
            self.ses.pause()
            log.info("session paused (%s)", ", ".join(sorted(reasons)))
        elif not reasons and self.ses.is_paused():
            self.ses.resume()
            log.info("session resumed")

    def _check_interface(self, initial=False, force=False):
        name = self.settings["net.interface"]
        if not name:
            self.iface = {"name": "", "usable": True, "exists": True, "up": True, "addresses": []}
            return
        st = netif.status(name)
        prev = self.iface.get("usable") if self.iface.get("name") == name else None
        self.iface = st
        changed = prev is not None and prev != st["usable"]
        if (changed or force) and st["usable"]:
            self.event("info", "interface", "%s is up (%s): transfers allowed" % (name, ", ".join(st["addresses"])))
            self._apply_pack(force_listen=True)
            try:
                self.ses.reopen_network_sockets()
            except Exception:
                pass
            self._natpmp_wake.set()
        elif changed:
            self.event("warning", "interface", "%s went down: %s" % (
                name, "all transfers are paused" if self.settings["net.killswitch"] else "no connections possible"))
        elif (initial or force) and not st["usable"]:
            self.event("warning", "interface", "%s is not available%s" % (
                name, ": transfers stay paused until it is" if self.settings["net.killswitch"] else ""))

    def set_alt_speed(self, on):
        """Manual slow-mode toggle (lasts until the schedule's next change)."""
        if on == "toggle":
            on = not self.alt_active()
        self.alt_manual = bool(on)
        self.state.set("alt_manual", self.alt_manual)
        self._apply_pack()
        return self.alt_active()

    # ------------------------------------------------------------------ persistence
    def _load_session_state(self):
        try:
            with open(paths.session_file(), "rb") as fh:
                self.ses.load_state(lt.bdecode(fh.read()), lt.save_state_flags_t.save_dht_state)
        except (OSError, RuntimeError):
            pass

    def _save_session_state(self):
        try:
            st = self.ses.save_state(lt.save_state_flags_t.save_dht_state)
            util.write_atomic(paths.session_file(), lt.bencode(st))
        except Exception:
            log.exception("could not save the session state")

    def save_meta(self):
        with self.lock:
            util.write_json(paths.meta_file(), self.meta)

    def _resume_path(self, tid):
        return os.path.join(paths.torrents_dir(), tid + ".resume")

    def _torrent_path(self, tid):
        return os.path.join(paths.torrents_dir(), tid + ".torrent")

    def _load_torrents(self):
        d = paths.torrents_dir()
        seen = set()
        for fn in sorted(os.listdir(d)):
            tid, ext = os.path.splitext(fn)
            if tid in seen or ext not in (".resume", ".torrent"):
                continue
            seen.add(tid)
            try:
                atp = None
                if os.path.exists(self._resume_path(tid)):
                    with open(self._resume_path(tid), "rb") as fh:
                        atp = lt.read_resume_data(fh.read())
                    if atp.ti is None and os.path.exists(self._torrent_path(tid)):
                        with open(self._torrent_path(tid), "rb") as fh:
                            atp.ti = lt.torrent_info(lt.bdecode(fh.read()))
                else:
                    with open(self._torrent_path(tid), "rb") as fh:
                        atp = lt.add_torrent_params()
                        atp.ti = lt.torrent_info(lt.bdecode(fh.read()))
                    m = self.meta.get(tid, {})
                    atp.save_path = m.get("save_path") or self.settings["downloads.save_path"]
                h = self.ses.add_torrent(atp)
                self.handles[tid] = h
                self.meta.setdefault(tid, {"added": time.time(), "label": ""})
            except Exception as e:
                log.error("could not load torrent %s: %s", tid, e)
        # magnets that were added but never saved resume data (crash right after adding)
        for tid, m in list(self.meta.items()):
            if tid not in self.handles:
                if m.get("magnet"):
                    try:
                        atp = lt.parse_magnet_uri(m["magnet"])
                        atp.save_path = m.get("save_path") or self.settings["downloads.save_path"]
                        self.handles[tid] = self.ses.add_torrent(atp)
                        continue
                    except Exception:
                        pass
                del self.meta[tid]
        log.info("loaded %d torrents", len(self.handles))

    def save_resume(self, h, flags=None):
        try:
            if not h.is_valid():
                return
            with self.resume_cv:
                self.pending_resume += 1
            h.save_resume_data(lt.torrent_handle.save_info_dict if flags is None else flags)
        except Exception:
            with self.resume_cv:
                self.pending_resume -= 1

    def shutdown(self):
        log.info("shutting down")
        self.ses.pause()
        for _, h in self._items():
            self.save_resume(h)
        deadline = time.time() + 20
        with self.resume_cv:
            while self.pending_resume > 0 and time.time() < deadline:
                self.resume_cv.wait(0.5)
        self.running = False
        self._natpmp_wake.set()
        self._save_session_state()
        self._save_totals()
        self.save_meta()
        self.alert_thread.join(2)

    def _save_totals(self):
        down, up = self.alltime()
        self.state.data.update(alltime_down=down, alltime_up=up)
        self.state.save()

    def alltime(self):
        return (self.alltime_base["down"] + self.stats.get("net.recv_payload_bytes", 0),
                self.alltime_base["up"] + self.stats.get("net.sent_payload_bytes", 0))

    # ------------------------------------------------------------------ events
    def event(self, level, kind, message, tid=None, name=None):
        with self.lock:
            self.event_seq += 1
            ev = {"seq": self.event_seq, "time": time.time(), "level": level, "kind": kind, "message": message,
                  "id": tid, "name": name}
            self.events.append(ev)
        getattr(log, {"error": "error", "warning": "warning"}.get(level, "info"))(message)
        return ev

    def events_since(self, seq):
        with self.lock:
            return [e for e in self.events if e["seq"] > seq]

    # ------------------------------------------------------------------ lookup helpers
    def _items(self):
        with self.lock:
            return list(self.handles.items())

    def _tid_of(self, h):
        try:
            ih = h.info_hashes()
        except Exception:
            return None
        with self.lock:
            for key in (str(ih.v1) if ih.has_v1() else None, str(ih.v2) if ih.has_v2() else None):
                if key and key in self.handles:
                    return key
            for tid, hh in self.handles.items():
                if hh == h:
                    return tid
        return None

    def resolve(self, ref):
        """Accept a full id, a unique id prefix (4+ chars), or a unique part of the name."""
        if ref is None:
            raise EngineError("no torrent given")
        ref = str(ref).strip()
        with self.lock:
            if ref in self.handles:
                return ref
            low = ref.lower()
            if len(low) >= 4:
                hits = [t for t in self.handles if t.startswith(low)]
                if len(hits) == 1:
                    return hits[0]
            hits = [t for t, m in self.meta.items() if t in self.handles and low in (m.get("name") or "").lower()]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                raise EngineError("%r matches %d torrents; use more of the name or the id" % (ref, len(hits)))
        raise EngineError("no torrent matches %r" % ref)

    def resolve_many(self, refs):
        if refs in ("all", ["all"]):
            return [t for t, _ in self._items()]
        if isinstance(refs, str):
            refs = [refs]
        return [self.resolve(r) for r in refs or []]

    def _h(self, tid):
        with self.lock:
            h = self.handles.get(tid)
        if h is None or not h.is_valid():
            raise EngineError("no such torrent: %s" % tid)
        return h

    # ------------------------------------------------------------------ adding
    def _download(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": "Cascade/%s" % __version__})
        with urllib.request.urlopen(req, timeout=30) as resp:
            final = resp.geturl()
            if final.lower().startswith("magnet:"):
                return final, None
            data = resp.read(MAX_TORRENT_BYTES + 1)
        if len(data) > MAX_TORRENT_BYTES:
            raise EngineError("the .torrent file is too large")
        return None, data

    def _source_params(self, magnet=None, data=None, path=None, url=None):
        """-> (add_torrent_params, raw .torrent bytes or None, magnet uri or None)"""
        if url:
            if util.is_magnet(url):
                magnet = url
            else:
                try:
                    magnet, data = self._download(url)
                except (OSError, ValueError) as e:
                    raise EngineError("could not download %s: %s" % (url, getattr(e, "reason", e)))
        if path:
            path = os.path.expanduser(path)
            try:
                with open(path, "rb") as fh:
                    data = fh.read(MAX_TORRENT_BYTES + 1)
            except OSError as e:
                raise EngineError("cannot read %s: %s" % (path, e.strerror))
        if isinstance(data, str):
            data = base64.b64decode(data)
        if magnet:
            try:
                return lt.parse_magnet_uri(magnet.strip()), None, magnet.strip()
            except RuntimeError as e:
                raise EngineError("not a valid magnet link: %s" % e)
        if not data:
            raise EngineError("nothing to add: give a magnet link, a .torrent file or a URL")
        try:
            ti = lt.torrent_info(lt.bdecode(data))
        except RuntimeError as e:
            raise EngineError("not a valid .torrent file: %s" % e)
        atp = lt.add_torrent_params()
        atp.ti = ti
        return atp, data, None

    def inspect(self, magnet=None, data=None, path=None, url=None):
        """What the add dialog shows before adding: name, size, files (pad files hidden)."""
        atp, raw, mag = self._source_params(magnet, data, path, url)
        if atp.ti is None:
            tid = id_of_hashes(atp.info_hashes)
            return {"id": tid, "name": atp.name or tid, "magnet": mag, "has_metadata": False, "files": [],
                    "size": None, "trackers": len(atp.trackers), "duplicate": tid in self.handles}
        ti = atp.ti
        fs = ti.files()
        files = [{"index": i, "path": fs.file_path(i), "size": fs.file_size(i)}
                 for i in range(fs.num_files()) if not fs.file_flags(i) & fs.flag_pad_file]
        tid = id_of_hashes(ti.info_hashes())
        out = {"id": tid, "name": ti.name(), "magnet": None, "has_metadata": True, "files": files,
               "size": sum(f["size"] for f in files), "comment": ti.comment(), "creator": ti.creator(),
               "private": ti.priv(), "pieces": ti.num_pieces(), "piece_size": ti.piece_length(),
               "trackers": len(list(ti.trackers())), "duplicate": tid in self.handles}
        if url and raw:
            out["data"] = base64.b64encode(raw).decode()
        return out

    def add(self, magnet=None, data=None, path=None, url=None, options=None):
        o = dict(options or {})
        s = self.settings
        atp, raw, mag = self._source_params(magnet, data, path, url)
        ti = atp.ti
        tid = id_of_hashes(ti.info_hashes() if ti is not None else atp.info_hashes)
        name = (ti.name() if ti is not None else atp.name) or tid
        with self.lock:
            if tid in self.handles:
                h = self.handles[tid]
                for t in (ti.trackers() if ti is not None else []):
                    h.add_tracker({"url": t.url, "tier": t.tier})
                for url_ in (atp.trackers if ti is None else []):
                    h.add_tracker({"url": url_})
                return {"id": tid, "name": self.meta.get(tid, {}).get("name") or name, "duplicate": True}

        labels = s["labels"] or {}
        label = (o.get("label") or "").strip()
        final = o.get("save_path") or (labels.get(label) or {}).get("path") or s["downloads.save_path"]
        final = os.path.abspath(os.path.expanduser(final))
        skip_check = bool(o.get("skip_check"))
        save_path = final
        move_to = None
        if s["downloads.use_incomplete"] and s["downloads.incomplete_path"] and not skip_check:
            save_path = s["downloads.incomplete_path"]
            move_to = final
        try:
            os.makedirs(save_path, exist_ok=True)
        except OSError as e:
            raise EngineError("cannot create %s: %s" % (save_path, e.strerror))
        atp.save_path = save_path
        atp.storage_mode = (lt.storage_mode_t.storage_mode_allocate if s["downloads.preallocate"]
                            else lt.storage_mode_t.storage_mode_sparse)
        paused = bool(o.get("paused", s["add.start_paused"]))
        flags = TF.default_flags & ~TF.paused & ~TF.auto_managed
        if paused:
            flags |= TF.paused
        elif s["queue.enabled"]:
            flags |= TF.auto_managed
        if o.get("sequential", s["add.sequential"]):
            flags |= TF.sequential_download
        if not s["net.pex"]:
            flags |= TF.disable_pex
        if skip_check:
            flags |= TF.seed_mode
        atp.flags = flags
        if s["add.extra_trackers_enabled"] and s["add.extra_trackers"]:
            atp.trackers = list(atp.trackers) + list(s["add.extra_trackers"])
        atp.max_connections = s["conn.max_per_torrent"]
        atp.max_uploads = s["conn.slots_per_torrent"]
        prios = o.get("file_priorities")
        if prios and ti is not None:
            atp.file_priorities = [max(0, min(7, int(p))) for p in prios]
        part = bool(s["downloads.part_extension"]) and not skip_check
        if part and ti is not None:
            fs = ti.files()
            # files that already exist (re-adding finished data) keep their names, or they would be downloaded again
            atp.renamed_files = {i: fs.file_path(i) + ".part" for i in range(fs.num_files())
                                 if not fs.file_flags(i) & fs.flag_pad_file and fs.file_size(i) > 0
                                 and not os.path.exists(os.path.join(save_path, fs.file_path(i)))}
        try:
            h = self.ses.add_torrent(atp)
        except RuntimeError as e:
            raise EngineError("libtorrent refused the torrent: %s" % e)
        first_last = bool(o.get("first_last", s["add.first_last"]))
        meta = {"name": name, "label": label, "added": time.time(), "move_to": move_to, "part": part,
                "first_last": first_last, "save_path": save_path}
        if mag:
            meta["magnet"] = mag
            if o.get("pause_after_metadata", s["add.magnet_choose_files"]):
                meta["pause_after_metadata"] = True
        with self.lock:
            self.handles[tid] = h
            self.meta[tid] = meta
        self.save_meta()
        if raw:
            util.write_atomic(self._torrent_path(tid), raw)
            self._copy_torrent_file(name, raw)
        if o.get("top_of_queue", s["add.top_of_queue"]):
            h.queue_position_top()
        if first_last and ti is not None:
            self._apply_first_last(h)
        self.save_resume(h)
        self.event("info", "added", "Added %s" % name, tid, name)
        self._run_hook("automation.on_added", tid)
        return {"id": tid, "name": name, "duplicate": False}

    def _copy_torrent_file(self, name, raw):
        s = self.settings
        if not (s["downloads.copy_torrents"] and s["downloads.copy_torrents_path"]):
            return
        try:
            d = s["downloads.copy_torrents_path"]
            os.makedirs(d, exist_ok=True)
            safe = "".join(c if c not in '/\\\0' else "_" for c in name)[:200] or "torrent"
            util.write_atomic(os.path.join(d, safe + ".torrent"), raw, 0o644)
        except OSError as e:
            log.warning("could not copy the .torrent file: %s", e)

    def _apply_first_last(self, h):
        ti = h.torrent_file()
        if ti is None:
            return
        fs = ti.files()
        prios = h.get_file_priorities()
        pp = list(h.get_piece_priorities())
        for i in range(fs.num_files()):
            size = fs.file_size(i)
            if size <= 0 or fs.file_flags(i) & fs.flag_pad_file or (i < len(prios) and prios[i] == 0):
                continue
            for off in (0, size - 1):
                p = ti.map_file(i, off, 1).piece
                if 0 <= p < len(pp):
                    pp[p] = 7
        h.prioritize_pieces(pp)

    def _apply_part_names(self, tid, h):
        ti = h.torrent_file()
        if ti is None:
            return
        fs = ti.files()
        prog = h.file_progress()
        for i in range(fs.num_files()):
            p, size = fs.file_path(i), fs.file_size(i)
            if fs.file_flags(i) & fs.flag_pad_file or size <= 0:
                continue
            done = i < len(prog) and prog[i] >= size
            if done and p.endswith(".part"):
                h.rename_file(i, p[:-5])
            elif not done and not p.endswith(".part"):
                h.rename_file(i, p + ".part")

    def _strip_part_names(self, h):
        ti = h.torrent_file()
        if ti is None:
            return
        fs = ti.files()
        prog = h.file_progress()
        for i in range(fs.num_files()):
            p = fs.file_path(i)
            if p.endswith(".part") and i < len(prog) and prog[i] >= fs.file_size(i):
                h.rename_file(i, p[:-5])

    # ------------------------------------------------------------------ status
    @staticmethod
    def _state_name(st):
        if st.errc.value():
            return "error"
        if st.moving_storage:
            return "moving"
        if st.state in (STATES.checking_files, STATES.checking_resume_data):
            return "checking"
        if st.paused:
            return "queued" if st.auto_managed else "paused"
        if st.state == STATES.downloading_metadata:
            return "metadata"
        if st.is_finished or st.state in (STATES.finished, STATES.seeding):
            return "seeding"
        return "downloading"

    def _summary(self, tid, h, st, m):
        size, done = st.total_wanted, st.total_wanted_done
        down, up = st.download_payload_rate, st.upload_payload_rate
        state = self._state_name(st)
        eta = int((size - done) / down) if state == "downloading" and down > 0 and size > done else -1
        denom = max(st.all_time_download, done)
        ratio = st.all_time_upload / denom if denom > 0 else (0.0 if not st.all_time_upload else -1)
        trk = m.get("tracker")
        if trk is None:
            trk = ""
            cur = st.current_tracker
            if not cur:
                try:
                    t = h.trackers()
                    cur = t[0]["url"] if t else ""
                except Exception:
                    cur = ""
            if cur:
                trk = urllib.parse.urlsplit(cur).hostname or ""
                m["tracker"] = trk
        err = ""
        if st.errc.value():
            err = st.errc.message() + ((" (%s)" % st.error_file) if getattr(st, "error_file", -1) >= 0 else "")
        return {
            "id": tid,
            "name": st.name or m.get("name") or tid,
            "state": state,
            "progress": st.progress,
            "size": size,
            "done": done,
            "down": down,
            "up": up,
            "eta": eta,
            "ratio": ratio,
            "uploaded": st.all_time_upload,
            "downloaded": st.all_time_download,
            "seeds": st.num_seeds,
            "peers": max(0, st.num_peers - st.num_seeds),
            "seeds_total": st.num_complete if st.num_complete >= 0 else st.list_seeds,
            "peers_total": st.num_incomplete if st.num_incomplete >= 0 else max(0, st.list_peers - st.list_seeds),
            "queue": st.queue_position,
            "label": m.get("label", ""),
            "added": st.added_time,
            "completed": st.completed_time,
            "save_path": st.save_path,
            "has_metadata": st.has_metadata,
            "auto_managed": st.auto_managed,
            "paused": st.paused,
            "forced": not st.auto_managed and not st.paused and self.settings["queue.enabled"],
            "sequential": bool(st.flags & TF.sequential_download),
            "finished": st.is_finished,
            "error": err,
            "tracker": trk,
            "move_to": m.get("move_to"),
        }

    def summaries(self):
        out = []
        for tid, h in self._items():
            try:
                st = h.status(Q_NAME | Q_SAVE_PATH)
            except Exception:
                continue
            with self.lock:
                m = self.meta.setdefault(tid, {"label": "", "added": time.time()})
            out.append(self._summary(tid, h, st, m))
        out.sort(key=lambda t: (t["queue"] if t["queue"] >= 0 else 1 << 30, -t["added"]))
        return out

    @staticmethod
    def counts(summaries):
        c = collections.Counter()
        labels, trackers = collections.Counter(), collections.Counter()
        for t in summaries:
            c["all"] += 1
            st = t["state"]
            if st in ("downloading", "metadata"):
                c["downloading"] += 1
            if st == "seeding":
                c["seeding"] += 1
            if t["finished"]:
                c["completed"] += 1
            if st in ("paused", "queued", "checking", "error"):
                c[st] += 1
            if t["down"] > 0 or t["up"] > 0:
                c["active"] += 1
            labels[t["label"] or ""] += 1
            if t["tracker"]:
                trackers[t["tracker"]] += 1
        return dict(c), dict(labels), dict(trackers)

    def info_texts(self, st):
        s = self.settings
        port = "%d" % self.port
        mode = s["net.port_mode"]
        if mode == "natpmp":
            port += " via %s" % (self.natpmp.get("gateway") or "NAT-PMP")
            if self.natpmp.get("ok") is False:
                port += " (forwarding failed: %s)" % self.natpmp.get("error")
            elif self.natpmp.get("ok") is None:
                port += " (waiting for the VPN)"
        elif mode == "file" and not s["net.port_file"]:
            port += " (choose the port file)"
        if st["incoming"]:
            port += " · reachable, peers are connecting in"
        elif self.listen_info["failed"]:
            port += " · " + "; ".join(sorted(set(self.listen_info["failed"].values())))
        if st["external_port"] and st["external_port"] != self.port:
            port += " · router maps %d" % st["external_port"]
        iface = st["interface"]
        if not iface["name"]:
            itxt = "Any interface"
        elif iface["usable"]:
            itxt = "%s is up (%s)" % (iface["name"], ", ".join(iface.get("addresses") or []))
        elif not iface.get("exists"):
            itxt = "%s does not exist%s" % (iface["name"], " · transfers paused" if s["net.killswitch"] else "")
        else:
            itxt = "%s is down%s" % (iface["name"], " · transfers paused" if s["net.killswitch"] else "")
        ipf = self.ipfilter_info
        if ipf["loading"]:
            iptxt = "Loading…"
        elif ipf["error"]:
            iptxt = "Error: %s" % ipf["error"]
        elif ipf["rules"]:
            iptxt = "%d ranges blocked · updated %s" % (ipf["rules"], util.fmt_time(ipf["updated"]))
        else:
            iptxt = "Not loaded"
        return {"port": port, "interface": itxt, "ipfilter": iptxt, "cli": paths.socket_path(), "log": paths.log_file()}

    def session_status(self, summaries=None):
        summaries = self.summaries() if summaries is None else summaries
        down = sum(t["down"] for t in summaries)
        up = sum(t["up"] for t in summaries)
        s = self.settings
        nxt, nmode = scheduler.next_change(s["sched.grid"]) if s["sched.enabled"] else (None, None)
        adown, aup = self.alltime()
        st = {
            "version": __version__,
            "libtorrent": lt.__version__,
            "down": down,
            "up": up,
            "dht_nodes": self.stats.get("dht.dht_nodes", 0),
            "peers": self.stats.get("peer.num_peers_connected", 0),
            "incoming": bool(self.stats.get("net.has_incoming_connections", 0)),
            "session_down": self.stats.get("net.recv_payload_bytes", 0),
            "session_up": self.stats.get("net.sent_payload_bytes", 0),
            "alltime_down": adown,
            "alltime_up": aup,
            "port": self.port,
            "port_mode": s["net.port_mode"],
            "listening": self.ses.is_listening(),
            "external_port": self.listen_info.get("external_port"),
            "external_ip": self.listen_info.get("external_ip"),
            "natpmp": dict(self.natpmp),
            "interface": dict(self.iface, killswitch=bool(s["net.interface"] and s["net.killswitch"])),
            "paused": sorted(self.pause_reasons),
            "alt_speed": self.alt_active(),
            "alt_source": ("manual" if self.alt_manual is not None else ("schedule" if self.alt_active() else None)),
            "schedule": {"enabled": s["sched.enabled"], "mode": scheduler.MODE_NAMES[self.sched_mode],
                         "next_change": nxt, "next_mode": scheduler.MODE_NAMES.get(nmode)},
            "free_space": util.free_space(s["downloads.save_path"]),
            "save_path": s["downloads.save_path"],
            "ipfilter": dict(self.ipfilter_info),
            "event_seq": self.event_seq,
            "settings_rev": self.settings.rev,
            "uptime": time.time() - self.session_start,
        }
        st["info"] = self.info_texts(st)
        return st

    def details(self, tid, sections=None):
        sections = set(sections or ("info", "files", "peers", "trackers", "pieces", "options"))
        h = self._h(tid)
        st = h.status(Q_ALL)
        with self.lock:
            m = self.meta.setdefault(tid, {"label": "", "added": time.time()})
        out = {"summary": self._summary(tid, h, st, m)}
        ti = h.torrent_file()
        if "info" in sections:
            ih = h.info_hashes()
            info = {
                "hash_v1": str(ih.v1) if ih.has_v1() else "",
                "hash_v2": str(ih.v2) if ih.has_v2() else "",
                "magnet": lt.make_magnet_uri(h),
                "save_path": st.save_path,
                "added": st.added_time,
                "completed": st.completed_time,
                "active_time": int(st.active_duration.total_seconds()),
                "seeding_time": int(st.seeding_duration.total_seconds()),
                "finished_time": int(st.finished_duration.total_seconds()),
                "last_upload": st.time_since_upload,
                "last_download": st.time_since_download,
                "wasted": st.total_failed_bytes + st.total_redundant_bytes,
                "availability": st.distributed_copies,
                "connections": st.num_connections,
                "next_announce": int(st.next_announce.total_seconds()) if st.next_announce else -1,
                "current_tracker": st.current_tracker,
                "total_size": ti.total_size() if ti else -1,
                "pieces": ti.num_pieces() if ti else 0,
                "piece_size": ti.piece_length() if ti else 0,
                "comment": ti.comment() if ti else "",
                "creator": ti.creator() if ti else "",
                "created": ti.creation_date() if ti else 0,
                "private": bool(ti.priv()) if ti else False,
                "num_files": 0,
                "label": m.get("label", ""),
                "move_to": m.get("move_to"),
            }
            if ti:
                fs = ti.files()
                info["num_files"] = sum(1 for i in range(fs.num_files()) if not fs.file_flags(i) & fs.flag_pad_file)
            out["info"] = info
        if "files" in sections:
            files = []
            if ti:
                fs = ti.files()
                prog = h.file_progress()
                prio = h.get_file_priorities()
                for i in range(fs.num_files()):
                    if fs.file_flags(i) & fs.flag_pad_file:
                        continue
                    size = fs.file_size(i)
                    files.append({"index": i, "path": strip_part(fs.file_path(i)), "size": size,
                                  "done": prog[i] if i < len(prog) else 0,
                                  "priority": prio[i] if i < len(prio) else 4})
            out["files"] = files
        if "peers" in sections:
            peers = []
            for p in h.get_peer_info():
                f = p.flags
                src = p.source
                flags = ""
                # D/d: we want data and the peer sends it / chokes us; U/u: the peer wants data and we send / choke
                if f & lt.peer_info.interesting:
                    flags += "d" if f & lt.peer_info.remote_choked else "D"
                if f & lt.peer_info.remote_interested:
                    flags += "u" if f & lt.peer_info.choked else "U"
                if f & (lt.peer_info.rc4_encrypted | lt.peer_info.plaintext_encrypted):
                    flags += "E"
                if f & UTP_SOCKET:
                    flags += "P"
                if not f & lt.peer_info.outgoing_connection:
                    flags += "I"
                if src & lt.peer_info.pex:
                    flags += "X"
                if src & lt.peer_info.dht:
                    flags += "H"
                if src & lt.peer_info.lsd:
                    flags += "L"
                if f & lt.peer_info.snubbed:
                    flags += "S"
                if f & lt.peer_info.optimistic_unchoke:
                    flags += "O"
                client = p.client
                if isinstance(client, bytes):
                    client = client.decode("utf-8", "replace")
                peers.append({"ip": p.ip[0], "port": p.ip[1], "client": client or "Unknown", "flags": flags,
                              "progress": p.progress, "down": p.payload_down_speed, "up": p.payload_up_speed,
                              "downloaded": p.total_download, "uploaded": p.total_upload,
                              "utp": bool(f & UTP_SOCKET), "seed": bool(f & lt.peer_info.seed),
                              "connecting": bool(f & (lt.peer_info.connecting | lt.peer_info.handshake))})
            peers.sort(key=lambda x: (-(x["down"] + x["up"]), x["ip"]))
            out["peers"] = peers
        if "trackers" in sections:
            trackers = []
            now = time.time()
            for t in h.trackers():
                eps = t.get("endpoints") or []
                infos = [ih for ep in eps for ih in (ep.get("info_hashes") or [])]
                msg = t.get("message") or next((i.get("message") for i in infos if i.get("message")), "")
                err = _err_text(t.get("last_error")) or next((_err_text(i.get("last_error")) for i in infos
                                                              if _err_text(i.get("last_error"))), "")
                updating = t.get("updating") or any(i.get("updating") for i in infos)
                working = t.get("verified") or any((i.get("fails", 0) == 0 and i.get("start_sent")) for i in infos)
                seeds = max([t.get("scrape_complete", -1)] + [i.get("scrape_complete", -1) for i in infos])
                leech = max([t.get("scrape_incomplete", -1)] + [i.get("scrape_incomplete", -1) for i in infos])
                dl = max([t.get("scrape_downloaded", -1)] + [i.get("scrape_downloaded", -1) for i in infos])
                nexts = [i.get("next_announce", 0) for i in infos if i.get("next_announce")] or \
                    [t.get("next_announce", 0)]
                nxt = min(nexts) if nexts else 0
                fails = max([t.get("fails", 0)] + [i.get("fails", 0) for i in infos])
                if updating:
                    status = "updating"
                elif err and fails:
                    status = "error"
                elif working:
                    status = "working"
                else:
                    status = "waiting"
                trackers.append({"url": t["url"], "tier": t.get("tier", 0), "status": status,
                                 "message": err if status == "error" else msg, "seeds": seeds, "peers": leech,
                                 "downloaded": dl, "next_announce": max(0, int(nxt - now)) if nxt else -1,
                                 "fails": fails})
            s = self.settings
            out["trackers"] = trackers
            out["sources"] = {"dht": s["net.dht"] and not (ti and ti.priv()),
                              "pex": s["net.pex"] and not (ti and ti.priv()),
                              "lsd": s["net.lsd"] and not (ti and ti.priv())}
        if "pieces" in sections:
            pieces = st.pieces or []
            downloading = []
            try:
                downloading = [q["piece_index"] for q in h.get_download_queue()]
            except Exception:
                pass
            bits = bytearray((len(pieces) + 7) // 8)
            for i, have in enumerate(pieces):
                if have:
                    bits[i >> 3] |= 0x80 >> (i & 7)
            out["pieces"] = {"count": len(pieces), "have": sum(1 for p in pieces if p),
                             "bitfield": base64.b64encode(bytes(bits)).decode(), "downloading": downloading[:2000],
                             "piece_size": ti.piece_length() if ti else 0}
        if "options" in sections:
            dl, ul = h.download_limit(), h.upload_limit()
            out["options"] = {
                "download_limit": dl // 1024 if dl > 0 else 0,
                "upload_limit": ul // 1024 if ul > 0 else 0,
                "max_connections": m.get("max_connections", 0),
                "max_uploads": m.get("max_uploads", 0),
                "sequential": bool(st.flags & TF.sequential_download),
                "first_last": bool(m.get("first_last")),
                "super_seeding": bool(st.flags & TF.super_seeding),
                "auto_managed": bool(st.auto_managed),
                "ratio_limit": m.get("ratio_limit"),
                "seed_time_limit": m.get("seed_time_limit"),
                "label": m.get("label", ""),
            }
        return out

    # ------------------------------------------------------------------ actions
    def pause(self, ids):
        for tid in ids:
            h = self._h(tid)
            h.unset_flags(TF.auto_managed)
            h.pause()
            self.save_resume(h)

    def resume(self, ids, force=False):
        for tid in ids:
            h = self._h(tid)
            if self.settings["queue.enabled"] and not force:
                h.set_flags(TF.auto_managed)
            else:
                h.unset_flags(TF.auto_managed)
            h.resume()
            with self.lock:
                self.meta.get(tid, {}).pop("limit_reached", None)
            self.save_resume(h)

    def recheck(self, ids):
        for tid in ids:
            self._h(tid).force_recheck()

    def reannounce(self, ids):
        for tid in ids:
            h = self._h(tid)
            h.force_reannounce()
            if self.settings["net.dht"]:
                h.force_dht_announce()

    def move(self, ids, path):
        path = os.path.abspath(os.path.expanduser(path))
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as e:
            raise EngineError("cannot create %s: %s" % (path, e.strerror))
        for tid in ids:
            h = self._h(tid)
            with self.lock:
                self.meta.get(tid, {})["move_to"] = None
            h.move_storage(path)

    def queue(self, ids, where):
        fn = {"up": "queue_position_up", "down": "queue_position_down", "top": "queue_position_top",
              "bottom": "queue_position_bottom"}.get(where)
        if not fn:
            raise EngineError("queue direction must be up, down, top or bottom")
        order = ids if where in ("down", "top") else list(reversed(ids))
        for tid in order:
            getattr(self._h(tid), fn)()

    def remove(self, ids, delete_data=False):
        for tid in ids:
            h = self._h(tid)
            with self.lock:
                name = self.meta.get(tid, {}).get("name") or tid
                self.handles.pop(tid, None)
                self.meta.pop(tid, None)
            self.ses.remove_torrent(h, lt.options_t.delete_files if delete_data else 0)
            for p in (self._resume_path(tid), self._torrent_path(tid)):
                try:
                    os.unlink(p)
                except OSError:
                    pass
            self.event("info", "removed", "Removed %s%s" % (name, " and its files" if delete_data else ""), tid, name)
        self.save_meta()

    def set_options(self, ids, opts):
        s = self.settings
        for tid in ids:
            h = self._h(tid)
            with self.lock:
                m = self.meta.setdefault(tid, {})
            if "download_limit" in opts:
                v = int(opts["download_limit"] or 0)
                h.set_download_limit(kib(v) if v > 0 else -1)
            if "upload_limit" in opts:
                v = int(opts["upload_limit"] or 0)
                h.set_upload_limit(kib(v) if v > 0 else -1)
            if "max_connections" in opts:
                v = int(opts["max_connections"] or 0)
                h.set_max_connections(v if v > 0 else s["conn.max_per_torrent"])
                if v > 0:
                    m["max_connections"] = v
                else:
                    m.pop("max_connections", None)
            if "max_uploads" in opts:
                v = int(opts["max_uploads"] or 0)
                h.set_max_uploads(v if v > 0 else s["conn.slots_per_torrent"])
                if v > 0:
                    m["max_uploads"] = v
                else:
                    m.pop("max_uploads", None)
            for key, flag in (("sequential", TF.sequential_download), ("super_seeding", TF.super_seeding)):
                if key in opts:
                    (h.set_flags if opts[key] else h.unset_flags)(flag)
            if "auto_managed" in opts:
                (h.set_flags if opts["auto_managed"] else h.unset_flags)(TF.auto_managed)
            if "first_last" in opts:
                m["first_last"] = bool(opts["first_last"])
                if m["first_last"]:
                    self._apply_first_last(h)
                else:
                    h.prioritize_files(h.get_file_priorities())
            for key in ("ratio_limit", "seed_time_limit"):
                if key in opts:
                    v = opts[key]
                    if v is None or v == "":
                        m.pop(key, None)
                    else:
                        m[key] = float(v) if key == "ratio_limit" else int(v)
            if "label" in opts:
                m["label"] = (opts["label"] or "").strip()
            self.save_resume(h)
        self.save_meta()

    def set_file_priorities(self, tid, priorities):
        """priorities: {index: 0..7} or a full list."""
        h = self._h(tid)
        cur = list(h.get_file_priorities())
        if isinstance(priorities, dict):
            for k, v in priorities.items():
                i = int(k)
                if 0 <= i < len(cur):
                    cur[i] = max(0, min(7, int(v)))
        else:
            cur = [max(0, min(7, int(v))) for v in priorities]
        h.prioritize_files(cur)
        with self.lock:
            m = self.meta.get(tid, {})
        if m.get("first_last"):
            time.sleep(0.05)
            self._apply_first_last(h)
        self.save_resume(h)

    def trackers_add(self, tid, urls):
        h = self._h(tid)
        tier = max([t.get("tier", 0) for t in h.trackers()] + [-1]) + 1
        for u in urls:
            u = u.strip()
            if u:
                h.add_tracker({"url": u, "tier": tier})
        with self.lock:
            self.meta.get(tid, {}).pop("tracker", None)
        h.force_reannounce()
        self.save_resume(h)

    def trackers_remove(self, tid, urls):
        h = self._h(tid)
        keep = [t for t in h.trackers() if t["url"] not in set(urls)]
        h.replace_trackers([{"url": t["url"], "tier": t.get("tier", 0)} for t in keep])
        with self.lock:
            self.meta.get(tid, {}).pop("tracker", None)
        self.save_resume(h)

    def trackers_edit(self, tid, old, new):
        h = self._h(tid)
        lst = [{"url": (new if t["url"] == old else t["url"]), "tier": t.get("tier", 0)} for t in h.trackers()]
        h.replace_trackers(lst)
        with self.lock:
            self.meta.get(tid, {}).pop("tracker", None)
        h.force_reannounce()
        self.save_resume(h)

    def add_peer(self, tid, address):
        """Connect to a peer by hand ("1.2.3.4:6881" or "[::1]:6881")."""
        address = address.strip()
        if address.startswith("["):
            host, _, port = address[1:].partition("]:")
        else:
            host, _, port = address.rpartition(":")
        try:
            port = int(port)
        except ValueError:
            raise EngineError("give the peer as address:port")
        if not host or not 0 < port < 65536:
            raise EngineError("give the peer as address:port")
        self._h(tid).connect_peer((host, port))

    def magnet(self, tid):
        return lt.make_magnet_uri(self._h(tid))

    def export_torrent(self, tid):
        h = self._h(tid)
        ti = h.torrent_file()
        if ti is None:
            raise EngineError("the metadata of this torrent is not downloaded yet")
        data = lt.bencode(lt.create_torrent(ti).generate())
        return {"name": ti.name(), "data": base64.b64encode(data).decode()}

    # ------------------------------------------------------------------ labels
    def labels(self):
        known = dict(self.settings["labels"] or {})
        with self.lock:
            for m in self.meta.values():
                if m.get("label") and m["label"] not in known:
                    known[m["label"]] = {"path": ""}
        return known

    def label_set(self, name, path=""):
        name = (name or "").strip()
        if not name:
            raise EngineError("the label needs a name")
        lab = dict(self.settings["labels"] or {})
        lab[name] = {"path": os.path.abspath(os.path.expanduser(path)) if path else ""}
        self.settings.update({"labels": lab})

    def label_remove(self, name):
        lab = dict(self.settings["labels"] or {})
        lab.pop(name, None)
        self.settings.update({"labels": lab})
        with self.lock:
            for m in self.meta.values():
                if m.get("label") == name:
                    m["label"] = ""
        self.save_meta()

    # ------------------------------------------------------------------ blocklist
    def _ipfilter_load(self, refresh):
        s = self.settings
        src = s["ipfilter.source"].strip()
        cache = paths.blocklist_cache()
        self.ipfilter_info.update(loading=True, error=None)
        try:
            if not src:
                raise EngineError("no blocklist URL or file set")
            data = None
            stale = (time.time() - (self.ipfilter_info.get("updated") or 0)) > s["ipfilter.update_days"] * 86400
            if util.is_url(src) and not refresh and not stale and os.path.exists(cache):
                with open(cache, "rb") as fh:
                    data = fh.read()
            if data is None:
                data = ipfilter.fetch(src)
                if util.is_url(src):
                    util.write_atomic(cache, data)
                self.ipfilter_info["updated"] = time.time()
                self.state.set("ipfilter_updated", self.ipfilter_info["updated"])
            ranges, bad = ipfilter.parse(ipfilter.decode(data))
            f = lt.ip_filter()
            n = 0
            for a, b in ranges:
                try:
                    f.add_rule(a, b, 1)
                    n += 1
                except Exception:
                    bad += 1
            if not s["ipfilter.enabled"]:
                return
            self.ses.set_ip_filter(f)
            self.ipfilter_info.update(rules=n, error=None)
            self.event("info", "ipfilter", "Blocklist loaded: %d ranges%s" % (
                n, (", %d lines skipped" % bad) if bad else ""))
        except Exception as e:
            self.ipfilter_info["error"] = str(getattr(e, "reason", e))
            self.event("error", "ipfilter", "Could not load the blocklist: %s" % self.ipfilter_info["error"])
        finally:
            self.ipfilter_info["loading"] = False

    def ipfilter_update(self):
        if not self.settings["ipfilter.enabled"]:
            raise EngineError("the blocklist is turned off")
        threading.Thread(target=self._ipfilter_load, args=(True,), daemon=True).start()
        return True

    # ------------------------------------------------------------------ torrent creation
    def create_start(self, path, trackers=None, piece_size=0, private=False, comment="", web_seeds=None,
                     source="", version="hybrid", output=None, seed=False):
        path = os.path.abspath(os.path.expanduser(path or ""))
        if not os.path.exists(path):
            raise EngineError("%s does not exist" % path)
        job = "%x" % random.getrandbits(48)
        self.jobs[job] = {"state": "hashing", "progress": 0.0, "error": None, "result": None}
        args = (job, path, trackers or [], int(piece_size or 0), bool(private), comment or "", web_seeds or [],
                source or "", version, output, bool(seed))
        threading.Thread(target=self._create, args=args, name="create", daemon=True).start()
        return job

    def _create(self, job, path, trackers, piece_size, private, comment, web_seeds, source, version, output, seed):
        j = self.jobs[job]
        try:
            fs = lt.file_storage()
            lt.add_files(fs, path)
            if fs.num_files() == 0:
                raise EngineError("there are no files in %s" % path)
            flags = 0
            if version == "v1":
                flags = lt.create_torrent.v1_only
            elif version == "v2":
                flags = lt.create_torrent.v2_only
            ct = lt.create_torrent(fs, piece_size, flags=flags)
            tier = 0
            for line in trackers:
                line = line.strip()
                if not line:
                    tier += 1
                    continue
                ct.add_tracker(line, tier)
            for w in web_seeds:
                if w.strip():
                    ct.add_url_seed(w.strip())
            ct.set_priv(private)
            if comment:
                ct.set_comment(comment)
            ct.set_creator("Cascade %s" % __version__)
            total = ct.num_pieces()

            def progress(i):
                j["progress"] = (i + 1) / max(1, total)

            lt.set_piece_hashes(ct, os.path.dirname(path), progress)
            entry = ct.generate()
            if source:
                entry[b"info"][b"source"] = source.encode()
            data = lt.bencode(entry)
            ti = lt.torrent_info(lt.bdecode(data))
            res = {"name": ti.name(), "id": id_of_hashes(ti.info_hashes()), "size": ti.total_size(),
                   "pieces": ti.num_pieces(), "piece_size": ti.piece_length(),
                   "magnet": lt.make_magnet_uri(ti), "data": base64.b64encode(data).decode(), "output": None}
            if output:
                output = os.path.abspath(os.path.expanduser(output))
                util.write_atomic(output, data, 0o644)
                res["output"] = output
            if seed:
                self.add(data=data, options={"save_path": os.path.dirname(path), "skip_check": True})
            j.update(state="done", progress=1.0, result=res)
        except Exception as e:
            log.exception("creating a torrent failed")
            j.update(state="error", error=str(e))

    def create_status(self, job):
        j = self.jobs.get(job)
        if j is None:
            raise EngineError("unknown job")
        if j["state"] in ("done", "error"):
            self.jobs.pop(job, None)
        return j

    # ------------------------------------------------------------------ hooks, notifications
    def _run_hook(self, key, tid):
        cmd = self.settings[key].strip()
        if not cmd:
            return
        with self.lock:
            h = self.handles.get(tid)
            m = dict(self.meta.get(tid, {}))
        if h is None:
            return
        try:
            st = h.status(Q_NAME | Q_SAVE_PATH)
            name = st.name or m.get("name") or tid
            values = {"%N": name, "%D": st.save_path, "%F": os.path.join(st.save_path, name), "%I": tid,
                      "%L": m.get("label", ""), "%Z": str(st.total_wanted)}
            args = []
            for tok in shlex.split(cmd):
                for k, v in values.items():
                    tok = tok.replace(k, v)
                args.append(tok)
            subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            log.info("ran %s for %s", key, name)
        except (OSError, ValueError) as e:
            self.event("error", "hook", "Could not run “%s”: %s" % (cmd, e), tid)

    def _notify(self, title, body):
        """Desktop notification when no GTK window is open to show it (the window shows its own)."""
        if not self.settings["notify.finished"] or time.time() - self.last_gui_poll < 15:
            return
        exe = shutil.which("notify-send")
        if exe and (os.environ.get("DBUS_SESSION_BUS_ADDRESS") or os.environ.get("XDG_RUNTIME_DIR")):
            try:
                subprocess.Popen([exe, "-a", "Cascade", "-i", "io.github.smiley_mcsmiles.Cascade", title, body],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
            except OSError:
                pass

    # ------------------------------------------------------------------ alerts
    def _alert_loop(self):
        while self.running:
            try:
                self.ses.wait_for_alert(500)
                alerts = self.ses.pop_alerts()
            except Exception:
                log.exception("alert loop")
                time.sleep(1)
                continue
            for a in alerts:
                try:
                    self._handle_alert(a)
                except Exception:
                    log.exception("handling %s", type(a).__name__)

    def _handle_alert(self, a):
        if isinstance(a, lt.save_resume_data_alert):
            tid = self._tid_of(a.handle)
            if tid:
                try:
                    util.write_atomic(self._resume_path(tid), lt.write_resume_data_buf(a.params))
                except OSError as e:
                    log.error("could not save resume data: %s", e)
            with self.resume_cv:
                self.pending_resume -= 1
                self.resume_cv.notify_all()
        elif isinstance(a, lt.save_resume_data_failed_alert):
            with self.resume_cv:
                self.pending_resume -= 1
                self.resume_cv.notify_all()
        elif isinstance(a, lt.session_stats_alert):
            v = a.values
            self.stats = {k: v.get(k, 0) for k in STATS_KEYS}
        elif isinstance(a, lt.torrent_finished_alert):
            self._on_finished(a.handle)
        elif isinstance(a, lt.metadata_received_alert):
            self._on_metadata(a.handle)
        elif isinstance(a, lt.file_completed_alert):
            tid = self._tid_of(a.handle)
            if tid and self.meta.get(tid, {}).get("part"):
                ti = a.handle.torrent_file()
                if ti is not None:
                    p = ti.files().file_path(a.index)
                    if p.endswith(".part"):
                        a.handle.rename_file(a.index, p[:-5])
        elif isinstance(a, lt.state_changed_alert):
            if a.state == STATES.downloading:
                tid = self._tid_of(a.handle)
                if tid:
                    with self.lock:
                        self.meta.get(tid, {})["downloading"] = True
        elif isinstance(a, lt.storage_moved_alert):
            tid = self._tid_of(a.handle)
            if tid:
                with self.lock:
                    m = self.meta.get(tid, {})
                    m["move_to"] = None
                    m["save_path"] = a.handle.status(Q_SAVE_PATH).save_path
                    name = m.get("name")
                self.save_meta()
                self.save_resume(a.handle)
                self.event("info", "moved", "Moved %s to %s" % (name or tid, m["save_path"]), tid, name)
        elif isinstance(a, lt.storage_moved_failed_alert):
            tid = self._tid_of(a.handle)
            self.event("error", "moved", "Moving failed: %s" % a.message(), tid)
        elif isinstance(a, (lt.torrent_error_alert, lt.file_error_alert)):
            tid = self._tid_of(a.handle)
            self.event("error", "error", a.message(), tid)
        elif isinstance(a, lt.listen_succeeded_alert):
            ep = "%s:%d" % (a.address, a.port)
            if ep not in self.listen_info["ok"]:
                self.listen_info["ok"].append(ep)
            self.listen_info["failed"].pop(str(a.address), None)
        elif isinstance(a, lt.listen_failed_alert):
            key = str(getattr(a, "listen_interface", "") or a.address)
            self.listen_info["failed"][key] = "cannot listen on %s: %s" % (key, a.error.message())
            log.warning("listen failed: %s", a.message())
        elif isinstance(a, lt.portmap_alert):
            self.listen_info["external_port"] = a.external_port
        elif isinstance(a, lt.external_ip_alert):
            self.listen_info["external_ip"] = str(a.external_address)
        elif isinstance(a, lt.fastresume_rejected_alert):
            log.warning("%s", a.message())
        elif isinstance(a, lt.performance_alert):
            log.debug("%s", a.message())

    def _on_finished(self, h):
        tid = self._tid_of(h)
        if not tid:
            return
        with self.lock:
            m = self.meta.setdefault(tid, {})
            # only a torrent that was really downloading "finished": existing data that was checked did not
            was_downloading = m.pop("downloading", False)
        if m.get("part"):
            self._strip_part_names(h)
        target = m.get("move_to")
        st = h.status(Q_NAME | Q_SAVE_PATH)
        if target and os.path.normpath(target) != os.path.normpath(st.save_path):
            try:
                os.makedirs(target, exist_ok=True)
                h.move_storage(target)
            except OSError as e:
                self.event("error", "moved", "Cannot move to %s: %s" % (target, e.strerror), tid)
        self.save_resume(h)
        self.save_meta()
        if not was_downloading:
            return
        name = st.name or m.get("name") or tid
        self.event("info", "finished", "Finished downloading %s" % name, tid, name)
        self._notify("Download finished", name)
        self._run_hook("automation.on_complete", tid)

    def _on_metadata(self, h):
        tid = self._tid_of(h)
        if not tid:
            return
        ti = h.torrent_file()
        with self.lock:
            m = self.meta.setdefault(tid, {})
            if ti is not None:
                m["name"] = ti.name()
        if ti is not None:
            try:
                data = lt.bencode(lt.create_torrent(ti).generate())
                util.write_atomic(self._torrent_path(tid), data)
                self._copy_torrent_file(ti.name(), data)
            except Exception as e:
                log.warning("could not store the metadata of %s: %s", tid, e)
            if m.get("part"):
                self._apply_part_names(tid, h)
            elif self.settings["downloads.part_extension"]:
                m["part"] = True
                self._apply_part_names(tid, h)
            if m.get("first_last"):
                self._apply_first_last(h)
        if m.pop("pause_after_metadata", False):
            h.unset_flags(TF.auto_managed)
            h.pause()
            self.event("info", "metadata", "%s: metadata received - choose the files, then resume it"
                       % m.get("name", tid), tid, m.get("name"))
        self.save_meta()
        self.save_resume(h)

    # ------------------------------------------------------------------ periodic work
    def tick(self):
        """Called once a second by the daemon."""
        self._tick_n += 1
        n = self._tick_n
        s = self.settings
        try:
            down = up = 0
            for _, h in self._items():
                st = h.status(0)
                down += st.download_payload_rate
                up += st.upload_payload_rate
            self.history.append((time.time(), down, up))
        except Exception:
            pass
        if n % 2 == 0:
            self.ses.post_session_stats()
            if s["net.interface"]:
                self._check_interface()
        mode = self._schedule_mode()
        if mode != self.sched_mode:
            self.sched_mode = mode
            if self.alt_manual is not None:
                self.alt_manual = None
                self.state.set("alt_manual", None)
            self.event("info", "schedule", "Schedule: %s" % {
                scheduler.NORMAL: "full speed", scheduler.SLOW: "slow mode", scheduler.PAUSED: "paused"}[mode])
            self._apply_pack()
        self._apply_pause()
        if n % 5 == 0:
            if s["net.port_mode"] == "file" and s["net.port_file"]:
                try:
                    mt = os.stat(s["net.port_file"]).st_mtime
                except OSError:
                    mt = None
                if mt != self._port_file_mtime:
                    p = self._read_port_file()
                    if p:
                        self._set_port(p, "port file")
            if s["watch.enabled"]:
                self._scan_watch()
        if n % 10 == 0:
            self._check_seed_limits()
        if n % 60 == 0:
            self._save_totals()
            if s["ipfilter.enabled"] and not self.ipfilter_info["loading"] and util.is_url(s["ipfilter.source"]):
                age = time.time() - (self.ipfilter_info.get("updated") or 0)
                if age > s["ipfilter.update_days"] * 86400:
                    threading.Thread(target=self._ipfilter_load, args=(True,), daemon=True).start()
        if n % 300 == 0:
            for _, h in self._items():
                try:
                    if h.need_save_resume_data():
                        self.save_resume(h)
                except Exception:
                    pass
            self._save_session_state()
            self.save_meta()

    def _scan_watch(self):
        d = self.settings["watch.path"]
        if not d or not os.path.isdir(d):
            return
        now = time.time()
        for fn in sorted(os.listdir(d)):
            low = fn.lower()
            if not low.endswith((".torrent", ".magnet")):
                continue
            p = os.path.join(d, fn)
            try:
                if now - os.stat(p).st_mtime < 2:
                    continue        # still being written
                opts = {"label": self.settings["watch.label"]}
                if low.endswith(".magnet"):
                    with open(p) as fh:
                        uri = fh.read().strip()
                    self.add(magnet=uri, options=opts)
                else:
                    self.add(path=p, options=opts)
                if self.settings["watch.after"] == "delete":
                    os.unlink(p)
                else:
                    os.replace(p, p + ".added")
            except (EngineError, OSError) as e:
                self.event("error", "watch", "Watch folder: could not add %s: %s" % (fn, e))
                try:
                    os.replace(p, p + ".invalid")
                except OSError:
                    pass

    def _check_seed_limits(self):
        s = self.settings
        for tid, h in self._items():
            try:
                st = h.status(0)
            except Exception:
                continue
            if not st.is_finished or st.paused or st.state in (STATES.checking_files, STATES.checking_resume_data):
                continue
            with self.lock:
                m = self.meta.get(tid, {})
            ratio_limit = m.get("ratio_limit", s["seed.ratio"] if s["seed.ratio_enabled"] else None)
            time_limit = m.get("seed_time_limit", s["seed.time"] if s["seed.time_enabled"] else None)
            reason = None
            denom = max(st.all_time_download, st.total_wanted_done)
            ratio = st.all_time_upload / denom if denom > 0 else 0
            if ratio_limit is not None and ratio_limit >= 0 and ratio >= ratio_limit:
                reason = "share ratio %.2f reached" % ratio
            seeding = st.seeding_duration.total_seconds()
            if not reason and time_limit is not None and time_limit > 0 and seeding >= time_limit * 60:
                reason = "seeded for %s" % util.fmt_duration(seeding, True)
            if not reason and s["seed.idle_enabled"]:
                idle = st.time_since_upload if st.time_since_upload >= 0 else seeding
                if idle >= s["seed.idle"] * 60 and seeding >= s["seed.idle"] * 60:
                    reason = "no uploads for %s" % util.fmt_duration(idle, True)
            if not reason:
                continue
            name = m.get("name") or tid
            action = s["seed.action"]
            if action == "pause":
                self.pause([tid])
                with self.lock:
                    m["limit_reached"] = True
                self.event("info", "limit", "%s: %s, paused" % (name, reason), tid, name)
            else:
                self.remove([tid], delete_data=(action == "remove_data"))
                self.event("info", "limit", "%s: %s, removed" % (name, reason), tid, name)
