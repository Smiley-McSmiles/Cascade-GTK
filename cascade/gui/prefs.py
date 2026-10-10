"""Preferences, generated from the settings schema (the web interface builds the same pages from the same schema).
Every change is applied at once and shared with the web interface and the CLI."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import schema as sch  # noqa: E402
from .. import scheduler  # noqa: E402
from .widgets import ScheduleGrid  # noqa: E402


class PrefsDialog(Adw.PreferencesDialog):
    def __init__(self, win, page=None):
        super().__init__(search_enabled=True)
        self.win = win
        self.rpc = win.rpc
        self.values = dict(win.settings)
        self.rows = {}          # key -> (spec, widget, setter)
        self.dep_rows = []      # (widget, depends)
        self.info_rows = {}
        self.pages = {}
        self._quiet = False
        self._debounce = {}
        for p in win.schema["pages"]:
            pg = Adw.PreferencesPage(title=p["title"], icon_name=p["icon"], name=p["id"])
            self.add(pg)
            self.pages[p["id"]] = pg
            for g in p["groups"]:
                grp = Adw.PreferencesGroup(title=g["title"], description=g["description"] or None)
                pg.add(grp)
                for r in g["rows"]:
                    w = self._make_row(r, grp)
                    if w is None:
                        continue
                    if r.get("depends"):
                        self.dep_rows.append((w, r["depends"]))
        if page and page in self.pages:
            self.set_visible_page_name(page)
        self._apply_depends()
        self._timer = GLib.timeout_add(2000, self._poll)
        self.connect("closed", lambda *_: GLib.source_remove(self._timer))
        self._poll()

    # ------------------------------------------------------------ rows
    def _make_row(self, r, grp):
        kind = r["kind"]
        if kind == "action":
            if r.get("client") not in (None, "gtk"):
                return None
            row = Adw.ActionRow(title=r["title"], subtitle=r.get("subtitle") or "")
            b = Gtk.Button(label=r["button"], valign=Gtk.Align.CENTER)
            if r.get("style") == "destructive":
                b.add_css_class("destructive-action")
            b.connect("clicked", lambda *_: self._run_action(r))
            row.add_suffix(b)
            row.set_activatable_widget(b)
            grp.add(row)
            return row
        if kind == "info":
            row = Adw.ActionRow(title=r["title"], subtitle="…", subtitle_selectable=True)
            row.add_css_class("property")
            if r["info"] == "webui":
                for icon, tip, cb in (("edit-copy-symbolic", "Copy the address", self.win.copy_webui),
                                      ("web-browser-symbolic", "Open in your browser", self.win.open_webui)):
                    b = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER, tooltip_text=tip)
                    b.add_css_class("flat")
                    b.connect("clicked", lambda _b, f=cb: f())
                    row.add_suffix(b)
            grp.add(row)
            self.info_rows[r["info"]] = row
            return row
        key, t = r["key"], r["type"]
        v = self.values.get(key)
        title = r["title"]
        subtitle = r.get("subtitle") or ""
        if t == "bool":
            row = Adw.SwitchRow(title=title, subtitle=subtitle, active=bool(v))
            row.connect("notify::active", lambda w, _p: self._set(key, w.get_active()))
            setter = lambda val, w=row: w.set_active(bool(val))  # noqa: E731
        elif t in ("int", "float"):
            adj = Gtk.Adjustment(lower=r.get("min", 0), upper=r.get("max", 1 << 31), step_increment=r.get("step", 1),
                                 page_increment=10 * r.get("step", 1))
            row = Adw.SpinRow(title=title, adjustment=adj, digits=r.get("digits", 0))
            sub = subtitle
            if r.get("unit"):
                sub = (sub + " · " if sub else "") + r["unit"]
            row.set_subtitle(sub)
            row.set_value(v or 0)
            row.connect("notify::value", lambda w, _p: self._set_later(
                key, w.get_value() if t == "float" else int(w.get_value())))
            setter = lambda val, w=row: w.set_value(val or 0)  # noqa: E731
        elif t == "choice":
            labels = [c[1] for c in r["choices"]]
            keys = [c[0] for c in r["choices"]]
            row = Adw.ComboRow(title=title, subtitle=subtitle, model=Gtk.StringList.new(labels))
            row.set_selected(keys.index(v) if v in keys else 0)
            row.connect("notify::selected", lambda w, _p: self._set(key, keys[w.get_selected()]))
            setter = lambda val, w=row: w.set_selected(keys.index(val) if val in keys else 0)  # noqa: E731
        elif t in ("str", "password", "secret"):
            cls = Adw.PasswordEntryRow if t in ("password", "secret") else Adw.EntryRow
            row = cls(title=title, show_apply_button=True)
            if t == "secret":
                row.set_title(title + (" (set)" if v else " (not set)"))
            elif v and v != sch.MASK:
                row.set_text(str(v))
            elif v == sch.MASK:
                row.set_text(sch.MASK)
            row.connect("apply", lambda w: self._set(key, w.get_text()))
            if subtitle:
                row.set_tooltip_text(subtitle)

            def setter(val, w=row, tt=t, base=title):
                if w.has_focus() or w.get_focus_child() is not None:
                    return
                if tt == "secret":
                    w.set_title(base + (" (set)" if val else " (not set)"))
                else:
                    w.set_text(str(val or ""))
        elif t in ("path", "file"):
            row = Adw.ActionRow(title=title, subtitle=v or "Not set", subtitle_selectable=True)
            row.add_css_class("property")
            b = Gtk.Button(icon_name="folder-open-symbolic" if t == "path" else "document-open-symbolic",
                           valign=Gtk.Align.CENTER, tooltip_text="Choose…")
            b.add_css_class("flat")
            b.connect("clicked", lambda *_: self._choose_path(key, t == "path"))
            row.add_suffix(b)
            if r.get("default") == "" or t == "file":
                c = Gtk.Button(icon_name="edit-clear-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Clear")
                c.add_css_class("flat")
                c.connect("clicked", lambda *_: self._set(key, ""))
                row.add_suffix(c)
            row.set_activatable_widget(b)
            setter = lambda val, w=row: w.set_subtitle(GLib.markup_escape_text(val or "Not set"))  # noqa: E731
        elif t == "interface":
            row = self._interface_row(r, v)
            setter = row.cascade_setter
        elif t == "lines":
            row = Adw.ExpanderRow(title=title, subtitle=subtitle)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=8,
                          margin_start=12, margin_end=12)
            tv = Gtk.TextView(monospace=True, wrap_mode=Gtk.WrapMode.CHAR, top_margin=6, bottom_margin=6,
                              left_margin=8, right_margin=8, height_request=110)
            tv.get_buffer().set_text("\n".join(v or []))
            frame = Gtk.Frame(child=tv)
            box.append(frame)
            save = Gtk.Button(label="Save", halign=Gtk.Align.END)
            save.add_css_class("suggested-action")
            save.connect("clicked", lambda *_: self._set(key, tv.get_buffer().get_text(
                tv.get_buffer().get_start_iter(), tv.get_buffer().get_end_iter(), False).splitlines()))
            box.append(save)
            lr = Gtk.ListBoxRow(activatable=False)
            lr.set_child(box)
            row.add_row(lr)

            def setter(val, w=tv):
                if not w.has_focus():
                    w.get_buffer().set_text("\n".join(val or []))
        elif t == "schedule":
            row = self._schedule_row(key, v)
            setter = row.cascade_setter
        else:
            return None
        grp.add(row)
        self.rows[key] = (r, row, setter)
        return row

    def _interface_row(self, r, v):
        row = Adw.ComboRow(title=r["title"], subtitle=r.get("subtitle") or "")
        row.names = []
        row.busy = False

        def fill(ifaces, current):
            row.busy = True
            names = [""] + [i["name"] for i in ifaces if not i["loopback"]]
            labels = ["Any interface"]
            for i in ifaces:
                if i["loopback"]:
                    continue
                state = ", ".join(i["addresses"]) if i["addresses"] else ("up" if i["up"] else "down")
                labels.append("%s  (%s%s)" % (i["name"], "VPN · " if i["vpn"] else "", state))
            if current and current not in names:
                names.append(current)
                labels.append("%s  (not connected)" % current)
            names.append("__custom__")
            labels.append("Other…")
            row.names = names
            row.set_model(Gtk.StringList.new(labels))
            row.set_selected(names.index(current) if current in names else 0)
            row.busy = False

        def changed(w, _p):
            if row.busy or not row.names:
                return
            name = row.names[w.get_selected()]
            if name == "__custom__":
                self.win.ask_text("Network Interface", "tun0", "Use",
                                  lambda n: self._set(r["key"], n),
                                  body="Type the name of a device that may not exist yet, such as tun0 or wg0. "
                                       "Transfers wait until it appears.")
                cur = self.values.get(r["key"], "")
                row.busy = True
                row.set_selected(row.names.index(cur) if cur in row.names else 0)
                row.busy = False
                return
            self._set(r["key"], name)

        def refresh(current=None):
            self.rpc.call("net.interfaces", {}, lambda lst: fill(lst, self.values.get(r["key"], "")
                                                                 if current is None else current))

        row.connect("notify::selected", changed)
        b = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Refresh the list")
        b.add_css_class("flat")
        b.connect("clicked", lambda *_: refresh())
        row.add_suffix(b)
        fill([], v)
        refresh(v)
        row.cascade_setter = lambda val: refresh(val)
        return row

    def _schedule_row(self, key, v):
        row = Gtk.ListBoxRow(activatable=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=12, margin_bottom=12,
                      margin_start=12, margin_end=12)
        row.set_child(box)
        tools = Gtk.Box(spacing=0, halign=Gtk.Align.CENTER)
        tools.add_css_class("linked")
        grid = ScheduleGrid(lambda g: self._set(key, g))
        group = None
        for mode, label, css in ((scheduler.NORMAL, "Full Speed", "sched-normal"),
                                 (scheduler.SLOW, "Slow Mode", "sched-slow"),
                                 (scheduler.PAUSED, "Paused", "sched-paused")):
            b = Gtk.ToggleButton()
            hb = Gtk.Box(spacing=6)
            dot = Gtk.Box(valign=Gtk.Align.CENTER)
            dot.add_css_class("legend-dot")
            dot.add_css_class(css)
            hb.append(dot)
            hb.append(Gtk.Label(label=label))
            b.set_child(hb)
            if group:
                b.set_group(group)
            else:
                group = b
            b.connect("toggled", lambda w, m=mode: w.get_active() and setattr(grid, "mode", m))
            if mode == scheduler.SLOW:
                b.set_active(True)
            tools.append(b)
        box.append(tools)
        hint = Gtk.Label(label="Pick a mode, then drag across the hours to paint them", wrap=True)
        hint.add_css_class("caption")
        hint.add_css_class("dim-label")
        box.append(hint)
        grid.set_grid(v)
        box.append(grid)
        row.cascade_setter = grid.set_grid
        return row

    # ------------------------------------------------------------ changes
    def _set(self, key, value):
        if self._quiet:
            return
        if self.values.get(key) == value:
            return
        self.values[key] = value
        self._apply_depends()
        self.rpc.call("settings.set", {"values": {key: value}}, self._after_set)

    def _set_later(self, key, value):
        if self._quiet:
            return
        if key in self._debounce:
            GLib.source_remove(self._debounce[key])
        self._debounce[key] = GLib.timeout_add(500, self._fire, key, value)

    def _fire(self, key, value):
        self._debounce.pop(key, None)
        self._set(key, value)
        return False

    def _after_set(self, r):
        for k, e in (r.get("errors") or {}).items():
            spec = sch.SPECS.get(k, {})
            self.add_toast(Adw.Toast(title=GLib.markup_escape_text("%s: %s" % (spec.get("title", k), e))))
        n = r.get("notices") or {}
        if n.get("webui_password"):
            self._show_password(n["webui_password"], (n.get("webui") or {}).get("urls") or [])
        elif n.get("webui"):
            w = n["webui"]
            if w.get("error"):
                self.add_toast(Adw.Toast(title=GLib.markup_escape_text(w["error"]), timeout=6))
        self.win.rpc.call("settings.get", {}, self.win._got_settings)
        self._poll()

    def _show_password(self, pw, urls):
        d = Adw.AlertDialog(heading="Web Interface Is On",
                            body="Sign in from any browser with this password. You can change it below.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lb.add_css_class("boxed-list")
        pr = Adw.ActionRow(title="Password", subtitle=pw, subtitle_selectable=True)
        pr.add_css_class("property")
        cb = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Copy")
        cb.add_css_class("flat")
        cb.connect("clicked", lambda *_: self.get_clipboard().set(pw))
        pr.add_suffix(cb)
        lb.append(pr)
        if urls:
            ur = Adw.ActionRow(title="Address", subtitle="\n".join(urls), subtitle_selectable=True)
            ur.add_css_class("property")
            lb.append(ur)
        box.append(lb)
        d.set_extra_child(box)
        d.add_response("ok", "Done")
        d.present(self)

    def _choose_path(self, key, folder):
        fd = Gtk.FileDialog(modal=True, title="Choose a Folder" if folder else "Choose a File")
        cur = self.values.get(key) or ""
        if cur:
            try:
                f = Gio.File.new_for_path(cur if folder else cur.rsplit("/", 1)[0] or "/")
                fd.set_initial_folder(f)
            except GLib.Error:
                pass

        def done(dlg, res):
            try:
                f = dlg.select_folder_finish(res) if folder else dlg.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self._set(key, f.get_path())
                self.rows[key][2](f.get_path())

        (fd.select_folder if folder else fd.open)(self.win, None, done)

    def _run_action(self, r):
        a = r["action"]
        if a == "webui.open":
            self.win.open_webui()
            return
        if a == "default.handler":
            self.win.app.make_default_handler()
            return
        if a == "settings.reset":
            d = Adw.AlertDialog(heading="Reset All Settings?",
                                body="Every preference goes back to its default. Your torrents are kept.")
            d.add_response("cancel", "Cancel")
            d.add_response("reset", "Reset")
            d.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
            d.connect("response", lambda _d, resp: resp == "reset" and self.rpc.call(
                "action.run", {"action": a}, lambda res: (self.add_toast(Adw.Toast(title=res["message"])),
                                                          self.win.rpc.call("settings.get", {}, self._reload))))
            d.present(self)
            return
        self.rpc.call("action.run", {"action": a},
                      lambda res: self.add_toast(Adw.Toast(title=GLib.markup_escape_text(res.get("message", "Done")))),
                      lambda m: self.add_toast(Adw.Toast(title=GLib.markup_escape_text(m))))

    def _reload(self, values):
        self.win._got_settings(values)
        self._update_values(values)

    # ------------------------------------------------------------ keeping up with other clients
    def _poll(self):
        self.rpc.call("settings.get", {}, self._update_values)
        s = self.win.session or {}
        info = dict(s.get("info") or {})
        w = s.get("webui") or {}
        info["webui"] = "  ".join(w.get("urls") or []) if w.get("running") else (w.get("error") or "Off")
        for k, row in self.info_rows.items():
            if k in info and row.get_subtitle() != info[k]:
                row.set_subtitle(GLib.markup_escape_text(info[k]))
        return True

    def _update_values(self, values):
        self._quiet = True
        try:
            for k, (spec, _w, setter) in self.rows.items():
                if k in values and values[k] != self.values.get(k):
                    self.values[k] = values[k]
                    setter(values[k])
        finally:
            self._quiet = False
        self._apply_depends()

    def _apply_depends(self):
        for w, d in self.dep_rows:
            ok = sch.depends_ok(d, self.values)
            if "in" in d or "not" in d:
                w.set_visible(ok)
            else:
                w.set_sensitive(ok)
