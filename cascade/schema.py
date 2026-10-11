"""The settings schema: the single source of truth for every preference.

The GTK preferences window, the web interface's preferences dialog and `cascade config` are all generated from
PAGES, so the three can never disagree about what exists, its type, range, default or wording.  The daemon validates
every change against it (settings.py) and applies it to libtorrent (engine.py).

Row kinds:
  setting  (the default)  key/type/default/title/subtitle + type options
  action   a button that calls an RPC method (port.randomize, ipfilter.update, ...)
  info     a read-only row whose text the client asks the daemon for (session.status -> "info")

Setting types: bool, int, float, str, password (stored, masked when read), secret (stored hashed, masked),
choice, path (folder), file, interface (network device name), lines (list of strings), schedule (168 chars),
json (hidden structured data).

`depends` controls when a row is usable: {"key": k} (truthy), {"key": k, "in": [...]}, {"key": k, "not": v}.
"""

import random

from . import paths

DEFAULT_BOOTSTRAP = ("dht.libtorrent.org:25401,router.bittorrent.com:6881,router.utorrent.com:6881,"
                     "dht.transmissionbt.com:6881")
MASK = "••••••••"
SCHEDULE_LEN = 7 * 24


def _random_port():
    return random.randint(49152, 65000)


def S(key, type_, default, title, subtitle="", **kw):
    d = {"kind": "setting", "key": key, "type": type_, "default": default, "title": title, "subtitle": subtitle}
    d.update(kw)
    return d


def A(action, title, subtitle="", button="", **kw):
    d = {"kind": "action", "action": action, "title": title, "subtitle": subtitle, "button": button or title}
    d.update(kw)
    return d


def I(info, title, subtitle="", **kw):  # noqa: E743 - a schema row constructor, not a variable
    d = {"kind": "info", "info": info, "title": title, "subtitle": subtitle}
    d.update(kw)
    return d


def dep(key, **kw):
    d = {"key": key}
    d.update(kw)
    return d


def group(title, rows, description=""):
    return {"title": title, "description": description, "rows": rows}


def page(id_, title, icon, groups):
    return {"id": id_, "title": title, "icon": icon, "groups": groups}


PORT_MODES = [("fixed", "Fixed port"), ("random", "New random port at every start"),
              ("natpmp", "VPN port forwarding (NAT-PMP)"), ("file", "Read the port from a file")]

PAGES = [
    page("general", "General", "preferences-system-symbolic", [
        group("Appearance", [
            S("app.theme", "choice", "system", "Style",
              choices=[["system", "Follow system"], ["light", "Light"], ["dark", "Dark"]]),
        ]),
        group("Behavior", [
            S("app.background", "bool", True, "Keep running in the background",
              "Transfers continue after the window is closed, and the web interface and command line stay available"),
            S("app.start_at_boot", "bool", False, "Start when the computer starts",
              "Before anyone logs in, so torrents and the web interface come back after a reboot (needs systemd)"),
            S("app.autostart", "bool", False, "Start on login", "Start the Cascade service when you log in",
              depends=dep("app.start_at_boot", **{"in": [False]})),
            S("app.confirm_remove", "bool", True, "Ask before removing torrents"),
            S("notify.finished", "bool", True, "Notify when a download finishes"),
            A("default.handler", "Open magnet links with Cascade",
              "Your browser and file manager hand magnet links and .torrent files to Cascade",
              button="Make Default", client="gtk"),
        ]),
        group("Adding Torrents", [
            S("add.show_dialog", "bool", True, "Show options before adding",
              "Choose the folder, files and label for each new torrent"),
            S("add.start_paused", "bool", False, "Add torrents paused"),
            S("add.top_of_queue", "bool", False, "Add to the top of the queue"),
            S("add.sequential", "bool", False, "Download pieces in order",
              "Good for streaming media, a little slower to finish"),
            S("add.first_last", "bool", False, "Download first and last pieces first",
              "Lets media players preview files early"),
            S("add.magnet_choose_files", "bool", False, "Pause magnet links when their metadata arrives",
              "So you can choose the files before the download starts"),
            S("add.extra_trackers_enabled", "bool", False, "Add extra trackers to every new torrent"),
            S("add.extra_trackers", "lines", [], "Extra trackers", "One tracker URL per line",
              depends=dep("add.extra_trackers_enabled")),
        ]),
    ]),
    page("downloads", "Downloads", "folder-download-symbolic", [
        group("Saving", [
            S("downloads.save_path", "path", paths.default_download_dir, "Download folder"),
            S("downloads.use_incomplete", "bool", False, "Keep unfinished downloads in another folder",
              "Torrents are moved to the download folder when they finish"),
            S("downloads.incomplete_path", "path", "", "Unfinished downloads folder",
              depends=dep("downloads.use_incomplete")),
            S("downloads.part_extension", "bool", False, "Add .part to unfinished files",
              "The extension is removed as soon as a file is complete"),
            S("downloads.preallocate", "bool", False, "Pre-allocate disk space",
              "Reserve the full size when a download starts, which avoids fragmentation"),
            S("downloads.copy_torrents", "bool", False, "Keep a copy of added .torrent files"),
            S("downloads.copy_torrents_path", "path", "", "Copy .torrent files to",
              depends=dep("downloads.copy_torrents")),
        ]),
        group("Watch Folder", [
            S("watch.enabled", "bool", False, "Watch a folder"),
            S("watch.path", "path", "", "Folder", depends=dep("watch.enabled")),
            S("watch.after", "choice", "rename", "After adding",
              choices=[["rename", "Rename the file to .added"], ["delete", "Delete the file"]],
              depends=dep("watch.enabled")),
            S("watch.label", "str", "", "Label", "Given to torrents added from the watch folder",
              placeholder="No label", depends=dep("watch.enabled")),
        ], "Torrent files and .magnet files saved into this folder are added automatically"),
        group("Automation", [
            S("automation.on_added", "str", "", "Run when a torrent is added", placeholder='logger "added %N"'),
            S("automation.on_complete", "str", "", "Run when a download finishes",
              placeholder='notify-send "Finished" "%N"'),
        ], "Placeholders: %N name, %F content path, %D folder, %I info hash, %L label, %Z size in bytes"),
    ]),
    page("connection", "Connection", "network-wired-symbolic", [
        group("Listening Port", [
            S("net.port_mode", "choice", "fixed", "Port mode", choices=[list(c) for c in PORT_MODES]),
            S("net.port", "int", _random_port, "Port", min=1024, max=65535,
              depends=dep("net.port_mode", **{"in": ["fixed"]})),
            S("net.random_min", "int", 49152, "Lowest random port", min=1024, max=65535,
              depends=dep("net.port_mode", **{"in": ["random"]})),
            S("net.random_max", "int", 65535, "Highest random port", min=1024, max=65535,
              depends=dep("net.port_mode", **{"in": ["random"]})),
            A("port.randomize", "Pick a random port now", button="Randomize",
              depends=dep("net.port_mode", **{"in": ["fixed", "random"]})),
            S("net.natpmp_gateway", "str", "10.2.0.1", "VPN gateway",
              "ProtonVPN uses 10.2.0.1. Leave it empty to use the gateway of the bound interface",
              placeholder="Automatic", depends=dep("net.port_mode", **{"in": ["natpmp"]})),
            S("net.port_file", "file", "", "Port file",
              "A file your VPN client keeps up to date, such as /tmp/gluetun/forwarded_port",
              depends=dep("net.port_mode", **{"in": ["file"]})),
            S("net.upnp", "bool", True, "Forward the port on my router", "Uses UPnP and NAT-PMP"),
            I("port", "Current port"),
        ], "Peers connect to you on this port. A forwarded port gives the best speeds"),
        group("Network Interface", [
            S("net.interface", "interface", "", "Network interface",
              "Send and receive only through this device, for example a VPN tunnel such as tun0 or wg0"),
            S("net.killswitch", "bool", True, "Pause everything when it disconnects",
              "Nothing leaks over your normal connection if the VPN drops",
              depends=dep("net.interface", **{"not": ""})),
            S("net.ipv6", "bool", True, "Use IPv6", depends=dep("net.interface", **{"in": [""]})),
            I("interface", "Interface status"),
        ]),
        group("Peer Discovery", [
            S("net.dht", "bool", True, "Distributed hash table (DHT)", "Find peers without a tracker"),
            S("net.pex", "bool", True, "Peer exchange (PeX)", "Learn about more peers from connected peers"),
            S("net.lsd", "bool", True, "Local peer discovery", "Find peers on your local network"),
        ]),
        group("Protocol", [
            S("net.encryption", "choice", "prefer", "Encryption",
              choices=[["prefer", "Prefer encrypted connections"], ["require", "Require encryption"],
                       ["allow", "Allow encryption"], ["disable", "Disable encryption"]]),
            S("net.utp", "bool", True, "µTP", "A low-latency transport that backs off when your network is busy"),
            S("net.anonymous", "bool", False, "Anonymous mode",
              "Hide the client name and version. Use it together with a proxy or a VPN"),
        ]),
        group("Proxy", [
            S("proxy.type", "choice", "none", "Proxy",
              choices=[["none", "None"], ["socks5", "SOCKS5"], ["socks4", "SOCKS4"], ["http", "HTTP"],
                       ["i2p", "I2P (SAM)"]]),
            S("proxy.host", "str", "", "Host", placeholder="proxy.example.com",
              depends=dep("proxy.type", **{"not": "none"})),
            S("proxy.port", "int", 1080, "Port", min=1, max=65535, depends=dep("proxy.type", **{"not": "none"})),
            S("proxy.username", "str", "", "User name", depends=dep("proxy.type", **{"in": ["socks5", "http"]})),
            S("proxy.password", "password", "", "Password",
              depends=dep("proxy.type", **{"in": ["socks5", "http"]})),
            S("proxy.peers", "bool", True, "Use it for peer connections",
              depends=dep("proxy.type", **{"not": "none"})),
            S("proxy.trackers", "bool", True, "Use it for trackers", depends=dep("proxy.type", **{"not": "none"})),
            S("proxy.hostnames", "bool", True, "Resolve host names through the proxy",
              depends=dep("proxy.type", **{"not": "none"})),
        ]),
    ]),
    page("speed", "Speed", "network-transmit-receive-symbolic", [
        group("Speed Limits", [
            S("speed.download", "int", 0, "Download limit", min=0, max=100000000, unit="KiB/s"),
            S("speed.upload", "int", 0, "Upload limit", min=0, max=100000000, unit="KiB/s"),
            S("speed.limit_lan", "bool", False, "Apply the limits to the local network too"),
            S("speed.overhead", "bool", False, "Count protocol overhead towards the limits"),
        ], "0 means unlimited"),
        group("Slow Mode", [
            S("alt.download", "int", 1024, "Download limit", min=0, max=100000000, unit="KiB/s"),
            S("alt.upload", "int", 256, "Upload limit", min=0, max=100000000, unit="KiB/s"),
        ], "Alternative limits, switched on with the turtle button or by the schedule. 0 means unlimited"),
        group("Schedule", [
            S("sched.enabled", "bool", False, "Follow a weekly schedule"),
            S("sched.grid", "schedule", "0" * SCHEDULE_LEN, "Week", depends=dep("sched.enabled")),
        ], "Choose when Cascade runs at full speed, in slow mode, or pauses"),
        group("Connections", [
            S("conn.max", "int", 500, "Maximum connections", min=2, max=65535),
            S("conn.max_per_torrent", "int", 100, "Maximum connections per torrent", min=2, max=65535),
            S("conn.slots", "int", 20, "Upload slots", "Peers you upload to at the same time", min=1, max=10000),
            S("conn.slots_per_torrent", "int", 4, "Upload slots per torrent", min=1, max=10000),
        ]),
    ]),
    page("queue", "Queue", "view-list-ordered-symbolic", [
        group("Queue", [
            S("queue.enabled", "bool", True, "Queue torrents automatically"),
            S("queue.max_downloads", "int", 3, "Active downloads", min=1, max=10000, depends=dep("queue.enabled")),
            S("queue.max_seeds", "int", 5, "Active seeds", min=1, max=10000, depends=dep("queue.enabled")),
            S("queue.max_active", "int", 8, "Active torrents", "Downloads and seeds together", min=1, max=10000,
              depends=dep("queue.enabled")),
            S("queue.ignore_slow", "bool", True, "Don't count slow torrents", depends=dep("queue.enabled")),
            S("queue.slow_rate", "int", 2, "Slow below", min=1, max=100000, unit="KiB/s",
              depends=dep("queue.ignore_slow")),
        ], "Limit how many torrents are active at once; the others wait their turn"),
        group("Seeding Limits", [
            S("seed.ratio_enabled", "bool", False, "Stop at a share ratio"),
            S("seed.ratio", "float", 2.0, "Share ratio", min=0.0, max=1000.0, step=0.05, digits=2,
              depends=dep("seed.ratio_enabled")),
            S("seed.time_enabled", "bool", False, "Stop after seeding for a while"),
            S("seed.time", "int", 1440, "Seeding time", min=1, max=5256000, unit="minutes",
              depends=dep("seed.time_enabled")),
            S("seed.idle_enabled", "bool", False, "Stop when nobody downloads from you"),
            S("seed.idle", "int", 60, "Idle time", min=1, max=5256000, unit="minutes",
              depends=dep("seed.idle_enabled")),
            S("seed.action", "choice", "pause", "When a limit is reached",
              choices=[["pause", "Pause the torrent"], ["remove", "Remove the torrent"],
                       ["remove_data", "Remove the torrent and its files"]]),
        ]),
    ]),
    page("remote", "Remote", "network-server-symbolic", [
        group("Web Interface", [
            S("webui.enabled", "bool", False, "Web interface", "Turn it on with one click"),
            I("webui", "Address", depends=dep("webui.enabled")),
            A("webui.open", "Open the web interface", button="Open", depends=dep("webui.enabled"), client="gtk"),
            S("webui.password", "secret", "", "Password", "Needed to sign in"),
            S("webui.host", "str", "0.0.0.0", "Listen address",
              "0.0.0.0 accepts every network, 127.0.0.1 only this computer"),
            S("webui.port", "int", 8150, "Port", min=1, max=65535),
            S("webui.local_no_auth", "bool", False, "No password from this computer",
              "Connections from 127.0.0.1 skip the sign-in"),
            S("webui.https", "bool", False, "Use HTTPS"),
            S("webui.cert", "file", "", "Certificate (PEM)", depends=dep("webui.https")),
            S("webui.key", "file", "", "Private key (PEM)", depends=dep("webui.https")),
        ], "Use Cascade from any browser. It shows the same torrents and settings as the app and the command line"),
        group("Command Line", [
            I("cli", "Control socket"),
        ], "Run “cascade --help” in a terminal. The command line talks to the same Cascade service"),
    ]),
    page("advanced", "Advanced", "preferences-other-symbolic", [
        group("IP Blocklist", [
            S("ipfilter.enabled", "bool", False, "Use a blocklist"),
            S("ipfilter.source", "str", "", "Blocklist URL or file", placeholder="https://… or /path/to/list.p2p.gz",
              depends=dep("ipfilter.enabled")),
            S("ipfilter.update_days", "int", 7, "Update every", min=1, max=365, unit="days",
              depends=dep("ipfilter.enabled")),
            S("ipfilter.trackers", "bool", True, "Block trackers too", depends=dep("ipfilter.enabled")),
            A("ipfilter.update", "Load the blocklist now", button="Update", depends=dep("ipfilter.enabled")),
            I("ipfilter", "Blocklist status", depends=dep("ipfilter.enabled")),
        ], "P2P, DAT and CIDR lists, plain, gzip or zip"),
        group("Trackers", [
            S("adv.announce_all_trackers", "bool", False, "Announce to every tracker"),
            S("adv.announce_all_tiers", "bool", False, "Announce to every tier"),
            S("adv.validate_https", "bool", True, "Verify tracker HTTPS certificates"),
        ]),
        group("Disk", [
            S("adv.os_cache", "bool", True, "Use the system file cache"),
            S("adv.aio_threads", "int", 10, "Disk threads", min=1, max=64),
            S("adv.hashing_threads", "int", 2, "Hashing threads", min=1, max=64),
            S("adv.file_pool", "int", 100, "Open files limit", min=4, max=100000),
            S("adv.checking_mem", "int", 32, "Memory for checking files", min=1, max=4096, unit="MiB"),
        ]),
        group("Network", [
            S("adv.outgoing_port", "int", 0, "First outgoing port", "0 lets the system choose", min=0, max=65535),
            S("adv.outgoing_ports", "int", 0, "Number of outgoing ports", min=0, max=65535),
            S("adv.max_peerlist", "int", 3000, "Peers remembered per torrent", min=100, max=1000000),
            S("adv.connection_speed", "int", 30, "New connections per second", min=1, max=10000),
            S("adv.dscp", "int", 4, "DSCP for peer traffic",
              "Lets routers deprioritise torrent traffic; 0 turns it off",
              min=0, max=63),
            S("adv.mixed_mode", "choice", "proportional", "TCP and µTP bandwidth",
              choices=[["proportional", "Share by number of peers"], ["prefer_tcp", "Prefer TCP"]]),
            S("adv.choking", "choice", "fixed", "Upload slot algorithm",
              choices=[["fixed", "Fixed number of slots"], ["rate_based", "Rate based"]]),
            S("adv.seed_choking", "choice", "fastest_upload", "Seeding algorithm",
              choices=[["round_robin", "Round robin"], ["fastest_upload", "Fastest upload"],
                       ["anti_leech", "Anti-leech"]]),
            S("adv.dht_bootstrap", "str", DEFAULT_BOOTSTRAP, "DHT bootstrap nodes"),
        ]),
        group("Logging", [
            S("log.level", "choice", "info", "Log level",
              choices=[["error", "Errors"], ["warning", "Warnings"], ["info", "Information"], ["debug", "Debug"]]),
            I("log", "Log file"),
        ]),
        group("Reset", [
            A("settings.reset", "Reset all settings", "Your torrents are kept", button="Reset", style="destructive"),
        ]),
    ]),
]

# Settings that are stored and shared like the others but have no row in the preferences.
HIDDEN = [
    S("labels", "json", {}, "Labels"),        # {"name": {"path": "/save/path or empty"}}
    S("ui.sort", "choice", "queue", "Sort by",
      choices=[[k, k] for k in ("queue", "name", "added", "progress", "size", "down", "up", "ratio", "eta")]),
    S("ui.sort_desc", "bool", False, "Descending"),
    S("ui.default_asked", "bool", False, "Asked to become the magnet link handler"),
]

SORT_LABELS = [("queue", "Queue order"), ("name", "Name"), ("added", "Date added"), ("progress", "Progress"),
               ("size", "Size"), ("down", "Download speed"), ("up", "Upload speed"), ("ratio", "Ratio"),
               ("eta", "Time left")]


def all_settings():
    for p in PAGES:
        for g in p["groups"]:
            for r in g["rows"]:
                if r["kind"] == "setting":
                    yield r
    for r in HIDDEN:
        yield r


SPECS = {s["key"]: s for s in all_settings()}


def default(key):
    d = SPECS[key]["default"]
    return d() if callable(d) else (list(d) if isinstance(d, list) else (dict(d) if isinstance(d, dict) else d))


def public_schema():
    """The schema as plain JSON (callable defaults resolved) for the GTK and web clients."""
    out = []
    for p in PAGES:
        groups = []
        for g in p["groups"]:
            rows = []
            for r in g["rows"]:
                r = dict(r)
                if callable(r.get("default")):
                    r["default"] = None
                rows.append(r)
            groups.append({"title": g["title"], "description": g["description"], "rows": rows})
        out.append({"id": p["id"], "title": p["title"], "icon": p["icon"], "groups": groups})
    return out


def depends_ok(depends, values):
    """Evaluate a `depends` rule against a {key: value} mapping (the clients use the same logic)."""
    if not depends:
        return True
    v = values.get(depends["key"])
    if "in" in depends:
        return v in depends["in"]
    if "not" in depends:
        return v != depends["not"]
    return bool(v)
