#!/usr/bin/python3 -I
"""Drops the double clicks of a mouse with a worn switch.

The Attack Shark X11 (2.4G dongle 1d57:fa60) sometimes sends one click as
two: the button is let go and pressed again 30–60 ms later. libinput only
filters bounces up to ~25 ms, so apps saw a double click (the speaker
button muted and unmuted at once).

This service takes the mouse for itself (EVIOCGRAB), passes everything on
through a virtual copy of it (uinput) and leaves out a press that comes
less than WINDOW ms after the same button was let go – and the release
that belongs to it. Only this mouse: the touchpad and the TrackPoint are
not touched. If the mouse goes away (dongle out, switched off) it waits
for it to come back.

Runs as root (systemd: mouse-debounce.service) – it needs the mouse's
/dev/input node and /dev/uinput. Needs python3-evdev.
"""
import select
import sys
import time

import evdev
from evdev import ecodes as e

MICE = {(0x1d57, 0xfa60)}       # (vendor, product): the Attack Shark X11's dongle
WINDOW = 0.06                   # s from a release to the next press of that button
BUTTONS = {e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_SIDE, e.BTN_EXTRA}


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def find_mouse():
    """The mouse part of the dongle (it also shows up as keyboards)."""
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        caps = d.capabilities()
        if ((d.info.vendor, d.info.product) in MICE and e.BTN_LEFT in caps.get(e.EV_KEY, [])
                and e.REL_X in caps.get(e.EV_REL, [])):
            return d
        d.close()
    return None


def run(dev):
    log(f"filtering {dev.path} ({dev.name})")
    ui = evdev.UInput.from_device(dev, name=f"{dev.name} (debounced)")
    dev.grab()
    released = {}               # button -> when it was let go
    dropped = set()             # buttons whose press was left out (their release too)
    try:
        while True:
            select.select([dev.fd], [], [])
            for ev in dev.read():
                if ev.type == e.EV_KEY and ev.code in BUTTONS:
                    now = time.monotonic()
                    if ev.value == 1 and now - released.get(ev.code, -1.0) < WINDOW:
                        dropped.add(ev.code)
                        log(f"dropped a bounce of {e.BTN[ev.code] if ev.code in e.BTN else ev.code} "
                            f"({(now - released[ev.code]) * 1000:.0f} ms after release)")
                        continue
                    if ev.value == 0:
                        if ev.code in dropped:
                            dropped.discard(ev.code)
                            continue
                        released[ev.code] = now
                ui.write_event(ev)
    finally:
        try:
            dev.ungrab()
        except OSError:
            pass
        ui.close()


def main():
    while True:
        dev = find_mouse()
        if not dev:
            time.sleep(2)
            continue
        try:
            run(dev)
        except OSError as err:            # dongle pulled out, mouse off
            log(f"mouse gone ({err}), waiting for it")
            time.sleep(1)


if __name__ == "__main__":
    main()
