"""Create a .torrent file from a file or a folder."""

import base64
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402


PIECE_SIZES = [("Automatic", 0)] + [("%d KiB" % k if k < 1024 else "%d MiB" % (k // 1024), k * 1024)
                                    for k in (16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384)]
VERSIONS = [("Hybrid (v1 and v2)", "hybrid"), ("Version 1", "v1"), ("Version 2", "v2")]


class CreateDialog(Adw.Dialog):
    def __init__(self, win):
        super().__init__(title="Create Torrent", content_width=560, content_height=700)
        self.win = win
        self.path = None
        self.job = None
        tv = Adw.ToolbarView()
        self.set_child(tv)
        hb = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        hb.pack_start(cancel)
        self.create_btn = Gtk.Button(label="Create", sensitive=False)
        self.create_btn.add_css_class("suggested-action")
        self.create_btn.connect("clicked", self._on_create)
        hb.pack_end(self.create_btn)
        tv.add_top_bar(hb)

        page = Adw.PreferencesPage()
        tv.set_content(page)
        g = Adw.PreferencesGroup(title="Content")
        page.add(g)
        self.src_row = Adw.ActionRow(title="File or folder", subtitle="Nothing chosen yet")
        self.src_row.add_css_class("property")
        bf = Gtk.Button(icon_name="document-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Choose a file")
        bf.add_css_class("flat")
        bf.connect("clicked", lambda *_: self._choose(False))
        bd = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Choose a folder")
        bd.add_css_class("flat")
        bd.connect("clicked", lambda *_: self._choose(True))
        self.src_row.add_suffix(bf)
        self.src_row.add_suffix(bd)
        g.add(self.src_row)

        tg = Adw.PreferencesGroup(title="Trackers", description="One URL per line; an empty line starts a new tier")
        page.add(tg)
        self.trackers = self._text(tg)
        wg = Adw.PreferencesGroup(title="Web Seeds", description="Optional HTTP mirrors, one per line")
        page.add(wg)
        self.webseeds = self._text(wg, 60)

        og = Adw.PreferencesGroup(title="Options")
        page.add(og)
        self.comment = Adw.EntryRow(title="Comment")
        og.add(self.comment)
        self.source = Adw.EntryRow(title="Source tag (for private trackers)")
        og.add(self.source)
        self.private = Adw.SwitchRow(title="Private torrent", subtitle="Peers only come from its trackers")
        og.add(self.private)
        self.piece = Adw.ComboRow(title="Piece size", model=Gtk.StringList.new([p[0] for p in PIECE_SIZES]))
        og.add(self.piece)
        self.version = Adw.ComboRow(title="Format", model=Gtk.StringList.new([v[0] for v in VERSIONS]))
        og.add(self.version)
        self.seed = Adw.SwitchRow(title="Start seeding it", active=True)
        og.add(self.seed)
        self.progress = Gtk.ProgressBar(visible=False, show_text=True, margin_top=12)
        og.add(self.progress)

    def _text(self, group, height=90):
        frame = Gtk.Frame()
        tv = Gtk.TextView(monospace=True, top_margin=8, bottom_margin=8, left_margin=10, right_margin=10,
                          wrap_mode=Gtk.WrapMode.CHAR, height_request=height)
        frame.set_child(tv)
        frame.add_css_class("view")
        group.add(frame)
        return tv

    def _choose(self, folder):
        fd = Gtk.FileDialog(title="Choose a Folder" if folder else "Choose a File", modal=True)

        def done(dlg, res):
            try:
                f = dlg.select_folder_finish(res) if folder else dlg.open_finish(res)
            except GLib.Error:
                return
            self.path = f.get_path()
            self.src_row.set_subtitle(GLib.markup_escape_text(self.path))
            self.create_btn.set_sensitive(True)

        (fd.select_folder if folder else fd.open)(self.win, None, done)

    def _lines(self, tv):
        b = tv.get_buffer()
        return b.get_text(b.get_start_iter(), b.get_end_iter(), False).splitlines()

    def _on_create(self, _b):
        name = os.path.basename(self.path.rstrip("/")) + ".torrent"
        fd = Gtk.FileDialog(title="Save the .torrent File", initial_name=name)
        fd.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.path)))

        def done(dlg, res):
            try:
                f = dlg.save_finish(res)
            except GLib.Error:
                return
            self.output = f.get_path()
            self._start()

        fd.save(self.win, None, done)

    def _start(self):
        params = {"path": self.path, "trackers": self._lines(self.trackers),
                  "web_seeds": [w for w in self._lines(self.webseeds) if w.strip()],
                  "comment": self.comment.get_text(), "source": self.source.get_text(),
                  "private": self.private.get_active(), "piece_size": PIECE_SIZES[self.piece.get_selected()][1],
                  "version": VERSIONS[self.version.get_selected()][1], "seed": self.seed.get_active()}
        self.create_btn.set_sensitive(False)
        self.progress.set_visible(True)
        self.progress.set_text("Hashing…")
        self.win.rpc.call("create.start", params, self._started, self._failed)

    def _started(self, job):
        self.job = job
        GLib.timeout_add(300, self._poll)

    def _poll(self):
        self.win.rpc.call("create.status", {"job": self.job}, self._status, self._failed)
        return False

    def _status(self, st):
        if st["state"] == "hashing":
            self.progress.set_fraction(st["progress"])
            self.progress.set_text("Hashing… %d%%" % (st["progress"] * 100))
            GLib.timeout_add(300, self._poll)
        elif st["state"] == "done":
            res = st["result"]
            with open(self.output, "wb") as fh:
                fh.write(base64.b64decode(res["data"]))
            self.win.toast("Created %s" % os.path.basename(self.output))
            self.win.refresh_soon()
            self.close()
        else:
            self._failed(st.get("error") or "failed")

    def _failed(self, msg):
        self.progress.set_visible(False)
        self.create_btn.set_sensitive(True)
        self.win.toast("Cannot create the torrent: %s" % msg)
