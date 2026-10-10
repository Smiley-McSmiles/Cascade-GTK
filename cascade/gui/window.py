"""The main window: one list of torrent cards, a filter bar that rolls down from the header bar, a status bar.
Everything (details, files, peers, trackers, per-torrent options) opens inside the list - no extra windows."""

import base64
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from .. import APP_NAME, schema, util  # noqa: E402
from .torrent_row import TorrentRow  # noqa: E402
from .widgets import SpeedGraph  # noqa: E402

FILTERS = [(k, title) for k, title, _icon in util.FILTERS]


def matches(t, f):
    st = t["state"]
    if f == "all":
        return True
    if f == "downloading":
        return st in ("downloading", "metadata")
    if f == "completed":
        return t["finished"]
    if f == "active":
        return t["down"] > 0 or t["up"] > 0
    return st == f


SORT_KEYS = {
    "queue": lambda t: (t["queue"] if t["queue"] >= 0 else 1 << 30, -t["added"]),
    "name": lambda t: t["name"].lower(),
    "added": lambda t: t["added"],
    "progress": lambda t: t["progress"],
    "size": lambda t: t["size"],
    "down": lambda t: t["down"],
    "up": lambda t: t["up"],
    "ratio": lambda t: t["ratio"],
    "eta": lambda t: t["eta"] if t["eta"] >= 0 else 1 << 40,
}
DYNAMIC_SORTS = {"progress", "down", "up", "ratio", "eta", "queue"}


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME)
        self.app = app
        self.rpc = app.rpc
        self.rows = {}
        self.settings = {}
        self.settings_rev = None
        self.schema = None
        self.filter = "all"
        self.label_filter = None          # None = all labels, "" = no label
        self.tracker_filter = None
        self.search = ""
        self.sort = "queue"
        self.sort_desc = False
        self.selection_mode = False
        self.event_seq = None
        self.syncing = False
        self.connected = False
        self.session = {}
        self.label_counts = {}
        self.tracker_counts = {}
        self._label_model_keys = None
        self._tracker_model_keys = None
        self._updating_ui = False
        self._sync_n = 0
        self.set_default_size(1000, 760)
        self.set_size_request(360, 420)
        self.set_icon_name(app.get_application_id())

        self.toasts = Adw.ToastOverlay()
        self.set_content(self.toasts)
        self.view = Adw.ToolbarView()
        self.toasts.set_child(self.view)
        self._build_header()
        self._build_filter_bar()
        self._build_banner()
        self._build_content()
        self._build_statusbar()
        self._build_actionbar()
        self._install_actions()
        self._install_dnd()

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560sp"))
        bp.add_setter(self.status_extra, "visible", False)
        bp.add_setter(self.select_btn, "visible", False)
        self.add_breakpoint(bp)
        self.stack.set_visible_child_name("connecting")

    # ================================================================ building
    def _build_header(self):
        hb = Adw.HeaderBar()
        self.view.add_top_bar(hb)
        self.title_widget = Adw.WindowTitle(title=APP_NAME)
        hb.set_title_widget(self.title_widget)

        add_menu = Gio.Menu()
        add_menu.append("Open Torrent File…", "win.add-file")
        add_menu.append("Add Magnet Link or URL…", "win.add-magnet")
        add_menu.append("Paste from Clipboard", "win.paste")
        sec = Gio.Menu()
        sec.append("Create Torrent…", "win.create")
        add_menu.append_section(None, sec)
        self.add_btn = Adw.SplitButton(icon_name="list-add-symbolic", tooltip_text="Open a torrent file (Ctrl+O)",
                                       menu_model=add_menu, dropdown_tooltip="More ways to add")
        self.add_btn.set_action_name("win.add-file")
        hb.pack_start(self.add_btn)

        self.filter_btn = Gtk.ToggleButton(icon_name="cascade-filter-symbolic", tooltip_text="Filter and sort (Ctrl+F)")
        self.filter_btn.connect("toggled", lambda b: self.filter_revealer.set_reveal_child(b.get_active()))
        hb.pack_start(self.filter_btn)

        menu = Gio.Menu()
        s1 = Gio.Menu()
        s1.append("Resume All", "win.resume-all")
        s1.append("Pause All", "win.pause-all")
        menu.append_section(None, s1)
        s2 = Gio.Menu()
        s2.append("Open Web Interface", "win.open-webui")
        s2.append("Statistics", "win.statistics")
        menu.append_section(None, s2)
        s3 = Gio.Menu()
        s3.append("Preferences", "app.preferences")
        s3.append("Keyboard Shortcuts", "app.shortcuts")
        s3.append("About Cascade", "app.about")
        menu.append_section(None, s3)
        s4 = Gio.Menu()
        s4.append("Quit and Stop Transfers", "app.quit-all")
        menu.append_section(None, s4)
        mb = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu, tooltip_text="Main menu", primary=True)
        hb.pack_end(mb)

        self.slow_btn = Gtk.ToggleButton(icon_name="cascade-slow-symbolic",
                                         tooltip_text="Slow mode: use the alternative speed limits (Ctrl+T)")
        self.slow_btn.connect("toggled", self._on_slow_toggled)
        hb.pack_end(self.slow_btn)

        self.select_btn = Gtk.ToggleButton(icon_name="selection-mode-symbolic", tooltip_text="Select torrents")
        self.select_btn.connect("toggled", lambda b: self.set_selection_mode(b.get_active()))
        hb.pack_end(self.select_btn)

        self.search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Search (Ctrl+K)")
        hb.pack_end(self.search_btn)

        self.searchbar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search torrents", hexpand=True)
        clamp = Adw.Clamp(maximum_size=520, child=self.search_entry)
        self.searchbar.set_child(clamp)
        self.searchbar.connect_entry(self.search_entry)
        self.searchbar.bind_property("search-mode-enabled", self.search_btn, "active",
                                     GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE)
        self.search_entry.connect("search-changed", self._on_search)
        self.searchbar.set_key_capture_widget(self)
        self.view.add_top_bar(self.searchbar)

    def _build_filter_bar(self):
        self.filter_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                            transition_duration=200)
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        bar.add_css_class("filter-bar")
        self.filter_revealer.set_child(bar)

        sc = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        sc.set_propagate_natural_height(True)
        chips = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        sc.set_child(chips)
        bar.append(sc)
        self.chips = {}
        group = None
        for key, title in FILTERS:
            b = Gtk.ToggleButton()
            b.add_css_class("filter-chip")
            box = Gtk.Box(spacing=6)
            box.append(Gtk.Label(label=title))
            count = Gtk.Label(label="0")
            count.add_css_class("chip-count")
            count.add_css_class("numeric")
            box.append(count)
            b.set_child(box)
            if group:
                b.set_group(group)
            else:
                group = b
            b.connect("toggled", self._on_chip, key)
            b.count = count
            chips.append(b)
            self.chips[key] = b
        self.chips["all"].set_active(True)

        row = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        bar.append(row)
        self.label_drop = Gtk.DropDown.new_from_strings(["All labels"])
        self.label_drop.set_tooltip_text("Label")
        self.label_drop.connect("notify::selected", self._on_label_drop)
        row.append(self._with_icon("cascade-label-symbolic", self.label_drop))
        self.tracker_drop = Gtk.DropDown.new_from_strings(["All trackers"])
        self.tracker_drop.set_tooltip_text("Tracker")
        self.tracker_drop.connect("notify::selected", self._on_tracker_drop)
        row.append(self._with_icon("network-server-symbolic", self.tracker_drop))
        self.sort_drop = Gtk.DropDown.new_from_strings([label for _k, label in schema.SORT_LABELS])
        self.sort_drop.set_tooltip_text("Sort by")
        self.sort_drop.connect("notify::selected", self._on_sort_drop)
        row.append(self._with_icon("view-sort-descending-symbolic", self.sort_drop))
        self.order_btn = Gtk.ToggleButton(icon_name="view-sort-ascending-symbolic", tooltip_text="Reverse order")
        self.order_btn.add_css_class("flat")
        self.order_btn.connect("toggled", self._on_order)
        row.append(self.order_btn)
        self.reset_filter_btn = Gtk.Button(label="Reset", tooltip_text="Show every torrent")
        self.reset_filter_btn.add_css_class("flat")
        self.reset_filter_btn.connect("clicked", lambda *_: self.reset_filters())
        row.append(self.reset_filter_btn)
        self.view.add_top_bar(self.filter_revealer)

    def _with_icon(self, icon, widget):
        b = Gtk.Box(spacing=4)
        img = Gtk.Image(icon_name=icon)
        img.add_css_class("dim-label")
        b.append(img)
        widget.add_css_class("flat")
        b.append(widget)
        return b

    def _build_banner(self):
        self.banner = Adw.Banner(revealed=False)
        self.banner.connect("button-clicked", self._on_banner_button)
        self.view.add_top_bar(self.banner)

    def _build_content(self):
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.view.set_content(self.stack)

        sp = Adw.StatusPage(title="Starting Cascade…", description="Connecting to the Cascade service")
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32, halign=Gtk.Align.CENTER)
        sp.set_child(spinner)
        self.stack.add_named(sp, "connecting")

        self.error_page = Adw.StatusPage(icon_name="network-error-symbolic", title="Cannot Reach the Cascade Service")
        retry = Gtk.Button(label="Try Again", halign=Gtk.Align.CENTER)
        retry.add_css_class("pill")
        retry.add_css_class("suggested-action")
        retry.connect("clicked", lambda *_: self.app.connect_service())
        self.error_page.set_child(retry)
        self.stack.add_named(self.error_page, "error")

        empty = Adw.StatusPage(icon_name="io.github.smiley_mcsmiles.Cascade-symbolic", title="No Torrents Yet",
                               description="Drop a .torrent file or a magnet link here, or click a magnet link "
                                           "in your browser")
        eb = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
        b1 = Gtk.Button(label="Open Torrent File…", action_name="win.add-file")
        b1.add_css_class("pill")
        b1.add_css_class("suggested-action")
        b2 = Gtk.Button(label="Add Magnet Link…", action_name="win.add-magnet")
        b2.add_css_class("pill")
        eb.append(b1)
        eb.append(b2)
        empty.set_child(eb)
        self.stack.add_named(empty, "empty")

        self.nomatch = Adw.StatusPage(icon_name="edit-find-symbolic", title="No Matching Torrents",
                                      description="Try another filter or search")
        nb = Gtk.Button(label="Show All Torrents", halign=Gtk.Align.CENTER)
        nb.add_css_class("pill")
        nb.connect("clicked", lambda *_: self.reset_filters())
        self.nomatch.set_child(nb)
        self.stack.add_named(self.nomatch, "nomatch")

        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        clamp = Adw.Clamp(maximum_size=1100, tightening_threshold=800)
        self.scroller.set_child(clamp)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, valign=Gtk.Align.START)
        self.listbox.add_css_class("torrent-list")
        self.listbox.set_filter_func(self._filter_func)
        self.listbox.set_sort_func(self._sort_func)
        clamp.set_child(self.listbox)
        self.stack.add_named(self.scroller, "list")

    def _build_statusbar(self):
        bar = Gtk.Box(spacing=4)
        bar.add_css_class("statusbar")
        self.view.add_bottom_bar(bar)
        self.speed_btn = Gtk.MenuButton(tooltip_text="Statistics")
        self.speed_btn.add_css_class("flat")
        self.speed_label = Gtk.Label(label="↓ 0 B/s   ↑ 0 B/s")
        self.speed_label.add_css_class("numeric")
        self.speed_btn.set_child(self.speed_label)
        self.speed_btn.set_popover(self._stats_popover())
        bar.append(self.speed_btn)
        bar.append(Gtk.Box(hexpand=True))
        self.status_extra = Gtk.Box(spacing=14)
        bar.append(self.status_extra)

        def item(icon, tooltip):
            b = Gtk.Box(spacing=5, valign=Gtk.Align.CENTER)
            img = Gtk.Image(icon_name=icon)
            lbl = Gtk.Label()
            lbl.add_css_class("numeric")
            b.append(img)
            b.append(lbl)
            b.set_tooltip_text(tooltip)
            b.label = lbl
            b.image = img
            return b

        self.st_free = item("drive-harddisk-symbolic", "Free space in the download folder")
        self.st_dht = item("network-workgroup-symbolic", "DHT nodes")
        self.st_port = item("network-wired-symbolic", "Listening port")
        self.st_iface = item("network-vpn-symbolic", "Bound network interface")
        for w in (self.st_free, self.st_dht, self.st_port):
            self.status_extra.append(w)
        self.status_extra.append(self.st_iface)
        self.st_slow = Gtk.Image(icon_name="cascade-slow-symbolic", tooltip_text="Slow mode is on", visible=False)
        self.st_slow.add_css_class("warning")
        bar.append(self.st_slow)
        self.st_web = Gtk.MenuButton(icon_name="web-browser-symbolic", visible=False, tooltip_text="Web interface")
        self.st_web.add_css_class("flat")
        wm = Gio.Menu()
        wm.append("Open in Browser", "win.open-webui")
        wm.append("Copy Address", "win.copy-webui")
        self.st_web.set_menu_model(wm)
        bar.append(self.st_web)

    def _stats_popover(self):
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        title = Gtk.Label(label="Statistics", xalign=0)
        title.add_css_class("heading")
        box.append(title)
        self.graph = SpeedGraph()
        box.append(self.graph)
        legend = Gtk.Label(xalign=0, use_markup=True)
        legend.add_css_class("caption")
        legend.set_markup("<span foreground='#3584e4'>●</span> Download   <span foreground='#26a269'>●</span> Upload"
                          "   · last 5 minutes")
        box.append(legend)
        grid = Gtk.Grid(column_spacing=18, row_spacing=4)
        self.stat_labels = {}
        for i, (k, t) in enumerate((("session", "This session"), ("alltime", "All time"), ("ratio", "Overall ratio"),
                                    ("peers", "Connected peers"), ("uptime", "Running for"))):
            a = Gtk.Label(label=t, xalign=0)
            a.add_css_class("dim-label")
            b = Gtk.Label(xalign=1, hexpand=True)
            b.add_css_class("numeric")
            grid.attach(a, 0, i, 1, 1)
            grid.attach(b, 1, i, 1, 1)
            self.stat_labels[k] = b
        box.append(grid)
        pop.set_child(box)
        pop.connect("notify::visible", lambda p, _x: p.get_visible() and self._refresh_graph())
        return pop

    def _build_actionbar(self):
        self.actionbar = Gtk.ActionBar(revealed=False)
        self.sel_label = Gtk.Label(label="No torrents selected")
        self.actionbar.set_center_widget(self.sel_label)
        for icon, tip, method in (("media-playback-start-symbolic", "Resume", "torrents.resume"),
                                  ("media-playback-pause-symbolic", "Pause", "torrents.pause"),
                                  ("view-refresh-symbolic", "Verify data", "torrents.recheck")):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip)
            b.connect("clicked", lambda _b, m=method: self.torrent_action(m, self.selected_ids()))
            self.actionbar.pack_start(b)
        lb = Gtk.MenuButton(icon_name="cascade-label-symbolic", tooltip_text="Set label")
        lb.set_create_popup_func(self._fill_bulk_label_menu)
        self.actionbar.pack_start(lb)
        qm = Gio.Menu()
        for w, t in (("top", "Move to Top"), ("up", "Move Up"), ("down", "Move Down"), ("bottom", "Move to Bottom")):
            qm.append(t, "win.queue-selected::" + w)
        self.actionbar.pack_start(Gtk.MenuButton(icon_name="view-list-ordered-symbolic", menu_model=qm,
                                                 tooltip_text="Queue"))
        sa = Gtk.Button(label="Select All")
        sa.connect("clicked", lambda *_: self.select_all())
        self.actionbar.pack_end(sa)
        rm = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove")
        rm.add_css_class("destructive-action")
        rm.connect("clicked", lambda *_: self.selected_ids() and self.confirm_remove(self.selected_ids()))
        self.actionbar.pack_end(rm)
        self.view.add_bottom_bar(self.actionbar)

    def _install_actions(self):
        def add(name, cb, accel=None, ptype=None):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new(ptype) if ptype else None)
            a.connect("activate", cb)
            self.add_action(a)
            if accel:
                self.app.set_accels_for_action("win." + name if not ptype else "win.%s" % name, accel)
            return a

        add("add-file", lambda *_: self.add_file_dialog(), ["<Control>o"])
        add("add-magnet", lambda *_: self.add_magnet_dialog(), ["<Control>u"])
        add("paste", lambda *_: self.paste(), ["<Control><Shift>v"])
        add("create", lambda *_: self.create_dialog(), ["<Control>n"])
        add("filter", lambda *_: self.filter_btn.set_active(not self.filter_btn.get_active()), ["<Control>f"])
        add("search", lambda *_: self.searchbar.set_search_mode(True), ["<Control>k"])
        add("select-all", lambda *_: (self.set_selection_mode(True), self.select_all()), ["<Control>a"])
        add("slow", lambda *_: self.slow_btn.set_active(not self.slow_btn.get_active()), ["<Control>t"])
        add("resume-all", lambda *_: self.torrent_action("torrents.resume", "all", "Resumed every torrent"))
        add("pause-all", lambda *_: self.torrent_action("torrents.pause", "all", "Paused every torrent"))
        add("open-webui", lambda *_: self.open_webui())
        add("copy-webui", lambda *_: self.copy_webui())
        add("statistics", lambda *_: self.speed_btn.popup())
        add("escape", lambda *_: self._on_escape(), ["Escape"])
        add("queue-selected", lambda _a, v: self.torrent_action("torrents.queue", self.selected_ids(),
                                                                 where=v.get_string()), None, "s")

    def _install_dnd(self):
        drop = Gtk.DropTarget.new(GObject.TYPE_NONE, Gdk.DragAction.COPY)
        drop.set_gtypes([Gdk.FileList, GObject.TYPE_STRING])
        drop.connect("drop", self._on_drop)
        drop.connect("enter", lambda *_: (self.add_css_class("drop-target"), Gdk.DragAction.COPY)[1])
        drop.connect("leave", lambda *_: self.remove_css_class("drop-target"))
        self.add_controller(drop)

    # ================================================================ connection & sync
    def on_connected(self):
        self.connected = True
        self.rpc.call("settings.schema", {}, self._got_schema)
        self.rpc.call("settings.get", {}, self._got_settings)
        self.sync()
        if not getattr(self, "_timer", None):
            self._timer = GLib.timeout_add(1000, self._tick)

    def on_connect_failed(self, msg):
        self.connected = False
        self.error_page.set_description(msg)
        self.stack.set_visible_child_name("error")

    def _got_schema(self, s):
        self.schema = s

    def _got_settings(self, values):
        self.settings = values
        self._updating_ui = True
        try:
            self.sort = values.get("ui.sort", "queue")
            self.sort_desc = bool(values.get("ui.sort_desc"))
            keys = [k for k, _ in schema.SORT_LABELS]
            if self.sort in keys:
                self.sort_drop.set_selected(keys.index(self.sort))
            self.order_btn.set_active(self.sort_desc)
        finally:
            self._updating_ui = False
        self.app.apply_theme(values.get("app.theme", "system"))
        self.listbox.invalidate_sort()
        if not getattr(self, "_offered_default", False):
            self._offered_default = True
            GLib.timeout_add_seconds(2, lambda: (self.app.offer_default_handler(), False)[1])

    def _tick(self):
        if self.connected:
            self.sync()
        return True

    def refresh_soon(self):
        GLib.timeout_add(250, lambda: (self.sync(), False)[1])

    def sync(self):
        if self.syncing:
            return
        self.syncing = True
        self.rpc.call("sync", {"since": self.event_seq or 0, "client": "gtk"}, self._on_sync, self._on_sync_failed)

    def _on_sync_failed(self, msg):
        self.syncing = False

    def on_disconnected(self, msg):
        if self.connected:
            self.connected = False
            self.toast("Lost the connection to the Cascade service: %s" % msg)
            self.app.connect_service()

    def _on_sync(self, d):
        self.syncing = False
        self._sync_n += 1
        s = d["session"]
        self.session = s
        first = self.event_seq is None
        if s.get("settings_rev") != self.settings_rev:
            if self.settings_rev is not None:
                self.rpc.call("settings.get", {}, self._got_settings)
            self.settings_rev = s.get("settings_rev")
        seen = set()
        new_rows = False
        for t in d["torrents"]:
            seen.add(t["id"])
            row = self.rows.get(t["id"])
            if row is None:
                row = TorrentRow(self, t)
                self.rows[t["id"]] = row
                self.listbox.append(row)
                row.check.set_visible(self.selection_mode)
                new_rows = True
            else:
                row.update(t)
        for tid in [k for k in self.rows if k not in seen]:
            self.listbox.remove(self.rows.pop(tid))
        self.listbox.invalidate_filter()
        if new_rows or self.sort in DYNAMIC_SORTS:
            self.listbox.invalidate_sort()
        self._update_counts(d["counts"], d["labels"], d["trackers"])
        self._update_page()
        self._update_status(s)
        if self._sync_n % 2 == 0:
            for row in self.rows.values():
                row.refresh_details()
        events = d.get("events") or []
        if not first:
            for ev in events:
                self._on_event(ev)
        self.event_seq = s.get("event_seq", self.event_seq or 0)

    def _update_page(self):
        if not self.rows:
            name = "empty"
        elif not any(self._visible(r.data) for r in self.rows.values()):
            name = "nomatch"
        else:
            name = "list"
        if self.stack.get_visible_child_name() != name:
            self.stack.set_visible_child_name(name)

    def _update_counts(self, counts, labels, trackers):
        for key, b in self.chips.items():
            b.count.set_label(str(counts.get(key, 0)))
        self.label_counts, self.tracker_counts = labels, trackers
        lkeys = sorted(k for k in labels if k)
        if lkeys != self._label_model_keys:
            self._label_model_keys = lkeys
            self._updating_ui = True
            self.label_drop.set_model(Gtk.StringList.new(["All labels", "No label"] + lkeys))
            sel = 0 if self.label_filter is None else (1 if self.label_filter == "" else
                                                       (lkeys.index(self.label_filter) + 2
                                                        if self.label_filter in lkeys else 0))
            if sel == 0:
                self.label_filter = None
            self.label_drop.set_selected(sel)
            self._updating_ui = False
        tkeys = sorted(trackers)
        if tkeys != self._tracker_model_keys:
            self._tracker_model_keys = tkeys
            self._updating_ui = True
            self.tracker_drop.set_model(Gtk.StringList.new(["All trackers"] + tkeys))
            sel = tkeys.index(self.tracker_filter) + 1 if self.tracker_filter in tkeys else 0
            if sel == 0:
                self.tracker_filter = None
            self.tracker_drop.set_selected(sel)
            self._updating_ui = False
        total = counts.get("all", 0)
        shown = sum(1 for r in self.rows.values() if self._visible(r.data))
        parts = []
        if self.filter != "all" or self.label_filter is not None or self.tracker_filter or self.search:
            names = []
            if self.filter != "all":
                names.append(dict(FILTERS)[self.filter])
            if self.label_filter is not None:
                names.append(self.label_filter or "No label")
            if self.tracker_filter:
                names.append(self.tracker_filter)
            if self.search:
                names.append("“%s”" % self.search)
            parts.append("%s · %d of %d" % (", ".join(names), shown, total))
            self.filter_btn.add_css_class("filter-active")
        else:
            parts.append("%d torrent%s" % (total, "" if total == 1 else "s"))
            self.filter_btn.remove_css_class("filter-active")
        dl = counts.get("downloading", 0)
        if dl and self.filter == "all":
            parts.append("%d downloading" % dl)
        self.title_widget.set_subtitle(" · ".join(parts))

    def _update_status(self, s):
        self.speed_label.set_label("↓ %s   ↑ %s" % (util.fmt_speed(s["down"]), util.fmt_speed(s["up"])))
        self.st_free.label.set_label(util.fmt_size(s["free_space"]) + " free" if s["free_space"] >= 0 else "?")
        self.st_dht.label.set_label(str(s["dht_nodes"]))
        self.st_dht.set_visible(bool(self.settings.get("net.dht", True)))
        self.st_port.label.set_label(str(s["port"]))
        self.st_port.set_tooltip_text(s["info"]["port"])
        (self.st_port.add_css_class if s["incoming"] else self.st_port.remove_css_class)("success")
        iface = s["interface"]
        self.st_iface.set_visible(bool(iface.get("name")))
        if iface.get("name"):
            self.st_iface.label.set_label(iface["name"])
            self.st_iface.set_tooltip_text(s["info"]["interface"])
            for c in ("success", "error"):
                self.st_iface.remove_css_class(c)
            self.st_iface.add_css_class("success" if iface.get("usable") else "error")
        self.st_slow.set_visible(s["alt_speed"])
        self.st_slow.set_tooltip_text("Slow mode is on%s" % (" (by the schedule)" if s["alt_source"] == "schedule"
                                                             else ""))
        self._updating_ui = True
        self.slow_btn.set_active(s["alt_speed"])
        self._updating_ui = False
        w = s.get("webui") or {}
        self.st_web.set_visible(bool(w.get("running")))
        if w.get("running"):
            self.st_web.set_tooltip_text("Web interface: " + "  ".join(w.get("urls", [])))
        self.lookup_action("open-webui").set_enabled(bool(w.get("running")))
        # pause banner
        paused = s.get("paused") or []
        if "killswitch" in paused:
            self.banner.set_title("Paused: %s is down. Nothing is sent over your normal connection"
                                  % (iface.get("name") or "the interface"))
            self.banner.set_button_label("Network Settings")
            self.banner.mode = "connection"
            self.banner.set_revealed(True)
        elif "schedule" in paused:
            sch = s.get("schedule") or {}
            nxt = sch.get("next_change")
            self.banner.set_title("Paused by the schedule" + (" for %s" % util.fmt_duration(nxt, True) if nxt else ""))
            self.banner.set_button_label("Edit Schedule")
            self.banner.mode = "speed"
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)
        if self.speed_btn.get_popover().get_visible() and self._sync_n % 2 == 0:
            self._refresh_graph()

    def _refresh_graph(self):
        s = self.session
        if s:
            self.stat_labels["session"].set_label("↓ %s  ↑ %s" % (util.fmt_size(s["session_down"]),
                                                                   util.fmt_size(s["session_up"])))
            self.stat_labels["alltime"].set_label("↓ %s  ↑ %s" % (util.fmt_size(s["alltime_down"]),
                                                                   util.fmt_size(s["alltime_up"])))
            ratio = s["alltime_up"] / s["alltime_down"] if s["alltime_down"] else 0
            self.stat_labels["ratio"].set_label("%.2f" % ratio)
            self.stat_labels["peers"].set_label("%d · DHT %d nodes" % (s["peers"], s["dht_nodes"]))
            self.stat_labels["uptime"].set_label(util.fmt_duration(s["uptime"], True))
        self.rpc.call("stats.history", {}, self.graph.update)

    def _on_banner_button(self, _b):
        self.app.show_preferences(getattr(self.banner, "mode", None))

    # ================================================================ events
    def _on_event(self, ev):
        kind, level = ev["kind"], ev["level"]
        if kind == "finished":
            self.app.notify("Download finished", ev.get("name") or ev["message"], "finished-" + (ev.get("id") or ""))
            if self.is_active():
                self.toast(ev["message"])
        elif kind == "metadata":
            t = Adw.Toast(title=GLib.markup_escape_text(ev["message"]), button_label="Choose Files", timeout=8)
            t.connect("button-clicked", lambda *_: self.reveal_torrent(ev.get("id"), "files"))
            self.toasts.add_toast(t)
        elif kind == "limit" or kind == "moved" and level == "error":
            self.toast(ev["message"])
        elif level == "error" or kind in ("interface", "webui", "ipfilter", "port") and level == "warning":
            self.toast(ev["message"], 6)

    def toast(self, msg, timeout=3):
        t = Adw.Toast(title=GLib.markup_escape_text(str(msg)), timeout=timeout)
        self.toasts.add_toast(t)

    def reveal_torrent(self, tid, section=None):
        row = self.rows.get(tid)
        if not row:
            return
        self.reset_filters()
        row.set_expanded(True)
        if section and row.details:
            row.details.exp[section].set_expanded(True)
        GLib.idle_add(lambda: (row.grab_focus(), False)[1])

    # ================================================================ filtering & sorting
    def _visible(self, t):
        if not matches(t, self.filter):
            return False
        if self.label_filter is not None and t["label"] != self.label_filter:
            return False
        if self.tracker_filter and t["tracker"] != self.tracker_filter:
            return False
        if self.search and self.search not in t["name"].lower():
            return False
        return True

    def _filter_func(self, row):
        return self._visible(row.data)

    def _sort_func(self, a, b):
        key = SORT_KEYS.get(self.sort, SORT_KEYS["queue"])
        ka, kb = key(a.data), key(b.data)
        r = (ka > kb) - (ka < kb)
        if r == 0:
            r = (a.data["name"].lower() > b.data["name"].lower()) - (a.data["name"].lower() < b.data["name"].lower())
            return r
        return -r if self.sort_desc else r

    def _on_chip(self, b, key):
        if b.get_active():
            self.filter = key
            self._refilter()

    def _on_label_drop(self, d, _p):
        if self._updating_ui:
            return
        i = d.get_selected()
        keys = self._label_model_keys or []
        self.label_filter = None if i == 0 else ("" if i == 1 else keys[i - 2])
        self._refilter()

    def _on_tracker_drop(self, d, _p):
        if self._updating_ui:
            return
        i = d.get_selected()
        keys = self._tracker_model_keys or []
        self.tracker_filter = None if i == 0 or i - 1 >= len(keys) else keys[i - 1]
        self._refilter()

    def _on_sort_drop(self, d, _p):
        if self._updating_ui:
            return
        self.sort = schema.SORT_LABELS[d.get_selected()][0]
        self.listbox.invalidate_sort()
        self.rpc.call("settings.set", {"values": {"ui.sort": self.sort}})

    def _on_order(self, b):
        b.set_icon_name("view-sort-descending-symbolic" if b.get_active() else "view-sort-ascending-symbolic")
        if self._updating_ui:
            return
        self.sort_desc = b.get_active()
        self.listbox.invalidate_sort()
        self.rpc.call("settings.set", {"values": {"ui.sort_desc": self.sort_desc}})

    def _on_search(self, e):
        self.search = e.get_text().strip().lower()
        self._refilter()

    def _refilter(self):
        if not hasattr(self, "listbox"):
            return          # still building the window
        self.listbox.invalidate_filter()
        self._update_page()
        self.sync()

    def reset_filters(self):
        self.chips["all"].set_active(True)
        self.label_drop.set_selected(0)
        self.tracker_drop.set_selected(0)
        self.label_filter = self.tracker_filter = None
        self.search_entry.set_text("")
        self.searchbar.set_search_mode(False)
        self._refilter()

    def _on_escape(self):
        if self.selection_mode:
            self.select_btn.set_active(False)
        elif self.searchbar.get_search_mode():
            self.searchbar.set_search_mode(False)
        elif self.filter_btn.get_active():
            self.filter_btn.set_active(False)

    # ================================================================ selection mode
    def set_selection_mode(self, on):
        self.selection_mode = on
        if self.select_btn.get_active() != on:
            self.select_btn.set_active(on)
        for r in self.rows.values():
            r.check.set_visible(on)
            if not on:
                r.check.set_active(False)
        self.actionbar.set_revealed(on)
        if not on:
            self._anchor = None
        self.on_selection_changed()

    def visible_rows(self):
        """Rows in the order they are shown (sorted, filtered)."""
        out, i = [], 0
        while True:
            r = self.listbox.get_row_at_index(i)
            if r is None:
                return out
            if self._visible(r.data):
                out.append(r)
            i += 1

    def select_click(self, row, ctrl, shift):
        """Ctrl+click toggles one torrent, Shift+click selects the range from the last clicked one."""
        if not self.selection_mode:
            self.set_selection_mode(True)
        anchor = getattr(self, "_anchor", None)
        rows = self.visible_rows()
        if shift and anchor in rows and row in rows:
            a, b = sorted((rows.index(anchor), rows.index(row)))
            for r in rows[a:b + 1]:
                r.check.set_active(True)
        else:
            row.check.set_active(not row.check.get_active())
            self._anchor = row
        self.on_selection_changed()

    def selected_ids(self):
        return [tid for tid, r in self.rows.items() if r.check.get_active() and r.get_child_visible()]

    def select_all(self):
        visible = [r for r in self.rows.values() if self._visible(r.data)]
        on = not all(r.check.get_active() for r in visible)
        for r in visible:
            r.check.set_active(on)

    def on_selection_changed(self):
        n = len(self.selected_ids())
        self.sel_label.set_label("%d selected" % n if n else "No torrents selected")

    def on_expanded_changed(self, row):
        pass

    def label_names(self):
        return [k for k in self.label_counts if k]

    def _fill_bulk_label_menu(self, button):
        m = Gio.Menu()
        m.append("No Label", "win.bulk-label::")
        for name in sorted(self.label_names()):
            m.append(name, "win.bulk-label::" + name)
        m.append("New Label…", "win.bulk-label-new")
        if not self.lookup_action("bulk-label"):
            a = Gio.SimpleAction.new("bulk-label", GLib.VariantType.new("s"))
            a.connect("activate", lambda _a, v: self.torrent_action("torrents.set_options", self.selected_ids(),
                                                                     options={"label": v.get_string()}))
            self.add_action(a)
            a2 = Gio.SimpleAction.new("bulk-label-new", None)
            a2.connect("activate", lambda *_: self.ask_text("New Label", "Label name", "Set",
                                                            lambda name: self.torrent_action(
                                                                "torrents.set_options", self.selected_ids(),
                                                                options={"label": name})))
            self.add_action(a2)
        button.set_menu_model(m)

    # ================================================================ actions
    def torrent_action(self, method, ids, message=None, **params):
        if not ids:
            self.toast("Select some torrents first")
            return
        params["ids"] = ids
        self.rpc.call(method, params, lambda _r: (message and self.toast(message), self.refresh_soon()),
                      self.toast)

    def _on_slow_toggled(self, b):
        if self._updating_ui:
            return
        on = b.get_active()
        self.rpc.call("alt_speed.set", {"on": on},
                      lambda r: self.toast("Slow mode on" if r else "Slow mode off"), self.toast)

    def confirm_remove(self, ids):
        names = [self.rows[i].data["name"] for i in ids if i in self.rows]
        if not names:
            return
        d = Adw.AlertDialog(heading="Remove %s?" % ("“%s”" % names[0] if len(names) == 1 else "%d Torrents"
                                                    % len(names)),
                            body="The downloaded files are kept unless you delete them too.")
        d.add_response("cancel", "Cancel")
        d.add_response("data", "Delete Files Too")
        d.add_response("remove", "Remove")
        d.set_response_appearance("data", Adw.ResponseAppearance.DESTRUCTIVE)
        d.set_response_appearance("remove", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("remove")
        d.set_close_response("cancel")

        def done(_d, resp):
            if resp in ("remove", "data"):
                self.rpc.call("torrents.remove", {"ids": ids, "delete_data": resp == "data"},
                              lambda _r: (self.toast("Removed %s" % (names[0] if len(names) == 1 else
                                                                     "%d torrents" % len(names))),
                                          self.set_selection_mode(False) if self.selection_mode else None,
                                          self.refresh_soon()), self.toast)

        if not self.settings.get("app.confirm_remove", True):
            done(None, "remove")
            return
        d.connect("response", done)
        d.present(self)

    def ask_text(self, heading, placeholder, button, cb, text="", body=None):
        d = Adw.AlertDialog(heading=heading, body=body or "")
        entry = Gtk.Entry(placeholder_text=placeholder, text=text, activates_default=True)
        d.set_extra_child(entry)
        d.add_response("cancel", "Cancel")
        d.add_response("ok", button)
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")
        d.set_close_response("cancel")
        d.connect("response", lambda _d, r: r == "ok" and entry.get_text().strip() and cb(entry.get_text().strip()))
        d.present(self)
        entry.grab_focus()

    def choose_folder(self, title, cb, initial=None):
        fd = Gtk.FileDialog(title=title, modal=True)
        if initial and os.path.isdir(initial):
            fd.set_initial_folder(Gio.File.new_for_path(initial))

        def done(dlg, res):
            try:
                f = dlg.select_folder_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                cb(f.get_path())

        fd.select_folder(self, None, done)

    def export_torrent(self, tid):
        def got(r):
            fd = Gtk.FileDialog(title="Save .torrent File", initial_name=r["name"] + ".torrent")

            def done(dlg, res):
                try:
                    f = dlg.save_finish(res)
                except GLib.Error:
                    return
                with open(f.get_path(), "wb") as fh:
                    fh.write(base64.b64decode(r["data"]))
                self.toast("Saved %s" % os.path.basename(f.get_path()))

            fd.save(self, None, done)

        self.rpc.call("torrents.export", {"id": tid}, got, self.toast)

    # ================================================================ adding torrents
    def add_file_dialog(self):
        fd = Gtk.FileDialog(title="Open Torrent Files", modal=True)
        flt = Gtk.FileFilter(name="Torrent files")
        flt.add_pattern("*.torrent")
        flt.add_mime_type("application/x-bittorrent")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(flt)
        fd.set_filters(filters)
        fd.set_default_filter(flt)

        def done(dlg, res):
            try:
                files = dlg.open_multiple_finish(res)
            except GLib.Error:
                return
            for i in range(files.get_n_items()):
                self.add_source(path=files.get_item(i).get_path())

        fd.open_multiple(self, None, done)

    def add_magnet_dialog(self, text=""):
        if not text:
            self._clipboard_text(lambda t: self._magnet_dialog("\n".join(util.split_sources(t))))
        else:
            self._magnet_dialog(text)

    def _magnet_dialog(self, text):
        d = Adw.AlertDialog(heading="Add Magnet Links",
                            body="Paste one or more magnet links or web addresses of .torrent files, one per line")
        tv = Gtk.TextView(wrap_mode=Gtk.WrapMode.CHAR, top_margin=8, bottom_margin=8, left_margin=10,
                          right_margin=10, accepts_tab=False)
        tv.add_css_class("magnet-text")
        tv.get_buffer().set_text(text)
        sw = Gtk.ScrolledWindow(child=tv, min_content_height=110, max_content_height=260,
                                propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        frame = Gtk.Frame(child=sw)
        d.set_extra_child(frame)
        d.add_response("cancel", "Cancel")
        d.add_response("ok", "Add")
        d.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        d.set_default_response("ok")
        d.set_close_response("cancel")

        def done(_d, r):
            if r != "ok":
                return
            b = tv.get_buffer()
            self.add_many(b.get_text(b.get_start_iter(), b.get_end_iter(), False))

        d.connect("response", done)
        d.present(self)
        tv.grab_focus()

    def add_many(self, text):
        """One link opens the add sheet; several are added straight away with the default options."""
        items = util.split_sources(text)
        if not items:
            self.toast("No magnet links or URLs found")
        elif len(items) == 1:
            self.add_source_text(items[0])
        else:
            n = {"ok": 0, "left": len(items)}

            def one(_r=None, failed=False):
                n["left"] -= 1
                n["ok"] += 0 if failed else 1
                if n["left"] == 0:
                    self.toast("Added %d of %d torrents" % (n["ok"], len(items)))
                    self.refresh_soon()

            for it in items:
                src = {"magnet": it} if util.is_magnet(it) else {"url": it}
                self.rpc.call("torrents.add", dict(src, options={}), one, lambda m: (self.toast(m), one(failed=True)))

    def _clipboard_text(self, cb):
        cl = self.get_clipboard()

        def done(c, res):
            try:
                cb((c.read_text_finish(res) or "").strip())
            except GLib.Error:
                cb("")

        cl.read_text_async(None, done)

    def paste(self):
        def got(text):
            if util.split_sources(text):
                self.add_many(text)
            else:
                self.toast("The clipboard has no magnet link or URL")

        self._clipboard_text(got)

    def add_source_text(self, text):
        text = text.strip()
        if util.is_magnet(text):
            self.add_source(magnet=text)
        elif util.is_url(text):
            self.add_source(url=text)
        elif os.path.isfile(os.path.expanduser(text)):
            self.add_source(path=os.path.expanduser(text))
        else:
            self.toast("Not a magnet link, URL or torrent file")

    def add_source(self, magnet=None, path=None, url=None, data=None):
        src = {k: v for k, v in (("magnet", magnet), ("path", path), ("url", url), ("data", data)) if v}
        if not self.connected:
            self.app.pending.append(src)
            return
        if not self.settings.get("add.show_dialog", True):
            self.rpc.call("torrents.add", dict(src, options={}), self._added, self.toast)
            return
        from .add_dialog import AddDialog

        def got(info):
            AddDialog(self, src, info).present(self)

        self.rpc.call("torrents.inspect", src, got, lambda m: self.toast("Cannot add: %s" % m))

    def _added(self, r):
        if r.get("duplicate"):
            self.toast("“%s” is already in the list (trackers merged)" % r["name"])
        else:
            self.toast("Added “%s”" % r["name"])
        self.refresh_soon()

    def _on_drop(self, _t, value, _x, _y):
        self.remove_css_class("drop-target")
        if isinstance(value, Gdk.FileList):
            for f in value.get_files():
                p = f.get_path()
                if p and p.lower().endswith(".torrent"):
                    self.add_source(path=p)
                elif f.get_uri().startswith("magnet:"):
                    self.add_source(magnet=f.get_uri())
            return True
        if isinstance(value, str):
            self.add_many(value)
            return True
        return False

    def create_dialog(self):
        from .create_dialog import CreateDialog
        CreateDialog(self).present(self)

    # ================================================================ web interface
    def _webui_url(self):
        w = (self.session or {}).get("webui") or {}
        urls = w.get("urls") or []
        return urls[0] if urls else None

    def open_webui(self):
        url = self._webui_url()
        if url:
            Gtk.UriLauncher.new(url).launch(self, None, None)
        else:
            self.toast("The web interface is off - turn it on in Preferences → Remote")

    def copy_webui(self):
        w = (self.session or {}).get("webui") or {}
        urls = w.get("urls") or []
        if urls:
            self.get_clipboard().set(urls[-1])
            self.toast("Copied %s" % urls[-1])
