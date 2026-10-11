"""One torrent in the main list: a card that expands in place to show everything about the torrent.

Clicking a torrent never opens a window: the card slides open with transfer tiles and rows for Information, Files,
Peers, Trackers, Pieces and Options, each of which rolls down when clicked."""

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import util  # noqa: E402
from .widgets import PiecesBar, stat_tile  # noqa: E402

STATE_ICONS = {
    "downloading": "folder-download-symbolic",
    "seeding": "send-to-symbolic",
    "paused": "media-playback-pause-symbolic",
    "queued": "view-list-ordered-symbolic",
    "checking": "view-refresh-symbolic",
    "metadata": "network-receive-symbolic",
    "moving": "folder-symbolic",
    "error": "dialog-error-symbolic",
}
PRIO_LABELS = ["Skip", "Low", "Normal", "High"]
PRIO_VALUES = [0, 1, 4, 7]
TRACKER_STATUS = {"working": "Working", "updating": "Announcing…", "error": "Error", "waiting": "Not contacted yet"}


def prio_index(p):
    return 0 if p == 0 else (1 if p < 4 else (2 if p < 7 else 3))


def subtitle_for(t):
    st = t["state"]
    if t["error"]:
        return t["error"]
    if st == "metadata":
        return "Fetching metadata from %d peer%s" % (t["peers"] + t["seeds"], "" if t["peers"] + t["seeds"] == 1
                                                     else "s")
    if st == "checking":
        return "Checking files · %.1f%%" % (t["progress"] * 100)
    if st == "moving":
        return "Moving files…"
    size = "%s of %s" % (util.fmt_size(t["done"]), util.fmt_size(t["size"])) if t["progress"] < 1 else \
        util.fmt_size(t["size"])
    parts = []
    if st == "downloading":
        parts.append(size + " · %.1f%%" % (t["progress"] * 100))
        if t["eta"] >= 0:
            parts.append(util.fmt_duration(t["eta"], True) + " left")
        parts.append("%d of %d peers" % (t["seeds"] + t["peers"], max(t["seeds_total"], 0) + max(t["peers_total"], 0)))
    elif st == "seeding":
        parts.append(size)
        parts.append("ratio " + util.fmt_ratio(t["ratio"]))
        parts.append("uploading to %d" % t["peers"] if t["peers"] else "seeding")
    else:
        parts.append(size + (" · %.1f%%" % (t["progress"] * 100) if t["progress"] < 1 else ""))
        parts.append("Queued" if st == "queued" else "Paused")
    return " · ".join(parts)


def speeds_for(t):
    out = []
    if t["down"] > 0:
        out.append("↓ " + util.fmt_speed(t["down"]))
    if t["up"] > 0:
        out.append("↑ " + util.fmt_speed(t["up"]))
    return "   ".join(out)


def copy_text(widget, text):
    widget.get_clipboard().set(text)


def info_row(title, copy=False):
    row = Adw.ActionRow(title=title)
    val = Gtk.Label(xalign=1, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True, max_width_chars=64,
                    justify=Gtk.Justification.RIGHT)
    # a wrapping label only asks for its narrowest width, so the row squeezed values down to "N/o": ask for the
    # whole value (up to a hash) and wrap only what is longer than that
    val.connect("notify::label", lambda v, _p: v.set_width_chars(min(len(v.get_label()), 40)))
    val.add_css_class("dim-label")
    val.add_css_class("numeric")
    row.add_suffix(val)
    if copy:
        b = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Copy")
        b.add_css_class("flat")
        b.connect("clicked", lambda *_: copy_text(b, val.get_label()))
        row.add_suffix(b)
    return row, val


class TorrentRow(Gtk.ListBoxRow):
    def __init__(self, window, t):
        super().__init__(activatable=False, selectable=False)
        self.win = window
        self.tid = t["id"]
        self.data = t
        self.expanded = False
        self.details = None
        self.add_css_class("torrent-card")

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(outer)
        top = Gtk.Box(spacing=0)
        outer.append(top)

        self.check = Gtk.CheckButton(valign=Gtk.Align.CENTER, margin_start=12, visible=False)
        self.check.add_css_class("selection-mode")
        self.check.connect("toggled", lambda *_: self.win.on_selection_changed())
        top.append(self.check)

        self.header = Gtk.Button(hexpand=True)
        self.header.add_css_class("flat")
        self.header.add_css_class("torrent-header")
        self.header.connect("clicked", self._on_header)
        # Ctrl+click / Shift+click select, like a file manager - no need to turn on selection mode first
        mod = Gtk.GestureClick(button=1, propagation_phase=Gtk.PropagationPhase.CAPTURE)
        mod.connect("pressed", self._on_modified_press)
        self.header.add_controller(mod)
        top.append(self.header)

        hb = Gtk.Box(spacing=12)
        self.header.set_child(hb)
        self.icon = Gtk.Image(icon_name="folder-download-symbolic", valign=Gtk.Align.CENTER, pixel_size=16)
        self.icon.add_css_class("state-icon")
        hb.append(self.icon)

        mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True, valign=Gtk.Align.CENTER)
        hb.append(mid)
        trow = Gtk.Box(spacing=8)
        self.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, hexpand=True)
        self.title.add_css_class("heading")
        trow.append(self.title)
        self.chip = Gtk.Label(visible=False, valign=Gtk.Align.CENTER)
        self.chip.add_css_class("label-chip")
        trow.append(self.chip)
        mid.append(trow)
        self.progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER)
        self.progress.add_css_class("torrent-progress")
        mid.append(self.progress)
        srow = Gtk.Box(spacing=12)
        self.sub = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        self.sub.add_css_class("caption")
        self.sub.add_css_class("dim-label")
        self.sub.add_css_class("numeric")
        srow.append(self.sub)
        self.speed = Gtk.Label(xalign=1)
        self.speed.add_css_class("caption")
        self.speed.add_css_class("numeric")
        self.speed.add_css_class("speed-label")
        srow.append(self.speed)
        mid.append(srow)

        self.arrow = Gtk.Image(icon_name="pan-down-symbolic", valign=Gtk.Align.CENTER)
        self.arrow.add_css_class("expand-arrow")
        hb.append(self.arrow)

        self.toggle_btn = Gtk.Button(valign=Gtk.Align.CENTER, margin_end=10, margin_start=2)
        self.toggle_btn.add_css_class("circular")
        self.toggle_btn.add_css_class("flat")
        self.toggle_btn.connect("clicked", self._on_toggle_pause)
        top.append(self.toggle_btn)

        self.revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=220)
        outer.append(self.revealer)
        self.update(t, force=True)

    # ------------------------------------------------------------ summary
    def update(self, t, force=False):
        old = self.data
        self.data = t
        if force or old["name"] != t["name"]:
            self.title.set_label(t["name"])
            self.title.set_tooltip_text(t["name"])
        if force or old["state"] != t["state"] or old["forced"] != t["forced"]:
            for s in STATE_ICONS:
                self.remove_css_class("state-" + s)
            self.add_css_class("state-" + t["state"])
            self.icon.set_from_icon_name(STATE_ICONS.get(t["state"], "folder-download-symbolic"))
            self.icon.set_tooltip_text(util.STATE_LABELS.get(t["state"], t["state"]) + (" (forced)" if t["forced"]
                                                                                        else ""))
        paused = t["state"] in ("paused", "queued") or (t["paused"] and not t["auto_managed"])
        self.toggle_btn.set_icon_name("media-playback-start-symbolic" if paused else "media-playback-pause-symbolic")
        self.toggle_btn.set_tooltip_text("Resume" if paused else "Pause")
        self.progress.set_fraction(t["progress"])
        self.sub.set_label(subtitle_for(t))
        self.speed.set_label(speeds_for(t))
        if force or old["label"] != t["label"]:
            self.chip.set_label(t["label"])
            self.chip.set_visible(bool(t["label"]))
        if self.details:
            self.details.update_summary(t)

    def _on_toggle_pause(self, _b):
        t = self.data
        paused = t["state"] in ("paused", "queued") or (t["paused"] and not t["auto_managed"])
        self.win.torrent_action("torrents.resume" if paused else "torrents.pause", [self.tid])

    # ------------------------------------------------------------ expansion
    def _on_modified_press(self, g, _n, _x, _y):
        state = g.get_current_event_state()
        ctrl = bool(state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.META_MASK))
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if ctrl or shift:
            self._modified_click = True        # the button still emits "clicked" afterwards: skip that one
            self.win.select_click(self, ctrl, shift)

    def _on_header(self, _b):
        if getattr(self, "_modified_click", False):
            self._modified_click = False
            return
        if self.win.selection_mode:
            self.win.select_click(self, True, False)
            return
        self.set_expanded(not self.expanded)

    def set_expanded(self, on):
        if on == self.expanded:
            return
        self.expanded = on
        if on and self.details is None:
            self.details = Details(self)
            self.revealer.set_child(self.details)
            self.details.update_summary(self.data)
        (self.add_css_class if on else self.remove_css_class)("expanded")
        self.revealer.set_reveal_child(on)
        if on:
            self.details.refresh()
        self.win.on_expanded_changed(self)

    def refresh_details(self):
        if self.expanded and self.details:
            self.details.refresh()


class Details(Gtk.Box):
    """The expanded part of a torrent card."""

    def __init__(self, row):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.row = row
        self.win = row.win
        self.tid = row.tid
        self.add_css_class("torrent-details")
        self.loading = False
        self.options_loaded = False
        self.file_rows = {}
        self.peer_rows = {}
        self.tracker_rows = {}
        self.last_info = {}
        self._building_options = False

        # ---- actions
        bar = Gtk.Box(spacing=6)
        bar.add_css_class("details-actions")
        self.append(bar)
        self.pause_btn = self._action_button("media-playback-pause-symbolic", "Pause", self._toggle_pause)
        bar.append(self.pause_btn)
        bar.append(self._action_button("folder-open-symbolic", "Open Folder", self._open_folder))
        bar.append(self._action_button("insert-link-symbolic", "Copy Magnet", self._copy_magnet))
        spacer = Gtk.Box(hexpand=True)
        bar.append(spacer)
        more = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text="More actions", valign=Gtk.Align.CENTER)
        more.add_css_class("flat")
        more.set_menu_model(self._menu())
        bar.append(more)
        rm = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove…", valign=Gtk.Align.CENTER)
        rm.add_css_class("flat")
        rm.add_css_class("destructive-text")
        rm.connect("clicked", lambda *_: self.win.confirm_remove([self.tid]))
        bar.append(rm)
        self.insert_action_group("t", self._actions())

        # ---- transfer tiles
        self.tiles = {}
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True, min_children_per_line=2,
                           max_children_per_line=6, column_spacing=8, row_spacing=8)
        flow.add_css_class("stat-flow")
        for key, cap in (("downloaded", "Downloaded"), ("uploaded", "Uploaded"), ("ratio", "Ratio"),
                         ("down", "Download speed"), ("up", "Upload speed"), ("eta", "Time left"),
                         ("seeds", "Seeds"), ("peers", "Peers"), ("availability", "Availability"),
                         ("active", "Active time"), ("seeding", "Seeding time"), ("size", "Size")):
            w, lbl = stat_tile(cap)
            self.tiles[key] = lbl
            child = Gtk.FlowBoxChild(focusable=False)
            child.set_child(w)
            flow.append(child)
        self.append(flow)

        # ---- the rolling-down rows
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lb.add_css_class("boxed-list")
        self.append(lb)
        self.exp = {}
        for key, title, icon in (("info", "Information", "dialog-information-symbolic"),
                                 ("files", "Files", "folder-documents-symbolic"),
                                 ("peers", "Peers", "system-users-symbolic"),
                                 ("trackers", "Trackers", "network-server-symbolic"),
                                 ("pieces", "Pieces", "view-grid-symbolic"),
                                 ("options", "Options", "emblem-system-symbolic")):
            e = Adw.ExpanderRow(title=title)
            e.add_prefix(Gtk.Image(icon_name=icon))
            e.connect("notify::expanded", self._on_expander)
            lb.append(e)
            self.exp[key] = e
        self._build_info()
        self._build_pieces()
        self._build_peers_extra()
        self._build_trackers_extra()
        self._build_options()

    # ------------------------------------------------------------ action helpers
    def _action_button(self, icon, label, cb):
        b = Gtk.Button(valign=Gtk.Align.CENTER)
        b.set_child(Adw.ButtonContent(icon_name=icon, label=label))
        b.add_css_class("flat")
        b.connect("clicked", lambda *_: cb())
        return b

    def _menu(self):
        m = Gio.Menu()
        s1 = Gio.Menu()
        s1.append("Force Start", "t.force")
        s1.append("Verify Data", "t.recheck")
        s1.append("Ask Trackers for Peers", "t.reannounce")
        m.append_section(None, s1)
        s2 = Gio.Menu()
        s2.append("Move to Top of Queue", "t.queue::top")
        s2.append("Move Up in Queue", "t.queue::up")
        s2.append("Move Down in Queue", "t.queue::down")
        s2.append("Move to Bottom of Queue", "t.queue::bottom")
        m.append_section(None, s2)
        s3 = Gio.Menu()
        s3.append("Move Data To…", "t.move")
        s3.append("Save .torrent File…", "t.export")
        s3.append("Copy Info Hash", "t.copyhash")
        m.append_section(None, s3)
        return m

    def _actions(self):
        g = Gio.SimpleActionGroup()

        def add(name, cb, ptype=None):
            a = Gio.SimpleAction.new(name, GLib.VariantType.new(ptype) if ptype else None)
            a.connect("activate", cb)
            g.add_action(a)

        add("force", lambda *_: self.win.torrent_action("torrents.force_start", [self.tid]))
        add("recheck", lambda *_: self.win.torrent_action("torrents.recheck", [self.tid]))
        add("reannounce", lambda *_: self.win.torrent_action("torrents.reannounce", [self.tid], "Announced"))
        add("queue", lambda _a, v: self.win.torrent_action("torrents.queue", [self.tid], where=v.get_string()), "s")
        add("move", lambda *_: self.win.choose_folder("Move Data To", lambda p: self.win.torrent_action(
            "torrents.move", [self.tid], "Moving…", path=p), self.row.data.get("save_path")))
        add("export", lambda *_: self.win.export_torrent(self.tid))
        add("copyhash", lambda *_: (copy_text(self, self.tid), self.win.toast("Info hash copied")))
        return g

    def _toggle_pause(self):
        self.row._on_toggle_pause(None)

    def _open_folder(self):
        path = self.row.data.get("save_path") or ""
        name = self.row.data.get("name") or ""
        target = os.path.join(path, name)
        f = Gio.File.new_for_path(target if os.path.exists(target) else path)
        if os.path.isdir(f.get_path() or ""):
            Gtk.FileLauncher.new(f).launch(self.win, None, None)
        else:
            Gtk.FileLauncher.new(f).open_containing_folder(self.win, None, None)

    def _copy_magnet(self):
        self.win.rpc.call("torrents.magnet", {"id": self.tid},
                          lambda m: (copy_text(self, m), self.win.toast("Magnet link copied")), self.win.toast)

    # ------------------------------------------------------------ building rows
    def _build_info(self):
        e = self.exp["info"]
        self.info = {}
        for key, title, copy in (("hash_v1", "Info hash v1", True), ("hash_v2", "Info hash v2", True),
                                 ("total_size", "Total size", False), ("pieces", "Pieces", False),
                                 ("num_files", "Files", False), ("save_path", "Saved in", True),
                                 ("added", "Added", False), ("completed", "Completed", False),
                                 ("created", "Created", False), ("comment", "Comment", False),
                                 ("private", "Private", False), ("wasted", "Wasted", False),
                                 ("current_tracker", "Current tracker", False),
                                 ("next_announce", "Next announce", False)):
            r, v = info_row(title, copy)
            e.add_row(r)
            self.info[key] = (r, v)

    def _build_pieces(self):
        e = self.exp["pieces"]
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        self.pieces_bar = PiecesBar()
        box.append(self.pieces_bar)
        legend = Gtk.Box(spacing=16)
        for css, text in (("legend-have", "Downloaded"), ("legend-active", "Downloading"),
                          ("legend-missing", "Missing")):
            dot = Gtk.Box(valign=Gtk.Align.CENTER)
            dot.add_css_class("legend-dot")
            dot.add_css_class(css)
            lbl = Gtk.Label(label=text)
            lbl.add_css_class("caption")
            hb = Gtk.Box(spacing=6)
            hb.append(dot)
            hb.append(lbl)
            legend.append(hb)
        box.append(legend)
        row = Gtk.ListBoxRow(activatable=False)
        row.set_child(box)
        e.add_row(row)

    def _build_peers_extra(self):
        self.peers_empty = Adw.ActionRow(title="No peers connected")
        self.peers_empty.add_css_class("dim-label")
        self.exp["peers"].add_row(self.peers_empty)
        self.peer_add = Adw.EntryRow(title="Connect to a peer (address:port)", show_apply_button=True)
        self.peer_add.connect("apply", self._on_add_peer)
        self.exp["peers"].add_row(self.peer_add)

    def _build_trackers_extra(self):
        e = self.exp["trackers"]
        self.sources_row = Adw.ActionRow(title="DHT · PeX · LSD")
        e.add_row(self.sources_row)
        self.tracker_add = Adw.EntryRow(title="Add a tracker URL", show_apply_button=True)
        self.tracker_add.connect("apply", self._on_add_tracker)
        e.add_row(self.tracker_add)

    def _build_options(self):
        e = self.exp["options"]
        self.opt = {}

        def spin(key, title, subtitle, upper):
            r = Adw.SpinRow.new_with_range(0, upper, 1)
            r.set_title(title)
            r.set_subtitle(subtitle)
            r.connect("notify::value", lambda w, _p: self._opt_changed(key, int(w.get_value())))
            e.add_row(r)
            self.opt[key] = r

        def switch(key, title, subtitle=""):
            r = Adw.SwitchRow(title=title, subtitle=subtitle)
            r.connect("notify::active", lambda w, _p: self._opt_changed(key, w.get_active()))
            e.add_row(r)
            self.opt[key] = r

        spin("download_limit", "Download limit", "KiB/s · 0 = no limit", 10000000)
        spin("upload_limit", "Upload limit", "KiB/s · 0 = no limit", 10000000)
        spin("max_connections", "Maximum connections", "0 = the global setting", 65535)
        spin("max_uploads", "Upload slots", "0 = the global setting", 10000)
        switch("sequential", "Download pieces in order")
        switch("first_last", "First and last pieces first")
        switch("super_seeding", "Super seeding", "Helps spread a torrent you are the first seed of")
        switch("auto_managed", "Managed by the queue")
        r = Adw.ComboRow(title="Seeding limit", model=Gtk.StringList.new(
            ["Use the global setting", "Never stop", "Stop at a ratio"]))
        r.connect("notify::selected", self._on_ratio_mode)
        e.add_row(r)
        self.opt["ratio_mode"] = r
        r = Adw.SpinRow.new_with_range(0, 1000, 0.05)
        r.set_digits(2)
        r.set_title("Share ratio")
        r.set_visible(False)
        r.connect("notify::value", lambda w, _p: self._opt_changed("ratio_limit", round(w.get_value(), 2)))
        e.add_row(r)
        self.opt["ratio_limit"] = r
        lab = Adw.EntryRow(title="Label", show_apply_button=True)
        lab.connect("apply", lambda w: self._opt_changed("label", w.get_text().strip()))
        self.label_menu = Gtk.MenuButton(icon_name="pan-down-symbolic", valign=Gtk.Align.CENTER,
                                         tooltip_text="Existing labels")
        self.label_menu.add_css_class("flat")
        self.label_menu.set_create_popup_func(self._fill_label_menu)
        lab.add_suffix(self.label_menu)
        e.add_row(lab)
        self.opt["label"] = lab

    def _fill_label_menu(self, button):
        m = Gio.Menu()
        m.append("No label", "t.setlabel::")
        for name in sorted(self.win.label_names()):
            m.append(name, "t.setlabel::" + name)
        g = self.get_action_group("t")
        if not g.lookup_action("setlabel"):
            a = Gio.SimpleAction.new("setlabel", GLib.VariantType.new("s"))
            a.connect("activate", lambda _a, v: (self.opt["label"].set_text(v.get_string()),
                                                 self._opt_changed("label", v.get_string())))
            g.add_action(a)
        button.set_menu_model(m)

    # ------------------------------------------------------------ option changes
    def _on_ratio_mode(self, combo, _p):
        idx = combo.get_selected()
        self.opt["ratio_limit"].set_visible(idx == 2)
        if self._building_options:
            return
        if idx == 0:
            self._opt_changed("ratio_limit", None)
        elif idx == 1:
            self._opt_changed("ratio_limit", -1)
        else:
            self._opt_changed("ratio_limit", round(self.opt["ratio_limit"].get_value(), 2))

    def _opt_changed(self, key, value):
        if self._building_options:
            return
        if key in ("download_limit", "upload_limit", "max_connections", "max_uploads", "ratio_limit"):
            # spin buttons fire on every step: wait until the value settles
            if getattr(self, "_debounce", None):
                GLib.source_remove(self._debounce[0])
            self._debounce = (GLib.timeout_add(600, self._send_opt, key, value), key)
            return
        self._send_opt(key, value)

    def _send_opt(self, key, value):
        self._debounce = None
        self.win.rpc.call("torrents.set_options", {"ids": [self.tid], "options": {key: value}},
                          lambda _r: self.win.refresh_soon(), self.win.toast)
        return False

    def _on_add_peer(self, entry):
        addr = entry.get_text().strip()
        if addr:
            self.win.rpc.call("peers.add", {"id": self.tid, "address": addr},
                              lambda _r: (entry.set_text(""), self.win.toast("Connecting to %s" % addr)),
                              self.win.toast)

    def _on_add_tracker(self, entry):
        url = entry.get_text().strip()
        if url:
            self.win.rpc.call("trackers.add", {"id": self.tid, "urls": [url]},
                              lambda _r: (entry.set_text(""), self.refresh()), self.win.toast)

    def _reannounce_tracker(self, url):
        self.win.rpc.call("trackers.reannounce", {"id": self.tid, "url": url},
                          lambda _r: (self.win.toast("Announcing to %s" % url.split("/")[2] if "//" in url else url),
                                      GLib.timeout_add(1500, lambda: (self.refresh(), False)[1])), self.win.toast)

    def _remove_tracker(self, url):
        self.win.rpc.call("trackers.remove", {"id": self.tid, "urls": [url]}, lambda _r: self.refresh(),
                          self.win.toast)

    def _on_expander(self, e, _p):
        if e.get_expanded():
            self.refresh()

    # ------------------------------------------------------------ refreshing
    def update_summary(self, t):
        T = self.tiles
        T["downloaded"].set_label(util.fmt_size(t["downloaded"]))
        T["uploaded"].set_label(util.fmt_size(t["uploaded"]))
        T["ratio"].set_label(util.fmt_ratio(t["ratio"]))
        T["down"].set_label(util.fmt_speed(t["down"]))
        T["up"].set_label(util.fmt_speed(t["up"]))
        T["eta"].set_label(util.fmt_duration(t["eta"], True) if t["eta"] >= 0 else ("Done" if t["finished"] else "∞"))
        T["seeds"].set_label("%d (%d)" % (t["seeds"], max(0, t["seeds_total"])))
        T["peers"].set_label("%d (%d)" % (t["peers"], max(0, t["peers_total"])))
        T["size"].set_label(util.fmt_size(t["size"]) if t["size"] else "Unknown")
        paused = t["state"] in ("paused", "queued") or (t["paused"] and not t["auto_managed"])
        self.pause_btn.get_child().set_icon_name("media-playback-start-symbolic" if paused
                                                 else "media-playback-pause-symbolic")
        self.pause_btn.get_child().set_label("Resume" if paused else "Pause")

    def refresh(self):
        if self.loading:
            return
        sections = ["info"]
        for key in ("files", "peers", "trackers", "pieces"):
            if self.exp[key].get_expanded():
                sections.append(key)
        if not self.options_loaded or self.exp["options"].get_expanded() and not self._focus_in_options():
            sections.append("options")
        # counts in the subtitles are useful even while collapsed
        if "peers" not in sections:
            sections.append("peers")
        if "trackers" not in sections and not self.tracker_rows:
            sections.append("trackers")
        self.loading = True
        self.win.rpc.call("torrents.details", {"id": self.tid, "sections": sections}, self._apply, self._failed)

    def _focus_in_options(self):
        f = self.win.get_focus()
        while f is not None:
            if f is self.exp["options"]:
                return True
            f = f.get_parent()
        return False

    def _failed(self, msg):
        self.loading = False

    def _apply(self, d):
        self.loading = False
        if "info" in d:
            self._apply_info(d["info"])
        if "files" in d:
            self._apply_files(d["files"])
        if "peers" in d:
            self._apply_peers(d["peers"])
        if "trackers" in d:
            self._apply_trackers(d["trackers"], d.get("sources", {}))
        if "pieces" in d:
            p = d["pieces"]
            self.pieces_bar.update(p)
            self.exp["pieces"].set_subtitle("%d of %d · %s each" % (p["have"], p["count"],
                                                                    util.fmt_size(p["piece_size"])))
        if "options" in d:
            self._apply_options(d["options"])

    def _apply_info(self, i):
        T = self.tiles
        T["availability"].set_label("%.2f" % i["availability"] if i["availability"] >= 0 else "-")
        T["active"].set_label(util.fmt_duration(i["active_time"], True))
        T["seeding"].set_label(util.fmt_duration(i["seeding_time"], True) if i["seeding_time"] else "-")
        vals = {
            "hash_v1": i["hash_v1"] or "-",
            "hash_v2": i["hash_v2"] or "-",
            "total_size": util.fmt_size(i["total_size"]) if i["total_size"] >= 0 else "Unknown",
            "pieces": ("%d × %s" % (i["pieces"], util.fmt_size(i["piece_size"]))) if i["pieces"] else "-",
            "num_files": str(i["num_files"]) if i["num_files"] else "-",
            "save_path": i["save_path"] + (("  →  " + i["move_to"]) if i.get("move_to") else ""),
            "added": util.fmt_time(i["added"]),
            "completed": util.fmt_time(i["completed"]),
            "created": util.fmt_time(i["created"]) + ((" by " + i["creator"]) if i["creator"] else ""),
            "comment": i["comment"] or "-",
            "private": "Yes" if i["private"] else "No",
            "wasted": util.fmt_size(i["wasted"]),
            "current_tracker": i["current_tracker"] or "-",
            "next_announce": util.fmt_duration(i["next_announce"], True) if i["next_announce"] >= 0 else "-",
        }
        for k, (row, lbl) in self.info.items():
            if lbl.get_label() != vals[k]:
                lbl.set_label(vals[k])
            row.set_visible(not (k == "hash_v2" and not i["hash_v2"]) and not (k == "hash_v1" and not i["hash_v1"]))
        h = i["hash_v1"] or i["hash_v2"]
        self.exp["info"].set_subtitle("%s · %s%s" % (h[:12] + "…", vals["total_size"],
                                                      " · private" if i["private"] else ""))
        self.exp["files"].set_subtitle("%d file%s" % (i["num_files"], "" if i["num_files"] == 1 else "s")
                                       if i["num_files"] else "Waiting for metadata")
        self.last_info = i

    def _apply_files(self, files):
        e = self.exp["files"]
        limit = 1000
        for f in files[:limit]:
            r = self.file_rows.get(f["index"])
            if r is None:
                r = FileRow(self, f)
                e.add_row(r)
                self.file_rows[f["index"]] = r
            r.update(f)
        if len(files) > limit and not getattr(self, "_more_row", None):
            self._more_row = Adw.ActionRow(title="%d more files" % (len(files) - limit),
                                           subtitle="Use “cascade files” in a terminal to see them all")
            e.add_row(self._more_row)

    def _apply_peers(self, peers):
        e = self.exp["peers"]
        e.set_subtitle("%d connected" % len(peers) if peers else "None connected")
        if not e.get_expanded():
            return
        seen = set()
        added = False
        for p in peers[:300]:
            key = "%s:%d" % (p["ip"], p["port"])
            seen.add(key)
            r = self.peer_rows.get(key)
            if r is None:
                added = True
                r = Adw.ActionRow(title=("[%s]:%d" if ":" in p["ip"] else "%s:%d") % (p["ip"], p["port"]))
                r.add_css_class("property-row")
                sl = Gtk.Label(xalign=1)
                sl.add_css_class("numeric")
                sl.add_css_class("caption")
                r.add_suffix(sl)
                r.speed = sl
                e.add_row(r)
                self.peer_rows[key] = r
            r.set_subtitle("%s · %s · %.0f%%%s" % (GLib.markup_escape_text(p["client"]), p["flags"] or "-",
                                                    p["progress"] * 100, " · µTP" if p["utp"] else ""))
            r.speed.set_label("↓ %s\n↑ %s" % (util.fmt_speed(p["down"]), util.fmt_speed(p["up"])))
        for key in list(self.peer_rows):
            if key not in seen:
                e.remove(self.peer_rows.pop(key))
        if added and not self.peer_add.has_focus():
            # ExpanderRow only appends: keep "connect to a peer" below the list
            e.remove(self.peer_add)
            e.add_row(self.peer_add)
        self.peers_empty.set_visible(not peers)

    def _apply_trackers(self, trackers, sources):
        e = self.exp["trackers"]
        working = sum(1 for t in trackers if t["status"] == "working")
        e.set_subtitle("%d tracker%s · %d working" % (len(trackers), "" if len(trackers) == 1 else "s", working)
                       if trackers else "No trackers (DHT and PeX only)")
        seen = set()
        added = False
        for t in trackers:
            seen.add(t["url"])
            r = self.tracker_rows.get(t["url"])
            if r is None:
                added = True
                r = Adw.ActionRow(title=GLib.markup_escape_text(t["url"]))
                r.set_title_lines(1)
                r.set_subtitle_lines(2)
                ra = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text="Reannounce to this tracker now")
                ra.add_css_class("flat")
                ra.connect("clicked", lambda _b, u=t["url"]: self._reannounce_tracker(u))
                r.add_suffix(ra)
                b = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Remove")
                b.add_css_class("flat")
                b.connect("clicked", lambda _b, u=t["url"]: self._remove_tracker(u))
                r.add_suffix(b)
                e.add_row(r)
                self.tracker_rows[t["url"]] = r
            bits = [TRACKER_STATUS.get(t["status"], t["status"])]
            if t["seeds"] >= 0:
                bits.append("%d seeds · %d peers" % (t["seeds"], max(0, t["peers"])))
            if t["downloaded"] >= 0:
                bits.append("%d downloads" % t["downloaded"])
            if t["next_announce"] >= 0 and t["status"] != "updating":
                bits.append("next in " + util.fmt_duration(t["next_announce"], True))
            if t["message"]:
                bits.append(t["message"])
            r.set_subtitle(GLib.markup_escape_text(" · ".join(bits)))
            (r.add_css_class if t["status"] == "error" else r.remove_css_class)("error-row")
        for url in list(self.tracker_rows):
            if url not in seen:
                e.remove(self.tracker_rows.pop(url))
        if added and not self.tracker_add.has_focus():
            for w in (self.sources_row, self.tracker_add):
                e.remove(w)
                e.add_row(w)
        on = [k.upper() if k != "pex" else "PeX" for k in ("dht", "pex", "lsd") if sources.get(k)]
        self.sources_row.set_subtitle(("Also finding peers through " + ", ".join(on)) if on else
                                      "DHT, PeX and LSD are off for this torrent")

    def _apply_options(self, o):
        self._building_options = True
        try:
            for k in ("download_limit", "upload_limit", "max_connections", "max_uploads"):
                self.opt[k].set_value(o[k] or 0)
            for k in ("sequential", "first_last", "super_seeding", "auto_managed"):
                self.opt[k].set_active(bool(o[k]))
            rl = o.get("ratio_limit")
            self.opt["ratio_mode"].set_selected(0 if rl is None else (1 if rl < 0 else 2))
            self.opt["ratio_limit"].set_visible(rl is not None and rl >= 0)
            if rl is not None and rl >= 0:
                self.opt["ratio_limit"].set_value(rl)
            if not self.opt["label"].has_focus():
                self.opt["label"].set_text(o.get("label") or "")
            bits = []
            if o["download_limit"] or o["upload_limit"]:
                bits.append("speed limited")
            if o["sequential"]:
                bits.append("in order")
            if o["super_seeding"]:
                bits.append("super seeding")
            if not o["auto_managed"]:
                bits.append("not queued")
            self.exp["options"].set_subtitle(", ".join(bits).capitalize() if bits else "Default settings")
        finally:
            self._building_options = False
        self.options_loaded = True


class FileRow(Adw.ActionRow):
    def __init__(self, details, f):
        super().__init__()
        self.details = details
        self.index = f["index"]
        self.prio = None
        self._setting = False
        name = f["path"].rsplit("/", 1)
        self.set_title(GLib.markup_escape_text(name[-1]))
        self.folder = name[0] if len(name) > 1 else ""
        self.set_title_lines(2)
        self.check = Gtk.CheckButton(valign=Gtk.Align.CENTER, tooltip_text="Download this file")
        self.check.connect("toggled", self._on_check)
        self.add_prefix(self.check)
        self.level = Gtk.LevelBar(valign=Gtk.Align.CENTER, width_request=60)
        self.level.add_css_class("file-level")
        self.add_suffix(self.level)
        self.drop = Gtk.DropDown.new_from_strings(PRIO_LABELS)
        self.drop.set_valign(Gtk.Align.CENTER)
        self.drop.add_css_class("flat")
        self.drop.connect("notify::selected", self._on_drop)
        self.add_suffix(self.drop)

    def update(self, f):
        frac = f["done"] / f["size"] if f["size"] else 1.0
        self.set_subtitle(GLib.markup_escape_text("%s%s of %s · %.0f%%" % (
            (self.folder + " · ") if self.folder else "", util.fmt_size(f["done"]), util.fmt_size(f["size"]),
            frac * 100)))
        self.level.set_value(frac)
        if f["priority"] != self.prio:
            self.prio = f["priority"]
            self._setting = True
            self.check.set_active(f["priority"] > 0)
            self.drop.set_selected(prio_index(f["priority"]))
            self._setting = False

    def _send(self, prio):
        self.prio = prio
        self.details.win.rpc.call("torrents.set_files", {"id": self.details.tid,
                                                         "priorities": {str(self.index): prio}},
                                  None, self.details.win.toast)

    def _on_check(self, c):
        if self._setting:
            return
        prio = 4 if c.get_active() else 0
        self._setting = True
        self.drop.set_selected(prio_index(prio))
        self._setting = False
        self._send(prio)

    def _on_drop(self, d, _p):
        if self._setting:
            return
        prio = PRIO_VALUES[d.get_selected()]
        self._setting = True
        self.check.set_active(prio > 0)
        self._setting = False
        self._send(prio)
