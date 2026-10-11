"""Custom widgets: the pieces bar, the weekly schedule grid and the speed graph."""

import base64
import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Adw, Gtk, Pango, PangoCairo  # noqa: E402

from .. import scheduler  # noqa: E402

# libadwaita's palette (the named colours are not reachable from cairo)
LIGHT = {"accent": (0.21, 0.52, 0.89), "green": (0.15, 0.64, 0.41), "yellow": (0.90, 0.65, 0.04),
         "red": (0.88, 0.11, 0.14), "fg": (0.0, 0.0, 0.0)}
DARK = {"accent": (0.47, 0.68, 0.93), "green": (0.18, 0.76, 0.49), "yellow": (0.96, 0.83, 0.18),
        "red": (1.0, 0.48, 0.39), "fg": (1.0, 1.0, 1.0)}


def palette():
    return DARK if Adw.StyleManager.get_default().get_dark() else LIGHT


def rounded_rect(cr, x, y, w, h, r):
    r = min(r, w / 2, h / 2)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


class PiecesBar(Gtk.DrawingArea):
    """Every piece of the torrent, left to right: downloaded, downloading, missing."""

    def __init__(self, height=26):
        super().__init__()
        self.set_content_height(height)
        self.set_hexpand(True)
        self.count = 0
        self.bits = b""
        self.downloading = set()
        self.set_draw_func(self._draw)

    def update(self, pieces):
        self.count = pieces.get("count", 0)
        self.bits = base64.b64decode(pieces.get("bitfield", "") or "")
        self.downloading = set(pieces.get("downloading") or [])
        self.queue_draw()

    def _have(self, i):
        b = i >> 3
        return b < len(self.bits) and bool(self.bits[b] & (0x80 >> (i & 7)))

    def _draw(self, _area, cr, w, h):
        pal = palette()
        rounded_rect(cr, 0, 0, w, h, 6)
        cr.clip()
        cr.set_source_rgba(*pal["fg"], 0.08)
        cr.paint()
        if not self.count:
            return
        cols = max(1, int(w))
        per = self.count / cols
        for x in range(cols):
            a = int(x * per)
            b = max(a + 1, int((x + 1) * per))
            n = b - a
            have = sum(1 for i in range(a, min(b, self.count)) if self._have(i))
            if have:
                cr.set_source_rgba(*pal["accent"], 0.25 + 0.75 * have / n)
                cr.rectangle(x, 0, 1.0, h)
                cr.fill()
            elif any(i in self.downloading for i in range(a, b)):
                cr.set_source_rgba(*pal["green"], 0.8)
                cr.rectangle(x, 0, 1.0, h)
                cr.fill()


class ScheduleGrid(Gtk.DrawingArea):
    """7 x 24 hour grid.  Drag to paint a rectangle with the current mode; on_change(grid) when released."""

    LEFT = 44
    TOP = 20
    CELL_H = 24
    GAP = 2

    def __init__(self, on_change=None):
        super().__init__()
        self.grid = "0" * 168
        self.mode = scheduler.SLOW
        self.on_change = on_change
        self.drag = None
        self.set_hexpand(True)
        self.set_content_height(self.TOP + 7 * (self.CELL_H + self.GAP))
        self.set_draw_func(self._draw)
        g = Gtk.GestureDrag()
        g.connect("drag-begin", self._begin)
        g.connect("drag-update", self._update)
        g.connect("drag-end", self._end)
        self.add_controller(g)
        self.set_cursor_from_name("crosshair")

    def set_grid(self, grid):
        if grid and len(grid) == 168 and grid != self.grid and self.drag is None:
            self.grid = grid
            self.queue_draw()

    def _cell(self, x, y):
        w = self.get_width()
        cw = (w - self.LEFT) / 24.0
        col = int((x - self.LEFT) // cw)
        row = int((y - self.TOP) // (self.CELL_H + self.GAP))
        return max(0, min(6, row)), max(0, min(23, col))

    def _begin(self, g, x, y):
        if x < self.LEFT - 4 or y < self.TOP - 4:
            g.set_state(Gtk.EventSequenceState.DENIED)
            return
        self.drag = (self._cell(x, y), self._cell(x, y))
        self.queue_draw()

    def _update(self, g, dx, dy):
        if self.drag is None:
            return
        ok, sx, sy = g.get_start_point()
        self.drag = (self.drag[0], self._cell(sx + dx, sy + dy))
        self.queue_draw()

    def _end(self, g, dx, dy):
        if self.drag is None:
            return
        (r1, c1), (r2, c2) = self.drag
        self.drag = None
        days = range(min(r1, r2), max(r1, r2) + 1)
        hours = range(min(c1, c2), max(c1, c2) + 1)
        new = scheduler.set_range(self.grid, days, hours, self.mode)
        if new != self.grid:
            self.grid = new
            if self.on_change:
                self.on_change(new)
        self.queue_draw()

    def _draw(self, _area, cr, w, h):
        pal = palette()
        colors = {scheduler.NORMAL: pal["green"], scheduler.SLOW: pal["yellow"], scheduler.PAUSED: pal["red"]}
        cw = (w - self.LEFT) / 24.0
        layout = PangoCairo.create_layout(cr)
        desc = Pango.FontDescription.from_string("Sans 8")
        layout.set_font_description(desc)
        cr.set_source_rgba(*pal["fg"], 0.55)
        for hr in range(0, 24, 3):
            layout.set_text("%02d" % hr, -1)
            cr.move_to(self.LEFT + hr * cw + 1, 2)
            PangoCairo.show_layout(cr, layout)
        sel = None
        if self.drag:
            (r1, c1), (r2, c2) = self.drag
            sel = (min(r1, r2), max(r1, r2), min(c1, c2), max(c1, c2))
        for d in range(7):
            y = self.TOP + d * (self.CELL_H + self.GAP)
            cr.set_source_rgba(*pal["fg"], 0.7)
            layout.set_text(scheduler.DAYS[d][:3], -1)
            cr.move_to(0, y + 4)
            PangoCairo.show_layout(cr, layout)
            for hr in range(24):
                m = self.grid[d * 24 + hr]
                if sel and sel[0] <= d <= sel[1] and sel[2] <= hr <= sel[3]:
                    m = self.mode
                x = self.LEFT + hr * cw
                rounded_rect(cr, x + 1, y, cw - self.GAP, self.CELL_H, 4)
                cr.set_source_rgba(*colors[m], 0.85 if m != scheduler.NORMAL else 0.45)
                cr.fill()


class SpeedGraph(Gtk.DrawingArea):
    """Download (blue) and upload (green) speed over the last minutes."""

    def __init__(self, height=120):
        super().__init__()
        self.set_content_height(height)
        self.set_content_width(320)
        self.samples = []
        self.set_draw_func(self._draw)

    def update(self, samples):
        self.samples = samples[-300:]
        self.queue_draw()

    def _draw(self, _area, cr, w, h):
        pal = palette()
        rounded_rect(cr, 0, 0, w, h, 8)
        cr.set_source_rgba(*pal["fg"], 0.05)
        cr.fill()
        if len(self.samples) < 2:
            return
        top = max(max(s[1] for s in self.samples), max(s[2] for s in self.samples), 1)
        n = len(self.samples)
        for idx, col in ((1, pal["accent"]), (2, pal["green"])):
            cr.move_to(0, h)
            for i, s in enumerate(self.samples):
                cr.line_to(i * w / (n - 1), h - 4 - (h - 10) * s[idx] / top)
            cr.line_to(w, h)
            cr.close_path()
            cr.set_source_rgba(*col, 0.18)
            cr.fill_preserve()
            cr.new_path()
            for i, s in enumerate(self.samples):
                cr.line_to(i * w / (n - 1), h - 4 - (h - 10) * s[idx] / top)
            cr.set_source_rgba(*col, 0.95)
            cr.set_line_width(1.6)
            cr.stroke()


def stat_tile(caption):
    """A small card with a caption and a value; returns (widget, value_label)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    box.add_css_class("stat-tile")
    cap = Gtk.Label(label=caption, xalign=0)
    cap.add_css_class("caption")
    cap.add_css_class("dim-label")
    val = Gtk.Label(label="-", xalign=0, ellipsize=Pango.EllipsizeMode.END)
    val.add_css_class("numeric")
    val.add_css_class("stat-value")
    box.append(cap)
    box.append(val)
    return box, val
