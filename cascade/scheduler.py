"""The weekly speed schedule: 7 x 24 hourly slots, each 0 (normal speed), 1 (slow mode) or 2 (paused).
Slot index = weekday * 24 + hour, Monday = 0, in local time."""

import time

NORMAL, SLOW, PAUSED = "0", "1", "2"
MODE_NAMES = {NORMAL: "normal", SLOW: "slow", PAUSED: "paused"}
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def slot(now=None):
    t = time.localtime(now if now is not None else time.time())
    return t.tm_wday * 24 + t.tm_hour


def mode_at(grid, now=None):
    if not grid or len(grid) != 168:
        return NORMAL
    return grid[slot(now)]


def next_change(grid, now=None):
    """(seconds until the mode changes, new mode) or (None, None) when the whole week is one mode."""
    if not grid or len(set(grid)) <= 1:
        return None, None
    now = time.time() if now is None else now
    t = time.localtime(now)
    cur = slot(now)
    into_hour = t.tm_min * 60 + t.tm_sec
    for i in range(1, 169):
        s = (cur + i) % 168
        if grid[s] != grid[cur]:
            return i * 3600 - into_hour, grid[s]
    return None, None


def set_range(grid, days, hours, mode):
    g = list(grid)
    for d in days:
        for h in hours:
            g[d * 24 + h] = mode
    return "".join(g)


def describe(grid):
    """Human-readable summary for the CLI: one line per day with ranges."""
    lines = []
    for d, name in enumerate(DAYS):
        row = grid[d * 24:(d + 1) * 24]
        parts, start = [], 0
        for h in range(1, 25):
            if h == 24 or row[h] != row[start]:
                if row[start] != NORMAL:
                    parts.append("%02d:00-%02d:00 %s" % (start, h, MODE_NAMES[row[start]]))
                start = h
        lines.append("%-9s %s" % (name, ", ".join(parts) if parts else "normal all day"))
    return lines
