"""Cascade's test-suite.

    python3 -m unittest tests.test_cascade -v

The unit tests need nothing but Python.  The integration tests run real daemons (libtorrent's Python bindings
required) in temporary homes and move a real torrent between two of them over 127.0.0.1; the GUI smoke test also
needs GTK 4, libadwaita and a display (CI runs it under xvfb-run).
"""

import base64
import gzip
import io
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, ROOT)

from cascade import ipc, ipfilter, natpmp, scheduler, schema, settings, util  # noqa: E402

try:
    import libtorrent as lt  # noqa: F401
    HAVE_LT = True
except ImportError:
    HAVE_LT = False


def have_gtk():
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw  # noqa: F401
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    except (ImportError, ValueError):
        return False


NOPROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# ====================================================================== unit tests
class UtilTest(unittest.TestCase):
    def test_sizes(self):
        self.assertEqual(util.fmt_size(0), "0 B")
        self.assertEqual(util.fmt_size(999), "999 B")
        self.assertEqual(util.fmt_size(1500), "1.5 kB")
        self.assertEqual(util.fmt_size(3000000), "3.0 MB")
        self.assertEqual(util.fmt_size(-1), "-")
        self.assertEqual(util.fmt_speed(0), "0 B/s")
        self.assertEqual(util.fmt_speed(2_100_000), "2.1 MB/s")

    def test_durations(self):
        self.assertEqual(util.fmt_duration(45), "45 s")
        self.assertEqual(util.fmt_duration(125), "2 min 5 s")
        self.assertEqual(util.fmt_duration(125, True), "2 min")
        self.assertEqual(util.fmt_duration(3 * 3600 + 60), "3 h 1 min")
        self.assertEqual(util.fmt_duration(-1), "∞")
        self.assertEqual(util.fmt_ratio(-1), "∞")
        self.assertEqual(util.fmt_ratio(1.234), "1.23")

    def test_detection(self):
        self.assertTrue(util.is_magnet("magnet:?xt=urn:btih:abc"))
        self.assertFalse(util.is_magnet("http://x"))
        self.assertTrue(util.is_url("https://example.org/a.torrent"))

    def test_atomic_json(self):
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "sub", "x.json")
            util.write_json(p, {"a": 1})
            self.assertEqual(util.read_json(p), {"a": 1})
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
            self.assertEqual(util.read_json(os.path.join(d, "missing"), "dflt"), "dflt")
        finally:
            shutil.rmtree(d)


class SchemaTest(unittest.TestCase):
    def test_every_setting_has_a_valid_default(self):
        for key, spec in schema.SPECS.items():
            v = schema.default(key)
            if spec["type"] in ("secret",):
                continue
            self.assertEqual(settings.coerce(spec, v), v, key)

    def test_depends_point_at_real_settings(self):
        for p in schema.PAGES:
            for g in p["groups"]:
                for r in g["rows"]:
                    if r.get("depends"):
                        self.assertIn(r["depends"]["key"], schema.SPECS, r)

    def test_public_schema_is_json(self):
        json.dumps(schema.public_schema())

    def test_depends_logic(self):
        self.assertTrue(schema.depends_ok(None, {}))
        self.assertTrue(schema.depends_ok({"key": "a"}, {"a": True}))
        self.assertFalse(schema.depends_ok({"key": "a", "in": ["x"]}, {"a": "y"}))
        self.assertTrue(schema.depends_ok({"key": "a", "not": ""}, {"a": "tun0"}))

    def test_keys_unique(self):
        keys = [s["key"] for s in schema.all_settings()]
        self.assertEqual(len(keys), len(set(keys)))


class SettingsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "settings.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_defaults_and_random_port_persist(self):
        s = settings.Settings(self.path)
        port = s["net.port"]
        self.assertTrue(49152 <= port <= 65000)
        self.assertEqual(settings.Settings(self.path)["net.port"], port)

    def test_update_validates(self):
        s = settings.Settings(self.path)
        seen = []
        s.listeners.append(seen.append)
        changed, errors = s.update({"speed.download": "512", "net.dht": "off", "net.port": 80, "nope": 1,
                                    "net.port_mode": "bogus", "net.interface": "tun0"})
        self.assertEqual(sorted(changed), ["net.dht", "net.interface", "speed.download"])
        self.assertIn("net.port", errors)
        self.assertIn("nope", errors)
        self.assertIn("net.port_mode", errors)
        self.assertEqual(s["speed.download"], 512)
        self.assertIs(s["net.dht"], False)
        self.assertEqual(seen, [changed])
        self.assertEqual(s.rev, 1)
        self.assertEqual(settings.Settings(self.path)["net.interface"], "tun0")

    def test_bad_interface_and_schedule(self):
        s = settings.Settings(self.path)
        _, errors = s.update({"net.interface": "tun0; rm -rf /", "sched.grid": "3" * 168})
        self.assertIn("net.interface", errors)
        self.assertIn("sched.grid", errors)

    def test_secret_is_hashed_and_masked(self):
        s = settings.Settings(self.path)
        s.update({"webui.password": "hunter2", "proxy.password": "pw"})
        self.assertTrue(s["webui.password"].startswith("pbkdf2_sha256$"))
        self.assertTrue(s.check_password("webui.password", "hunter2"))
        self.assertFalse(s.check_password("webui.password", "wrong"))
        snap = s.snapshot()
        self.assertEqual(snap["webui.password"], schema.MASK)
        self.assertEqual(snap["proxy.password"], schema.MASK)
        changed, _ = s.update({"webui.password": schema.MASK})      # the masked value coming back changes nothing
        self.assertEqual(changed, [])
        with open(self.path) as fh:
            self.assertNotIn("hunter2", fh.read())

    def test_lines_and_reset(self):
        s = settings.Settings(self.path)
        s.update({"add.extra_trackers": "udp://a:1\n\n udp://b:2 ", "speed.upload": 9})
        self.assertEqual(s["add.extra_trackers"], ["udp://a:1", "udp://b:2"])
        port = s["net.port"]
        s.reset()
        self.assertEqual(s["speed.upload"], 0)
        self.assertEqual(s["net.port"], port)       # the generated port survives a reset


class IpFilterTest(unittest.TestCase):
    def test_formats(self):
        text = "\n".join([
            "# comment",
            "Some Org:1.2.3.0-1.2.3.255",
            "Org: with: colons:5.6.7.8-5.6.7.9",
            "001.002.004.000 - 001.002.004.255 , 000 , DAT entry",
            "009.009.009.000 - 009.009.009.255 , 200 , allowed",
            "10.0.0.0/8",
            "8.8.8.8",
            "2001:db8::1-2001:db8::ff",
            "garbage line",
        ])
        ranges, bad = ipfilter.parse(text)
        self.assertIn(("1.2.3.0", "1.2.3.255"), ranges)
        self.assertIn(("5.6.7.8", "5.6.7.9"), ranges)
        self.assertIn(("1.2.4.0", "1.2.4.255"), ranges)
        self.assertNotIn(("9.9.9.0", "9.9.9.255"), ranges)
        self.assertIn(("10.0.0.0", "10.255.255.255"), ranges)
        self.assertIn(("8.8.8.8", "8.8.8.8"), ranges)
        self.assertIn(("2001:db8::1", "2001:db8::ff"), ranges)
        self.assertEqual(bad, 1)

    def test_compressed(self):
        raw = b"x:1.1.1.1-1.1.1.2\n"
        self.assertEqual(ipfilter.decode(gzip.compress(raw)), raw.decode())
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("list.p2p", raw)
        self.assertEqual(ipfilter.decode(buf.getvalue()), raw.decode())


class SchedulerTest(unittest.TestCase):
    def test_modes(self):
        g = scheduler.set_range("0" * 168, range(0, 5), range(9, 17), scheduler.SLOW)
        g = scheduler.set_range(g, [5, 6], range(0, 6), scheduler.PAUSED)
        self.assertEqual(g.count("1"), 5 * 8)
        self.assertEqual(g.count("2"), 2 * 6)
        # 2026-10-12 is a Monday
        t = time.mktime((2026, 10, 12, 10, 30, 0, 0, 0, -1))
        self.assertEqual(scheduler.mode_at(g, t), scheduler.SLOW)
        secs, mode = scheduler.next_change(g, t)
        self.assertEqual(mode, scheduler.NORMAL)
        self.assertEqual(secs, 6 * 3600 + 30 * 60)
        self.assertEqual(scheduler.next_change("0" * 168, t), (None, None))
        self.assertIn("09:00-17:00 slow", scheduler.describe(g)[0])


class NatPmpTest(unittest.TestCase):
    def test_forward_against_fake_gateway(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            srv.bind(("127.0.0.1", natpmp.PORT))
        except OSError:
            self.skipTest("port 5351 is busy")
        stop = threading.Event()

        def serve():
            srv.settimeout(0.2)
            while not stop.is_set():
                try:
                    data, addr = srv.recvfrom(64)
                except socket.timeout:
                    continue
                op = data[1]
                if op == 0:
                    srv.sendto(struct.pack("!BBHI4s", 0, 128, 0, 1, socket.inet_aton("203.0.113.7")), addr)
                else:
                    _v, _op, _r, priv, _pub, life = struct.unpack("!BBHHHI", data[:12])
                    srv.sendto(struct.pack("!BBHIHHI", 0, 128 + op, 0, 1, priv, 41234, life), addr)

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        try:
            self.assertEqual(natpmp.forward("127.0.0.1"), 41234)
            self.assertEqual(natpmp.external_address("127.0.0.1"), "203.0.113.7")
        finally:
            stop.set()
            t.join(2)
            srv.close()

    def test_no_answer(self):
        with self.assertRaises(natpmp.NatPmpError):
            natpmp._request("127.0.0.1", b"\0\0", 0, tries=1, first_timeout=0.1)


class CliParserTest(unittest.TestCase):
    def test_parser_and_schedule_specs(self):
        from cascade import cli
        p = cli.build_parser()
        a = p.parse_args(["add", "x.torrent", "-p", "/tmp", "--paused", "-l", "linux"])
        self.assertEqual((a.sources, a.save_path, a.paused, a.label), (["x.torrent"], "/tmp", True, "linux"))
        self.assertEqual(cli.parse_days("mon-fri"), [0, 1, 2, 3, 4])
        self.assertEqual(cli.parse_days("sat,sun"), [5, 6])
        self.assertEqual(cli.parse_days("fri-mon"), [0, 4, 5, 6])
        self.assertEqual(cli.parse_hours("9-17"), list(range(9, 17)))
        self.assertEqual(cli.parse_hours("22-6"), [0, 1, 2, 3, 4, 5, 22, 23])


# ====================================================================== integration tests
class Daemon:
    """A real cascade daemon in a temporary home."""

    def __init__(self, base, name):
        self.home = os.path.join(base, name)
        os.makedirs(self.home)
        self.env = dict(os.environ, CASCADE_HOME=self.home, HOME=self.home, PYTHONPATH=ROOT,
                        PYTHONDONTWRITEBYTECODE="1")
        self.env.pop("CASCADE_URL", None)
        self.env.pop("XDG_CONFIG_HOME", None)
        self.socket = os.path.join(self.home, "run", "cascade.sock")
        self.proc = None
        self.client = None

    def start(self, settings_=None):
        if settings_:
            os.makedirs(os.path.join(self.home, "config"), exist_ok=True)
            with open(os.path.join(self.home, "config", "settings.json"), "w") as fh:
                json.dump(settings_, fh)
        self.proc = subprocess.Popen([sys.executable, "-m", "cascade", "daemon", "--quiet"], env=self.env, cwd=ROOT,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.client = ipc.Client(self.socket, timeout=30)
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.client.alive():
                return self
            if self.proc.poll() is not None:
                raise RuntimeError("daemon died: %s" % self.proc.stderr.read().decode())
            time.sleep(0.1)
        raise RuntimeError("daemon did not start")

    def call(self, method, **params):
        return self.client.call(method, **params)

    def cli(self, *args):
        r = subprocess.run([sys.executable, "-m", "cascade", "--no-start"] + list(args), env=self.env, cwd=ROOT,
                           capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout, r.stderr

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.call("daemon.shutdown")
            except (ipc.DaemonUnavailable, ipc.RpcError):
                pass
            try:
                self.proc.wait(30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.client:
            self.client.close()
        if self.proc and self.proc.stderr:
            self.proc.stderr.close()


BASE_SETTINGS = {"net.dht": False, "net.lsd": False, "net.upnp": False, "net.ipv6": False}


def wait_for(fn, timeout=30, step=0.25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


@unittest.skipUnless(HAVE_LT, "libtorrent's Python bindings are not installed")
class DaemonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="cascade-test-")
        cls.content = os.path.join(cls.base, "content")
        os.makedirs(os.path.join(cls.content, "album", "cd2"))
        for rel, size in (("album/a.flac", 700_000), ("album/cd2/b.flac", 500_000), ("album/cover.jpg", 30_000)):
            with open(os.path.join(cls.content, rel), "wb") as fh:
                fh.write(os.urandom(size))
        cls.seeder = Daemon(cls.base, "seeder").start(dict(BASE_SETTINGS, **{
            "downloads.save_path": os.path.join(cls.base, "seeder-dl")}))
        cls.leecher = Daemon(cls.base, "leecher").start(dict(BASE_SETTINGS, **{
            "downloads.save_path": os.path.join(cls.base, "leecher-done"),
            "downloads.use_incomplete": True,
            "downloads.incomplete_path": os.path.join(cls.base, "leecher-inc"),
            "downloads.part_extension": True}))

    @classmethod
    def tearDownClass(cls):
        cls.seeder.stop()
        cls.leecher.stop()
        shutil.rmtree(cls.base, ignore_errors=True)

    def _create(self):
        job = self.seeder.call("create.start", path=os.path.join(self.content, "album"),
                               trackers=["http://127.0.0.1:9/announce"], comment="test album")
        st = wait_for(lambda: (lambda s: s if s["state"] != "hashing" else None)(
            self.seeder.call("create.status", job=job)), 30)
        self.assertEqual(st["state"], "done", st)
        return st["result"]

    def test_1_transfer_between_two_daemons(self):
        res = self._create()
        data = res["data"]
        # the seeder adds it with the data in place
        r = self.seeder.call("torrents.add", data=data, options={"save_path": self.content, "skip_check": True})
        self.assertFalse(r["duplicate"])
        tid = r["id"]
        info = self.leecher.call("torrents.inspect", data=data)
        self.assertEqual(info["name"], "album")
        self.assertEqual(sorted(f["path"] for f in info["files"]),
                         ["album/a.flac", "album/cd2/b.flac", "album/cover.jpg"])
        # skip the cover: priorities by file index
        prios = [0 if f["path"].endswith("cover.jpg") else 4 for f in sorted(info["files"], key=lambda f: f["index"])]
        full = [4] * (max(f["index"] for f in info["files"]) + 1)
        for f, p in zip(sorted(info["files"], key=lambda f: f["index"]), prios):
            full[f["index"]] = p
        self.leecher.call("torrents.add", data=data, options={"file_priorities": full, "label": "music"})
        port = self.seeder.call("session.status")["port"]
        self.leecher.call("peers.add", id=tid, address="127.0.0.1:%d" % port)

        def done():
            lst = self.leecher.call("torrents.list")["torrents"]
            t = [x for x in lst if x["id"] == tid][0]
            return t if t["finished"] and not t["move_to"] else None

        t = wait_for(done, 60)
        self.assertIsNotNone(t, self.leecher.call("torrents.list"))
        self.assertEqual(t["label"], "music")
        # moved from the incomplete folder to the download folder, .part names removed
        final = os.path.join(self.base, "leecher-done", "album")
        self.assertTrue(wait_for(lambda: os.path.exists(os.path.join(final, "a.flac")), 15))
        for rel in ("a.flac", "cd2/b.flac"):
            with open(os.path.join(final, rel), "rb") as a, open(os.path.join(self.content, "album", rel), "rb") as b:
                self.assertEqual(a.read(), b.read(), rel)
        self.assertFalse(os.path.exists(os.path.join(final, "cover.jpg")))
        names = [f for _r, _d, fs in os.walk(final) for f in fs]
        self.assertFalse([n for n in names if n.endswith(".part") and "cover" not in n], names)
        d = self.leecher.call("torrents.details", id=tid)
        self.assertEqual(d["info"]["comment"], "test album")
        self.assertEqual({f["path"]: f["priority"] for f in d["files"]}["album/cover.jpg"], 0)
        self.assertEqual(d["trackers"][0]["url"], "http://127.0.0.1:9/announce")
        self.assertIn("magnet:?", d["info"]["magnet"])
        ev = self.leecher.call("events.poll", since=0)
        self.assertTrue(any(e["kind"] == "finished" for e in ev), ev)
        # the seeder saw an upload
        st = wait_for(lambda: self.seeder.call("torrents.list")["torrents"][0]["uploaded"] > 0, 10)
        self.assertTrue(st)

    def test_2_actions_and_options(self):
        lst = self.leecher.call("torrents.list")["torrents"]
        if not lst:
            self.skipTest("needs the transfer test")
        tid = lst[0]["id"]
        self.leecher.call("torrents.pause", ids=[tid])
        t = self.leecher.call("torrents.list")["torrents"][0]
        self.assertEqual(t["state"], "paused")
        self.leecher.call("torrents.resume", ids="all")
        self.leecher.call("torrents.set_options", ids=[tid], options={"download_limit": 100, "sequential": True,
                                                                      "ratio_limit": 3.5, "max_connections": 20})
        o = self.leecher.call("torrents.details", id=tid, sections=["options"])["options"]
        self.assertEqual((o["download_limit"], o["sequential"], o["ratio_limit"], o["max_connections"]),
                         (100, True, 3.5, 20))
        self.leecher.call("trackers.add", id=tid, urls=["udp://127.0.0.1:10/announce"])
        urls = [x["url"] for x in self.leecher.call("torrents.details", id=tid, sections=["trackers"])["trackers"]]
        self.assertIn("udp://127.0.0.1:10/announce", urls)
        self.leecher.call("trackers.remove", id=tid, urls=["udp://127.0.0.1:10/announce"])
        with self.assertRaises(ipc.RpcError):
            self.leecher.call("torrents.pause", ids=["doesnotexist"])
        # prefix and name resolution
        self.leecher.call("torrents.pause", ids=[tid[:8]])
        self.leecher.call("torrents.resume", ids=["albu"])

    def test_3_magnet_and_settings(self):
        mag = "magnet:?xt=urn:btih:0123456789abcdef0123456789abcdef01234567&dn=Some+Thing&tr=udp%3A%2F%2Fx%3A1"
        info = self.seeder.call("torrents.inspect", magnet=mag)
        self.assertEqual((info["name"], info["has_metadata"]), ("Some Thing", False))
        r = self.seeder.call("torrents.add", magnet=mag, options={"paused": True})
        self.assertEqual(self.seeder.call("torrents.add", magnet=mag)["duplicate"], True)
        st = [t for t in self.seeder.call("torrents.list")["torrents"] if t["id"] == r["id"]][0]
        self.assertEqual(st["state"], "paused")
        with self.assertRaises(ipc.RpcError):
            self.seeder.call("torrents.add", magnet="magnet:?xt=bogus")
        with self.assertRaises(ipc.RpcError):
            self.seeder.call("torrents.add", data=base64.b64encode(b"not a torrent").decode())
        res = self.seeder.call("settings.set", values={"alt.download": 77, "speed.download": -5})
        self.assertEqual(res["changed"], ["alt.download"])
        self.assertIn("speed.download", res["errors"])
        self.assertTrue(self.seeder.call("alt_speed.set", on=True))
        self.assertTrue(self.seeder.call("session.status")["alt_speed"])
        self.assertFalse(self.seeder.call("alt_speed.set", on="toggle"))
        p = self.seeder.call("port.randomize")
        self.assertEqual(self.seeder.call("session.status")["port"], p)
        sch = self.seeder.call("settings.schema")
        self.assertEqual([p_["id"] for p_ in sch["pages"]],
                         ["general", "downloads", "connection", "speed", "queue", "remote", "advanced"])
        self.seeder.call("labels.set", name="linux", path=os.path.join(self.base, "isos"))
        self.assertIn("linux", self.seeder.call("labels.list"))
        self.seeder.call("torrents.remove", ids=[r["id"]])
        self.assertNotIn(r["id"], [t["id"] for t in self.seeder.call("torrents.list")["torrents"]])

    def test_4_interface_killswitch(self):
        d = self.seeder
        d.call("settings.set", values={"net.interface": "cascade-nonexistent0"})
        st = wait_for(lambda: (lambda s: s if "killswitch" in s["paused"] else None)(d.call("session.status")), 10)
        self.assertIsNotNone(st, "the kill switch did not pause the session")
        self.assertIn("does not exist", st["info"]["interface"])
        d.call("settings.set", values={"net.killswitch": False})
        self.assertTrue(wait_for(lambda: not d.call("session.status")["paused"], 10))
        d.call("settings.set", values={"net.interface": "lo", "net.killswitch": True})
        st = wait_for(lambda: (lambda s: s if not s["paused"] else None)(d.call("session.status")), 10)
        self.assertIsNotNone(st)
        self.assertTrue(st["interface"]["usable"])
        d.call("settings.set", values={"net.interface": ""})

    def test_5_cli(self):
        rc, out, err = self.leecher.cli("--json", "list")
        self.assertEqual(rc, 0, err)
        lst = json.loads(out)
        self.assertEqual(lst[0]["name"], "album")
        rc, out, err = self.leecher.cli("info", "1")
        self.assertEqual(rc, 0, err)
        self.assertIn("Info hash", out)
        rc, out, _ = self.leecher.cli("files", "1")
        self.assertIn("cover.jpg", out)
        rc, out, _ = self.leecher.cli("set", "queue.max_downloads", "7", "net.port", "12")
        self.assertEqual(rc, 1)
        self.assertIn("queue.max_downloads", out)
        rc, out, _ = self.leecher.cli("get", "queue.max_downloads")
        self.assertEqual(out.strip(), "7")
        rc, out, _ = self.leecher.cli("schedule", "set", "sat,sun", "0-24", "slow")
        self.assertEqual(rc, 0)
        self.assertIn("Saturday  00:00-24:00 slow", out)
        rc, out, _ = self.leecher.cli("config", "speed")
        self.assertIn("speed.download", out)
        rc, out, err = self.leecher.cli("pause", "99")
        self.assertEqual(rc, 2)
        rc, _out, _err = self.leecher.cli("status")
        self.assertEqual(rc, 0)

    def test_6_webui(self):
        d = self.seeder
        port = free_port()
        r = d.call("settings.set", values={"webui.enabled": True, "webui.port": port, "webui.host": "127.0.0.1"})
        pw = r["notices"].get("webui_password")
        self.assertTrue(pw)
        base = "http://127.0.0.1:%d" % port
        self.assertTrue(wait_for(lambda: d.call("webui.status")["running"], 10))

        def post(path, body, token=None, headers=None):
            h = {"Content-Type": "application/json"}
            if token:
                h["Authorization"] = "Bearer " + token
            h.update(headers or {})
            req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers=h)
            try:
                with NOPROXY.open(req, timeout=10) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read())

        with NOPROXY.open(base + "/", timeout=10) as resp:
            html = resp.read().decode()
            self.assertIn("app.js", html)
            self.assertIn("default-src 'self'", resp.headers["Content-Security-Policy"])
        self.assertEqual(post("/api/rpc", {"method": "ping"})[0], 401)
        self.assertEqual(post("/api/login", {"password": "nope"})[0], 401)
        code, res = post("/api/login", {"password": pw})
        self.assertEqual(code, 200)
        tok = res["result"]["token"]
        code, res = post("/api/rpc", {"method": "sync", "params": {}}, tok)
        self.assertTrue(res["ok"])
        self.assertIn("torrents", res["result"])
        code, res = post("/api/rpc", {"method": "daemon.shutdown"}, tok)
        self.assertFalse(res["ok"])
        self.assertEqual(post("/api/rpc", {"method": "ping"}, tok, {"Origin": "http://evil.example"})[0], 403)
        # the CLI through the web interface
        rc, out, err = self.seeder.cli("--url", base, "--password", pw, "--json", "status")
        self.assertEqual(rc, 0, err)
        self.assertIn("port", json.loads(out))
        # password-free local access only when asked for, and never for a foreign Host header (DNS rebinding)
        d.call("settings.set", values={"webui.local_no_auth": True})
        self.assertEqual(post("/api/rpc", {"method": "ping"})[0], 200)
        self.assertEqual(post("/api/rpc", {"method": "ping"}, headers={"Host": "evil.example:%d" % port})[0], 401)
        d.call("settings.set", values={"webui.local_no_auth": False, "webui.password": "changed"})
        self.assertEqual(post("/api/rpc", {"method": "ping"}, tok)[0], 401)     # a new password signs everyone out
        d.call("settings.set", values={"webui.enabled": False})
        self.assertTrue(wait_for(lambda: not d.call("webui.status")["running"], 10))

    def test_7_persistence(self):
        before = {t["id"]: t for t in self.leecher.call("torrents.list")["torrents"]}
        self.leecher.stop()
        self.leecher.start()
        after = {t["id"]: t for t in self.leecher.call("torrents.list")["torrents"]}
        self.assertEqual(set(before), set(after))
        for tid, t in after.items():
            self.assertEqual(t["label"], before[tid]["label"])
            self.assertEqual(t["sequential"], before[tid]["sequential"])
        self.assertEqual(self.leecher.call("settings.get", keys=["queue.max_downloads"])["queue.max_downloads"], 7)


@unittest.skipUnless(HAVE_LT and have_gtk(), "needs libtorrent, GTK 4, libadwaita and a display")
class GuiSmokeTest(unittest.TestCase):
    def test_window_starts_and_connects(self):
        base = tempfile.mkdtemp(prefix="cascade-gui-")
        d = Daemon(base, "gui").start(BASE_SETTINGS)
        try:
            d.call("torrents.add", magnet="magnet:?xt=urn:btih:%s&dn=GUI+test" % ("ab" * 20), options={"paused": True})
            env = dict(d.env, GSK_RENDERER="cairo", GTK_A11Y="none", NO_AT_BRIDGE="1")
            cmd = [sys.executable, "-m", "cascade", "gui"]
            if shutil.which("dbus-run-session"):
                cmd = ["dbus-run-session", "--"] + cmd
            logf = os.path.join(base, "gui.log")
            with open(logf, "wb") as out:
                # its own process group: dbus-run-session starts a bus daemon that must go away with it
                p = subprocess.Popen(cmd, env=env, cwd=ROOT, stdout=out, stderr=subprocess.STDOUT,
                                     start_new_session=True)
                time.sleep(8)
                alive = p.poll() is None
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    p.wait(10)
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait(5)
            with open(logf, encoding="utf-8", errors="replace") as fh:
                log = fh.read()
            self.assertTrue(alive, log)
            self.assertNotIn("Traceback", log, log)
            self.assertTrue(d.client.alive())
        finally:
            d.stop()
            shutil.rmtree(base, ignore_errors=True)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


if __name__ == "__main__":
    unittest.main()
