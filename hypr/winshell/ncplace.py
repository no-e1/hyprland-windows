#!/usr/bin/env python3
"""Where the notification center (swaync) sits: right above the calendar,
like Windows 11. The calendar can be folded up (its arrow), then the
notification center moves down with it.

swaync is started with a copy of swaync/config.json (scripts/swaync.sh)
that has the right bottom margin; winshell writes a new copy whenever the
calendar folds and tells swaync to reload it.
"""
import json
import os
import subprocess
import threading

SRC = os.path.expanduser("~/.config/swaync/config.json")
DST = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "winshell-swaync.json")
STATE = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
                     "winshell", "calendar.json")
GAP = 12                    # between the panels and to the taskbar
TASKBAR = 48                # px, winshell's taskbar at the bottom
_SYNC = threading.Lock()
EMPTY_HEIGHT = 200          # no notifications: a smaller panel (like Windows)


def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


_SAVE = threading.Lock()


def save(**kw):
    """Update the state file – atomically, so a reader never sees half of it
    (that read as "no state" and placed the panel for a default height)."""
    with _SAVE:
        st = load()
        st.update(kw)
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, STATE)
        return st


def screen_height():
    """Usable height of the focused screen (without the taskbar), logical px."""
    try:
        out = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True, timeout=3).stdout
        mons = json.loads(out)
        m = next((m for m in mons if m.get("focused")), mons[0])
        # the taskbar may not be up yet (winshell starting, login): then
        # Hyprland reserves nothing and the panel landed 48 px too low – in
        # the calendar. Always count the taskbar's height.
        top, bottom = m["reserved"][1], max(m["reserved"][3], TASKBAR)
        return int(m["height"] / m["scale"]) - top - bottom
    except (OSError, ValueError, LookupError, subprocess.SubprocessError):
        return 0


def write(calendar_height=None):
    """Write swaync's config for a calendar that is `calendar_height` px tall
    (and a smaller panel while there are no notifications)."""
    st = load()
    if calendar_height is None:
        calendar_height = st.get("height", 399)
    if not isinstance(calendar_height, (int, float)) or calendar_height < 40:
        calendar_height = 399               # bogus value: assume the unfolded calendar
    with open(SRC) as f:
        cfg = json.load(f)
    if st.get("empty", True):
        cfg["control-center-height"] = EMPTY_HEIGHT
    # Anchored at the top (then new notifications are listed at the top,
    # like Windows), pushed down so its bottom edge sits above the calendar.
    free = screen_height()
    if free:
        cfg["control-center-positionY"] = "top"
        cfg["control-center-margin-top"] = max(GAP, free - GAP - int(calendar_height) - GAP
                                               - int(cfg.get("control-center-height", 410)))
        cfg["control-center-margin-bottom"] = 0
    else:
        cfg["control-center-positionY"] = "bottom"
        cfg["control-center-margin-bottom"] = GAP + int(calendar_height) + GAP
    tmp = DST + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=4)
    os.replace(tmp, DST)
    return DST


def sync():
    """Write the config for the *current* state and make swaync reload it.
    One at a time, each reading the newest state: when the calendar is
    folded and unfolded quickly (or a notification arrives meanwhile), the
    last state always wins – the panels can never end up overlapping."""
    with _SYNC:
        write()
        try:
            subprocess.run(["swaync-client", "-R"], capture_output=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            pass


if __name__ == "__main__":
    print(write())
