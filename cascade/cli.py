"""cascade: the command line.  It talks to the same service as the GTK app and the web interface, so everything you
do here shows up there instantly (and the other way round).

Torrents can be named by their position in `cascade list` (1, 2, ...), a unique id prefix (4+ hex digits) or a
unique part of their name.  Add --json to any command for machine-readable output.
"""

import argparse
import base64
import getpass
import json
import os
import shutil
import subprocess
import sys
import time

from . import APP_NAME, __version__, ipc, scheduler, schema, util

PRIOS = {"skip": 0, "off": 0, "low": 1, "normal": 4, "high": 7}
PRIO_NAMES = {0: "skip", 1: "low", 2: "low", 3: "low", 4: "normal", 5: "high", 6: "high", 7: "high"}
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MODES = {"normal": "0", "full": "0", "fast": "0", "slow": "1", "pause": "2", "paused": "2"}

C = {"reset": "\033[0m", "bold": "\033[1m", "dim": "\033[2m", "red": "\033[31m", "green": "\033[32m",
     "yellow": "\033[33m", "blue": "\033[34m", "cyan": "\033[36m"}
STATE_COLOR = {"downloading": "blue", "seeding": "green", "paused": "dim", "queued": "dim", "checking": "yellow",
               "metadata": "cyan", "error": "red", "moving": "yellow"}


class CliError(Exception):
    pass


def color(text, *names):
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return "".join(C[n] for n in names) + text + C["reset"]


def width():
    return shutil.get_terminal_size((100, 24)).columns


def trunc(s, n):
    return s if len(s) <= n else s[:max(1, n - 1)] + "…"


def bar(frac, n=10):
    full = int(round(max(0.0, min(1.0, frac)) * n))
    return "█" * full + "░" * (n - full)


class Cli:
    def __init__(self, args):
        self.args = args
        self._client = None
        self._list = None

    @property
    def c(self):
        if self._client is None:
            from . import client
            self._client = client.connect(self.args.url, self.args.password, autostart=not self.args.no_start,
                                          insecure=self.args.insecure)
        return self._client

    def call(self, method, **params):
        return self.c.call(method, **params)

    def out(self, obj, text=None):
        if self.args.json:
            print(json.dumps(obj, indent=2, default=str))
        elif text is not None:
            print(text)

    # ---------------------------------------------------------------- torrent references
    def torrents(self):
        if self._list is None:
            self._list = self.call("torrents.list")["torrents"]
        return self._list

    def ref(self, r):
        r = str(r).strip().lstrip("#")
        if r.isdigit() and len(r) < 4:
            lst = self.torrents()
            i = int(r)
            if 1 <= i <= len(lst):
                return lst[i - 1]["id"]
            raise CliError("there is no torrent number %d (see: cascade list)" % i)
        return r

    def refs(self, rs):
        if not rs:
            raise CliError("name at least one torrent (a number from `cascade list`, an id or part of a name), or all")
        if len(rs) == 1 and rs[0] == "all":
            return "all"
        return [self.ref(r) for r in rs]

    # ---------------------------------------------------------------- commands
    def cmd_status(self):
        st = self.call("session.status")
        if self.args.json:
            return self.out(st)
        lines = [
            color("%s %s" % (APP_NAME, st["version"]), "bold") + color("  (libtorrent %s)" % st["libtorrent"], "dim"),
            "Speed      ↓ %s   ↑ %s%s" % (util.fmt_speed(st["down"]), util.fmt_speed(st["up"]),
                                         color("   🐢 slow mode" + (" (%s)" % st["alt_source"] if st["alt_source"]
                                                                   else ""), "yellow") if st["alt_speed"] else ""),
            "Port       %s" % st["info"]["port"],
            "Interface  %s" % st["info"]["interface"],
            "Peers      %d connected · DHT %d nodes" % (st["peers"], st["dht_nodes"]),
            "Session    ↓ %s  ↑ %s     All time ↓ %s  ↑ %s" % (
                util.fmt_size(st["session_down"]), util.fmt_size(st["session_up"]),
                util.fmt_size(st["alltime_down"]), util.fmt_size(st["alltime_up"])),
            "Free space %s in %s" % (util.fmt_size(st["free_space"]), st["save_path"]),
        ]
        if st["paused"]:
            why = {"killswitch": "the network interface is down (kill switch)", "schedule": "the schedule says pause"}
            lines.append(color("PAUSED     " + "; ".join(why.get(p, p) for p in st["paused"]), "red", "bold"))
        sch = st["schedule"]
        if sch["enabled"]:
            nxt = (" · %s in %s" % (sch["next_mode"], util.fmt_duration(sch["next_change"], True))
                   if sch["next_change"] else "")
            lines.append("Schedule   %s now%s" % (sch["mode"], nxt))
        w = st.get("webui") or {}
        if w.get("running"):
            lines.append("Web UI     " + "  ".join(w["urls"]))
        elif w.get("error"):
            lines.append(color("Web UI     " + w["error"], "red"))
        else:
            lines.append("Web UI     off (turn on: cascade webui on)")
        print("\n".join(lines))

    def _table(self, lst):
        w = width()
        name_w = max(16, w - 74)
        hdr = "%3s  %-*s %-11s %6s %9s %10s %10s %8s %6s" % ("#", name_w, "Name", "State", "Done", "Size", "Down",
                                                            "Up", "ETA", "Ratio")
        print(color(hdr, "bold"))
        for i, t in self._numbered(lst):
            state = util.STATE_LABELS.get(t["state"], t["state"])
            if t["forced"]:
                state = "Forced"
            print("%3d  %-*s %s %5.1f%% %9s %10s %10s %8s %6s" % (
                i, name_w, trunc(t["name"], name_w), color("%-11s" % trunc(state, 11), STATE_COLOR.get(t["state"],
                                                                                                      "reset")),
                t["progress"] * 100, util.fmt_size(t["size"]),
                util.fmt_speed(t["down"]) if t["down"] else "-", util.fmt_speed(t["up"]) if t["up"] else "-",
                util.fmt_duration(t["eta"], True) if t["eta"] >= 0 else "-", util.fmt_ratio(t["ratio"])))

    def _numbered(self, lst):
        order = {t["id"]: n for n, t in enumerate(self.torrents(), 1)}
        return [(order.get(t["id"], 0), t) for t in lst]

    def _filtered(self, lst):
        a = self.args
        f = getattr(a, "filter", None) or "all"
        out = []
        for t in lst:
            st = t["state"]
            ok = (f == "all" or (f == "downloading" and st in ("downloading", "metadata")) or f == st
                  or (f == "completed" and t["finished"]) or (f == "active" and (t["down"] or t["up"])))
            if getattr(a, "label", None) is not None and t["label"] != a.label:
                ok = False
            if getattr(a, "search", None) and a.search.lower() not in t["name"].lower():
                ok = False
            if ok:
                out.append(t)
        key = {"name": lambda t: t["name"].lower(), "added": lambda t: t["added"], "progress": lambda t: t["progress"],
               "size": lambda t: t["size"], "down": lambda t: t["down"], "up": lambda t: t["up"],
               "ratio": lambda t: t["ratio"], "eta": lambda t: t["eta"] if t["eta"] >= 0 else 1 << 40}
        if getattr(a, "sort", None) in key:
            out.sort(key=key[a.sort], reverse=bool(a.reverse))
        elif getattr(a, "reverse", False):
            out.reverse()
        return out

    def cmd_list(self):
        lst = self._filtered(self.torrents())
        if self.args.json:
            return self.out(lst)
        if not lst:
            print("No torrents%s. Add one with: cascade add <magnet link | file.torrent | URL>"
                  % ("" if not self.torrents() else " match"))
            return
        self._table(lst)
        down = sum(t["down"] for t in lst)
        up = sum(t["up"] for t in lst)
        print(color("%d torrent%s · ↓ %s · ↑ %s" % (len(lst), "" if len(lst) == 1 else "s", util.fmt_speed(down),
                                                    util.fmt_speed(up)), "dim"))

    def cmd_watch(self):
        try:
            while True:
                self._list = None
                data = self.call("sync")
                self._list = data["torrents"]
                st = data["session"]
                sys.stdout.write("\033[H\033[2J")
                head = "%s · ↓ %s · ↑ %s · port %d · %d peers%s%s" % (
                    APP_NAME, util.fmt_speed(st["down"]), util.fmt_speed(st["up"]), st["port"], st["peers"],
                    " · 🐢 slow" if st["alt_speed"] else "", (" · PAUSED (%s)" % ", ".join(st["paused"]))
                    if st["paused"] else "")
                print(color(head, "bold"))
                print(color(time.strftime("%H:%M:%S") + "  (Ctrl+C to quit)", "dim"))
                lst = self._filtered(self._list)
                if lst:
                    self._table(lst)
                else:
                    print("No torrents.")
                sys.stdout.flush()
                time.sleep(self.args.interval)
        except KeyboardInterrupt:
            print()

    def cmd_info(self):
        d = self.call("torrents.details", id=self.ref(self.args.torrent), sections=["info", "options"])
        if self.args.json:
            return self.out(d)
        t, i, o = d["summary"], d["info"], d["options"]
        rows = [
            ("Name", t["name"]), ("State", util.STATE_LABELS.get(t["state"], t["state"])
                                  + (" - " + t["error"] if t["error"] else "")),
            ("Progress", "%s %.1f%%  (%s of %s)" % (bar(t["progress"], 20), t["progress"] * 100,
                                                    util.fmt_size(t["done"]), util.fmt_size(t["size"]))),
            ("Speed", "↓ %s  ↑ %s" % (util.fmt_speed(t["down"]), util.fmt_speed(t["up"]))),
            ("Time left", util.fmt_duration(t["eta"]) if t["eta"] >= 0 else "-"),
            ("Downloaded", util.fmt_size(t["downloaded"])), ("Uploaded", util.fmt_size(t["uploaded"])),
            ("Ratio", util.fmt_ratio(t["ratio"])),
            ("Seeds", "%d connected (%d in swarm)" % (t["seeds"], max(0, t["seeds_total"]))),
            ("Peers", "%d connected (%d in swarm)" % (t["peers"], max(0, t["peers_total"]))),
            ("Availability", "%.2f" % i["availability"] if i["availability"] >= 0 else "-"),
            ("Active time", util.fmt_duration(i["active_time"])),
            ("Seeding time", util.fmt_duration(i["seeding_time"])),
            ("Info hash v1", i["hash_v1"] or "-"), ("Info hash v2", i["hash_v2"] or "-"),
            ("Size", util.fmt_size(i["total_size"]) if i["total_size"] >= 0 else "unknown (no metadata yet)"),
            ("Pieces", "%d × %s" % (i["pieces"], util.fmt_size(i["piece_size"])) if i["pieces"] else "-"),
            ("Files", str(i["num_files"])), ("Save path", i["save_path"]),
            ("Label", i["label"] or "-"), ("Queue", str(t["queue"]) if t["queue"] >= 0 else "-"),
            ("Added", util.fmt_time(i["added"])), ("Completed", util.fmt_time(i["completed"])),
            ("Created", util.fmt_time(i["created"]) + (" by " + i["creator"] if i["creator"] else "")),
            ("Comment", i["comment"] or "-"), ("Private", "yes" if i["private"] else "no"),
            ("Wasted", util.fmt_size(i["wasted"])), ("Tracker", i["current_tracker"] or "-"),
            ("Limits", "↓ %s  ↑ %s  connections %s  slots %s" % (
                ("%d KiB/s" % o["download_limit"]) if o["download_limit"] else "∞",
                ("%d KiB/s" % o["upload_limit"]) if o["upload_limit"] else "∞",
                o["max_connections"] or "default", o["max_uploads"] or "default")),
            ("Options", ", ".join(k for k in ("sequential", "first_last", "super_seeding") if o[k]) or "-"),
            ("Magnet", i["magnet"]),
        ]
        for k, v in rows:
            print("%s %s" % (color("%-13s" % k, "bold"), v))

    def cmd_files(self):
        tid = self.ref(self.args.torrent)
        if self.args.set:
            pr = {}
            for item in self.args.set:
                if "=" not in item:
                    raise CliError("use INDEX=PRIORITY, e.g. 3=skip or 1-5=high")
                idx, p = item.split("=", 1)
                p = PRIOS.get(p.lower(), None) if not p.isdigit() else int(p)
                if p is None:
                    raise CliError("priority must be skip, low, normal, high or 0-7")
                if "-" in idx:
                    a, b = idx.split("-", 1)
                    for n in range(int(a), int(b) + 1):
                        pr[n - 1] = p
                elif idx == "all":
                    for f in self.call("torrents.details", id=tid, sections=["files"])["files"]:
                        pr[f["index"]] = p
                else:
                    pr[int(idx) - 1] = p
            self.call("torrents.set_files", id=tid, priorities={str(k): v for k, v in pr.items()})
        files = self.call("torrents.details", id=tid, sections=["files"])["files"]
        if self.args.json:
            return self.out(files)
        if not files:
            print("The file list is not known yet (the metadata is still downloading).")
            return
        name_w = max(20, width() - 40)
        for f in files:
            frac = f["done"] / f["size"] if f["size"] else 1
            print("%4d  %-*s %9s %6.1f%%  %s" % (f["index"] + 1, name_w, trunc(f["path"], name_w),
                                                util.fmt_size(f["size"]), frac * 100,
                                                color(PRIO_NAMES.get(f["priority"], str(f["priority"])),
                                                      "dim" if f["priority"] == 0 else "reset")))

    def cmd_peers(self):
        tid = self.ref(self.args.torrent)
        for addr in self.args.add or []:
            self.call("peers.add", id=tid, address=addr)
        peers = self.call("torrents.details", id=tid, sections=["peers"])["peers"]
        if self.args.json:
            return self.out(peers)
        if not peers:
            print("No peers connected.")
            return
        print(color("%-40s %-24s %-7s %6s %10s %10s" % ("Address", "Client", "Flags", "Done", "Down", "Up"), "bold"))
        for p in peers:
            addr = ("[%s]:%d" if ":" in p["ip"] else "%s:%d") % (p["ip"], p["port"])
            print("%-40s %-24s %-7s %5.1f%% %10s %10s" % (addr, trunc(p["client"], 24), p["flags"],
                                                          p["progress"] * 100, util.fmt_speed(p["down"]),
                                                          util.fmt_speed(p["up"])))

    def cmd_trackers(self):
        tid = self.ref(self.args.torrent)
        if self.args.add:
            self.call("trackers.add", id=tid, urls=self.args.add)
        if self.args.remove:
            self.call("trackers.remove", id=tid, urls=self.args.remove)
        d = self.call("torrents.details", id=tid, sections=["trackers"])
        if self.args.json:
            return self.out(d["trackers"])
        for t in d["trackers"]:
            sc = " · %s seeds, %s peers" % (t["seeds"] if t["seeds"] >= 0 else "?",
                                           t["peers"] if t["peers"] >= 0 else "?")
            print("%s %s  %s%s%s" % (color("[tier %d]" % t["tier"], "dim"), t["url"],
                                     color(t["status"], {"working": "green", "error": "red"}.get(t["status"], "dim")),
                                     sc, (" - " + t["message"]) if t["message"] else ""))
        src = d.get("sources", {})
        print(color("DHT %s · PeX %s · LSD %s" % tuple("on" if src.get(k) else "off" for k in ("dht", "pex", "lsd")),
                    "dim"))

    def cmd_add(self):
        a = self.args
        remote = bool(a.url or os.environ.get("CASCADE_URL"))
        opts = {}
        for k in ("save_path", "label"):
            if getattr(a, k):
                opts[k] = getattr(a, k)
        if opts.get("save_path"):
            opts["save_path"] = os.path.abspath(os.path.expanduser(opts["save_path"])) if not remote else opts[
                "save_path"]
        for k in ("paused", "sequential", "first_last", "skip_check", "top_of_queue"):
            if getattr(a, k):
                opts[k] = True
        results = []
        for src in a.sources:
            kw = {}
            if util.is_magnet(src):
                kw["magnet"] = src
            elif util.is_url(src):
                kw["url"] = src
            else:
                p = os.path.abspath(os.path.expanduser(src))
                if not os.path.isfile(p):
                    raise CliError("%s: not a file, magnet link or URL" % src)
                if remote:
                    with open(p, "rb") as fh:
                        kw["data"] = base64.b64encode(fh.read()).decode()
                else:
                    kw["path"] = p
            try:
                r = self.call("torrents.add", options=opts, **kw)
            except ipc.RpcError as e:
                print(color("✗ %s: %s" % (src, e), "red"), file=sys.stderr)
                results.append({"source": src, "error": str(e)})
                continue
            results.append(r)
            if not a.json:
                print(("%s %s" % (color("•", "yellow"), "Already added: ") if r.get("duplicate") else
                       "%s Added " % color("✓", "green")) + r["name"])
        if a.json:
            self.out(results)
        if any("error" in r for r in results):
            return 1

    def _simple(self, method, msg, **extra):
        ids = self.refs(self.args.torrents)
        self.call(method, ids=ids, **extra)
        self.out(True, msg % ("all torrents" if ids == "all" else "%d torrent%s" % (len(ids), "" if len(ids) == 1
                                                                                     else "s")))

    def cmd_pause(self):
        self._simple("torrents.pause", "Paused %s")

    def cmd_resume(self):
        self._simple("torrents.resume", "Resumed %s")

    def cmd_start(self):
        self._simple("torrents.force_start", "Force-started %s (they skip the queue)")

    def cmd_recheck(self):
        self._simple("torrents.recheck", "Checking %s")

    def cmd_reannounce(self):
        self._simple("torrents.reannounce", "Announced %s")

    def cmd_remove(self):
        ids = self.refs(self.args.torrents)
        names = [t["name"] for t in self.torrents() if ids == "all" or t["id"] in ids]
        if not self.args.yes and sys.stdin.isatty():
            what = "%d torrents" % len(names) if len(names) != 1 else "“%s”" % names[0]
            ans = input("Remove %s%s? [y/N] " % (what, " AND DELETE THEIR FILES" if self.args.data else ""))
            if ans.strip().lower() not in ("y", "yes"):
                print("Nothing removed.")
                return 1
        self.call("torrents.remove", ids=ids, delete_data=self.args.data)
        self.out(True, "Removed %d torrent%s%s" % (len(names), "" if len(names) == 1 else "s",
                                                   " and their files" if self.args.data else ""))

    def cmd_move(self):
        self.call("torrents.move", ids=self.refs(self.args.torrents), path=self.args.destination)
        self.out(True, "Moving to %s" % self.args.destination)

    def cmd_queue(self):
        self.call("torrents.queue", ids=self.refs(self.args.torrents), where=self.args.where)
        self.out(True, "Moved %s" % self.args.where)

    def cmd_limit(self):
        a = self.args
        opts = {}
        for k, key in (("down", "download_limit"), ("up", "upload_limit"), ("connections", "max_connections"),
                       ("slots", "max_uploads")):
            if getattr(a, k) is not None:
                opts[key] = getattr(a, k)
        if not opts:
            raise CliError("give at least one of --down, --up, --connections, --slots (0 = no limit)")
        self.call("torrents.set_options", ids=self.refs(a.torrents), options=opts)
        self.out(True, "Limits changed")

    def cmd_option(self):
        opts = {}
        for item in self.args.options:
            if "=" not in item:
                raise CliError("use key=value, e.g. sequential=on ratio_limit=2 label=movies")
            k, v = item.split("=", 1)
            if k in ("sequential", "first_last", "super_seeding", "auto_managed"):
                v = v.lower() in ("1", "on", "yes", "true")
            elif k in ("ratio_limit", "seed_time_limit"):
                v = None if v.lower() in ("", "default", "global") else (-1 if v.lower() in ("none", "off", "never")
                                                                          else float(v))
            elif k != "label":
                raise CliError("unknown option %s (sequential, first_last, super_seeding, auto_managed, ratio_limit, "
                               "seed_time_limit, label)" % k)
            opts[k] = v
        self.call("torrents.set_options", ids=self.refs(self.args.torrents), options=opts)
        self.out(True, "Options changed")

    def cmd_label(self):
        self.call("torrents.set_options", ids=self.refs(self.args.torrents), options={"label": self.args.label})
        self.out(True, "Label set to %s" % (self.args.label or "none"))

    def cmd_labels(self):
        a = self.args
        if a.add:
            self.call("labels.set", name=a.add, path=a.path or "")
        if a.remove:
            self.call("labels.remove", name=a.remove)
        labels = self.call("labels.list")
        if a.json:
            return self.out(labels)
        if not labels:
            print("No labels. Create one with: cascade labels --add NAME [--path FOLDER]")
        for k, v in labels.items():
            print("%-20s %3d torrent%s%s" % (k, v["count"], "" if v["count"] == 1 else "s",
                                            ("   → " + v["path"]) if v["path"] else ""))

    def cmd_magnet(self):
        print(self.call("torrents.magnet", id=self.ref(self.args.torrent)))

    def cmd_export(self):
        r = self.call("torrents.export", id=self.ref(self.args.torrent))
        out = self.args.output or (r["name"] + ".torrent")
        with open(out, "wb") as fh:
            fh.write(base64.b64decode(r["data"]))
        self.out({"path": out}, "Saved %s" % out)

    def cmd_slow(self):
        mode = self.args.mode
        if mode is None:
            st = self.call("session.status")
            return self.out({"slow": st["alt_speed"], "source": st["alt_source"]},
                            "Slow mode is %s%s" % ("on" if st["alt_speed"] else "off",
                                                   " (%s)" % st["alt_source"] if st["alt_source"] else ""))
        on = {"on": True, "off": False, "toggle": "toggle"}[mode]
        r = self.call("alt_speed.set", on=on)
        self.out(r, "Slow mode is now %s" % ("on" if r else "off"))

    def cmd_schedule(self):
        a = self.args
        grid = self.call("settings.get", keys=["sched.grid", "sched.enabled"])
        g, enabled = grid["sched.grid"], grid["sched.enabled"]
        if a.action in ("on", "off"):
            self.call("settings.set", values={"sched.enabled": a.action == "on"})
            return self.out(True, "Schedule turned %s" % a.action)
        if a.action == "clear":
            self.call("settings.set", values={"sched.grid": "0" * 168})
            return self.out(True, "Schedule cleared (full speed all week)")
        if a.action == "set":
            if len(a.spec) != 3:
                raise CliError("usage: cascade schedule set DAYS HOURS MODE   e.g. mon-fri 9-17 slow")
            days = parse_days(a.spec[0])
            hours = parse_hours(a.spec[1])
            mode = MODES.get(a.spec[2].lower())
            if mode is None:
                raise CliError("mode must be normal, slow or pause")
            g = scheduler.set_range(g, days, hours, mode)
            vals = {"sched.grid": g}
            if not enabled:
                vals["sched.enabled"] = True
            self.call("settings.set", values=vals)
            enabled = True
        if a.json:
            return self.out({"enabled": enabled, "grid": g})
        print("Schedule is %s" % ("on" if enabled else "off (turn on: cascade schedule on)"))
        print("\n".join(scheduler.describe(g)))

    def cmd_config(self):
        sch = self.call("settings.schema")
        vals = self.call("settings.get")
        if self.args.json:
            return self.out({"schema": sch["pages"], "values": vals})
        target = (self.args.what or "").lower()
        for p in sch["pages"]:
            if target and target not in (p["id"], p["title"].lower()) and not any(
                    r.get("key") == target for g in p["groups"] for r in g["rows"]):
                continue
            print(color("━━ %s " % p["title"], "bold", "blue") + color("━" * max(0, 40 - len(p["title"])), "blue"))
            for g in p["groups"]:
                rows = [r for r in g["rows"] if r["kind"] == "setting" and (not target or target in
                                                                             (p["id"], p["title"].lower(), r["key"]))]
                if not rows:
                    continue
                print(color("  %s" % g["title"], "bold"))
                for r in rows:
                    v = vals.get(r["key"])
                    print("    %-34s %s" % (r["key"], fmt_value(r, v)) + color("  %s" % r["title"], "dim"))
                    if target == r["key"]:
                        print_setting_help(r)

    def cmd_get(self):
        vals = self.call("settings.get", keys=self.args.keys)
        if self.args.json:
            return self.out(vals)
        missing = [k for k in self.args.keys if k not in vals]
        if missing:
            raise CliError("unknown setting%s: %s (see: cascade config)" % ("s" if len(missing) > 1 else "",
                                                                          ", ".join(missing)))
        for k in self.args.keys:
            spec = schema.SPECS.get(k, {"type": "str"})
            print(fmt_value(spec, vals[k]) if len(self.args.keys) == 1 else "%s = %s" % (k, fmt_value(spec, vals[k])))

    def cmd_set(self):
        items = self.args.pairs
        if len(items) % 2:
            raise CliError("usage: cascade set KEY VALUE [KEY VALUE ...]")
        values = {}
        for k, v in zip(items[::2], items[1::2]):
            spec = schema.SPECS.get(k)
            if spec is None:
                raise CliError("unknown setting %s (see: cascade config)" % k)
            if spec["type"] == "lines":
                v = [x.strip() for x in v.replace("\n", ",").split(",") if x.strip()]
            elif spec["type"] == "json":
                v = json.loads(v)
            values[k] = v
        r = self.call("settings.set", values=values)
        if self.args.json:
            return self.out(r)
        for k, e in r["errors"].items():
            print(color("✗ %s: %s" % (k, e), "red"), file=sys.stderr)
        for k in r["changed"]:
            print("%s %s" % (color("✓", "green"), k))
        if not r["changed"] and not r["errors"]:
            print("Nothing changed.")
        self._webui_notice(r.get("notices", {}))
        return 1 if r["errors"] else 0

    def _webui_notice(self, n):
        if n.get("webui_password"):
            print(color("Web interface password: %s" % n["webui_password"], "bold")
                  + "  (change it with: cascade webui password)")
        w = n.get("webui")
        if w:
            if w.get("running"):
                print("Web interface: " + "  ".join(w["urls"]))
            elif w.get("error"):
                print(color("Web interface: %s" % w["error"], "red"))

    def cmd_reset(self):
        if not self.args.yes and sys.stdin.isatty():
            if input("Reset every setting to its default? Torrents are kept. [y/N] ").strip().lower() not in ("y",
                                                                                                             "yes"):
                return 1
        r = self.call("settings.reset")
        self.out(r, "Reset %d settings" % len(r["changed"]))

    def cmd_port(self):
        if self.args.action == "random":
            p = self.call("port.randomize")
            return self.out(p, "Now listening on port %d" % p)
        st = self.call("session.status")
        self.out({"port": st["port"], "mode": st["port_mode"], "natpmp": st["natpmp"]}, st["info"]["port"])

    def cmd_interfaces(self):
        lst = self.call("net.interfaces")
        cur = self.call("settings.get", keys=["net.interface"])["net.interface"]
        if self.args.json:
            return self.out(lst)
        for i in lst:
            mark = color("●", "green") if i["up"] and i["addresses"] else color("○", "dim")
            print("%s %-16s %-5s %s%s%s" % (mark, i["name"], "VPN" if i["vpn"] else "", ", ".join(i["addresses"])
                                            or "no address", color("  ← bound", "bold") if i["name"] == cur else "",
                                            color("  (loopback)", "dim") if i["loopback"] else ""))
        print(color("Bind with: cascade set net.interface tun0   (unbind: cascade set net.interface \"\")", "dim"))

    def cmd_webui(self):
        a = self.args
        if a.action in ("on", "off"):
            r = self.call("settings.set", values={"webui.enabled": a.action == "on"})
            if a.json:
                return self.out(r)
            if a.action == "off":
                print("Web interface turned off")
            self._webui_notice(r.get("notices", {}))
            if a.action == "on" and not r.get("notices", {}).get("webui"):
                st = self.call("webui.status")
                print("Web interface: " + ("  ".join(st["urls"]) if st["running"] else st.get("error") or "starting"))
            return
        if a.action == "password":
            pw = a.value
            if not pw:
                pw = getpass.getpass("New web interface password: ")
                if pw != getpass.getpass("Again: "):
                    raise CliError("the passwords differ")
            if not pw:
                raise CliError("the password cannot be empty")
            self.call("settings.set", values={"webui.password": pw})
            return self.out(True, "Password changed (signed-in browsers must sign in again)")
        st = self.call("webui.status")
        if a.action == "open":
            if not st["running"]:
                raise CliError("the web interface is off (turn it on: cascade webui on)")
            import webbrowser
            webbrowser.open(st["urls"][0])
            return
        if a.json:
            return self.out(st)
        if st["running"]:
            print("Web interface is on: " + "  ".join(st["urls"]))
            if not st["password_set"]:
                print(color("No password is set: cascade webui password", "yellow"))
        else:
            why = (" - " + st["error"]) if st.get("error") else " (turn on: cascade webui on)"
            print("Web interface is off" + why)

    def cmd_blocklist(self):
        self.call("ipfilter.update")
        self.out(True, "Loading the blocklist (see: cascade status / cascade log)")

    def cmd_create(self):
        a = self.args
        remote = bool(a.url or os.environ.get("CASCADE_URL"))
        path = a.path if remote else os.path.abspath(os.path.expanduser(a.path))
        out = a.output or (os.path.basename(path.rstrip("/")) + ".torrent")
        version = "v1" if a.v1 else ("v2" if a.v2 else "hybrid")
        job = self.call("create.start", path=path, trackers=a.tracker or [], piece_size=(a.piece_size or 0) * 1024,
                        private=a.private, comment=a.comment or "", web_seeds=a.web_seed or [], source=a.source or "",
                        version=version, output=None, seed=a.seed)
        while True:
            st = self.call("create.status", job=job)
            if st["state"] == "done":
                break
            if st["state"] == "error":
                raise CliError(st["error"])
            if sys.stdout.isatty() and not a.json:
                sys.stdout.write("\rHashing %s %3d%%" % (bar(st["progress"], 30), st["progress"] * 100))
                sys.stdout.flush()
            time.sleep(0.3)
        res = st["result"]
        if sys.stdout.isatty() and not a.json:
            sys.stdout.write("\r" + " " * 50 + "\r")
        with open(out, "wb") as fh:
            fh.write(base64.b64decode(res["data"]))
        res.pop("data", None)
        res["output"] = os.path.abspath(out)
        self.out(res, "Created %s (%s, %d pieces of %s)\n%s%s" % (
            out, util.fmt_size(res["size"]), res["pieces"], util.fmt_size(res["piece_size"]), res["magnet"],
            "\nSeeding it now." if a.seed else ""))

    def cmd_log(self):
        lines = self.call("log.tail", lines=self.args.lines)
        self.out(lines, "\n".join(lines))

    def cmd_boot(self):
        """Start the service when the computer starts, before login (any init system)."""
        import pwd
        from . import bootsvc
        a = self.args
        user = (a.user or os.environ.get("SUDO_USER") or os.environ.get("DOAS_USER")
                or pwd.getpwuid(os.getuid()).pw_name)
        init = bootsvc.detect()
        if a.action == "status":
            mine = user == pwd.getpwuid(os.getuid()).pw_name
            on = bootsvc.status() if mine else bootsvc.status_system(user, init)
            text = "Starting at boot is %s for %s (%s)" % ("unknown" if on is None else "on" if on else "off", user,
                                                           init or "unknown init")
            return self.out({"init": init, "user": user, "enabled": on}, text)
        enable = a.action == "enable"
        try:
            if os.getuid() == 0:
                if user == "root":
                    raise CliError("name the user who owns the torrents: cascade boot %s --user NAME" % a.action)
                (bootsvc.enable_system if enable else bootsvc.disable_system)(user, init)
            elif init == "systemd" and bootsvc._user_bus() and bootsvc.running_unit() is None:
                from .settings import State
                (bootsvc.enable_user_systemd if enable else bootsvc.disable_user_systemd)(State())
            else:
                su = shutil.which("sudo") or shutil.which("doas")
                if not su:
                    raise CliError("run this as root (no sudo or doas found)")
                return subprocess.call([su] + bootsvc.cascade_command() + ["boot", a.action, "--user", user])
        except bootsvc.BootError as e:
            raise CliError(str(e))
        self.out(True, "Cascade %s at boot for %s (%s)" % ("starts" if enable else "no longer starts", user, init))

    def cmd_stop(self):
        try:
            from . import ipc as _ipc
            c = _ipc.Client()
            c.call("daemon.shutdown")
            for _ in range(100):
                if not c.alive():
                    break
                time.sleep(0.2)
            print("Cascade service stopped")
        except ipc.DaemonUnavailable:
            print("The Cascade service is not running")


def parse_days(spec):
    spec = spec.lower()
    if spec in ("all", "every", "daily", "week"):
        return list(range(7))
    if spec in ("weekdays", "workdays"):
        return list(range(5))
    if spec in ("weekend", "weekends"):
        return [5, 6]
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            ia, ib = DAY_NAMES.index(a[:3]), DAY_NAMES.index(b[:3])
            out += list(range(ia, ib + 1)) if ia <= ib else list(range(ia, 7)) + list(range(0, ib + 1))
        else:
            out.append(DAY_NAMES.index(part[:3]))
    return sorted(set(out))


def parse_hours(spec):
    if spec in ("all", "allday", "0-24"):
        return list(range(24))
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = (int(x.split(":")[0]) for x in part.split("-", 1))
            out += list(range(a, b)) if a < b else list(range(a, 24)) + list(range(0, b))
        else:
            out.append(int(part.split(":")[0]))
    if any(h < 0 or h > 23 for h in out):
        raise CliError("hours go from 0 to 24")
    return sorted(set(out))


def fmt_value(spec, v):
    t = spec.get("type")
    if t == "bool":
        return color("on", "green") if v else color("off", "dim")
    if t == "lines":
        return ", ".join(v) if v else color("(none)", "dim")
    if t == "schedule":
        return "%d slow / %d paused hours" % (v.count("1"), v.count("2"))
    if t == "choice":
        return str(v)
    if t in ("password", "secret"):
        return "(set)" if v else color("(not set)", "dim")
    if v == "" or v is None:
        return color("(empty)", "dim")
    if spec.get("unit"):
        return "%s %s" % (v, spec["unit"])
    if t == "json":
        return json.dumps(v)
    return str(v)


def print_setting_help(r):
    if r.get("subtitle"):
        print("      " + r["subtitle"])
    t = r["type"]
    if t == "choice":
        print("      choices: " + ", ".join("%s (%s)" % (c[0], c[1]) for c in r["choices"]))
    elif t in ("int", "float"):
        print("      range: %s to %s" % (r.get("min", "-"), r.get("max", "-")))
    elif t == "bool":
        print("      values: on, off")


def build_parser():
    p = argparse.ArgumentParser(prog="cascade", description="Cascade BitTorrent client - command line. "
                                "Torrents are named by their number in `cascade list`, an id prefix or part of their "
                                "name.", formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog="Examples:\n  cascade add 'magnet:?xt=urn:btih:…'\n  cascade add file.torrent "
                                       "-p ~/Videos -l movies\n  cascade list -f downloading\n  cascade pause 2\n"
                                       "  cascade set net.interface tun0\n  cascade schedule set mon-fri 9-17 slow\n"
                                       "  cascade webui on")
    p.add_argument("--version", action="version", version="%s %s" % (APP_NAME, __version__))
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--url", help="use a remote Cascade web interface, e.g. http://nas:8150 (or $CASCADE_URL)")
    p.add_argument("--password", help="its password (or $CASCADE_PASSWORD)")
    p.add_argument("--insecure", action="store_true", help="accept a self-signed HTTPS certificate")
    p.add_argument("--no-start", action="store_true", help="do not start the service if it is not running")
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")

    def add(name, help_, aliases=()):
        sp = sub.add_parser(name, help=help_, aliases=list(aliases), description=help_)
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
        return sp

    sp = add("gui", "open the GTK app (optionally adding magnet links, URLs or .torrent files)")
    sp.add_argument("uris", nargs="*", metavar="TORRENT")
    sp.add_argument("--hidden", action="store_true", help="start without showing the window")
    sp = add("daemon", "run the Cascade service in the foreground")
    sp.add_argument("--detach", action="store_true", help="start it in the background and return")
    sp.add_argument("--quiet", action="store_true", help="log only to the log file")
    add("stop", "stop the Cascade service")
    sp = add("boot", "start the service when the computer starts (systemd, runit, OpenRC, FreeBSD, OpenBSD)")
    sp.add_argument("action", nargs="?", default="status", choices=["status", "enable", "disable"])
    sp.add_argument("--user", help="whose torrents (default: you, or the user who ran sudo)")
    add("status", "speeds, port, interface, slow mode and web interface")

    def listing(sp):
        sp.add_argument("-f", "--filter", choices=[f[0] for f in util.FILTERS], default="all")
        sp.add_argument("-l", "--label", help="only this label")
        sp.add_argument("-s", "--sort", choices=["queue", "name", "added", "progress", "size", "down", "up",
                                                 "ratio", "eta"])
        sp.add_argument("-r", "--reverse", action="store_true")
        sp.add_argument("search", nargs="?", help="only names containing this")

    listing(add("list", "list torrents", ["ls"]))
    sp = add("watch", "live view of the torrents (Ctrl+C to quit)", ["top"])
    listing(sp)
    sp.add_argument("-n", "--interval", type=float, default=1.0)

    for name, help_ in (("info", "everything about one torrent"), ("magnet", "print the magnet link")):
        add(name, help_).add_argument("torrent")
    sp = add("peers", "connected peers, or connect to one by hand")
    sp.add_argument("torrent")
    sp.add_argument("--add", nargs="+", metavar="IP:PORT")
    sp = add("files", "list files, or set their priorities")
    sp.add_argument("torrent")
    sp.add_argument("--set", nargs="+", metavar="N=PRIO", help="e.g. 3=skip 1-2=high all=normal")
    sp = add("trackers", "list, add or remove trackers")
    sp.add_argument("torrent")
    sp.add_argument("--add", nargs="+", metavar="URL")
    sp.add_argument("--remove", nargs="+", metavar="URL")
    sp = add("export", "save the .torrent file")
    sp.add_argument("torrent")
    sp.add_argument("-o", "--output")

    sp = add("add", "add magnet links, .torrent files or URLs")
    sp.add_argument("sources", nargs="+")
    sp.add_argument("-p", "--path", dest="save_path", help="download folder")
    sp.add_argument("-l", "--label")
    sp.add_argument("--paused", action="store_true")
    sp.add_argument("--sequential", action="store_true")
    sp.add_argument("--first-last", action="store_true", help="first and last pieces first")
    sp.add_argument("--skip-check", action="store_true", help="the data is already complete")
    sp.add_argument("--top", dest="top_of_queue", action="store_true", help="top of the queue")

    for name, help_ in (("pause", "pause torrents"), ("resume", "resume torrents"),
                        ("start", "force-start torrents (skip the queue)"), ("recheck", "verify the data"),
                        ("reannounce", "contact the trackers and DHT now")):
        add(name, help_).add_argument("torrents", nargs="+", help="numbers, ids, names or all")
    sp = add("remove", "remove torrents", ["rm"])
    sp.add_argument("torrents", nargs="+")
    sp.add_argument("--data", action="store_true", help="also delete the downloaded files")
    sp.add_argument("-y", "--yes", action="store_true")
    sp = add("move", "move torrents' data to another folder")
    sp.add_argument("torrents", nargs="+")
    sp.add_argument("destination")
    sp = add("queue", "move torrents in the queue")
    sp.add_argument("torrents", nargs="+")
    sp.add_argument("where", choices=["up", "down", "top", "bottom"])
    sp = add("limit", "per-torrent speed and connection limits (0 = default/unlimited)")
    sp.add_argument("torrents", nargs="+")
    sp.add_argument("--down", type=int, metavar="KIB")
    sp.add_argument("--up", type=int, metavar="KIB")
    sp.add_argument("--connections", type=int)
    sp.add_argument("--slots", type=int)
    sp = add("option", "per-torrent options: sequential, first_last, super_seeding, ratio_limit, seed_time_limit")
    sp.add_argument("torrents", nargs="+")
    sp.add_argument("--set", dest="options", nargs="+", required=True, metavar="KEY=VALUE")
    sp = add("label", "set the label of torrents (\"\" removes it)")
    sp.add_argument("torrents", nargs="+")
    sp.add_argument("--to", dest="label", required=True)
    sp = add("labels", "list, create or remove labels")
    sp.add_argument("--add", metavar="NAME")
    sp.add_argument("--path", help="download folder for the new label")
    sp.add_argument("--remove", metavar="NAME")

    sp = add("slow", "slow mode (the alternative speed limits)", ["turtle"])
    sp.add_argument("mode", nargs="?", choices=["on", "off", "toggle"])
    sp = add("schedule", "the weekly speed schedule")
    sp.add_argument("action", nargs="?", default="show", choices=["show", "set", "clear", "on", "off"])
    sp.add_argument("spec", nargs="*", help="for set: DAYS HOURS MODE, e.g. mon-fri 9-17 slow")
    sp = add("config", "show every setting (optionally one page or key)")
    sp.add_argument("what", nargs="?")
    sp = add("get", "print settings")
    sp.add_argument("keys", nargs="+")
    sp = add("set", "change settings: cascade set KEY VALUE [KEY VALUE ...]")
    sp.add_argument("pairs", nargs="+")
    add("reset", "reset every setting to its default").add_argument("-y", "--yes", action="store_true")
    sp = add("port", "show the listening port, or pick a random one")
    sp.add_argument("action", nargs="?", choices=["show", "random"], default="show")
    add("interfaces", "network interfaces you can bind to (net.interface)")
    sp = add("webui", "the web interface")
    sp.add_argument("action", nargs="?", default="status", choices=["status", "on", "off", "password", "open"])
    sp.add_argument("value", nargs="?")
    add("blocklist", "reload the IP blocklist now")
    sp = add("create", "create a .torrent file")
    sp.add_argument("path")
    sp.add_argument("-o", "--output")
    sp.add_argument("-t", "--tracker", action="append", help="repeat for more; an empty one starts a new tier")
    sp.add_argument("-w", "--web-seed", action="append")
    sp.add_argument("-c", "--comment")
    sp.add_argument("--source", help="source tag (some private trackers want one)")
    sp.add_argument("--private", action="store_true")
    sp.add_argument("--piece-size", type=int, metavar="KIB", help="default: automatic")
    g = sp.add_mutually_exclusive_group()
    g.add_argument("--v1", action="store_true", help="BitTorrent v1 only")
    g.add_argument("--v2", action="store_true", help="BitTorrent v2 only (default: hybrid)")
    sp.add_argument("--seed", action="store_true", help="start seeding it right away")
    add("log", "show the service log").add_argument("-n", "--lines", type=int, default=50)
    return p


ALIASES = {"ls": "list", "top": "watch", "rm": "remove", "turtle": "slow"}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "json"):
        args.json = False
    cmd = ALIASES.get(args.cmd, args.cmd) or "list"
    if cmd == "list" and args.cmd is None:
        for k, v in (("filter", "all"), ("label", None), ("sort", None), ("reverse", False), ("search", None)):
            setattr(args, k, v)
    if cmd == "gui":
        from .gui.app import main as gui_main
        return gui_main(["cascade-gtk"] + (["--hidden"] if args.hidden else []) + args.uris)
    if cmd == "daemon":
        from . import daemon
        if args.detach:
            from . import client
            try:
                client.ensure_daemon()
            except ipc.DaemonUnavailable as e:
                print("cascade: %s" % e, file=sys.stderr)
                return 1
            print("Cascade service is running")
            return 0
        try:
            import libtorrent  # noqa: F401
        except ImportError:
            print("cascade: the Python bindings of libtorrent are missing for %s %d.%d. Install them with: %s"
                  % (sys.executable, sys.version_info[0], sys.version_info[1], util.libtorrent_hint()),
                  file=sys.stderr)
            return 1
        try:
            return daemon.run(foreground=not args.quiet)
        except daemon.AlreadyRunning as e:
            print("cascade: %s" % e, file=sys.stderr)
            return 0 if args.quiet else 1
    cli = Cli(args)
    try:
        rc = getattr(cli, "cmd_" + cmd)()
        return rc or 0
    except CliError as e:
        print("cascade: %s" % e, file=sys.stderr)
        return 2
    except ipc.DaemonUnavailable as e:
        print("cascade: %s" % e, file=sys.stderr)
        return 3
    except ipc.RpcError as e:
        print("cascade: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0
