"""The GTK application.  One instance per session: magnet links clicked in a browser and .torrent files opened from
the file manager are handed to the running window (Gio.Application handles the single-instance part)."""

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import APP_ID, APP_NAME, URL, __version__, paths  # noqa: E402
from .rpc import AsyncRpc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

SHORTCUTS = """<?xml version="1.0" encoding="UTF-8"?>
<interface>
  <object class="GtkShortcutsWindow" id="shortcuts">
    <property name="modal">1</property>
    <child>
      <object class="GtkShortcutsSection">
        <property name="section-name">main</property>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">Torrents</property>
            %s
          </object>
        </child>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title">General</property>
            %s
          </object>
        </child>
      </object>
    </child>
  </object>
</interface>"""


def _sc(accel, title):
    return ('<child><object class="GtkShortcutsShortcut"><property name="accelerator">%s</property>'
            '<property name="title">%s</property></object></child>' % (GLib.markup_escape_text(accel), title))


class CascadeApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        GLib.set_application_name(APP_NAME)
        self.win = None
        self.rpc = None
        self.pending = []
        self.quitting = False
        self.add_main_option("hidden", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Start without showing the window", None)

    # ------------------------------------------------------------ lifecycle
    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(HERE, "style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        theme.add_search_path(os.path.join(HERE, "icons"))
        share = paths.share_dir()
        if share and os.path.isdir(os.path.join(share, "icons")):
            theme.add_search_path(os.path.join(share, "icons"))
        Gtk.Window.set_default_icon_name(APP_ID)

        def act(name, cb, accels=None):
            a = Gio.SimpleAction.new(name, None)
            a.connect("activate", cb)
            self.add_action(a)
            if accels:
                self.set_accels_for_action("app." + name, accels)

        act("preferences", lambda *_: self.show_preferences(), ["<Control>comma"])
        act("about", lambda *_: self.show_about())
        act("shortcuts", lambda *_: self.show_shortcuts(), ["<Control>question"])
        act("quit", lambda *_: self.quit_window(), ["<Control>q"])
        act("quit-all", lambda *_: self.quit_all())

        self.rpc = AsyncRpc()
        self.rpc.on_disconnect = lambda msg: self.win and self.win.on_disconnected(msg)

    def do_command_line(self, cmdline):
        opts = cmdline.get_options_dict().end().unpack()
        args = cmdline.get_arguments()[1:]
        files = []
        for a in args:
            if a == "gui":
                continue
            if a.startswith("magnet:") or a.startswith(("http://", "https://")):
                files.append(a)
            else:
                f = cmdline.create_file_for_arg(a)
                files.append(f.get_path() or f.get_uri())
        self._ensure_window(show=not opts.get("hidden"))
        for f in files:
            self._open_source(f)
        return 0

    def do_activate(self):
        self._ensure_window()

    def do_open(self, files, n, hint):
        self._ensure_window()
        for f in files:
            uri = f.get_uri()
            self._open_source(uri if uri.startswith("magnet:") else (f.get_path() or uri))

    def _open_source(self, s):
        if s.startswith("magnet:"):
            src = {"magnet": s}
        elif s.startswith(("http://", "https://")):
            src = {"url": s}
        elif s.startswith("file://"):
            src = {"path": Gio.File.new_for_uri(s).get_path()}
        else:
            src = {"path": s}
        if self.win and self.win.connected:
            self.win.add_source(**src)
        else:
            self.pending.append(src)

    def _ensure_window(self, show=True):
        if self.win is None:
            from .window import MainWindow
            self.win = MainWindow(self)
            self.win.connect("close-request", self._on_close)
            self.connect_service()
        if show:
            self.win.present()

    def connect_service(self):
        if self.win:
            self.win.stack.set_visible_child_name("connecting")
        self.rpc.connect(self._connected, self._connect_failed)

    def _connected(self, _ok):
        self.win.on_connected()
        pending, self.pending = self.pending, []

        def flush():
            for src in pending:
                self.win.add_source(**src)
            return False

        if pending:
            GLib.timeout_add(600, flush)

    def _connect_failed(self, msg):
        self.win.on_connect_failed(msg)

    # ------------------------------------------------------------ closing
    def _on_close(self, _w):
        s = self.win.settings if self.win else {}
        if not s.get("app.background", True) and not self.quitting:
            try:
                self.rpc.call_sync("daemon.shutdown")
            except Exception:
                pass
        self.win = None
        self.quit()
        return False

    def quit_window(self):
        if self.win:
            self.win.close()
        else:
            self.quit()

    def quit_all(self):
        self.quitting = True
        try:
            self.rpc.call_sync("daemon.shutdown")
        except Exception:
            pass
        self.quit_window()

    # ------------------------------------------------------------ helpers
    def apply_theme(self, theme):
        sm = Adw.StyleManager.get_default()
        sm.set_color_scheme({"light": Adw.ColorScheme.FORCE_LIGHT, "dark": Adw.ColorScheme.FORCE_DARK}.get(
            theme, Adw.ColorScheme.DEFAULT))

    def notify(self, title, body, nid=None, always=False):
        if not self.win or not (always or self.win.settings.get("notify.finished", True)):
            return
        n = Gio.Notification.new(title)
        n.set_body(body)
        n.set_icon(Gio.ThemedIcon.new(APP_ID))
        self.send_notification(nid, n)

    @staticmethod
    def desktop_info():
        try:
            return Gio.DesktopAppInfo.new(APP_ID + ".desktop")
        except TypeError:          # PyGObject raises instead of returning None when it is not installed
            return None

    def is_default_handler(self):
        try:
            cur = Gio.AppInfo.get_default_for_uri_scheme("magnet")
        except Exception:
            cur = None
        return bool(cur and cur.get_id() == APP_ID + ".desktop")

    def make_default_handler(self):
        """Make Cascade the handler of magnet: links and .torrent files (what a browser asks the desktop for)."""
        info = self.desktop_info()
        if info is None:
            if self.win:
                self.win.toast("Install Cascade first (./install.sh) so the desktop knows its launcher")
            return False
        try:
            info.set_as_default_for_type("x-scheme-handler/magnet")
            info.set_as_default_for_type("application/x-bittorrent")
        except GLib.Error as e:
            if self.win:
                self.win.toast("Could not change the default: %s" % e.message)
            return False
        if self.win:
            self.win.toast("Magnet links and .torrent files now open in Cascade")
        return True

    def offer_default_handler(self):
        """Once, ask to become the magnet link handler when another app (or nothing) is."""
        win = self.win
        if not win or win.settings.get("ui.default_asked") or self.is_default_handler():
            return
        if self.desktop_info() is None:
            return            # running from a source checkout: there is no launcher to register
        t = Adw.Toast(title="Open magnet links from your browser with Cascade?", button_label="Make Default",
                      timeout=0)
        t.connect("button-clicked", lambda *_: self.make_default_handler())
        win.toasts.add_toast(t)
        win.rpc.call("settings.set", {"values": {"ui.default_asked": True}})

    def show_preferences(self, page=None):
        if not self.win:
            return
        if not (self.win.connected and self.win.schema):
            self.win.toast("Preferences need the Cascade service, which is not running: %s"
                           % (self.win.error_page.get_description() or "still starting"), 8)
            return
        from .prefs import PrefsDialog
        PrefsDialog(self.win, page).present(self.win)

    def show_about(self):
        about = Adw.AboutDialog(application_name=APP_NAME, application_icon=APP_ID, version=__version__,
                                developer_name="Smiley-McSmiles", website=URL, issue_url=URL + "/issues",
                                license_type=Gtk.License.MIT_X11,
                                comments="A BitTorrent client for GNOME with a command line and a web interface, "
                                         "powered by libtorrent")
        about.set_developers(["WOOSAH (Smiley-McSmiles)", "Claude"])
        s = (self.win.session if self.win else {}) or {}
        if s.get("libtorrent"):
            about.set_debug_info("Cascade %s\nlibtorrent %s\nGTK %d.%d.%d\nlibadwaita %d.%d.%d\nPython %s\n"
                                 "Socket: %s\nLog: %s" % (
                                     __version__, s["libtorrent"], Gtk.get_major_version(), Gtk.get_minor_version(),
                                     Gtk.get_micro_version(), Adw.get_major_version(), Adw.get_minor_version(),
                                     Adw.get_micro_version(), sys.version.split()[0], paths.socket_path(),
                                     paths.log_file()))
        about.present(self.win)

    def show_shortcuts(self):
        torrents = "".join(_sc(a, t) for a, t in (
            ("<Control>o", "Open torrent files"), ("<Control>u", "Add a magnet link or URL"),
            ("<Control><Shift>v", "Add from the clipboard"), ("<Control>n", "Create a torrent"),
            ("<Control>a", "Select all"), ("<Control>t", "Slow mode")))
        general = "".join(_sc(a, t) for a, t in (
            ("<Control>f", "Filter and sort"), ("<Control>k", "Search"), ("<Control>comma", "Preferences"),
            ("<Control>question", "Keyboard shortcuts"), ("<Control>q", "Close the window"),
            ("Escape", "Leave selection, search or filter")))
        b = Gtk.Builder.new_from_string(SHORTCUTS % (torrents, general), -1)
        w = b.get_object("shortcuts")
        w.set_transient_for(self.win)
        w.present()


def main(argv=None):
    app = CascadeApp()
    return app.run(argv if argv is not None else sys.argv)
