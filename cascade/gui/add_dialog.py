"""The sheet shown before adding a torrent: folder, label, options and which files to download."""

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import util  # noqa: E402

MAX_FILE_ROWS = 2000


class AddDialog(Adw.Dialog):
    def __init__(self, win, source, info):
        super().__init__(title="Add Torrent", content_width=560, content_height=680)
        self.win = win
        self.source = dict(source)
        if info.get("data") and "url" in self.source:
            self.source = {"data": info["data"]}       # the daemon already downloaded it: don't fetch twice
        self.info = info
        s = win.settings
        labels = win.label_names()

        tv = Adw.ToolbarView()
        self.set_child(tv)
        hb = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        hb.pack_start(cancel)
        self.add_btn = Gtk.Button(label="Add")
        self.add_btn.add_css_class("suggested-action")
        self.add_btn.connect("clicked", self._on_add)
        hb.pack_end(self.add_btn)
        tv.add_top_bar(hb)
        self.set_default_widget(self.add_btn)
        # Enter adds, wherever the focus is, unless the focused widget uses Enter itself (a button, a switch)
        keys = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.BUBBLE)
        keys.add_shortcut(Gtk.Shortcut(trigger=Gtk.ShortcutTrigger.parse_string("Return|KP_Enter"),
                                       action=Gtk.CallbackAction.new(self._on_enter)))
        self.add_controller(keys)
        # GTK would focus Cancel, the first button, and Enter would cancel: start on Add like GNOME's dialogs
        self.connect("map", lambda *_: self.add_btn.grab_focus())

        page = Adw.PreferencesPage()
        tv.set_content(page)

        g = Adw.PreferencesGroup()
        page.add(g)
        head = Adw.ActionRow(title=GLib.markup_escape_text(info["name"]), title_lines=3)
        head.add_css_class("heading-row")
        icon = Gtk.Image(icon_name="insert-link-symbolic" if not info["has_metadata"] else
                         "io.github.smiley_mcsmiles.Cascade-symbolic", pixel_size=32)
        head.add_prefix(icon)
        if info["has_metadata"]:
            n = len(info["files"])
            sub = "%s · %d file%s%s" % (util.fmt_size(info["size"]), n, "" if n == 1 else "s",
                                        " · private" if info.get("private") else "")
        else:
            sub = "Magnet link · the file list arrives with the metadata"
        head.set_subtitle(sub)
        g.add(head)
        if info.get("duplicate"):
            warn = Adw.ActionRow(title="Already in your list",
                                 subtitle="Adding it again only merges its trackers into the existing torrent")
            warn.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
            warn.add_css_class("warning")
            g.add(warn)
        if info.get("comment"):
            c = Adw.ActionRow(title="Comment", subtitle=GLib.markup_escape_text(info["comment"]))
            c.add_css_class("property")
            g.add(c)

        og = Adw.PreferencesGroup(title="Options")
        page.add(og)
        self.save_path = s.get("downloads.save_path") or os.path.expanduser("~/Downloads")
        self.path_row = Adw.ActionRow(title="Save to", subtitle=GLib.markup_escape_text(self.save_path))
        self.path_row.add_css_class("property")
        pick = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Choose a folder")
        pick.add_css_class("flat")
        pick.connect("clicked", self._pick_folder)
        self.path_row.add_suffix(pick)
        self.path_row.set_activatable_widget(pick)
        og.add(self.path_row)
        free = util.free_space(self.save_path)
        self.free_label = Gtk.Label(label="%s free" % util.fmt_size(free) if free >= 0 else "")
        self.free_label.add_css_class("dim-label")
        self.free_label.add_css_class("caption")
        self.path_row.add_suffix(self.free_label)
        self.path_row.add_suffix(Gtk.Box())

        self.label_row = Adw.EntryRow(title="Label", activates_default=True)
        if labels:
            mb = Gtk.MenuButton(icon_name="pan-down-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Labels")
            mb.add_css_class("flat")
            pop = Gtk.Popover()
            lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            lb.add_css_class("menu")
            for name in sorted(labels):
                r = Gtk.ListBoxRow()
                r.set_child(Gtk.Label(label=name, xalign=0, margin_start=6, margin_end=6, margin_top=4,
                                      margin_bottom=4))
                r.name = name
                lb.append(r)
            lb.connect("row-activated", lambda _l, r: (self.label_row.set_text(r.name), pop.popdown()))
            pop.set_child(lb)
            mb.set_popover(pop)
            self.label_row.add_suffix(mb)
        og.add(self.label_row)

        self.sw = {}
        rows = [("paused", "Add paused", s.get("add.start_paused", False)),
                ("sequential", "Download pieces in order", s.get("add.sequential", False)),
                ("first_last", "First and last pieces first", s.get("add.first_last", False)),
                ("top_of_queue", "Put it at the top of the queue", s.get("add.top_of_queue", False))]
        if info["has_metadata"]:
            rows.append(("skip_check", "The files are already complete", False))
        else:
            rows.append(("pause_after_metadata", "Choose files when the metadata arrives",
                         s.get("add.magnet_choose_files", False)))
        for key, title, val in rows:
            r = Adw.SwitchRow(title=title, active=bool(val))
            if key == "skip_check":
                r.set_subtitle("Skip checking: only for data you know is complete")
            og.add(r)
            self.sw[key] = r

        self.file_checks = []
        if info["has_metadata"] and info["files"]:
            fg = Adw.PreferencesGroup(title="Files")
            sel = Gtk.Box(spacing=6)
            for label, on in (("All", True), ("None", False)):
                b = Gtk.Button(label=label)
                b.add_css_class("flat")
                b.connect("clicked", lambda _b, v=on: [c.set_active(v) for _i, c in self.file_checks])
                sel.append(b)
            fg.set_header_suffix(sel)
            page.add(fg)
            self.selected_label = Gtk.Label()
            for f in info["files"][:MAX_FILE_ROWS]:
                parts = f["path"].rsplit("/", 1)
                r = Adw.ActionRow(title=GLib.markup_escape_text(parts[-1]), title_lines=2)
                r.set_subtitle(GLib.markup_escape_text(("%s · " % parts[0] if len(parts) > 1 else "")
                                                        + util.fmt_size(f["size"])))
                chk = Gtk.CheckButton(active=True, valign=Gtk.Align.CENTER)
                chk.connect("toggled", lambda *_: self._update_selected())
                r.add_prefix(chk)
                r.set_activatable_widget(chk)
                fg.add(r)
                self.file_checks.append((f["index"], chk))
            if len(info["files"]) > MAX_FILE_ROWS:
                fg.add(Adw.ActionRow(title="%d more files are included" % (len(info["files"]) - MAX_FILE_ROWS)))
            self.files_group = fg
            self._update_selected()

    def _update_selected(self):
        total = sum(f["size"] for f in self.info["files"])
        idx = {i for i, c in self.file_checks if c.get_active()}
        chosen = sum(f["size"] for f in self.info["files"] if f["index"] in idx or
                     f["index"] not in {i for i, _ in self.file_checks})
        self.files_group.set_description("%s of %s selected" % (util.fmt_size(chosen), util.fmt_size(total)))
        self.add_btn.set_sensitive(bool(idx) or not self.file_checks)

    def _pick_folder(self, _b):
        def done(path):
            self.save_path = path
            self.path_row.set_subtitle(GLib.markup_escape_text(path))
            free = util.free_space(path)
            self.free_label.set_label("%s free" % util.fmt_size(free) if free >= 0 else "")

        self.win.choose_folder("Save Torrent To", done, self.save_path)

    def _on_enter(self, *_a):
        if self.add_btn.get_sensitive():
            self.add_btn.emit("clicked")
        return True

    def _on_add(self, _b):
        opts = {k: r.get_active() for k, r in self.sw.items()}
        label = self.label_row.get_text().strip()
        if label:
            opts["label"] = label
        if self.save_path != self.win.settings.get("downloads.save_path"):
            opts["save_path"] = self.save_path
        if self.file_checks:
            n = max(f["index"] for f in self.info["files"]) + 1
            prios = [4] * n
            for i, c in self.file_checks:
                prios[i] = 4 if c.get_active() else 0
            # pad files are not listed; libtorrent sets them itself, so a longer list is harmless
            opts["file_priorities"] = prios
        params = dict(self.source)
        params["options"] = opts
        self.add_btn.set_sensitive(False)
        self.win.rpc.call("torrents.add", params, self._done, self._failed)

    def _done(self, r):
        self.close()
        self.win._added(r)

    def _failed(self, msg):
        self.add_btn.set_sensitive(True)
        self.win.toast("Cannot add: %s" % msg)
