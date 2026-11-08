#!/usr/bin/env python3
"""winshell – Windows 11 style shell pieces for Hyprland.

  * Alt+Tab switcher with live window previews
  * Quick settings flyout (Wi-Fi, Bluetooth, airplane mode, do not disturb,
    brightness, volume, battery) with Wi-Fi and Bluetooth pages

Runs as a background daemon. Control it with winshell-ctl:
  winshell-ctl switch next|prev|commit|cancel
  winshell-ctl qs [toggle|wifi|bluetooth|hide]
"""
import fcntl
import json
import math
import os
import pwd
import re
import socket
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gtk4LayerShell", "1.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gsk, Gtk, Pango  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402
try:                                    # GLib >= 2.86 moved it to GioUnix
    gi.require_version("GioUnix", "2.0")
    from gi.repository import GioUnix  # noqa: E402
    DesktopAppInfo = GioUnix.DesktopAppInfo
except (ValueError, ImportError):
    DesktopAppInfo = Gio.DesktopAppInfo

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import ncplace                  # noqa: E402  where the notification center sits
ICONS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "icons")
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
FIFO = os.path.join(RUNTIME, "winshell.fifo")
READY = os.path.join(RUNTIME, "winshell.ready")
LOCK = os.path.join(RUNTIME, "winshell.lock")
AIRPLANE = os.path.join(RUNTIME, "winshell-airplane.json")   # radios that were on before airplane mode
SCRIPTS = os.path.expanduser("~/.config/hypr/scripts")


# ── helpers ──────────────────────────────────────────────────────────────
def sh(cmd, timeout=8):
    try:
        return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                              text=True, timeout=timeout).stdout
    except Exception:
        return ""


def ok(cmd, timeout=30):
    try:
        return subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                              timeout=timeout).returncode == 0
    except Exception:
        return False


def run_out(cmd, timeout=30):
    """(succeeded?, stdout + stderr)"""
    try:
        r = subprocess.run(cmd, shell=isinstance(cmd, str), capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode == 0, r.stdout + r.stderr
    except Exception as e:
        return False, str(e)


# environment only winshell itself needs (see winshell-ctl); apps and tools
# started from here must not inherit it
OWN_ENV = ("LD_PRELOAD", "LC_TIME", "GSK_DEBUG")


def child_env():
    return {k: v for k, v in os.environ.items() if k not in OWN_ENV}


def spawn(cmd):
    subprocess.Popen(cmd, shell=True, start_new_session=True, env=child_env(),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def crisp_text():
    """Draw text unhinted: with GTK's default, the tops of round letters
    and digits (2, 3, 8, a, o …) get snapped to the pixel grid and look cut
    off by a pixel."""
    s = Gtk.Settings.get_default()
    if s is None:
        return
    try:
        s.set_property("gtk-font-rendering", Gtk.FontRendering.MANUAL)
        s.set_property("gtk-xft-hinting", 0)
        s.set_property("gtk-xft-hintstyle", "hintnone")
        s.set_property("gtk-xft-antialias", 1)
    except (TypeError, AttributeError):       # older GTK
        pass


def die_with_parent():
    """For long-running helpers: end when winshell ends (else every restart
    left one more behind)."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").prctl(1, 15)       # PR_SET_PDEATHSIG, SIGTERM
    except OSError:
        pass


def bg(fn, done=None):
    """Run fn in a thread, then done(result) on the GTK main loop."""
    def run():
        res = fn()
        if done:
            GLib.idle_add(lambda: done(res) and False)
    threading.Thread(target=run, daemon=True).start()


def hypr_socket(name):
    return os.path.join(RUNTIME, "hypr", os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", ""), name)


def hypr(req):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(hypr_socket(".socket.sock"))
        s.sendall(req.encode())
        data = b""
        while chunk := s.recv(65536):
            data += chunk
    return data.decode(errors="replace")


def hypr_json(what):
    try:
        return json.loads(hypr("j/" + what))
    except Exception:
        try:
            return json.loads(sh(["hyprctl", "-j", what]))
        except Exception:
            return None


def dispatch(lua):
    """Hyprland dispatch straight over its socket (no hyprctl process)."""
    try:
        hypr("dispatch " + lua)
    except OSError:
        subprocess.run(["hyprctl", "dispatch", lua], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def hypr_eval(lua):
    try:
        hypr("eval " + lua)
    except OSError:
        subprocess.run(["hyprctl", "eval", lua], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# Window events that change what the pinned taskbar buttons show. Waybar
# refreshes those modules on SIGRTMIN+8 instead of polling every few seconds.
TASKBAR_EVENTS = {"activewindowv2", "openwindow", "closewindow", "movewindowv2",
                  "workspacev2", "activespecialv2"}
_taskbar_timer = None


SHELL = None          # the WinShell application, set at startup


def refresh_taskbar():
    """Redraw the taskbar's app buttons (debounced: once per burst of events)."""
    global _taskbar_timer
    if _taskbar_timer is None and SHELL and getattr(SHELL, "taskbar", None):
        def fire():
            global _taskbar_timer
            _taskbar_timer = None
            SHELL.taskbar.refresh()
            return False
        _taskbar_timer = GLib.timeout_add(40, fire)
    return False


def wifi_device():
    return next((l.split(":")[0] for l in sh("nmcli -t -f DEVICE,TYPE dev").splitlines()
                 if l.endswith(":wifi")), "")


def icon(name, size=16):
    img = Gtk.Image.new_from_icon_name(name)
    img.set_pixel_size(size)
    return img


# Your profile picture and name – the start menu and the lock screen
# (lockscreen.py, through scripts/lock.sh) show the same ones.
# To set your own picture: put an image at ~/.face (square works best).
def profile_picture():
    user = pwd.getpwuid(os.getuid()).pw_name
    for p in (os.path.expanduser("~/.face"), os.path.expanduser("~/.face.icon"),
              f"/var/lib/AccountsService/icons/{user}"):
        if os.path.isfile(p):
            return p
    return None


def display_name():
    pw = pwd.getpwuid(os.getuid())
    name = pw.pw_gecos.split(",")[0].strip() or pw.pw_name
    return name[:1].upper() + name[1:]


def avatar(size):
    """The round profile picture (a person icon if there is none)."""
    box = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    box.add_css_class("avatar-pic")
    box.set_overflow(Gtk.Overflow.HIDDEN)
    img = Gtk.Image(pixel_size=size)
    pic = profile_picture()
    try:
        img.set_from_paintable(Gdk.Texture.new_from_filename(pic)) if pic else img.set_from_icon_name("win11-user")
    except GLib.Error:
        img.set_from_icon_name("win11-user")
    box.append(img)
    return box


def busy_button(btn, text, idle_text=None):
    """A button that shows a spinning circle while it works (text), or its
    normal label again (text None)."""
    if text:
        box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        sp = Gtk.Spinner(spinning=True)
        sp.add_css_class("btn-spinner")
        box.append(sp)
        box.append(Gtk.Label(label=text))
        btn.set_child(box)
    else:
        btn.set_child(Gtk.Label(label=idle_text or ""))


def label(text, *classes, xalign=0.0, ellipsize=True):
    lbl = Gtk.Label(label=text, xalign=xalign)
    if ellipsize:
        from gi.repository import Pango
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
    for c in classes:
        lbl.add_css_class(c)
    return lbl


def button(child, *classes, on_click=None, tooltip=None):
    if isinstance(child, str):
        child = Gtk.Label(label=child)
    b = Gtk.Button(child=child)
    for c in classes:
        b.add_css_class(c)
    if on_click:
        b.connect("clicked", lambda *_: on_click())
    if tooltip:
        b.set_tooltip_text(tooltip)
    return b


def slide_in(widget):
    """Windows 11 flyout entrance: glide up out of the taskbar and fade in."""
    widget.remove_css_class("shown")
    GLib.timeout_add(16, lambda: widget.add_css_class("shown") and False)


def clear(box):
    while child := box.get_first_child():
        box.remove(child)


def layer_window(app, namespace, layer, anchors=(), margins=None,
                 keyboard=LS.KeyboardMode.NONE, exclusive=0):
    w = Gtk.Window(application=app)
    LS.init_for_window(w)
    LS.set_namespace(w, namespace)
    LS.set_layer(w, layer)
    for edge in anchors:
        LS.set_anchor(w, edge, True)
    for edge, m in (margins or {}).items():
        LS.set_margin(w, edge, m)
    LS.set_keyboard_mode(w, keyboard)
    LS.set_exclusive_zone(w, exclusive)
    w.add_css_class("winshell")
    return w


class SlideBox(Gtk.Widget):
    """A row (or a grid with `columns`) of icons that glide to their new
    place whenever the order changes – like the Windows 11 taskbar, where the
    other icons smoothly make room while one is dragged."""
    TAU = 0.045                 # seconds; about 150 ms until an icon has arrived

    def __init__(self, columns=0, css=None):
        super().__init__()
        self.columns = columns
        self.items = []
        self.collapsed = set()  # in the box but take no room (dragged elsewhere)
        self.pos = {}           # widget -> (x, y) where it is drawn now
        self.target = {}
        self.tick_id = 0
        self.last = 0
        self.floating = None    # the dragged icon: drawn where the pointer is
        self.float_pos = (0, 0)
        if css:
            self.add_css_class(css)

    def item_at(self, x, y):
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and w.get_parent() is not self:
            w = w.get_parent()
        return w if w in self.items else None

    def set_float(self, w, x, y):
        """Draw `w` at (x, y) (it follows the pointer); its slot stays free."""
        self.floating, self.float_pos = w, (x, y)
        self.queue_allocate()

    def drop_float(self):
        """Let the dragged icon glide from the pointer into its slot."""
        if self.floating is not None:
            self.pos[self.floating] = self.float_pos
            self.floating = None
            self.queue_resize()

    def set_items(self, widgets):
        widgets = list(widgets)
        for w in self.items:
            if w not in widgets:
                w.unparent()
                self.pos.pop(w, None)
                self.collapsed.discard(w)
                if w is self.floating:
                    self.floating = None
        for w in widgets:
            if w.get_parent() is not self:
                w.set_parent(self)
        self.items = widgets
        self.queue_resize()

    def move_item(self, w, index):
        rest = [x for x in self.items if x is not w]
        rest.insert(max(0, min(index, len(rest))), w)
        if rest != self.items:
            self.items = rest
            self.queue_resize()

    def set_collapsed(self, w, on):
        if (w in self.collapsed) != on:
            (self.collapsed.add if on else self.collapsed.discard)(w)
            self.queue_resize()

    def visible_items(self):
        return [w for w in self.items if w.get_visible()]

    def cell(self):
        ws = [w.measure(Gtk.Orientation.HORIZONTAL, -1)[1] for w in self.visible_items()] or [0]
        hs = [w.measure(Gtk.Orientation.VERTICAL, -1)[1] for w in self.visible_items()] or [0]
        return max(ws), max(hs)

    def layout(self, height=None):
        """Target place of every item: {widget: (x, y, w, h)} and total size."""
        out, items = {}, self.visible_items()
        if self.columns:
            cw, ch = self.cell()
            n = len([w for w in items if w not in self.collapsed])
            cols = max(1, min(self.columns, n))
            i = 0
            for w in items:
                out[w] = ((i % cols) * cw, (i // cols) * ch, cw, ch)
                if w not in self.collapsed:
                    i += 1
            rows = max(1, -(-i // cols)) if i else 0
            return out, cols * cw if i else 0, rows * ch
        x = 0
        hmax = max([w.measure(Gtk.Orientation.VERTICAL, -1)[1] for w in items] or [0])
        for w in items:
            ww = w.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            out[w] = (x, 0, ww, height or hmax)
            if w not in self.collapsed:
                x += ww
        return out, x, hmax

    def index_at(self, x, y, exclude=None):
        """Where an icon dropped at (x, y) goes, counted without `exclude`."""
        slots, _w, _h = self.layout()
        others = [w for w in self.visible_items() if w is not exclude and w not in self.collapsed]
        if self.columns:
            cw, ch = self.cell()
            n = len(others) + 1
            cols = max(1, min(self.columns, n))
            if not cw or not ch:
                return len(others)
            col = max(0, min(cols - 1, int(x // cw)))
            row = max(0, int(y // ch))
            return max(0, min(len(others), row * cols + col))
        for i, w in enumerate(others):
            sx, _sy, sw, _sh = slots[w]
            if x < sx + sw / 2:
                return i
        return len(others)

    def do_measure(self, orientation, for_size):
        _slots, w, h = self.layout()
        size = w if orientation == Gtk.Orientation.HORIZONTAL else h
        return size, size, -1, -1

    def do_size_allocate(self, width, height, baseline):
        slots, _w, _h = self.layout(None if self.columns else height)
        self.target = {w: (x, y) for w, (x, y, _ww, _hh) in slots.items()}
        moving = False
        for w, (x, y, ww, hh) in slots.items():
            if w is self.floating:
                fx, fy = self.float_pos
                fx = max(0, min(fx, width - ww))
                fy = max(0, min(fy, max(0, height - hh))) if self.columns else y
                self.pos[w] = (fx, fy)
                w.allocate(ww, hh, -1, Gsk.Transform().translate(Graphene.Point().init(fx, fy)))
                continue
            px, py = self.pos.get(w, (x, y))
            if w not in self.pos:
                self.pos[w] = (x, y)
            if abs(px - x) > 0.5 or abs(py - y) > 0.5:
                moving = True
            t = Gsk.Transform().translate(Graphene.Point().init(px, py))
            w.allocate(ww, hh, -1, t)
        if moving and not self.tick_id:
            self.last = 0
            self.tick_id = self.add_tick_callback(self.on_tick)

    def do_snapshot(self, snapshot):
        for w in self.items:                    # the dragged icon on top
            if w is not self.floating:
                self.snapshot_child(w, snapshot)
        if self.floating is not None and self.floating.get_parent() is self:
            self.snapshot_child(self.floating, snapshot)

    def on_tick(self, _w, clock):
        now = clock.get_frame_time() / 1e6
        dt = min(0.05, now - self.last) if self.last else 1 / 60
        self.last = now
        k = 1 - math.exp(-dt / self.TAU)
        done = True
        for w, (tx, ty) in self.target.items():
            if w is self.floating:
                continue
            px, py = self.pos.get(w, (tx, ty))
            nx, ny = px + (tx - px) * k, py + (ty - py) * k
            if abs(tx - nx) < 0.5 and abs(ty - ny) < 0.5:
                nx, ny = tx, ty
            else:
                done = False
            self.pos[w] = (nx, ny)
        self.queue_allocate()
        if done:
            self.tick_id = 0
            return False
        return True


class AppIcons:
    """Maps window classes to desktop-entry icons."""

    def __init__(self):
        self.map = {}
        for app in Gio.AppInfo.get_all():
            gicon = app.get_icon()
            if not gicon:
                continue
            aid = (app.get_id() or "").removesuffix(".desktop").lower()
            self.map.setdefault(aid, gicon)
            self.map.setdefault(aid.split(".")[-1], gicon)
            wm = app.get_startup_wm_class() if hasattr(app, "get_startup_wm_class") else None
            if wm:
                self.map[wm.lower()] = gicon

    def get(self, cls):
        c = (cls or "").lower()
        return (self.map.get(c) or self.map.get(c.split(".")[-1])
                or Gio.ThemedIcon.new_from_names([c, "application-x-executable"]))


# ── Alt+Tab switcher ─────────────────────────────────────────────────────
class Thumb(Gtk.Picture):
    """A picture that always asks for the same size, whatever the texture."""

    def do_measure(self, orientation, for_size):
        n = Switcher.W if orientation == Gtk.Orientation.HORIZONTAL else Switcher.H
        return n, n, -1, -1


class Switcher:
    W, H = 240, 150

    def __init__(self, app, icons):
        self.icons = icons
        self.thumbs = {}            # address -> Gdk.Texture
        self.thumb_time = {}        # address -> when it was taken
        self.cap_queue = []         # windows waiting for a new preview
        self.geo = {}               # address -> (geometry, since when): grim crashes
                                    # on windows that change size mid-capture
        self.cap_busy = 0           # grims running
        self.grims = set()
        GLib.timeout_add_seconds(2, self.warm_thumbs)
        self.live = None            # renews the previews while the switcher is open
        self.items, self.cards = [], []
        self.sel, self.visible = 0, False
        self.task_view = False
        self.gesture = False        # opened by a 3-finger swipe
        self.paused_until = 0.0
        self.win = layer_window(app, "winshell-switcher", LS.Layer.OVERLAY)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                max_children_per_line=6, min_children_per_line=1,
                                column_spacing=6, row_spacing=6)
        # cards wrap into rows that fit the screen (like Windows); with very
        # many windows the rows scroll
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
                                         propagate_natural_width=True, propagate_natural_height=True)
        self.scroll.set_child(self.flow)
        panel = Gtk.Box()
        panel.add_css_class("switcher")
        panel.append(self.scroll)
        self.win.set_child(panel)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        keys.connect("key-released", self.on_key_released)
        self.win.add_controller(keys)
        self.poll = None
        threading.Thread(target=self.watch_events, daemon=True).start()

    # While the switcher is open it owns the keyboard, so it can tell the
    # moment Alt is let go – then it switches, exactly like Windows.
    def alt_down(self):
        seat = Gdk.Display.get_default().get_default_seat()
        kb = seat.get_keyboard() if seat else None
        return bool(kb and kb.get_modifier_state() & Gdk.ModifierType.ALT_MASK)

    def poll_alt(self):
        if not self.visible:
            self.poll = None
            return False
        if not self.alt_down():
            self.poll = None
            self.commit()
            return False
        return True

    def on_key(self, _c, keyval, _code, state):
        if keyval == Gdk.KEY_Escape:
            self.hide()
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self.commit()
        elif keyval == Gdk.KEY_Right:
            self.move(1, 0)
        elif keyval == Gdk.KEY_Left:
            self.move(-1, 0)
        elif keyval == Gdk.KEY_Down:
            self.move(0, 1)
        elif keyval == Gdk.KEY_Up:
            self.move(0, -1)
        elif keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            back = keyval == Gdk.KEY_ISO_Left_Tab or state & Gdk.ModifierType.SHIFT_MASK
            self.step(-1 if back else 1)
        return True

    def alt_released(self):
        """Hyprland saw Alt go up (see hyprland.lua)."""
        if self.visible and not self.task_view and not self.gesture:
            self.commit()

    def on_key_released(self, _c, keyval, _code, _state):
        if not self.task_view and not self.gesture and keyval in (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R):
            self.commit()

    # every open window – all desktops, minimized ones too – most recent first.
    # (Hyprland reports no workspace "id" for minimized windows.)
    def collect(self):
        clients = hypr_json("clients") or []
        wins = []
        for c in clients:
            name = (c.get("workspace") or {}).get("name", "")
            if not c.get("mapped") or c.get("hidden") or not c.get("address"):
                continue
            if name.startswith("special:") and name != "special:minimized":
                continue                    # scratchpads and the like
            wins.append(c)
        wins.sort(key=lambda c: c.get("focusHistoryID", 99))
        return wins

    def first_selection(self, delta):
        """Alt+Tab goes to the previous window – unless no window is in front
        (desktop shown, everything minimized): then the most recent one."""
        active = (hypr_json("activewindow") or {})
        top = self.items[0]
        front = (active.get("address") == top["address"] and
                 not top["workspace"].get("name", "").startswith("special:"))
        if delta > 0:
            return 1 % len(self.items) if front else 0
        return -1 % len(self.items)

    def view(self):
        """Win + Tab: like Alt+Tab, but stays open until a window is picked."""
        if self.visible:
            self.hide()
            return
        self.task_view = True
        self.step(1)

    def step(self, delta):
        if not self.visible:
            self.items = self.collect()
            if not self.items:
                return
            self.build()
            self.sel = self.first_selection(delta)
            self.visible = True
            self.update_selection()
            LS.set_keyboard_mode(self.win, LS.KeyboardMode.EXCLUSIVE)
            self.win.present()
            if not self.poll and not self.task_view and not self.gesture:
                self.poll = GLib.timeout_add(100, self.start_poll)
            # renew only old previews, one after the other: a grim per window
            # all at once made the switcher (and the swipe) stutter
            now = time.monotonic()
            order = [self.items[self.sel]] + [c for i, c in enumerate(self.items) if i != self.sel]
            for c in order:
                if now - self.thumb_time.get(c["address"], 0) > self.THUMB_FRESH:
                    self.capture(c)
            if not self.live:
                self.live = GLib.timeout_add(600, self.live_thumbs)
        else:
            self.sel = (self.sel + delta) % len(self.items)
            self.update_selection()

    # 3-finger swipe left/right on the touchpad (hyprland.lua): like Windows
    # the switcher shows up with the previous window selected, moving the
    # fingers moves the selection, lifting them switches.
    def swipe_begin(self):
        if self.visible:
            self.hide()
        self.gesture = True
        self.step(1)
        if not self.visible:
            self.gesture = False

    def swipe_step(self, dx, dy=0):
        if self.visible and self.gesture:
            self.move(dx, dy)

    def move(self, dx, dy):
        """Move the selection by columns/rows without wrapping around:
        left/right stay in the row (they stop at its ends), only up/down
        change the row."""
        n = len(self.items)
        if not n:
            return
        per = max(1, getattr(self, "per_line", n))
        row_start = (self.sel // per) * per
        row_end = min(n - 1, row_start + per - 1)
        sel = max(row_start, min(row_end, self.sel + dx))
        if dy:
            row = sel // per + dy
            last_row = (n - 1) // per
            row = max(0, min(last_row, row))
            sel = min(n - 1, row * per + sel % per)
        if sel != self.sel:
            self.sel = sel
            self.update_selection()

    def swipe_end(self, cancelled=False):
        if self.visible and self.gesture:
            if cancelled:
                self.hide()
            else:
                self.commit()

    def build(self):
        self.cards = []
        clear(self.flow)
        mon_w, mon_h = self.monitor_size()
        card_w = self.W + 24 + 6                # card padding + border, column spacing
        card_h = self.H + 70
        fit = max(1, int((mon_w * 0.92 - 28) // card_w))
        per_line = max(1, min(fit, len(self.items)))
        self.per_line = per_line
        self.flow.set_min_children_per_line(per_line)
        self.flow.set_max_children_per_line(per_line)
        self.scroll.set_max_content_height(max(card_h, int(mon_h * 0.8)))
        for i, c in enumerate(self.items):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            card.add_css_class("card")
            card.set_hexpand(False)
            head = Gtk.Box(spacing=8)
            img = Gtk.Image.new_from_gicon(self.icons.get(c["class"]))
            img.set_pixel_size(16)
            head.append(img)
            t = label(c["title"] or c["class"], "card-title")
            t.set_hexpand(True)
            t.set_width_chars(1)
            t.set_max_width_chars(1)
            head.set_size_request(self.W, -1)
            head.append(t)
            card.append(head)

            stack = Gtk.Stack()
            stack.set_size_request(self.W, self.H)
            stack.add_css_class("thumb")
            big = Gtk.Image.new_from_gicon(self.icons.get(c["class"]))
            big.set_pixel_size(48)
            stack.add_named(big, "icon")
            pic = Thumb(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
            stack.add_named(pic, "pic")
            card.append(stack)
            card.pic, card.stack = pic, stack
            if c["address"] in self.thumbs:
                pic.set_paintable(self.thumbs[c["address"]])
                stack.set_visible_child_name("pic")

            click = Gtk.GestureClick()
            click.connect("released", lambda *_a, i=i: self.pick(i))
            card.add_controller(click)
            self.flow.append(card)
            self.cards.append(card)

    def monitor_size(self):
        disp = Gdk.Display.get_default()
        mon = None
        surf = self.win.get_surface()
        if surf is not None:
            mon = disp.get_monitor_at_surface(surf)
        if mon is None:
            mons = disp.get_monitors()
            mon = mons.get_item(0) if mons.get_n_items() else None
        if mon is None:
            return 1920, 1080
        g = mon.get_geometry()
        return g.width, g.height

    def update_selection(self):
        for i, card in enumerate(self.cards):
            (card.add_css_class if i == self.sel else card.remove_css_class)("selected")
        GLib.idle_add(self.scroll_to_selection)

    def scroll_to_selection(self):
        if not self.cards or not self.visible:
            return False
        ok_, r = self.cards[self.sel].compute_bounds(self.flow)
        adj = self.scroll.get_vadjustment()
        if ok_ and adj:
            top, bottom = r.get_y(), r.get_y() + r.get_height()
            if top < adj.get_value():
                adj.set_value(top)
            elif bottom > adj.get_value() + adj.get_page_size():
                adj.set_value(bottom - adj.get_page_size())
        return False

    def pick(self, i):
        self.sel = i
        self.commit()

    def commit(self):
        if not self.visible:
            return
        target = self.items[self.sel]
        self.visible = False
        # 1. give the keyboard back while still on screen (Hyprland refocuses
        #    the previous window right then), 2. focus the target, 3. hide
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.NONE)

        def finish():
            self.focus(target["address"], target["class"], tries=4)
            GLib.timeout_add(30, lambda: self.hide() and False)
            return False
        GLib.timeout_add(20, finish)

    def focus(self, addr, cls, tries):
        """Focus a window and make sure it really ended up active and on top.
        ShowWindow (hyprland.lua) restores a minimized window before focusing it."""
        hypr_eval(f'ShowWindow("{addr}")')
        dispatch(f'hl.dsp.window.alter_zorder({{ mode = "top", window = "address:{addr}" }})')

        def check():
            active = (hypr_json("activewindow") or {}).get("address")
            print(f"winshell: switch to {cls} {addr} -> active {active}", file=sys.stderr, flush=True)
            if active != addr and tries > 1:
                self.focus(addr, cls, tries - 1)
            return False
        GLib.timeout_add(120, check)

    def start_poll(self):
        self.poll = GLib.timeout_add(30, self.poll_alt) if self.visible else None
        return False

    def hide(self):
        self.visible = False
        self.task_view = False
        self.gesture = False
        self.win.set_visible(False)
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.NONE)

    # previews ------------------------------------------------------------
    def pause_thumbs(self, seconds=1.2):
        """A window is about to change size or go away (hyprland.lua says so):
        no pictures for a moment, and the running ones stop – grim crashes when
        the window changes under it (and Fedora shows a crash report)."""
        self.paused_until = max(self.paused_until, time.monotonic() + seconds)
        for p in list(self.grims):
            try:
                p.kill()
            except OSError:
                pass

    def stable(self, c):
        """True once a window has kept its place and size for a second."""
        key = (tuple(c.get("at") or ()), tuple(c.get("size") or ()),
               (c.get("workspace") or {}).get("name"))
        now = time.monotonic()
        prev = self.geo.get(c["address"])
        if not prev or prev[0] != key:
            self.geo[c["address"]] = (key, now)
            return False
        return now - prev[1] >= 1.0

    def fresh(self, c):
        """The window as Hyprland sees it right now (or None if it is gone)."""
        return next((x for x in hypr_json("clients") or [] if x.get("address") == c["address"]), None)

    def capture(self, c, update=True):
        if time.monotonic() < self.paused_until:
            return
        c = self.fresh(c)
        if not c or not self.stable(c) or c["workspace"].get("name", "").startswith("special:"):
            return
        if c["workspace"].get("name", "").startswith("special:"):
            return                      # hidden windows are not rendered
        addr, sid = c["address"], c.get("stableId")
        if not sid:
            return
        factor = min(1.0, 3 * self.W / max(1, c["size"][0]))
        if any(q[0]["address"] == addr for q in self.cap_queue):
            return
        self.cap_queue.append((c, update, sid, factor))
        self.next_capture()

    THUMB_FRESH = 2             # s: a preview this young is shown as it is
    CAP_PARALLEL = 3

    def live_thumbs(self):
        """While the switcher is open: the selected window's preview every
        0.6 s, the others in turn – so they look live, like on Windows."""
        if not self.visible or not self.items:
            self.live = None
            return False
        if time.monotonic() < self.paused_until:
            return True
        sel = self.items[self.sel]
        self.capture(sel)
        others = [c for c in self.items if c is not sel]
        if others:
            oldest = min(others, key=lambda c: self.thumb_time.get(c["address"], 0))
            self.capture(oldest)
        return True

    def warm_thumbs(self):
        """Every few seconds, while the switcher is closed, renew the oldest
        preview of a visible window – so they are ready when it opens."""
        if not self.visible and not self.cap_queue and time.monotonic() >= self.paused_until:
            now = time.monotonic()
            wins = [c for c in self.collect()
                    if not c["workspace"].get("name", "").startswith("special:")]
            calm = [c for c in wins if self.stable(c)]     # also keeps track of who moves
            stale = [c for c in calm if now - self.thumb_time.get(c["address"], 0) > self.THUMB_FRESH]
            if stale:
                self.capture(min(stale, key=lambda c: self.thumb_time.get(c["address"], 0)), update=False)
        return True

    def next_capture(self):
        while self.cap_busy < self.CAP_PARALLEL and self.cap_queue:
            self.start_capture()
        return False

    def start_capture(self):
        self.cap_busy += 1
        c, update, sid, factor = self.cap_queue.pop(0)
        addr = c["address"]

        def grab():
            try:
                p = subprocess.Popen(["grim", "-T", sid, "-s", f"{factor:.3f}", "-t", "jpeg", "-q", "85", "-"],
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                self.grims.add(p)
                try:
                    out = p.communicate(timeout=3)[0]
                finally:
                    self.grims.discard(p)
                return Gdk.Texture.new_from_bytes(GLib.Bytes.new(out)) if out and p.returncode == 0 else None
            except Exception:
                return None

        def done(tex):
            self.cap_busy -= 1
            GLib.idle_add(self.next_capture)
            if tex:
                self.thumbs[addr] = tex
                self.thumb_time[addr] = time.monotonic()
                if update and self.visible:
                    for item, card in zip(self.items, self.cards):
                        if item["address"] == addr:
                            card.pic.set_paintable(tex)
                            card.stack.set_visible_child_name("pic")
        bg(grab, done)

    def watch_events(self):
        """Keep previews warm: capture each window when it becomes active."""
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(hypr_socket(".socket2.sock"))
            for line in s.makefile("r", errors="replace"):
                ev, _, data = line.strip().partition(">>")
                if ev in TASKBAR_EVENTS:
                    GLib.idle_add(refresh_taskbar)
                if ev == "configreloaded":
                    self.paused_until = time.monotonic() + 3   # grim can crash during a reload
                elif ev == "activewindowv2" and data:
                    GLib.idle_add(self.schedule_capture, "0x" + data)
                elif ev == "closewindow" and data:
                    self.thumbs.pop("0x" + data, None)
                    self.geo.pop("0x" + data, None)
                    self.thumb_time.pop("0x" + data, None)
        except Exception as e:
            print("winshell: event socket:", e, file=sys.stderr)

    @staticmethod
    def find(addr):
        return next((c for c in hypr_json("clients") or [] if c["address"] == addr), None)

    def schedule_capture(self, addr):
        # grim crashes when a window changes size mid-capture, so only capture
        # once the window has kept the same geometry for a while
        c = self.find(addr)
        if c:
            GLib.timeout_add(1500, self.capture_if_stable, addr, (c["at"], c["size"]))
        return False

    def capture_if_stable(self, addr, geometry):
        c = self.find(addr)
        if c and not self.visible and (c["at"], c["size"]) == geometry:
            self.capture(c, update=False)
        return False


# Windows-style "searching" indicator: little dots that run across, slowing
# down in the middle, one after the other.
class ScanDots(Gtk.DrawingArea):
    PERIOD = 2.4                # s for one round
    DOTS = 5

    def __init__(self):
        super().__init__()
        self.set_content_height(6)
        self.set_hexpand(True)
        self.set_draw_func(self.draw)
        self.tick = 0
        self.t0 = 0
        self.running = False
        self.stop_at = 0
        self.set_opacity(0)

    def start(self, delay=0.3):
        """Something is loading: show the dots – but only if it takes longer
        than `delay` (a quick look-up shows nothing)."""
        self.running = True
        self.stop_at = 0
        if self.tick:
            return
        GLib.timeout_add(int(delay * 1000), self.appear)

    def appear(self):
        if self.running and not self.tick:
            self.set_opacity(1)
            self.stop_at = time.monotonic() + 0.6       # once shown, not just a flash
            self.t0 = 0
            self.tick = self.add_tick_callback(self.on_tick)
        return False

    def stop(self):
        self.running = False

    def on_tick(self, _w, clock):
        now = clock.get_frame_time() / 1e6
        if not self.t0:
            self.t0 = now
        self.t = now - self.t0
        self.queue_draw()
        if not self.running and time.monotonic() >= self.stop_at:
            self.tick = 0
            self.set_opacity(0)
            return False
        return True

    def draw(self, _a, cr, w, h):
        if not self.tick:
            return
        cr.set_source_rgba(0x4c / 255, 0xc2 / 255, 1, 1)
        for i in range(self.DOTS):
            p = ((getattr(self, "t", 0) - i * 0.13) % self.PERIOD) / 1.6
            if not 0 <= p <= 1:
                continue
            x = w * (0.5 + 0.5 * (2 * p - 1) ** 3)       # fast at the ends, slow in the middle
            cr.arc(x, h / 2, 1.8, 0, 2 * math.pi)
            cr.fill()


# ── Quick settings ───────────────────────────────────────────────────────
class QuickSettings:
    def __init__(self, app):
        self.shell = app
        self.visible = False
        self.updating = False
        self.state = {}
        self.pending = {}
        self.last_slide = 0.0
        # tiles the user just clicked: key -> (wanted state, until). The
        # background refresh may read the old state while the change is
        # still being applied; it must not flip the tile back meanwhile.
        self.wanted = {}

        # transparent full-screen layer that closes the flyout on outside clicks
        self.backdrop = layer_window(app, "winshell-catcher", LS.Layer.TOP,
                                     anchors=(LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT),
                                     exclusive=-1)
        self.backdrop.add_css_class("catcher")
        self.backdrop.set_child(Gtk.Box(hexpand=True, vexpand=True))
        self.backdrop_for = None          # "qs" or "rofi"
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: self.outside_click())
        self.backdrop.add_controller(click)

        self.win = layer_window(app, "winshell-qs", LS.Layer.OVERLAY,
                                anchors=(LS.Edge.BOTTOM, LS.Edge.RIGHT),
                                margins={LS.Edge.BOTTOM: 12, LS.Edge.RIGHT: 12},
                                keyboard=LS.KeyboardMode.EXCLUSIVE)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.win.add_controller(keys)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("qs")
        panel.add_css_class("flyout")
        panel.set_overflow(Gtk.Overflow.HIDDEN)
        self.panel = panel
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
                               transition_duration=160, vhomogeneous=False, interpolate_size=True)
        self.stack.add_named(self.build_main(), "main")
        self.stack.add_named(self.build_wifi(), "wifi")
        self.stack.add_named(self.build_bt(), "bluetooth")
        self.stack.add_named(self.build_sound(), "sound")
        # A layer surface grows with its content but never shrinks back: after
        # leaving the (taller) Wi-Fi page the window kept its old height and
        # showed an empty grey area below the footer. Fit it again once the
        # page slide is done.
        self.stack.connect("notify::transition-running",
                           lambda s, _p: None if s.get_transition_running() else self.fit())
        panel.append(self.stack)
        panel.append(self.build_footer())
        self.win.set_child(panel)

        # keep the state warm so the flyout opens fully drawn, without a flash
        self.nets = []
        self.refresh()
        bg(lambda: self.scan_wifi(False), self.cache_nets)
        GLib.timeout_add_seconds(5, self.background_refresh)

    def cache_nets(self, nets):
        self.nets = nets

    def background_refresh(self):
        if not self.visible:
            self.refresh()
        return True

    # ── main page ──
    def build_main(self):
        # Windows 11 layout: the tiles, a divider line, then the sliders
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.add_css_class("qs-main")
        grid = Gtk.Grid(column_spacing=13, row_spacing=6, column_homogeneous=True)
        grid.add_css_class("qs-tiles")
        self.tiles = {}
        specs = [
            ("wifi", "win11-wifi-4", "Wi-Fi", self.toggle_wifi, lambda: self.open_page("wifi")),
            ("bt", "win11-bluetooth", "Bluetooth", self.toggle_bt, lambda: self.open_page("bluetooth")),
            ("airplane", "win11-airplane", "Airplane mode", self.toggle_airplane, None),
            ("dnd", "win11-moon", "Do not disturb", self.toggle_dnd, None),
            ("snip", "win11-snip", "Snipping Tool", self.snip, None),
            ("lock", "win11-lock", "Lock", self.lock, None),
        ]
        for i, (key, ic, text, on, more) in enumerate(specs):
            outer, row, lbl, img = self.tile(ic, text, on, more)
            self.tiles[key] = (row, lbl, img)
            grid.attach(outer, i % 3, i // 3, 1, 1)
        page.append(grid)

        self.bright_row, self.bright = self.slider("win11-sun", self.set_brightness)
        self.vol_row, self.vol = self.slider("win11-volume-3", self.set_volume,
                                             on_icon=self.toggle_mute,
                                             on_more=lambda: self.open_page("sound"),
                                             more_icon="win11-sound-output")
        sliders = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sliders.add_css_class("qs-sliders")
        sliders.append(self.bright_row)
        sliders.append(self.vol_row)
        page.append(sliders)
        return page

    def tile(self, ic, text, on_toggle, on_more):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9)
        row = Gtk.Box()
        row.add_css_class("tile")
        img = icon(ic, 20)
        main = button(img, "tile-main", on_click=on_toggle)
        main.set_hexpand(True)
        row.append(main)
        if on_more:
            row.append(button(icon("win11-chevron-right", 16), "tile-more", on_click=on_more))
        lbl = label(text, "tile-label", xalign=0.5)
        lbl.set_wrap(True)
        lbl.set_lines(2)
        lbl.set_justify(Gtk.Justification.CENTER)
        lbl.set_yalign(0.0)
        lbl.set_width_chars(1)              # wrap inside the tile's width
        outer.append(row)
        outer.append(lbl)
        return outer, row, lbl, img

    def slider(self, ic, on_change, on_icon=None, on_more=None, more_icon=None):
        row = Gtk.Box(spacing=10)
        row.add_css_class("slider-row")
        img = icon(ic, 22)
        lead = button(img, "flat-btn", on_click=on_icon)    # same size in both rows
        if not on_icon:
            lead.set_can_target(False)
            lead.set_can_focus(False)
        row.append(lead)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.set_hexpand(True)
        scale.set_draw_value(False)
        scale.connect("value-changed", lambda s: None if self.updating else on_change(int(s.get_value())))
        row.append(scale)
        # Like Windows, both sliders are equally long: the brightness row gets
        # an invisible stand-in for the volume row's ">" (output devices)
        # "select a sound output": output icon + ">", one button like Windows
        more_box = Gtk.Box(spacing=4)
        more_box.append(icon(more_icon or "win11-sound-output", 22))
        more_box.append(icon("win11-chevron-right", 16))
        more = button(more_box, "flat-btn", "slider-more", on_click=on_more,
                      tooltip="Select a sound output" if on_more else None)
        if not on_more:
            more.set_opacity(0)
            more.set_can_target(False)
            more.set_can_focus(False)
        row.append(more)
        row.img = img
        return row, scale

    def build_footer(self):
        foot = Gtk.Box(spacing=8)
        foot.add_css_class("qs-footer")
        self.bat_img = icon("win11-battery-100", 24)
        self.bat_lbl = label("", "footer-text")
        foot.append(self.bat_img)
        foot.append(self.bat_lbl)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        foot.append(spacer)
        foot.append(button(icon("win11-settings", 20), "flat-btn", tooltip="All settings",
                           on_click=lambda: (self.hide(), spawn(f"{SCRIPTS}/settings.sh"))))
        return foot

    # ── sub pages ──
    def page(self, title, on_switch, more_text, more_cmd, on_rescan=None):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.add_css_class("qs-page")
        head = Gtk.Box(spacing=8)
        head.append(button(icon("win11-chevron-left", 14), "flat-btn", on_click=lambda: self.open_page("main")))
        head.append(label(title, "page-title"))
        sp = Gtk.Box()
        sp.set_hexpand(True)
        head.append(sp)
        rescan = None
        if on_rescan:                           # search again (a search ends after a while)
            rescan = button(icon("win11-refresh", 16), "flat-btn", "rescan-btn",
                            on_click=on_rescan, tooltip="Search again")
            rescan.set_valign(Gtk.Align.CENTER)
            head.append(rescan)
        sw = Gtk.Switch(valign=Gtk.Align.CENTER)
        if on_switch:
            # GTK only reports a click at the end of the knob's slide; a
            # refresh in between could undo it (knob off, still blue, still
            # searching). So the click is taken right away and the switch's
            # colour is set by us (sync_switch), never by GTK.
            sw.connect("state-set", lambda s, v: True)
            sw.connect("notify::active", lambda s, _p: None if self.updating else on_switch(s.get_active()))
            head.append(sw)
        page.append(head)
        dots = ScanDots()                       # "searching…", like Windows
        page.append(dots)

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    min_content_height=300, max_content_height=300)
        lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lst.dots = dots
        lst.rescan = rescan
        self.show_rescan(lst, False)
        lst.seen = set()                        # rows already shown (they don't animate again)
        scroll.set_child(lst)
        page.append(scroll)

        foot = Gtk.Box()
        foot.append(button(more_text, "link-btn", on_click=lambda: (self.hide(), spawn(more_cmd))))
        page.append(foot)
        return page, sw, lst

    def build_wifi(self):
        page, self.wifi_switch, self.wifi_list = self.page(
            "Wi-Fi", self.set_wifi, "More Wi-Fi settings", "nm-connection-editor",
            on_rescan=lambda: self.load_wifi(rescan=True))
        return page

    def build_bt(self):
        page, self.bt_switch, self.bt_list = self.page(
            "Bluetooth", self.set_bt, "More Bluetooth settings", "blueman-manager",
            on_rescan=self.search_bt)
        return page

    def build_sound(self):
        page, _sw, self.sound_list = self.page(
            "Sound output", None, "More volume settings", "pavucontrol")
        return page

    @staticmethod
    def show_rescan(lst, show):
        """The "search again" button: only when the radio is on and no search runs."""
        if lst.rescan:
            lst.rescan.set_opacity(1 if show else 0)
            lst.rescan.set_can_target(show)
            lst.rescan.set_can_focus(show)

    # ── show / hide ──
    def toggle(self, page="main"):
        if self.visible and page == "main":
            self.hide()
        else:
            self.show(page)

    def show(self, page="main"):
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.open_page(page)
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        if not self.visible:
            spawn("pkill -x rofi")          # only one flyout at a time, like Windows
            if self.shell.start.visible:
                self.shell.start.hide()
            if self.shell.calendar.visible:
                self.shell.calendar.hide()
            self.visible = True
            if getattr(self.shell, "taskbar", None):
                self.shell.taskbar.close_overflow()
            self.backdrop_for = "qs"
            self.backdrop.present()
            slide_in(self.panel)
            self.win.present()
            GLib.timeout_add(1500, self.live_refresh)
        self.refresh()

    def live_refresh(self):
        if not self.visible:
            return False
        self.refresh()
        return True

    def hide(self):
        self.visible = False
        self.win.set_visible(False)
        self.backdrop.set_visible(False)
        self.backdrop_for = None
        self.stop_bt_search()

    # The start menu (rofi) uses the same click catcher: clicking anywhere
    # outside closes it, like on Windows.
    def backdrop_rofi(self, on):
        if on:
            if self.visible:
                self.hide()
            self.backdrop_for = "rofi"
            self.backdrop.present()
        elif self.backdrop_for == "rofi":
            self.backdrop_for = None
            self.backdrop.set_visible(False)

    def catcher_for(self, who):
        """Show the click catcher for another flyout (start menu), or hide it."""
        tb = getattr(self.shell, "taskbar", None)
        if who and who != "tray" and tb:
            tb.close_overflow()
        if who:
            self.backdrop_for = who
            self.backdrop.present()
        elif self.backdrop_for not in (None, "qs"):
            self.backdrop_for = None
            self.backdrop.set_visible(False)

    def outside_click(self):
        if self.backdrop_for == "rofi":
            spawn("pkill -x rofi")
            self.backdrop_rofi(False)
        elif self.backdrop_for == "start":
            self.shell.start.hide()
        elif self.backdrop_for == "calendar":
            self.shell.calendar.hide()
        elif self.backdrop_for == "tray":
            self.shell.taskbar.close_overflow()
        elif self.backdrop_for == "clipboard":
            self.shell.clip.hide()
        else:
            self.hide()

    def on_key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            if self.stack.get_visible_child_name() != "main":
                self.open_page("main")
            else:
                self.hide()
            return True
        return False

    def fit(self):
        self.win.set_default_size(1, 1)
        self.win.queue_resize()

    def open_page(self, name):
        if name != "bluetooth":
            self.stop_bt_search()
        if name == "wifi":
            self.wifi_list.seen.clear()
        elif name == "bluetooth":
            self.bt_list.seen.clear()
        elif name == "sound":
            self.sound_list.seen.clear()
        self.stack.set_visible_child_name(name)
        if self.stack.get_transition_type() == Gtk.StackTransitionType.NONE:
            self.fit()
        if name == "wifi":
            self.load_wifi(rescan=True)
        elif name == "bluetooth":
            self.bt_shown = None
            self.load_bt()
            self.search_bt()
        elif name == "sound":
            self.load_sound()

    # ── state ──
    def refresh(self):
        bg(self.read_state, self.apply_state)

    def read_state(self):
        st = {}
        st["wifi"] = sh("nmcli radio wifi").strip() == "enabled"
        st["ssid"] = ""
        for line in sh("nmcli -t -f ACTIVE,SIGNAL,SSID dev wifi list --rescan no").splitlines():
            if line.startswith("yes:"):
                _, sig, ssid = line.split(":", 2)
                st["ssid"], st["signal"] = ssid.replace("\\:", ":"), int(sig or 0)
        show = sh(["bluetoothctl", "show"], timeout=3)
        st["bt"] = "Powered: yes" in show
        con = [l.split(" ", 2)[2] for l in sh(["bluetoothctl", "devices", "Connected"], timeout=3).splitlines()
               if l.startswith("Device ") and l.count(" ") >= 2]
        st["bt_dev"] = con[0] if st["bt"] and con else ""
        rf = sh("rfkill -n -o SOFT")
        st["airplane"] = bool(rf.split()) and all(x == "blocked" for x in rf.split())
        st["dnd"] = sh("swaync-client -D").strip() == "true"
        m = sh("brightnessctl -m").split(",")
        st["bright"] = int(m[3].rstrip("%")) if len(m) > 3 else None
        v = sh("wpctl get-volume @DEFAULT_AUDIO_SINK@")
        mv = re.search(r"([\d.]+)", v)
        st["vol"] = round(float(mv.group(1)) * 100) if mv else 0
        st["muted"] = "MUTED" in v
        try:
            st["bat"] = int(open("/sys/class/power_supply/BAT0/capacity").read())
            st["bat_status"] = open("/sys/class/power_supply/BAT0/status").read().strip()
        except OSError:
            st["bat"] = None
        return st

    def apply_state(self, st):
        now = time.monotonic()
        for key, (value, until) in list(self.wanted.items()):
            if st.get(key) == value or now > until:
                del self.wanted[key]
            else:
                st[key] = value
        self.state = st
        self.updating = True
        try:
            self.set_tile("wifi", st["wifi"], st["ssid"] or "Wi-Fi")
            sig = st.get("signal", 0)
            self.tiles["wifi"][2].set_from_icon_name(
                f"win11-wifi-{min(4, 1 + sig // 25)}" if st["ssid"] else "win11-wifi-4")
            self.set_tile("bt", st["bt"], st["bt_dev"] or "Bluetooth")
            self.set_tile("airplane", st["airplane"])
            self.set_tile("dnd", st["dnd"])
            self.sync_switch(self.wifi_switch, st["wifi"])
            self.sync_switch(self.bt_switch, st["bt"])
            recent = time.monotonic() - self.last_slide < 1.5
            if st["bright"] is None:
                self.bright_row.set_visible(False)
            elif not recent:
                self.bright.set_value(st["bright"])
            if not recent:
                self.vol.set_value(st["vol"])
            self.vol_row.img.set_from_icon_name(self.volume_icon(st["vol"], st["muted"]))
            if st["bat"] is None:
                self.bat_img.set_visible(False)
                self.bat_lbl.set_visible(False)
            else:
                kind = {"Charging": "-charging", "Full": "-plugged",
                        "Not charging": "-plugged"}.get(st["bat_status"], "")
                self.bat_img.set_from_icon_name(f"win11-battery-{round(st['bat'], -1)}{kind}")
                self.bat_lbl.set_text(f"{st['bat']}%")
        finally:
            self.updating = False

    def sync_switch(self, sw, on):
        """Knob and colour to `on`, without acting on it."""
        was, self.updating = self.updating, True
        try:
            if sw.get_active() != on:
                sw.set_active(on)
            sw.set_state(on)
        finally:
            self.updating = was

    def want(self, key, on, hold=4):
        """Show a clicked tile's new state right away (before it is applied)."""
        self.wanted[key] = (on, time.monotonic() + hold)
        self.state[key] = on
        self.set_tile(key, on)

    def set_tile(self, key, on, text=None):
        row, lbl, _ = self.tiles[key]
        (row.add_css_class if on else row.remove_css_class)("on")
        if text is not None:
            lbl.set_text(text)

    @staticmethod
    def volume_icon(vol, muted):
        if muted or vol == 0:
            return "win11-volume-mute"
        return f"win11-volume-{1 if vol < 34 else 2 if vol < 67 else 3}"

    def later(self, fn, delay=600):
        GLib.timeout_add(delay, lambda: fn() and False)

    # ── actions ──
    def toggle_wifi(self):
        self.set_wifi(not self.state.get("wifi"))

    def set_wifi(self, on):
        self.want("wifi", on, 8)
        self.sync_switch(self.wifi_switch, on)
        self.wifi_gen = getattr(self, "wifi_gen", 0) + 1     # a running scan's result is stale now
        gen = self.wifi_gen
        self.wifi_list.dots.stop()
        self.show_rescan(self.wifi_list, False)
        if self.stack.get_visible_child_name() == "wifi":
            clear(self.wifi_list)
            self.wifi_list.append(label("Looking for networks…" if on else "Wi-Fi is turned off",
                                        "empty-text", xalign=0.5))
            if on:
                self.wifi_list.dots.start(delay=0)

        def after(_r):
            if gen != self.wifi_gen:            # switched again meanwhile
                return
            self.refresh()
            if self.stack.get_visible_child_name() == "wifi":
                if on:
                    self.later(lambda: gen == self.wifi_gen and self.load_wifi(True), 1500)
                else:
                    self.load_wifi()
        bg(lambda: ok(f"nmcli radio wifi {'on' if on else 'off'}"), after)

    def toggle_bt(self):
        self.set_bt(not self.state.get("bt"))

    def set_bt(self, on):
        self.want("bt", on, 10)
        self.sync_switch(self.bt_switch, on)
        self.stop_bt_search()
        self.bt_op = getattr(self, "bt_op", 0) + 1
        op = self.bt_op
        self.bt_shown = None
        self.show_rescan(self.bt_list, False)
        if self.stack.get_visible_child_name() == "bluetooth":
            clear(self.bt_list)
            self.bt_list.append(label("Searching for devices…" if on else "Bluetooth is turned off",
                                      "empty-text", xalign=0.5))
            if on:
                self.bt_list.dots.start(delay=0)    # searching starts with the switch, not seconds later

        def after(r):
            if op != self.bt_op:                    # switched again meanwhile: that one decides
                return
            if not r:
                self.wanted.pop("bt", None)         # didn't work: show the real state again
            self.refresh()
            if on and r:
                self.state["bt"] = True
                if self.stack.get_visible_child_name() == "bluetooth":
                    self.search_bt()                # just turned on: look for devices right away
                    return
            self.bt_list.dots.stop()
            if self.stack.get_visible_child_name() == "bluetooth":
                self.load_bt()
        bg(lambda: self.bt_power(on), after)

    BT_LOCK = threading.Lock()          # one switch at a time: the last click wins

    @staticmethod
    def bt_power(on):
        """Switch Bluetooth on/off and wait until it really is. After "rfkill
        unblock" the controller only shows up a moment later – powering it on
        right away failed, so it took a second click."""
        with QuickSettings.BT_LOCK:
            return QuickSettings._bt_power(on)

    @staticmethod
    def _bt_power(on):
        if on:
            ok("rfkill unblock bluetooth")
            for _ in range(50):
                if "Controller" in sh(["bluetoothctl", "list"], timeout=3):
                    break
                time.sleep(0.1)
        for _ in range(6):
            ok(["bluetoothctl", "power", "on" if on else "off"], timeout=5)
            if ("Powered: yes" in sh(["bluetoothctl", "show"], timeout=3)) == on:
                return True
            time.sleep(0.4)
        return False

    # Like Windows, airplane mode remembers which radios were on and turns
    # exactly those back on afterwards. Unblocking Bluetooth makes bluez
    # power it on by itself, so it is switched off again if it was off.
    def toggle_airplane(self):
        on = not self.state.get("airplane")
        self.want("airplane", on)
        if on:
            before = {"wifi": bool(self.state.get("wifi", True)), "bt": bool(self.state.get("bt"))}
            try:
                with open(AIRPLANE, "w") as f:
                    json.dump(before, f)
            except OSError:
                pass
            bg(lambda: ok("rfkill block all"), lambda _r: self.later(self.refresh))
            return
        try:
            with open(AIRPLANE) as f:
                before = json.load(f)
        except (OSError, ValueError):
            before = {"wifi": True, "bt": False}
        self.want("wifi", before.get("wifi", True))
        self.want("bt", before.get("bt", False))

        def run():
            ok("rfkill unblock all")
            ok(f"nmcli radio wifi {'on' if before.get('wifi', True) else 'off'}")
            if before.get("bt"):
                ok(["bluetoothctl", "power", "on"], timeout=5)
            else:
                for _ in range(12):         # bluez may power it on a moment later
                    if "Powered: yes" in sh(["bluetoothctl", "show"], timeout=3):
                        ok(["bluetoothctl", "power", "off"], timeout=5)
                    time.sleep(0.25)
            try:
                os.unlink(AIRPLANE)
            except OSError:
                pass
        bg(run, lambda _r: self.refresh())

    def toggle_dnd(self):
        on = not self.state.get("dnd")
        self.want("dnd", on)
        bg(lambda: ok(f"swaync-client {'-dn' if on else '-df'}"), lambda _r: self.refresh())

    def snip(self):
        self.hide()
        spawn(f"sleep 0.3; {SCRIPTS}/screenshot.sh area")

    def lock(self):
        self.hide()
        spawn(f"{SCRIPTS}/lock.sh")

    def set_brightness(self, v):
        self.debounce("bright", lambda: ok(f"brightnessctl -q set {max(1, v)}%"))

    def set_volume(self, v):
        self.vol_row.img.set_from_icon_name(self.volume_icon(v, False))
        self.debounce("vol", lambda: ok(f"wpctl set-mute @DEFAULT_AUDIO_SINK@ 0; "
                                        f"wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ {v / 100:.2f}"))

    def toggle_mute(self):
        bg(lambda: ok("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"), lambda _r: self.refresh())

    def debounce(self, key, fn):
        self.last_slide = time.monotonic()
        if key in self.pending:
            GLib.source_remove(self.pending[key])

        def fire():
            self.pending.pop(key, None)
            threading.Thread(target=fn, daemon=True).start()
            return False
        self.pending[key] = GLib.timeout_add(40, fire)

    # ── Wi-Fi page ──
    def load_wifi(self, rescan=False):
        if self.nets:
            self.show_wifi(self.nets)
        bg(lambda: self.scan_wifi(False), self.show_wifi)
        on = self.state.get("wifi", True)
        if rescan and on:                                   # a real scan takes a few seconds
            gen = getattr(self, "wifi_gen", 0)
            self.wifi_list.dots.start()
            self.show_rescan(self.wifi_list, False)

            def done(nets):
                if gen != getattr(self, "wifi_gen", 0):     # Wi-Fi switched meanwhile
                    return
                self.wifi_list.dots.stop()
                self.show_wifi(nets)
                self.show_rescan(self.wifi_list, bool(self.state.get("wifi", True)))
            bg(lambda: self.scan_wifi(True), done)
        elif not self.wifi_list.dots.running:
            self.show_rescan(self.wifi_list, bool(on))

    @staticmethod
    def scan_wifi(rescan):
        out = sh(["nmcli", "-t", "-f", "IN-USE,SIGNAL,SECURITY,SSID", "dev", "wifi", "list",
                  "--rescan", "yes" if rescan else "no"], timeout=20)
        known = {l.rsplit(":", 1)[0].replace("\\:", ":") for l in
                 sh(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"]).splitlines()
                 if l.endswith(":802-11-wireless")}
        nets = {}
        for line in out.splitlines():
            parts = [p.replace("\\:", ":") for p in re.split(r"(?<!\\):", line, maxsplit=3)]
            if len(parts) < 4 or not parts[3]:
                continue
            inuse, sig, sec, ssid = parts
            net = {"ssid": ssid, "signal": int(sig or 0), "secure": sec not in ("", "--"),
                   "enterprise": "802.1X" in sec, "active": inuse == "*", "known": ssid in known}
            old = nets.get(ssid)
            if not old or net["active"] or (not old["active"] and net["signal"] > old["signal"]):
                nets[ssid] = net
        for net in nets.values():
            if net["enterprise"] and net["known"]:
                net["identity"], net["needs_password"] = QuickSettings.eap_secrets(net["ssid"])
        return sorted(nets.values(), key=lambda n: (not n["active"], -n["signal"]))

    @staticmethod
    def eap_secrets(con):
        """(user name, password missing?) of a saved school/company Wi-Fi.
        A password saved "for this user only" (agent-owned, e.g. by the
        connection editor) can't be used here – there is no keyring agent
        running – so it counts as missing too."""
        out = sh(["nmcli", "-s", "-g", "802-1x.identity,802-1x.password,802-1x.password-flags",
                  "connection", "show", "id", con]).splitlines()
        identity = out[0] if out else ""
        password = out[1] if len(out) > 1 else ""
        flags = (out[2].split() or ["0"])[0] if len(out) > 2 else "0"
        missing = not password or (flags.isdigit() and int(flags) & 0b101)   # agent-owned / not saved
        return identity, bool(missing)

    def show_wifi(self, nets):
        self.nets = nets
        focus = self.win.get_focus()
        if isinstance(focus, Gtk.Text) or isinstance(focus, Gtk.Entry):
            return                      # the user is typing a password
        clear(self.wifi_list)
        if not self.state.get("wifi", True):
            self.wifi_list.append(label("Wi-Fi is turned off", "empty-text", xalign=0.5))
            return
        if not nets:
            self.wifi_list.append(label("Looking for networks…", "empty-text", xalign=0.5))
            return
        self.add_rows(self.wifi_list, [(n["ssid"], self.wifi_row(n)) for n in nets])

    @staticmethod
    def add_rows(lst, rows):
        """Rows that weren't there before glide in, one shortly after the
        other (rows already shown just stay)."""
        new = 0
        for key, row in rows:
            if key not in lst.seen:
                row.add_css_class("appear")
                row.add_css_class(f"appear-{min(new, 8)}")
                new += 1
                lst.seen.add(key)
            lst.append(row)

    def wifi_row(self, n):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("net")
        top = Gtk.Box(spacing=12)
        lvl = min(4, 1 + n["signal"] // 25)
        top.append(icon(f"win11-wifi-{lvl}{'-lock' if n['secure'] else ''}", 20))
        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        txt.append(label(n["ssid"], "net-name"))
        status = ("Connected, secured" if n["secure"] else "Connected") if n["active"] else \
                 ("Secured" if n["secure"] else "Open")
        st_lbl = label(status, "net-status")
        txt.append(st_lbl)
        txt.set_hexpand(True)
        top.append(txt)
        box.append(top)

        rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=120)
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        entry = user = None
        if n["enterprise"] and not n["active"] and (not n["known"] or n.get("needs_password")):
            # school / company Wi-Fi: user name + password (PEAP / MSCHAPv2)
            user = Gtk.Entry(placeholder_text="User name", text=n.get("identity", ""))
            detail.append(user)
            entry = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="Password")
            detail.append(entry)
        elif n["secure"] and not n["known"] and not n["active"]:
            entry = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="Enter the network security key")
            detail.append(entry)
        msg = label("", "net-error")
        msg.set_visible(False)
        detail.append(msg)
        actions = Gtk.Box(halign=Gtk.Align.END)

        def ask_key():
            """A saved network whose key no longer works: ask for it again,
            like Windows does, instead of retrying the old one forever."""
            nonlocal entry
            if entry or user:
                return
            entry = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="Enter the network security key")
            entry.connect("activate", connect)
            detail.insert_child_after(entry, None)
            entry.grab_focus()

        def connect(*_):
            pw = entry.get_text() if entry else ""
            name = user.get_text().strip() if user else ""
            if user and not name:
                user.grab_focus()
                return
            if entry and not pw:
                entry.grab_focus()
                return
            btn.set_sensitive(False)
            busy_button(btn, "Connecting…")
            msg.set_visible(False)

            def run():
                if n["known"] and user:
                    # saved school/company Wi-Fi without a usable password:
                    # store user name + password with the connection, then connect
                    if not ok(["nmcli", "connection", "modify", "id", n["ssid"],
                               "802-1x.identity", name, "802-1x.password", pw,
                               "802-1x.password-flags", "0"], timeout=15):
                        return False
                    return ok(["nmcli", "connection", "up", "id", n["ssid"]], timeout=60)
                if n["known"] and pw:
                    # saved Wi-Fi, key typed in again: replace the saved one
                    if not ok(["nmcli", "connection", "modify", "id", n["ssid"],
                               "wifi-sec.psk", pw, "wifi-sec.psk-flags", "0"], timeout=15):
                        return False
                    return run_out(["nmcli", "connection", "up", "id", n["ssid"]], timeout=45)
                if n["known"]:
                    return run_out(["nmcli", "connection", "up", "id", n["ssid"]], timeout=45)
                if user:
                    dev = wifi_device()
                    added = ok(["nmcli", "connection", "add", "type", "wifi", "con-name", n["ssid"],
                                "ifname", dev, "ssid", n["ssid"],
                                "wifi-sec.key-mgmt", "wpa-eap", "802-1x.eap", "peap",
                                "802-1x.phase2-auth", "mschapv2", "802-1x.identity", name,
                                "802-1x.password", pw, "802-1x.password-flags", "0"], timeout=15)
                    if added and ok(["nmcli", "connection", "up", "id", n["ssid"]], timeout=60):
                        return True
                    ok(["nmcli", "connection", "delete", "id", n["ssid"]])
                    return False
                cmd = ["nmcli", "device", "wifi", "connect", n["ssid"]]
                if pw:
                    cmd += ["password", pw]
                res = run_out(cmd, timeout=45)
                if not res[0]:
                    # nmcli keeps the new profile even when the key was wrong;
                    # the network would then count as "saved" and never ask
                    # for the key again. Remove it, it never worked.
                    ok(["nmcli", "connection", "delete", "id", n["ssid"]])
                return res

            def done(res):
                success, out = res if isinstance(res, tuple) else (res, "")
                btn.set_sensitive(True)
                busy_button(btn, None, "Connect")
                if success:
                    self.refresh()
                    self.load_wifi()
                    return
                wrong_key = n["secure"] and not user and re.search(
                    r"[Ss]ecrets were required|password|psk|802-1X supplicant|4-way|key", out)
                if wrong_key or (n["known"] and n["secure"] and not user):
                    msg.set_text("The network security key isn't correct. Please try again.")
                    ask_key()
                    if entry:
                        entry.set_text("")
                        entry.grab_focus()
                else:
                    msg.set_text("Can't connect to this network")
                msg.set_visible(True)
            bg(run, done)

        def disconnect(*_):
            btn.set_sensitive(False)
            bg(lambda: ok(["nmcli", "device", "disconnect", wifi_device()]),
               lambda _r: (self.refresh(), self.load_wifi()))

        if n["active"]:
            btn = button("Disconnect", "plain-btn", on_click=disconnect)
        else:
            btn = button("Connect", "accent-btn", on_click=connect)
            if entry:
                entry.connect("activate", connect)
            if user:
                user.connect("activate", lambda *_: entry.grab_focus())
        actions.append(btn)
        detail.append(actions)
        rev.set_child(detail)
        box.append(rev)

        click = Gtk.GestureClick()

        def toggle_row(*_):
            opening = not rev.get_reveal_child()
            for row in list(self.iter_children(self.wifi_list)):
                if row is not box and hasattr(row, "rev"):
                    row.rev.set_reveal_child(False)
                    row.remove_css_class("expanded")
            rev.set_reveal_child(opening)
            (box.add_css_class if opening else box.remove_css_class)("expanded")
            if opening and (user or entry):
                (user or entry).grab_focus()
        click.connect("released", toggle_row)
        top.add_controller(click)
        box.rev = rev
        return box

    @staticmethod
    def iter_children(w):
        c = w.get_first_child()
        while c:
            yield c
            c = c.get_next_sibling()

    # ── Bluetooth page ──
    def load_bt(self):
        bg(self.scan_bt, self.show_bt)

    BT_SEARCH = 15                  # s

    def search_bt(self):
        """Look for devices nearby (like Windows when Bluetooth is turned on
        or its page opens): the dots run, the list fills as they turn up.
        Turning Bluetooth off, leaving the page or closing stops it."""
        if getattr(self, "bt_searching", False) or not self.state.get("bt"):
            return
        self.bt_searching = True
        self.bt_search_gen = gen = getattr(self, "bt_search_gen", 0) + 1
        self.bt_list.dots.start(delay=0)
        self.show_rescan(self.bt_list, False)
        self.bt_shown = None

        def tick():
            if not self.bt_searching or gen != self.bt_search_gen:
                return False
            self.load_bt()
            return True
        GLib.timeout_add(1500, tick)

        def run():
            try:
                p = subprocess.Popen(["bluetoothctl", "--timeout", str(self.BT_SEARCH), "scan", "on"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                return
            self.bt_proc = p
            try:
                p.wait(timeout=self.BT_SEARCH + 5)
            except subprocess.TimeoutExpired:
                p.kill()

        def done(_r):
            if gen != self.bt_search_gen:          # stopped (or a new search) meanwhile
                return
            self.bt_proc = None
            self.bt_searching = False
            self.bt_list.dots.stop()
            self.bt_shown = None
            self.load_bt()
        bg(run, done)

    def stop_bt_search(self):
        self.bt_search_gen = getattr(self, "bt_search_gen", 0) + 1
        self.bt_searching = False
        self.bt_list.dots.stop()
        p, self.bt_proc = getattr(self, "bt_proc", None), None
        if p and p.poll() is None:
            p.terminate()                           # bluez ends the discovery with it

    @staticmethod
    def scan_bt():
        def devs(kind):
            res = []
            for l in sh(["bluetoothctl", "devices"] + ([kind] if kind else []), timeout=4).splitlines():
                p = l.split(" ", 2)
                if len(p) == 3 and p[0] == "Device":
                    res.append((p[1], p[2]))
            return res
        connected = {m for m, _ in devs("Connected")}
        paired = dict(devs("Paired"))
        out = []
        for mac, name in devs(""):                   # all known, also found while searching
            if mac not in paired and re.fullmatch(r"([0-9A-F]{2}-){5}[0-9A-F]{2}", name):
                continue                            # nearby, but no name: skip like Windows
            info = sh(["bluetoothctl", "info", mac], timeout=3)
            ic = re.search(r"Icon: (\S+)", info)
            out.append({"mac": mac, "name": name, "connected": mac in connected, "paired": mac in paired,
                        "audio": bool(ic and "audio" in ic.group(1))})
        return sorted(out, key=lambda d: (not d["connected"], not d["paired"], d["name"].lower()))

    def show_bt(self, devices):
        """Like Windows: devices used before first, below them "New devices"
        found by the search."""
        on = bool(self.state.get("bt", True))
        searching = on and getattr(self, "bt_searching", False)
        if self.stack.get_visible_child_name() == "bluetooth":
            self.show_rescan(self.bt_list, on and not searching)
        key = (on, searching, json.dumps(devices, sort_keys=True))
        if key == getattr(self, "bt_shown", None):
            return                                  # nothing changed: don't rebuild (no flicker)
        self.bt_shown = key
        clear(self.bt_list)
        if not on:
            self.bt_list.append(label("Bluetooth is turned off", "empty-text", xalign=0.5))
            return
        known = [d for d in devices if d["paired"]]
        new = [d for d in devices if not d["paired"]]
        rows = [(d["mac"], self.bt_row(d)) for d in known]
        if known:
            sep = Gtk.Separator()
            sep.add_css_class("qs-sep")
            rows.append(("--sep", sep))
        rows.append(("--new", label("New devices", "section-title")))
        rows += [(d["mac"], self.bt_row(d)) for d in new]
        if not new:
            rows.append(("--none", label("Searching for devices…" if searching else "No new devices found",
                                         "empty-text", "empty-small", xalign=0.5)))
        self.add_rows(self.bt_list, rows)

    def bt_row(self, d):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("net")
        top = Gtk.Box(spacing=12)
        top.append(icon("win11-headphones" if d["audio"] else "win11-devices", 20))
        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        txt.set_hexpand(True)
        txt.append(label(d["name"], "net-name"))
        txt.append(label("Connected" if d["connected"] else "Paired" if d.get("paired", True)
                         else "Ready to pair", "net-status"))
        top.append(txt)
        box.append(top)

        rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=120)
        if getattr(self, "bt_open", None) == d["mac"]:     # was open before the list refreshed
            rev.set_transition_duration(0)
            rev.set_reveal_child(True)
            box.add_css_class("expanded")
            rev.set_transition_duration(120)
        actions = Gtk.Box(halign=Gtk.Align.END)
        verb = "disconnect" if d["connected"] else "connect" if d.get("paired", True) else "pair"

        def act():
            if verb != "pair":
                return ok(["bluetoothctl", verb, d["mac"]], timeout=20)
            # new device: pair, trust (reconnects by itself later), connect
            return (ok(["bluetoothctl", "pair", d["mac"]], timeout=30)
                    and ok(["bluetoothctl", "trust", d["mac"]], timeout=5)
                    and ok(["bluetoothctl", "connect", d["mac"]], timeout=20))

        def run_action(*_):
            btn.set_sensitive(False)
            busy_button(btn, {"disconnect": "Disconnecting…", "connect": "Connecting…",
                              "pair": "Pairing…"}[verb])
            bg(act, lambda _r: (self.refresh(), self.load_bt()))
        btn = button(verb.capitalize(), "plain-btn" if d["connected"] else "accent-btn", on_click=run_action)
        actions.append(btn)
        rev.set_child(actions)
        box.append(rev)

        click = Gtk.GestureClick()

        def toggle_row(*_):
            opening = not rev.get_reveal_child()
            for row in self.iter_children(self.bt_list):
                if row is not box and hasattr(row, "rev"):
                    row.rev.set_reveal_child(False)
                    row.remove_css_class("expanded")
            rev.set_reveal_child(opening)
            (box.add_css_class if opening else box.remove_css_class)("expanded")
            self.bt_open = d["mac"] if opening else None
        click.connect("released", toggle_row)
        top.add_controller(click)
        box.rev = rev
        return box

    # ── Sound output page (the ">" next to the volume, like Windows) ──
    def load_sound(self):
        bg(self.scan_sinks, self.show_sinks)

    @staticmethod
    def scan_sinks():
        try:
            sinks = json.loads(sh(["pactl", "-f", "json", "list", "sinks"], timeout=5) or "[]")
        except ValueError:
            sinks = []
        default = sh(["pactl", "get-default-sink"], timeout=3).strip()
        out = []
        for s in sinks:
            p = s.get("properties", {})
            dev = p.get("device.description") or s.get("description") or s.get("name", "")
            port = p.get("device.profile.description", "")
            name = f"{port} ({dev})" if p.get("device.api") == "alsa" and port else (s.get("description") or dev)
            what = f"{s.get('name', '')} {port} {p.get('device.icon_name', '')} {p.get('device.form_factor', '')}".lower()
            ic = ("win11-monitor" if "hdmi" in what or "displayport" in what else
                  "win11-headphones" if any(k in what for k in ("headphone", "headset", "bluez", "hands-free"))
                  else "win11-speaker")
            out.append({"name": s.get("name", ""), "label": name, "icon": ic, "default": s.get("name") == default})
        return out

    def show_sinks(self, sinks):
        clear(self.sound_list)
        self.sound_list.append(label("Output device", "section-title"))
        if not sinks:
            self.sound_list.append(label("No output devices", "empty-text", xalign=0.5))
            return
        rows = []
        for d in sinks:
            row = Gtk.Box(spacing=12)
            row.add_css_class("net")
            row.add_css_class("sink")
            if d["default"]:
                row.add_css_class("selected")
            bar = Gtk.Box(valign=Gtk.Align.CENTER)
            bar.add_css_class("sel-bar")
            row.append(bar)
            row.append(icon(d["icon"], 20))
            lbl = label(d["label"], "net-name")
            lbl.set_hexpand(True)
            row.append(lbl)
            click = Gtk.GestureClick()
            click.connect("released", lambda *_a, d=d, row=row: self.pick_sink(d, row))
            row.add_controller(click)
            rows.append((d["name"], row))
        self.add_rows(self.sound_list, rows)

    def pick_sink(self, d, row):
        for r in self.iter_children(self.sound_list):
            r.remove_css_class("selected")
        row.add_css_class("selected")               # right away, like Windows
        bg(lambda: ok(["pactl", "set-default-sink", d["name"]], timeout=5),
           lambda _r: (self.refresh(), self.load_sound()))


# ── Calendar flyout (click on the clock, like Windows 11) ──────────────────
class CalendarFlyout:
    """Own month grid (GTK's calendar scrolls on the mouse wheel and redraws badly)."""

    def __init__(self, app, shell):
        self.shell = shell
        self.visible = False
        self.year = self.month = 0
        self.win = layer_window(app, "winshell-calendar", LS.Layer.OVERLAY,
                                anchors=(LS.Edge.BOTTOM, LS.Edge.RIGHT),
                                margins={LS.Edge.BOTTOM: 12, LS.Edge.RIGHT: 12},
                                keyboard=LS.KeyboardMode.NONE)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", lambda _c, k, *_: (self.hide(), True)[1] if k == Gdk.KEY_Escape else False)
        self.win.add_controller(keys)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("cal")
        panel.add_css_class("flyout")
        panel.set_overflow(Gtk.Overflow.HIDDEN)
        self.panel = panel
        # header: the date and, like Windows, an arrow that folds the month away
        head = Gtk.Box()
        head.add_css_class("cal-head")
        self.head = label("", "cal-date")
        self.head.set_hexpand(True)
        self.head.set_valign(Gtk.Align.CENTER)
        head.append(self.head)
        self.fold_img = icon("win11-chevron-down", 12)
        fold = button(self.fold_img, "boxed-btn", on_click=self.toggle_fold)
        fold.set_valign(Gtk.Align.CENTER)
        head.append(fold)
        panel.append(head)
        self.folded = bool(ncplace.load().get("folded"))
        self.fold_img.set_from_icon_name("win11-chevron-up" if self.folded else "win11-chevron-down")
        if self.folded:
            head.add_css_class("folded")
        self.head_box = head
        self.placed_for = ncplace.load().get("height")      # swaync was started for this

        nav = Gtk.Box()
        nav.add_css_class("cal-nav")
        self.month_lbl = label("", "cal-month", ellipsize=False)
        self.month_lbl.set_hexpand(True)
        nav.append(self.month_lbl)
        nav.append(button(icon("win11-chevron-up", 12), "flat-btn", on_click=lambda: self.shift(-1)))
        nav.append(button(icon("win11-chevron-down", 12), "flat-btn", on_click=lambda: self.shift(1)))

        self.grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True, column_spacing=2, row_spacing=2)
        self.grid.add_css_class("cal-grid")
        # column 0: calendar week (ISO 8601, as used in Switzerland/Germany)
        self.grid.attach(label("KW", "cal-weekname", xalign=0.5, ellipsize=False), 0, 0, 1, 1)
        for i, d in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            self.grid.attach(label(d, "cal-dayname", xalign=0.5, ellipsize=False), i + 1, 0, 1, 1)
        self.cells, self.weeks = [], []
        for r in range(6):
            wk = label("", "cal-week", xalign=0.5, ellipsize=False)
            wk.set_valign(Gtk.Align.CENTER)
            self.grid.attach(wk, 0, r + 1, 1, 1)
            self.weeks.append(wk)
            for c in range(7):
                cell = label("", "cal-day", xalign=0.5, ellipsize=False)
                cell.set_size_request(40, 40)
                cell.set_valign(Gtk.Align.CENTER)
                cell.set_halign(Gtk.Align.CENTER)
                self.grid.attach(cell, c + 1, r + 1, 1, 1)
                self.cells.append(cell)
        month = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        month.add_css_class("cal-body")
        month.append(nav)
        month.append(self.grid)
        self.month_box = month
        self.body = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                                 transition_duration=200, reveal_child=not self.folded)
        self.body.set_child(month)
        self.body.connect("notify::child-revealed", lambda *_: self.folded_done())
        panel.append(self.body)
        self.win.set_child(panel)

        # mouse wheel / touchpad: one notch = one month
        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.DISCRETE)
        scroll.connect("scroll", self.on_scroll)
        panel.add_controller(scroll)

        # The notification center above the calendar can have the keyboard
        # (it opens a moment later); when it closes – Escape, a click
        # outside – the calendar closes with it.
        self.nc_open = False
        self.nc_empty = None                # notifications there? (panel size)
        threading.Thread(target=self.watch_notification_center, daemon=True).start()

    def watch_notification_center(self):
        while True:
            try:
                proc = subprocess.Popen(["swaync-client", "-s"], stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True,
                                        preexec_fn=die_with_parent)
                for line in proc.stdout:
                    try:
                        info = json.loads(line)
                    except ValueError:
                        continue
                    visible = bool(info.get("visible"))
                    empty = not info.get("count")
                    if empty != self.nc_empty:
                        # nothing to show: smaller panel; something arrived: full size
                        self.nc_empty = empty
                        ncplace.save(empty=empty)
                        ncplace.sync()
                    if self.nc_open and not visible:
                        GLib.idle_add(lambda: self.hide() and False)
                    self.nc_open = visible
            except OSError:
                pass
            time.sleep(2)            # swaync restarted or not running yet

    MIN_HEIGHT = 40             # px; anything smaller is a bogus measurement

    # ── fold the month away (the arrow in the header), like Windows ──
    def toggle_fold(self):
        self.folded = not self.folded
        ncplace.save(folded=self.folded)
        self.fold_img.set_from_icon_name("win11-chevron-up" if self.folded else "win11-chevron-down")
        (self.head_box.add_css_class if self.folded else self.head_box.remove_css_class)("folded")
        # the notification center moves right away, together with the month
        self.place_notifications(self.height(open_=not self.folded))
        self.body.set_reveal_child(not self.folded)

    def height(self, open_):
        """How tall the calendar is folded / unfolded."""
        whole = self.panel.measure(Gtk.Orientation.VERTICAL, -1)[1]
        body = self.body.measure(Gtk.Orientation.VERTICAL, -1)[1]
        month = self.month_box.measure(Gtk.Orientation.VERTICAL, -1)[1]
        return whole - body + (month if open_ else 0)

    def folded_done(self):
        if self.body.get_child_revealed() == (not self.folded):
            self.win.set_default_size(1, 1)         # a layer surface doesn't shrink by itself
            self.win.queue_resize()

    def place_notifications(self, height):
        if not self.win.get_mapped() or height < self.MIN_HEIGHT:
            return                                  # not measurable yet: keep the last good place
        if height == getattr(self, "placed_for", None):
            return
        self.placed_for = height
        ncplace.save(height=height)
        bg(ncplace.sync)

    def on_scroll(self, _c, _dx, dy):
        if dy:
            self.shift(1 if dy > 0 else -1)
        return True

    def shift(self, months):
        m = self.month - 1 + months
        self.year, self.month = self.year + m // 12, m % 12 + 1
        self.render()

    def render(self):
        today = GLib.DateTime.new_now_local()
        first = GLib.DateTime.new_local(self.year, self.month, 1, 0, 0, 0)
        self.month_lbl.set_text(first.format("%B %Y"))
        start = first.add_days(-(first.get_day_of_week() - 1))     # back to Monday
        for r, wk in enumerate(self.weeks):
            wk.set_text(str(start.add_days(7 * r).get_week_of_year()))
        for i, cell in enumerate(self.cells):
            day = start.add_days(i)
            cell.set_text(str(day.get_day_of_month()))
            for cls in ("other", "today"):
                cell.remove_css_class(cls)
            if day.get_month() != self.month:
                cell.add_css_class("other")
            if (day.get_year(), day.get_month(), day.get_day_of_month()) == \
                    (today.get_year(), today.get_month(), today.get_day_of_month()):
                cell.add_css_class("today")

    def toggle(self):
        self.hide() if self.visible else self.show()

    def show(self):
        for other in (self.shell.qs, self.shell.start):
            if other.visible:
                other.hide()
        now = GLib.DateTime.new_now_local()
        self.head.set_text(now.format("%A, %B %-d"))
        self.year, self.month = now.get_year(), now.get_month()
        self.render()
        self.visible = True
        self.shell.qs.catcher_for("calendar")
        slide_in(self.panel)
        # measured once the window is up: before its first showing the
        # calendar reported a far too small height, and the notification
        # center was placed over it
        GLib.timeout_add(60, lambda: self.visible and self.place_notifications(
            self.height(open_=not self.folded)) and False)
        spawn("swaync-client -op")     # notification center sits right above
        # take the keyboard (after swaync opened) so Escape closes both
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.EXCLUSIVE)
        self.win.present()

    def hide(self):
        if not self.visible:
            return
        self.visible = False
        self.win.set_visible(False)
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.NONE)
        self.shell.qs.catcher_for(None)
        spawn("swaync-client -cp")


# ── Start menu search ranking ───────────────────────────────────────────────
LAUNCHES = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
                        "winshell", "launches.json")
WORD_SPLIT = re.compile(r"[^0-9a-zäöüß]+")


def _words(text):
    return [w for w in WORD_SPLIT.split(text.lower()) if w]


def _subsequence(needle, hay):
    it = iter(hay)
    return all(ch in it for ch in needle)


def term_score(t, f):
    """How well one search word matches an app (0 = not at all)."""
    name = f["name"]
    if name == t:
        return 100
    if name.startswith(t):
        return 85
    if any(w.startswith(t) for w in f["name_words"]):
        return 70
    if len(t) >= 2 and "".join(w[0] for w in f["name_words"]).startswith(t):
        return 60                                   # initials: "vsc" -> Visual Studio Code
    if t in name:
        return 50
    if any(a.startswith(t) for a in f["alt"]):
        return 45                                   # other names, command (e.g. "kitty")
    if any(t in a for a in f["alt"]):
        return 35
    if any(w.startswith(t) for w in f["tag_words"]):
        return 30                                   # generic name / keywords ("terminal")
    if t in f["tags"]:
        return 20
    if t in f["desc"]:
        return 10
    if len(t) >= 3 and _subsequence(t, name):
        return 8                                    # typo-ish: "brve" -> brave
    return 0


def app_score(query, f, uses=0):
    """Rank of an app for the search text: every word has to match; whole
    phrase at the start of the name counts extra; apps you start often win
    ties. 0 = hidden."""
    terms = query.lower().split()
    total = 0
    for t in terms:
        sc = term_score(t, f)
        if not sc:
            return 0
        total += sc
    q = " ".join(terms)
    if len(terms) > 1 and f["name"].startswith(q):
        total += 40
    return total + min(uses, 20) * 1.5


def app_fields(name, alt_names, generic, keywords, executable, description):
    name = name.lower()
    alt = [a.lower() for a in alt_names + [os.path.basename(executable or "")] if a]
    tags = " ".join(filter(None, [generic or "", " ".join(keywords or [])])).lower()
    return {"name": name, "name_words": _words(name), "alt": alt,
            "tags": tags, "tag_words": _words(tags), "desc": (description or "").lower()}


# ── Start menu (Windows 11 layout) ─────────────────────────────────────────
class StartMenu:
    COLS = 6

    def __init__(self, app, shell):
        self.shell = shell
        self.visible = False
        self.apps = []
        self.win = layer_window(app, "winshell-start", LS.Layer.OVERLAY,
                                anchors=(LS.Edge.BOTTOM,), margins={LS.Edge.BOTTOM: 12})
        # capture phase: the search field would otherwise swallow Escape
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.win.add_controller(keys)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("start")
        panel.add_css_class("flyout")
        panel.set_overflow(Gtk.Overflow.HIDDEN)
        self.panel = panel

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.add_css_class("start-body")
        self.search = Gtk.SearchEntry(placeholder_text="Search for apps, settings, and documents")
        self.search.add_css_class("start-search")
        if hasattr(self.search.props, "search_delay"):
            self.search.props.search_delay = 40      # default 150 ms feels laggy
        self.search.connect("search-changed", lambda *_: self.filter())
        self.search.connect("activate", lambda *_: self.launch_selected() if self.query else self.launch_first())
        body.append(self.search)

        # page "apps": the A–Z grid; page "search": Windows 11 search results
        self.pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=90, vhomogeneous=True)
        apps_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box()
        head.add_css_class("start-head")
        self.title = label("All apps", "start-title")
        self.title.set_hexpand(True)
        head.append(self.title)
        apps_page.append(head)

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                min_children_per_line=self.COLS, max_children_per_line=self.COLS,
                                row_spacing=4, column_spacing=4, valign=Gtk.Align.START)
        self.query = ""
        self.flow.set_filter_func(self.match)
        self.flow.set_sort_func(self.order)
        self.flow.connect("child-activated", lambda _f, child: self.launch(child.app))
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    min_content_height=420, max_content_height=420)
        scroll.set_child(self.flow)
        self.scroll = scroll
        apps_page.append(scroll)
        self.pages.add_named(apps_page, "apps")
        self.pages.add_named(self.build_search_page(), "search")
        body.append(self.pages)
        panel.append(body)
        panel.append(self.build_footer())
        self.win.set_child(panel)

        self.load_apps()
        self.monitor = Gio.AppInfoMonitor.get()
        self.monitor.connect("changed", lambda *_: self.load_apps())

    # ── search results (Windows 11: best match + list left, details right) ──
    MAX_RESULTS = 8

    def build_search_page(self):
        page = Gtk.Box(spacing=12)
        page.add_css_class("search-page")
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        left.set_size_request(300, -1)
        self.results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.results.add_css_class("results")
        self.results.set_header_func(self.result_header)
        self.results.connect("row-activated", lambda _l, row: self.launch(row.app))
        self.results.connect("row-selected", lambda _l, row: self.show_details(row))
        self.no_results = label("No results", "empty-text", xalign=0.5)
        left.append(self.results)
        left.append(self.no_results)
        page.append(left)

        self.details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.details.add_css_class("details")
        self.details.set_hexpand(True)
        page.append(self.details)
        return page

    @staticmethod
    def result_header(row, before):
        """"Best match" above the first result, "Apps" above the second."""
        if before is None:
            row.set_header(label("Best match", "results-head"))
        elif before.get_index() == 0:
            row.set_header(label("Apps", "results-head"))
        else:
            row.set_header(None)

    def result_row(self, app, name, best):
        box = Gtk.Box(spacing=12)
        box.add_css_class("result")
        img = Gtk.Image.new_from_gicon(app.get_icon() or Gio.ThemedIcon.new("application-x-executable"))
        img.set_pixel_size(32 if best else 20)
        box.append(img)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        text.set_hexpand(True)
        text.append(label(name, "result-name"))
        if best:
            text.append(label("App", "result-kind"))
        box.append(text)
        chev = icon("win11-chevron-right", 12)
        chev.add_css_class("result-chevron")
        box.append(chev)
        row = Gtk.ListBoxRow(child=box)
        if best:
            row.add_css_class("best")
        row.app, row.name = app, name
        return row

    def show_results(self):
        clear(self.results)
        ranked = []
        c = self.flow.get_first_child()
        while c:
            if c.score > 0:
                ranked.append(c)
            c = c.get_next_sibling()
        ranked.sort(key=lambda c: (-c.score, len(c.fields["name"]), c.fields["name"]))
        ranked = ranked[:self.MAX_RESULTS]
        for i, c in enumerate(ranked):
            self.results.append(self.result_row(c.app, c.name, i == 0))
        self.no_results.set_visible(not ranked)
        self.details.set_visible(bool(ranked))
        if ranked:
            self.results.select_row(self.results.get_row_at_index(0))

    def show_details(self, row):
        clear(self.details)
        if row is None:
            return
        app = row.app
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)
        top.add_css_class("details-top")
        img = Gtk.Image.new_from_gicon(app.get_icon() or Gio.ThemedIcon.new("application-x-executable"))
        img.set_pixel_size(64)
        top.append(img)
        top.append(label(row.name, "details-name", xalign=0.5))
        top.append(label("App", "details-kind", xalign=0.5))
        self.details.append(top)
        sep = Gtk.Separator()
        sep.add_css_class("details-sep")
        self.details.append(sep)

        actions = [("win11-open", "Open", lambda: self.launch(app))]
        if hasattr(app, "list_actions"):                  # desktop-file apps
            for act in app.list_actions()[:5]:          # jump list: "New Window", …
                actions.append(("win11-window", app.get_action_name(act),
                                lambda a=act: self.launch_action(app, a)))
        for ic, text, fn in actions:
            line = Gtk.Box(spacing=12)
            line.append(icon(ic, 16))
            line.append(label(text, "action-text"))
            self.details.append(button(line, "action", on_click=fn))

    def move_selection(self, delta):
        row = self.results.get_selected_row()
        i = (row.get_index() if row else -1) + delta
        target = self.results.get_row_at_index(max(0, i))
        if target:
            self.results.select_row(target)

    def launch_selected(self):
        row = self.results.get_selected_row()
        if row:
            self.launch(row.app)

    def launch_action(self, app, action):
        self.hide()
        self.count_launch(app)
        try:
            ctx = Gdk.Display.get_default().get_app_launch_context()
            for var in OWN_ENV:
                ctx.unsetenv(var)
            app.launch_action(action, ctx)
        except Exception as e:
            print("winshell: action failed:", e, file=sys.stderr)

    def build_footer(self):
        foot = Gtk.Box()
        foot.add_css_class("start-footer")
        user = Gtk.Box(spacing=12)
        self.user_box = user
        self.user_pic = None                        # your profile picture (~/.face)
        self.refresh_avatar()
        user.append(label(display_name(), "start-user"))
        ub = Gtk.MenuButton(child=user, direction=Gtk.ArrowType.UP)
        ub.add_css_class("start-btn")
        ub.set_popover(self.menu([("win11-signout", "Sign out", "Sign out")]))
        foot.append(ub)
        sp = Gtk.Box()
        sp.set_hexpand(True)
        foot.append(sp)
        items = [("win11-sleep", "Sleep", "Sleep"),
                 ("win11-power", "Shut down", "Shut down"),
                 ("win11-restart", "Restart", "Restart")]
        cache = os.path.expanduser("~/.cache/hypr-has-windows")
        if os.path.exists(cache) and open(cache).read().strip() == "1":
            items.append(("win11-windows", "Restart to Windows", "Restart to Windows"))
        pb = Gtk.MenuButton(child=icon("win11-power", 16), direction=Gtk.ArrowType.UP)
        pb.add_css_class("start-btn")
        pb.set_tooltip_text("Power")
        pb.set_popover(self.menu(items))
        foot.append(pb)
        return foot

    def refresh_avatar(self):
        """(Re)load the profile picture – a new ~/.face shows up next time
        the start menu opens."""
        pic = profile_picture()
        stamp = (pic, os.path.getmtime(pic) if pic else 0)
        if self.user_pic is not None and getattr(self, "pic_stamp", None) == stamp:
            return
        self.pic_stamp = stamp
        new = avatar(28)
        if self.user_pic is not None:
            self.user_box.remove(self.user_pic)
        self.user_box.prepend(new)
        self.user_pic = new

    def menu(self, items):
        pop = Gtk.Popover(has_arrow=False)
        pop.add_css_class("start-menu")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for ic, text, action in items:
            row = Gtk.Box(spacing=12)
            row.append(icon(ic, 16))
            row.append(label(text, "menu-text"))
            b = button(row, "menu-item")
            b.connect("clicked", lambda _b, a=action, p=pop: (p.popdown(), self.power(a)))
            box.append(b)
        pop.set_child(box)
        return pop

    def power(self, action):
        self.hide()
        spawn(f"sleep 0.2; {SCRIPTS}/powermenu.sh '{action}'")

    # apps -----------------------------------------------------------------
    # kitty is the terminal of this setup: it shows up as "Terminal", and the
    # other terminal apps that came with the system stay out of the menu
    RENAME = {"kitty.desktop": "Terminal"}
    HIDDEN = {"org.kde.konsole.desktop", "xterm.desktop", "uxterm.desktop",
              "org.gnome.Terminal.desktop", "org.gnome.Ptyxis.desktop", "foot.desktop",
              "footclient.desktop", "foot-server.desktop"}

    def load_apps(self):
        apps = [a for a in Gio.AppInfo.get_all()
                if a.should_show() and a.get_id() not in self.HIDDEN]
        name_of = lambda a: self.RENAME.get(a.get_id(), a.get_display_name())
        apps.sort(key=lambda a: name_of(a).lower())
        self.apps = apps
        clear(self.flow)
        for a in apps:
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            tile.add_css_class("app")
            img = Gtk.Image.new_from_gicon(a.get_icon() or Gio.ThemedIcon.new("application-x-executable"))
            img.set_pixel_size(32)
            tile.append(img)
            name = label(name_of(a), "app-name", xalign=0.5)
            name.set_max_width_chars(10)
            name.set_width_chars(10)
            tile.append(name)
            child = Gtk.FlowBoxChild(child=tile)
            child.app = a
            child.name = name_of(a)
            generic = a.get_generic_name() if hasattr(a, "get_generic_name") else None
            child.fields = app_fields(name_of(a), [a.get_display_name(), a.get_name()], generic,
                                      a.get_keywords() if hasattr(a, "get_keywords") else [],
                                      a.get_executable(), a.get_description())
            child.score = 0
            click = Gtk.GestureClick()
            click.connect("released", lambda *_x, app=a: self.launch(app))
            child.add_controller(click)
            self.flow.append(child)

    def match(self, child):
        return not self.query or child.score > 0

    def order(self, a, b):
        """Best match first; without a search: A–Z (the order apps were added)."""
        if not self.query:
            return 0
        if a.score != b.score:
            return -1 if a.score > b.score else 1
        ka, kb = (len(a.fields["name"]), a.fields["name"]), (len(b.fields["name"]), b.fields["name"])
        return (ka > kb) - (ka < kb)

    def filter(self):
        self.query = self.search.get_text().strip()
        if self.query:
            uses = self.load_uses()
            c = self.flow.get_first_child()
            while c:
                c.score = app_score(self.query, c.fields, uses.get(c.app.get_id(), 0))
                c = c.get_next_sibling()
        if self.query:
            self.show_results()
            self.pages.set_visible_child_name("search")
        else:
            self.pages.set_visible_child_name("apps")
            self.scroll.get_vadjustment().set_value(0)

    def first_visible(self):
        i = 0
        while (c := self.flow.get_child_at_index(i)) is not None:   # in sorted order
            if self.match(c):
                return c
            i += 1
        return None

    # how often each app was started from here, so favourites win ties
    @staticmethod
    def load_uses():
        try:
            with open(LAUNCHES) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def count_launch(self, app):
        uses = self.load_uses()
        uses[app.get_id()] = uses.get(app.get_id(), 0) + 1
        try:
            os.makedirs(os.path.dirname(LAUNCHES), exist_ok=True)
            with open(LAUNCHES, "w") as f:
                json.dump(uses, f)
        except OSError:
            pass

    def launch_first(self):
        c = self.first_visible()
        if c:
            self.launch(c.app)

    def launch(self, app):
        self.hide()
        self.count_launch(app)
        try:
            ctx = Gdk.Display.get_default().get_app_launch_context()
            for var in OWN_ENV:
                ctx.unsetenv(var)
            app.launch([], ctx)
        except Exception as e:
            print("winshell: launch failed:", e, file=sys.stderr)

    # show / hide ------------------------------------------------------------
    def toggle(self):
        self.hide() if self.visible else self.show()

    def show(self):
        self.refresh_avatar()
        if self.shell.qs.visible:
            self.shell.qs.hide()
        if self.shell.calendar.visible:
            self.shell.calendar.hide()
        spawn("pkill -x rofi")
        if self.search.get_text():
            self.search.set_text("")
            self.filter()
        self.visible = True
        self.mark_open(True)
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.EXCLUSIVE)
        self.shell.qs.catcher_for("start")
        slide_in(self.panel)
        self.win.present()
        self.search.grab_focus()
        adj = self.flow.get_parent().get_parent().get_vadjustment() if self.flow.get_parent() else None
        if adj:
            adj.set_value(0)

    def hide(self):
        if not self.visible:
            return
        self.visible = False
        self.win.set_visible(False)
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.NONE)
        self.shell.qs.catcher_for(None)
        self.mark_open(False)

    def mark_open(self, on):
        """The taskbar's start button is grey while the menu is open."""
        tb = getattr(self.shell, "taskbar", None)
        if tb:
            tb.set_start_open(on)

    def on_key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
            else:
                self.hide()
            return True
        if self.query and keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            self.move_selection(1 if keyval == Gdk.KEY_Down else -1)
            return True
        if self.query and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.launch_selected()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Tab) and self.search.has_focus():
            c = self.first_visible()
            if c:
                c.grab_focus()
                return True
        return False


# ── Volume / brightness flyout (bottom center, like Windows 11) ───────────
class OSD:
    def __init__(self, app):
        self.timer = None
        self.win = layer_window(app, "winshell-osd", LS.Layer.OVERLAY,
                                anchors=(LS.Edge.BOTTOM,), margins={LS.Edge.BOTTOM: 24})
        box = Gtk.Box(spacing=14)
        box.add_css_class("osd")
        self.img = icon("win11-volume-3", 18)
        self.bar = Gtk.ProgressBar(hexpand=True, valign=Gtk.Align.CENTER)
        self.lbl = label("", "osd-value", xalign=1.0, ellipsize=False)
        self.lbl.set_width_chars(4)          # "100", "High": same width
        box.append(self.img)
        box.append(self.bar)
        box.append(self.lbl)
        self.win.set_child(box)
        self.watch_kbd_backlight()

    # The laptop switches its keyboard light itself (Fn+Space) – no key
    # reaches Hyprland. UPower reports every change on D-Bus; show the same
    # flyout as for volume then.
    def watch_kbd_backlight(self):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            r = bus.call_sync("org.freedesktop.UPower", "/org/freedesktop/UPower/KbdBacklight",
                              "org.freedesktop.UPower.KbdBacklight", "GetMaxBrightness",
                              None, GLib.VariantType("(i)"), Gio.DBusCallFlags.NONE, 2000, None)
            self.kbd_max = r.unpack()[0]
        except GLib.Error:
            return                          # no keyboard light (or no UPower)
        if self.kbd_max <= 0:
            return
        bus.signal_subscribe("org.freedesktop.UPower", "org.freedesktop.UPower.KbdBacklight",
                             "BrightnessChangedWithSource", "/org/freedesktop/UPower/KbdBacklight",
                             None, Gio.DBusSignalFlags.NONE,
                             lambda *a: self.show_kbd(a[5].unpack()[0]))
        self.kbd_bus = bus

    def show_kbd(self, level):
        steps = {0: "Off"} if self.kbd_max != 2 else {0: "Off", 1: "Low", 2: "High"}
        pct = round(100 * level / self.kbd_max)
        self.update(("win11-keyboard-light" if level else "win11-keyboard-light-off",
                     pct, level == 0), text=steps.get(level, str(pct)))

    @staticmethod
    def read(kind):
        if kind == "brightness":
            m = sh("brightnessctl -m").split(",")
            v = int(m[3].rstrip("%")) if len(m) > 3 else 0
            return "win11-sun", v, False
        out = sh("wpctl get-volume @DEFAULT_AUDIO_SINK@")
        mv = re.search(r"([\d.]+)", out)
        v = round(float(mv.group(1)) * 100) if mv else 0
        muted = "MUTED" in out
        return QuickSettings.volume_icon(v, muted), v, muted

    def show(self, kind):
        bg(lambda: self.read(kind), self.update)

    def update(self, res, text=None):
        name, value, muted = res
        self.img.set_from_icon_name(name)
        self.bar.set_fraction(max(0, min(100, value)) / 100)
        (self.bar.add_css_class if muted else self.bar.remove_css_class)("muted")
        self.lbl.set_text(text if text is not None else str(value))
        self.win.present()
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(1600, self.hide)

    def hide(self):
        self.timer = None
        self.win.set_visible(False)
        return False



# ── Snap preview (dragging a window to a screen edge) ──────────────────────
class SnapPreview:
    """The translucent area that shows where a dragged window will snap
    (hyprland.lua sends "snap show x y w h cursor_x cursor_y" / "snap hide").
    It grows out of the cursor into the area, like on Windows 11, and lets
    every click and the drag itself pass through."""
    ANIM = 0.18                 # seconds
    INSET = 8                   # px inside the snap area
    RADIUS = 8

    def __init__(self, app):
        self.win = layer_window(app, "winshell-snap", LS.Layer.TOP,
                                anchors=(LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT),
                                exclusive=-1)
        self.win.add_css_class("snap-window")
        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.area.set_draw_func(self.draw)
        self.win.set_child(self.area)
        self.win.connect("realize", lambda w: self.passthrough())
        self.rect = None                # drawn now (x, y, w, h)
        self.start = self.target = None
        self.t0 = 0
        self.alpha = 0.0
        self.alpha_from = self.alpha_to = 0.0
        self.tick = 0

    def passthrough(self):
        import cairo
        surf = self.win.get_surface()
        if surf:
            surf.set_input_region(cairo.Region())

    def show(self, x, y, w, h, cx, cy):
        target = (x + self.INSET, y + self.INSET, w - 2 * self.INSET, h - 2 * self.INSET)
        if not self.win.get_visible() or self.alpha_to == 0:
            self.rect = (cx - 30, cy - 30, 60, 60)      # grows out of the cursor
            self.alpha = 0.0
            self.win.present()
            self.passthrough()
        self.start, self.target = self.rect, target
        self.alpha_from, self.alpha_to = self.alpha, 1.0
        self.animate()

    def hide(self):
        if not self.win.get_visible():
            return
        self.start = self.target = self.rect
        self.alpha_from, self.alpha_to = self.alpha, 0.0
        self.animate()

    def animate(self):
        self.t0 = 0
        if not self.tick:
            self.tick = self.area.add_tick_callback(self.on_tick)

    def on_tick(self, _w, clock):
        now = clock.get_frame_time() / 1e6
        if not self.t0:
            self.t0 = now
        t = min(1.0, (now - self.t0) / self.ANIM)
        e = 1 - (1 - t) ** 3                            # ease out
        if self.start and self.target:
            self.rect = tuple(a + (b - a) * e for a, b in zip(self.start, self.target))
        self.alpha = self.alpha_from + (self.alpha_to - self.alpha_from) * e
        self.area.queue_draw()
        if t >= 1:
            self.tick = 0
            if self.alpha_to == 0:
                self.win.set_visible(False)
            return False
        return True

    def draw(self, _area, cr, _w, _h):
        if not self.rect or self.alpha <= 0:
            return
        x, y, w, h = self.rect
        r = min(self.RADIUS, w / 2, h / 2)
        import math as m
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -m.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, m.pi / 2)
        cr.arc(x + r, y + h - r, r, m.pi / 2, m.pi)
        cr.arc(x + r, y + r, r, m.pi, 3 * m.pi / 2)
        cr.close_path()
        a = self.alpha
        cr.set_source_rgba(0.55, 0.62, 0.75, 0.22 * a)  # frosted, slightly cool tint
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.45 * a)
        cr.set_line_width(1.5)
        cr.stroke()


# ── Show desktop animation (Win + D, 3-finger swipe down / up) ─────────────
class DeskCanvas(Gtk.Widget):
    """Draws the window pictures while they fly to / from the taskbar."""

    def __init__(self):
        super().__init__()
        self.items = []         # dicts: tex, bar, a (from rect), b (to rect)
        self.t = 0.0
        self.rising = False

    def do_snapshot(self, snap):
        t = self.t
        if self.rising:                                  # out of the taskbar
            e = 1 - (1 - t) ** 3
            alpha = min(1.0, t / 0.35)
        else:                                            # into the taskbar
            e = t * t * (3 - 2 * t)
            alpha = 1.0 - max(0.0, (t - 0.25) / 0.75) ** 1.5
        for it in self.items:
            ax, ay, aw, ah = it["a"]
            bx, by, bw, bh = it["b"]
            x, y = ax + (bx - ax) * e, ay + (by - ay) * e
            w, h = aw + (bw - aw) * e, ah + (bh - ah) * e
            rect = Graphene.Rect().init(x, y, w, h)        # the window's content only
            radius = 8 * min(1.0, w / max(1.0, aw)) + 2
            snap.push_opacity(max(0.0, min(1.0, alpha)))
            clip = Gsk.RoundedRect()        # (init_from_rect's return value is unusable in Python)
            clip.init_from_rect(rect, radius)
            snap.push_rounded_clip(clip)
            if it["tex"]:
                snap.append_texture(it["tex"], Graphene.Rect().init(x, y, w, h))
            else:
                c = Gdk.RGBA()
                c.parse("#2b2b2b")
                snap.append_color(c, Graphene.Rect().init(x, y, w, h))
            snap.pop()
            snap.pop()


class DesktopAnim:
    """Like Windows: on "show desktop" every window shrinks into its taskbar
    button while it fades, and grows back out of it when it returns.
    Hyprland can't scale windows, so pictures of them do it on a full-screen
    layer above everything (the windows themselves are hidden meanwhile).

    hyprland.lua:  desk out <callback> <gen> <window>…   → callback(gen) once shown
                   desk in  <callback> <gen> <window>…   → callback(gen) at the end
                   desk stop
    <window> = address|x|y|width|height|title bar height|class"""
    OUT = 0.24                  # seconds
    IN = 0.28

    def __init__(self, app, shell):
        self.shell = shell
        self.win = layer_window(app, "winshell-desk", LS.Layer.OVERLAY,
                                anchors=(LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT),
                                exclusive=-1)
        self.win.add_css_class("snap-window")           # transparent
        self.canvas = DeskCanvas()
        self.canvas.set_hexpand(True)
        self.canvas.set_vexpand(True)
        self.win.set_child(self.canvas)
        self.win.connect("realize", lambda *_: self.passthrough())
        self.tick = 0
        self.t0 = 0
        self.dur = self.OUT
        self.done = None
        self.gen = 0

    def passthrough(self):
        import cairo
        surf = self.win.get_surface()
        if surf:
            surf.set_input_region(cairo.Region())

    def target(self, cls, mon_h):
        """The taskbar button of that app (screen coordinates)."""
        tb = self.shell.taskbar
        if tb:
            key = tb.key_for({"class": cls})
            b = tb.buttons.get(key)
            if b and b.get_mapped():
                ok_, r = b.compute_bounds(tb.win)
                if ok_:
                    top = mon_h - tb.win.get_height()
                    cx, cy = r.get_x() + r.get_width() / 2, top + r.get_y() + r.get_height() / 2
                    return (cx - 20, cy - 14, 40, 28)
        return (self.win.get_width() / 2 - 20, mon_h - 34, 40, 28)

    def parse(self, tokens):
        items = []
        mon_h = self.win.get_height() or self.shell.switcher.monitor_size()[1]
        thumbs = self.shell.switcher.thumbs if self.shell.switcher else {}
        for tok in tokens:
            p = tok.split("|", 6)
            if len(p) < 7:
                continue
            addr, x, y, w, h, bar, cls = p[0], *map(float, p[1:6]), p[6]
            items.append({"tex": thumbs.get(addr), "bar": bar,
                          "win": (x, y, w, h), "icon": self.target(cls, mon_h)})
        return items

    def run(self, kind, callback, gen, tokens):
        self.gen = gen
        self.shell.switcher.pause_thumbs(self.OUT + self.IN + 0.4)
        items = self.parse(tokens)
        if not items:
            hypr_eval(f"{callback}({gen})")
            return
        rising = kind == "in"
        for it in items:
            it["a"], it["b"] = (it["icon"], it["win"]) if rising else (it["win"], it["icon"])
        self.canvas.items = items
        self.canvas.rising = rising
        self.canvas.t = 0.0
        self.dur = self.IN if rising else self.OUT
        self.done = None
        self.win.present()
        self.passthrough()
        self.canvas.queue_draw()
        if not rising:
            # the pictures cover the windows now: hide the real ones
            GLib.timeout_add(34, lambda: (hypr_eval(f"{callback}({gen})"), False)[1])
        else:
            self.done = lambda: hypr_eval(f"{callback}({gen})")
        self.t0 = 0
        if not self.tick:
            self.tick = self.canvas.add_tick_callback(self.on_tick)

    def on_tick(self, _w, clock):
        now = clock.get_frame_time() / 1e6
        if not self.t0:
            self.t0 = now
        t = min(1.0, (now - self.t0) / self.dur)
        self.canvas.t = t
        self.canvas.queue_draw()
        if t < 1.0:
            return True
        self.tick = 0
        if self.done:                   # windows come back right under the pictures
            done, self.done = self.done, None
            done()
            GLib.timeout_add(140, self.close)
        else:
            self.close()
        return False

    def close(self):
        self.canvas.items = []
        self.win.set_visible(False)
        return False

    def stop(self):
        self.done = None
        if self.tick:
            self.canvas.remove_tick_callback(self.tick)
            self.tick = 0
        self.close()


# ── Clipboard history (Win + V) ─────────────────────────────────────────────
CLIP_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "winshell", "clipboard")
TERMINALS = {"kitty", "foot", "alacritty", "org.wezfurlong.wezterm", "com.mitchellh.ghostty",
             "org.gnome.Console", "org.gnome.Ptyxis", "konsole"}


class ShutdownDialog:
    """Alt + F4 on the desktop (no window focused), like Windows: "shut down?"
    Enter or "Shut down" turns the computer off; Escape, "Cancel", a click
    outside or Alt + F4 again close it."""

    def __init__(self, app, shell):
        self.shell = shell
        self.visible = False
        # full screen and dimmed behind, the box in the middle
        self.win = layer_window(app, "winshell-shutdown", LS.Layer.OVERLAY,
                                anchors=(LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT),
                                keyboard=LS.KeyboardMode.EXCLUSIVE, exclusive=-1)
        self.win.add_css_class("shutdown-backdrop")
        outer = Gtk.Box(hexpand=True, vexpand=True)
        outside = Gtk.GestureClick()
        outside.connect("released", self.on_outside)
        outer.add_controller(outside)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=True)
        box.add_css_class("shutdown-dialog")
        box.add_css_class("flyout")
        self.box = box
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.add_css_class("shutdown-body")
        top = Gtk.Box(spacing=12)
        top.append(icon("win11-power", 24))
        top.append(label("Shut down", "shutdown-title"))
        body.append(top)
        text = label("Do you want to shut down this computer?\nOpen apps will be closed.",
                     "shutdown-text", ellipsize=False)
        text.set_wrap(True)
        body.append(text)
        box.append(body)

        foot = Gtk.Box(spacing=8, homogeneous=True)
        foot.add_css_class("shutdown-foot")
        self.ok_btn = button("Shut down", "accent-btn", on_click=self.shut_down)
        foot.append(self.ok_btn)
        foot.append(button("Cancel", "plain-btn", on_click=self.hide))
        box.append(foot)
        outer.append(box)
        self.win.set_child(outer)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.win.add_controller(keys)

    def on_outside(self, gesture, _n, x, y):
        b = self.box.compute_bounds(self.win.get_child())
        if b[0] and not b[1].contains_point(Graphene.Point().init(x, y)):
            self.hide()

    def on_key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            focus = self.win.get_focus()
            if isinstance(focus, Gtk.Button) and focus is not self.ok_btn:
                focus.activate()            # Tab to "Cancel", then Enter: cancel
            else:
                self.shut_down()
            return True
        return False

    def toggle(self):
        self.hide() if self.visible else self.show()

    def show(self):
        for other in (self.shell.start, self.shell.calendar, self.shell.qs, self.shell.clip):
            if getattr(other, "visible", False):
                other.hide()
        self.visible = True
        slide_in(self.box)
        self.win.present()
        self.ok_btn.grab_focus()

    def hide(self):
        if not self.visible:
            return
        self.visible = False
        self.win.set_visible(False)

    def shut_down(self):
        self.hide()
        spawn(f"sleep 0.2; {SCRIPTS}/powermenu.sh 'Shut down'")


class ClipboardPanel:
    """Like Windows 11's Win + V: what you copied lately (text and pictures),
    newest first. Click (or Enter) pastes it where you were; Delete removes
    one, "Clear all" everything. Stored by clipstore.py (wl-paste --watch)."""

    def __init__(self, app, shell):
        self.shell = shell
        self.visible = False
        self.target = None              # the window to paste into
        self.rows = []
        self.sel = 0
        self.win = layer_window(app, "winshell-clipboard", LS.Layer.OVERLAY,
                                anchors=(LS.Edge.BOTTOM, LS.Edge.RIGHT),
                                margins={LS.Edge.BOTTOM: 12, LS.Edge.RIGHT: 12},
                                keyboard=LS.KeyboardMode.EXCLUSIVE)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("clip")
        panel.add_css_class("flyout")
        self.panel = panel
        head = Gtk.Box()
        head.add_css_class("clip-head")
        head.append(label("Clipboard", "clip-title"))
        sp = Gtk.Box(hexpand=True)
        head.append(sp)
        head.append(button("Clear all", "plain-btn", on_click=self.clear_all))
        panel.append(head)
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         propagate_natural_height=True, max_content_height=460)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.list.add_css_class("clip-list")
        self.scroll.set_child(self.list)
        panel.append(self.scroll)
        self.empty = label("Nothing here yet.\nText and pictures you copy show up here.",
                           "clip-empty", xalign=0.5, ellipsize=False)
        self.empty.set_justify(Gtk.Justification.CENTER)
        panel.append(self.empty)
        self.win.set_child(panel)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.win.add_controller(keys)

    @staticmethod
    def entries():
        try:
            with open(os.path.join(CLIP_DIR, "index.json")) as f:
                return [e for e in json.load(f) if os.path.exists(os.path.join(CLIP_DIR, e["file"]))]
        except (OSError, ValueError):
            return []

    def toggle(self):
        self.hide() if self.visible else self.show()

    def show(self):
        for other in (self.shell.start, self.shell.calendar, self.shell.qs):
            if getattr(other, "visible", False):
                other.hide()
        self.target = hypr_json("activewindow") or {}
        self.build()
        self.visible = True
        self.shell.qs.catcher_for("clipboard")
        slide_in(self.panel)
        self.win.present()

    def hide(self):
        self.visible = False
        self.win.set_visible(False)
        self.shell.qs.catcher_for(None)

    def build(self):
        clear(self.list)
        self.rows = []
        for e in self.entries():
            path = os.path.join(CLIP_DIR, e["file"])
            card = Gtk.Box(spacing=8)
            card.add_css_class("clip-card")
            if e["kind"] == "image":
                pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True)
                pic.set_filename(path)
                pic.set_size_request(-1, 110)
                pic.set_halign(Gtk.Align.START)
                card.append(pic)
            else:
                try:
                    text = open(path, encoding="utf-8", errors="replace").read(600)
                except OSError:
                    continue
                lbl = Gtk.Label(label=text.strip(), xalign=0, wrap=True, lines=3, hexpand=True,
                                ellipsize=Pango.EllipsizeMode.END, max_width_chars=40)
                lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                lbl.add_css_class("clip-text")
                card.append(lbl)
            x = button(icon("win11-close", 12), "flat-btn", "clip-del", tooltip="Delete",
                       on_click=lambda e=e: self.delete(e))
            x.set_valign(Gtk.Align.START)
            card.append(x)
            click = Gtk.GestureClick()
            click.connect("released", lambda *_a, e=e: self.paste(e))
            card.add_controller(click)
            card.entry = e
            self.list.append(card)
            self.rows.append(card)
        self.empty.set_visible(not self.rows)
        self.scroll.set_visible(bool(self.rows))
        self.sel = 0
        self.mark()

    def mark(self):
        for i, r in enumerate(self.rows):
            (r.add_css_class if i == self.sel else r.remove_css_class)("selected")

    def on_key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.hide()
        elif keyval in (Gdk.KEY_Down, Gdk.KEY_Tab) and self.rows:
            self.sel = min(len(self.rows) - 1, self.sel + 1)
            self.mark()
        elif keyval == Gdk.KEY_Up and self.rows:
            self.sel = max(0, self.sel - 1)
            self.mark()
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.rows:
            self.paste(self.rows[self.sel].entry)
        elif keyval == Gdk.KEY_Delete and self.rows:
            self.delete(self.rows[self.sel].entry)
        return True

    def paste(self, e):
        """Put it on the clipboard and paste it into the window you were in."""
        path = os.path.join(CLIP_DIR, e["file"])
        args = ["wl-copy", "--type", "image/png"] if e["kind"] == "image" else ["wl-copy"]
        try:
            with open(path, "rb") as f:
                subprocess.Popen(args, stdin=f, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 env=child_env(), start_new_session=True)
        except OSError:
            return
        self.hide()
        t = self.target or {}
        if t.get("address"):
            mods = "CTRL SHIFT" if t.get("class") in TERMINALS else "CTRL"
            GLib.timeout_add(120, lambda: (dispatch(
                f'hl.dsp.send_shortcut({{ mods = "{mods}", key = "V", window = "address:{t["address"]}" }})'), False)[1])

    def write(self, entries):
        tmp = os.path.join(CLIP_DIR, "index.json.tmp")
        with open(tmp, "w") as f:
            json.dump(entries, f)
        os.replace(tmp, os.path.join(CLIP_DIR, "index.json"))

    def delete(self, e):
        entries = [x for x in self.entries() if x["file"] != e["file"]]
        try:
            os.unlink(os.path.join(CLIP_DIR, e["file"]))
        except OSError:
            pass
        self.write(entries)
        keep = self.sel
        self.build()
        self.sel = min(keep, max(0, len(self.rows) - 1))
        self.mark()

    def clear_all(self):
        for e in self.entries():
            try:
                os.unlink(os.path.join(CLIP_DIR, e["file"]))
            except OSError:
                pass
        self.write([])
        self.build()


# ── Taskbar (replaces Waybar) ───────────────────────────────────────────────
TASKBAR_CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
                              "winshell", "taskbar.json")
STATUS = os.path.expanduser("~/.config/waybar/scripts/status.sh")


class AppIndex:
    """Finds the desktop app (Gio.AppInfo) belonging to a window class."""

    def __init__(self):
        self.reload()
        Gio.AppInfoMonitor.get().connect("changed", lambda *_: self.reload())

    def reload(self):
        self.map = {}
        # apps shown in menus first: e.g. Brave ships a hidden second .desktop
        # file with the same window class
        apps = sorted(Gio.AppInfo.get_all(), key=lambda a: not a.should_show())
        passes = [
            lambda a: (a.get_startup_wm_class() or "") if hasattr(a, "get_startup_wm_class") else "",
            lambda a: (a.get_id() or "").removesuffix(".desktop"),
            lambda a: (a.get_id() or "").removesuffix(".desktop").split(".")[-1],
            lambda a: os.path.basename(a.get_executable() or ""),
        ]
        for get in passes:                       # earlier passes win
            for a in apps:
                k = get(a).lower()
                if k:
                    self.map.setdefault(k, a)

    def for_class(self, cls):
        c = (cls or "").lower()
        return self.map.get(c) or self.map.get(c.split(".")[-1])

    def canonical(self, app_id):
        """A hidden duplicate .desktop file (e.g. com.brave.Browser next to
        brave-browser) stands for the visible app with the same window class."""
        app = self.by_id(app_id)
        if app and not app.should_show():
            wm = (app.get_startup_wm_class() or "") if hasattr(app, "get_startup_wm_class") else ""
            alt = self.for_class(wm) if wm else None
            if alt and alt.should_show() and alt.get_id():
                return alt.get_id()
        return app_id

    @staticmethod
    def by_id(app_id):
        try:
            return DesktopAppInfo.new(app_id)
        except TypeError:
            return None


class Taskbar:
    """Windows 11 taskbar: start + pinned/running apps in the middle (drag to
    reorder, right click to pin/unpin), tray with overflow flyout, status
    icons, clock and the "show desktop" sliver on the right."""

    ICON_OVERRIDE = {"kitty.desktop": "utilities-terminal"}
    DEFAULT_PINNED = ["brave-browser.desktop", "kitty.desktop"]

    def __init__(self, app, shell):
        self.shell = shell
        self.app = app
        self.dragging_tray = False
        self.drag_key = None                          # app icon being dragged
        self.tdrag = None                             # tray icon being dragged
        self.apps = AppIndex()
        self.cfg = self.load_config()
        pinned = []
        for k in self.cfg["pinned"]:
            k = self.apps.canonical(k)
            if k not in pinned:
                pinned.append(k)
        self.cfg["pinned"] = pinned
        self.order = list(self.cfg["pinned"])          # display order of all known keys
        self.buttons = {}                              # key -> button
        self.shown = []                                # keys currently in the bar
        self.groups = {}                               # key -> windows, most recent first
        self.active_key = None

        self.win = layer_window(app, "winshell-taskbar", LS.Layer.TOP,
                                anchors=(LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT), exclusive=48)
        self.win.add_css_class("taskbar-window")
        self.win.set_size_request(-1, 48)
        bar = Gtk.CenterBox()
        bar.add_css_class("taskbar")

        center = Gtk.Box()
        self.start_btn = button(icon("win11-start", 26), "tb-start", tooltip="Start",
                                on_click=lambda: self.shell.start.toggle())
        center.append(self.start_btn)
        self.app_box = SlideBox()
        center.append(self.app_box)
        # drag an icon to reorder: it follows the pointer inside the taskbar
        # and the others make room right away, like on Windows
        self.drag_gesture(self.app_box, "app")
        bar.set_center_widget(center)
        bar.set_end_widget(self.build_right())
        self.win.set_child(bar)

        self.refresh()
        self.win.present()

    # ── settings: pinned apps, hidden tray icons ──
    def load_config(self):
        try:
            with open(TASKBAR_CONFIG) as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = {}
        cfg.setdefault("pinned", list(self.DEFAULT_PINNED))
        cfg.setdefault("tray_hidden", [])
        cfg.setdefault("tray_order", [])
        return cfg

    def save_config(self):
        try:
            os.makedirs(os.path.dirname(TASKBAR_CONFIG), exist_ok=True)
            with open(TASKBAR_CONFIG, "w") as f:
                json.dump(self.cfg, f, indent=2)
        except OSError as e:
            print("winshell: can't save taskbar settings:", e, file=sys.stderr)

    # ── apps in the middle ──
    def key_for(self, client):
        a = self.apps.for_class(client.get("class"))
        return a.get_id() if a and a.get_id() else "class:" + (client.get("class") or "?")

    def app_of(self, key):
        return None if key.startswith("class:") else self.apps.by_id(key)

    def refresh(self):
        clients = hypr_json("clients") or []
        active = hypr_json("activewindow") or {}
        groups = {}
        for c in sorted(clients, key=lambda c: c.get("focusHistoryID", 99)):
            ws = (c.get("workspace") or {}).get("name", "")
            if not c.get("mapped") or (ws.startswith("special:") and ws != "special:minimized"):
                continue
            groups.setdefault(self.key_for(c), []).append(c)
        self.groups = groups
        aws = (active.get("workspace") or {}).get("name", "")
        self.active_key = (self.key_for(active) if active.get("address") and not aws.startswith("special:")
                           else None)
        for k in groups:
            if k not in self.order:
                self.order.append(k)
        pinned = set(self.cfg["pinned"])
        keys = [k for k in self.order if k in pinned or k in groups]
        if keys != self.shown and not self.drag_key:
            for k in keys:
                if k not in self.buttons:
                    self.buttons[k] = self.make_app_button(k)
            self.app_box.set_items([self.buttons[k] for k in keys])
            self.shown = keys
        for k in keys:
            b, wins = self.buttons[k], groups.get(k, [])
            (b.add_css_class if wins else b.remove_css_class)("running")
            (b.add_css_class if k == self.active_key else b.remove_css_class)("active")
            app = self.app_of(k)
            b.set_tooltip_text((wins[0].get("title") if wins else None)
                               or (app.get_display_name() if app else k.split(":", 1)[-1]))

    def make_app_button(self, key):
        app = self.app_of(key)
        if key in self.ICON_OVERRIDE:
            img = icon(self.ICON_OVERRIDE[key], 24)
        else:
            gicon = app.get_icon() if app else None
            if not gicon:
                cls = key.split(":", 1)[-1]
                gicon = Gio.ThemedIcon.new_from_names([cls, cls.lower(), "application-x-executable"])
            img = Gtk.Image.new_from_gicon(gicon)
            img.set_pixel_size(24)
        dash = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.END)
        dash.add_css_class("dash")
        over = Gtk.Overlay(child=img)
        over.add_overlay(dash)
        b = Gtk.Button(child=over)
        b.add_css_class("tb-app")
        b.key = key
        b.connect("clicked", lambda *_: self.on_app_click(key))
        for num, fn in ((Gdk.BUTTON_MIDDLE, lambda: self.launch(key)),
                        (Gdk.BUTTON_SECONDARY, lambda: self.app_menu(key, b))):
            g = Gtk.GestureClick(button=num)
            g.connect("released", lambda *_a, f=fn: f())
            b.add_controller(g)
        return b

    # ── dragging icons (apps in the middle, tray icons, hidden icons) ──
    # Own dragging instead of Wayland drag and drop: Hyprland sometimes
    # cancels a drag right away, and on Windows the icon stays in the
    # taskbar anyway. While the button is held, all pointer events go to the
    # surface where the drag started, even outside of it.
    DRAG_THRESHOLD = 6

    def drag_gesture(self, box, kind):
        g = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        g.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        g.connect("drag-begin", lambda g_, x, y: self.drag_begin(g_, box, kind, x, y))
        g.connect("drag-update", lambda g_, dx, dy: self.drag_update(g_, dx, dy))
        g.connect("drag-end", lambda *_: self.drag_finish(True))
        g.connect("cancel", lambda *_: self.drag_finish(False))
        box.add_controller(g)

    def drag_begin(self, g, box, kind, x, y):
        # a drag whose end never arrived (the pointer left for another
        # surface): clean it up first, or the tray stays frozen
        if getattr(self, "idrag", None) and self.idrag.get("active"):
            self.drag_finish(False)
        elif self.tdrag:
            self.tray_drag_end({}, False)
        w = box.item_at(x, y)
        if w is None or (kind != "app" and not getattr(w, "ident", None)):
            self.idrag = None
            return
        px, py = box.pos.get(w, (0, 0))
        self.idrag = {"kind": kind, "box": box, "w": w, "sx": x, "sy": y,
                      "grab": (x - px, y - py), "active": False}

    def drag_update(self, g, dx, dy):
        st = getattr(self, "idrag", None)
        if not st:
            return
        x, y = st["sx"] + dx, st["sy"] + dy
        if not st["active"]:
            if math.hypot(dx, dy) < self.DRAG_THRESHOLD:
                return
            st["active"] = True
            g.set_state(Gtk.EventSequenceState.CLAIMED)   # no click on release
            if st["kind"] == "app":
                self.drag_key = st["w"].key
            else:
                self.tray_drag_start(st)
        if st["kind"] == "app":
            box, w = st["box"], st["w"]
            box.set_float(w, x - st["grab"][0], 0)
            box.move_item(w, box.index_at(x, y, exclude=w))
        else:
            self.tray_drag_move(st, x, y)

    def drag_finish(self, ok_):
        st, self.idrag = getattr(self, "idrag", None), None
        if not st or not st["active"]:
            return
        if st["kind"] == "app":
            self.drag_key = None
            st["box"].drop_float()
            keys = [w.key for w in self.app_box.items]
            if ok_:
                self.order = keys + [k for k in self.order if k not in keys]
                self.cfg["pinned"] = [k for k in self.order if k in self.cfg["pinned"]]
                self.save_config()
                self.shown = keys
            else:
                self.app_box.set_items([self.buttons[k] for k in self.shown])
            self.refresh()
        else:
            self.tray_drag_end(st, ok_)

    def on_app_click(self, key):
        wins = self.groups.get(key, [])
        if not wins:
            self.launch(key)
            return
        if key == self.active_key:
            if len(wins) == 1:          # the app in front: minimize it, like Windows
                hypr_eval(f'MinimizeWindow("{wins[0]["address"]}")')
                return
            target = wins[1]            # several windows: go to the next one
        else:
            target = wins[0]
        hypr_eval(f'ShowWindow("{target["address"]}")')

    def launch(self, key):
        app = self.app_of(key)
        if not app:
            return
        try:
            ctx = Gdk.Display.get_default().get_app_launch_context()
            for var in OWN_ENV:
                ctx.unsetenv(var)
            app.launch([], ctx)
        except Exception as e:
            print("winshell: launch failed:", e, file=sys.stderr)

    def app_menu(self, key, anchor):
        app = self.app_of(key)
        wins = self.groups.get(key, [])
        pinned = key in self.cfg["pinned"]
        items = []
        if app:
            items.append((app.get_display_name(), lambda: self.launch(key)))
            items.append(("Unpin from taskbar" if pinned else "Pin to taskbar",
                          lambda: self.set_pinned(key, not pinned)))
        if wins:
            items.append(("Close window" if len(wins) == 1 else "Close all windows",
                          lambda: [spawn(f"hyprctl dispatch 'hl.dsp.window.close({{ window = \"address:{w['address']}\" }})'")
                                   for w in wins]))
        popup_menu(anchor, items)

    def set_pinned(self, key, on):
        pinned = [k for k in self.cfg["pinned"] if k != key]
        if on:
            pinned.append(key)
        # keep the order the user sees
        self.cfg["pinned"] = [k for k in self.order if k in pinned] + [k for k in pinned if k not in self.order]
        self.save_config()
        self.refresh()

    def set_start_open(self, on):
        (self.start_btn.add_css_class if on else self.start_btn.remove_css_class)("open")

    # ── right side: tray, status icons, clock, show-desktop sliver ──
    def build_right(self):
        right = Gtk.Box()
        right.add_css_class("tb-right")

        self.right = right
        # "^": only there while icons are hidden (or while an icon is dragged)
        self.tray_arrow = button(icon("win11-chevron-up", 16), "tb-tray-arrow",
                                 tooltip="Show hidden icons", on_click=self.toggle_overflow)
        self.tray_arrow.set_visible(False)
        right.append(self.tray_arrow)

        self.tray_box = SlideBox(css="tb-tray")
        right.append(self.tray_box)
        # drag icons to reorder them, or up into the hidden-icons flyout (and
        # back); dropping anywhere on the taskbar shows an icon there
        self.drag_gesture(self.tray_box, "bar")
        self.build_overflow()

        # Wi-Fi / volume / battery, like Windows' quick settings button
        quick = Gtk.Box()
        self.st_net, self.st_vol, self.st_bat = icon("win11-wifi-4", 20), icon("win11-volume-3", 20), \
            icon("win11-battery-100", 24)
        for img, cls in ((self.st_net, "st-net"), (self.st_vol, "st-vol"), (self.st_bat, "st-bat")):
            img.add_css_class(cls)
            quick.append(img)
        self.quick_btn = button(quick, "tb-quick", on_click=lambda: self.shell.qs.toggle())
        g = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        g.connect("released", lambda *_: self.shell.qs.show("wifi"))
        self.quick_btn.add_controller(g)
        sc = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL |
                                       Gtk.EventControllerScrollFlags.DISCRETE)
        sc.connect("scroll", self.on_volume_scroll)
        self.quick_btn.add_controller(sc)
        right.append(self.quick_btn)
        self.status = {}
        for what in ("net", "vol", "bat"):
            threading.Thread(target=self.watch_status, args=(what,), daemon=True).start()

        self.clock_lbl = Gtk.Label(justify=Gtk.Justification.RIGHT, xalign=1.0)
        self.clock_lbl.add_css_class("tb-clock-text")
        right.append(button(self.clock_lbl, "tb-clock", on_click=lambda: self.shell.calendar.toggle()))
        self.tick()

        right.append(button(Gtk.Box(), "tb-peek", on_click=lambda: spawn(f"{SCRIPTS}/wallpaper.sh next")))
        return right

    def tick(self):
        now = GLib.DateTime.new_now_local()
        self.clock_lbl.set_text(now.format("%H:%M:%S\n%d.%m.%Y"))
        GLib.timeout_add(1000 - now.get_microsecond() // 1000 + 5, self.tick)
        return False

    def on_volume_scroll(self, _c, _dx, dy):
        step = "2%+" if dy < 0 else "2%-"
        spawn(f"wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ {step}")
        return True

    STATUS_ICON = {"ethernet": "win11-ethernet", "wifi-off": "win11-wifi-off", "mute": "win11-volume-mute"}

    def watch_status(self, what):
        """Follow status.sh (it prints a line whenever Wi-Fi, volume or the
        battery changes) and show the matching icon."""
        while True:
            try:
                proc = subprocess.Popen([STATUS, what], stdout=subprocess.PIPE, text=True,
                                        stderr=subprocess.DEVNULL, env=child_env())
                for line in proc.stdout:
                    try:
                        data = json.loads(line)
                    except ValueError:
                        continue
                    GLib.idle_add(self.show_status, what, data)
            except OSError:
                pass
            time.sleep(5)

    def show_status(self, what, data):
        cls = (data.get("class") or "").split()[0] if data.get("class") else ""
        name = self.STATUS_ICON.get(cls)
        if not name:
            m = re.match(r"(wifi|vol|bat)-(\d+)(-charging|-plugged)?", cls)
            if m:
                kind = {"wifi": "wifi", "vol": "volume", "bat": "battery"}[m.group(1)]
                name = f"win11-{kind}-{m.group(2)}{m.group(3) or ''}"
        img = {"net": self.st_net, "vol": self.st_vol, "bat": self.st_bat}[what]
        img.set_visible(bool(name))
        if name:
            img.set_from_icon_name(name)
        self.status[what] = (data.get("tooltip") or "").replace("\\n", "\n")
        self.quick_btn.set_tooltip_text("\n".join(t for t in (self.status.get(k) for k in ("net", "vol", "bat")) if t))
        return False

    # ── tray ──
    def tray_changed(self):
        items = [it for it in self.shell.tray.items.values() if it.props and not it.passive]
        order = self.cfg["tray_order"]
        for it in items:
            if it.ident not in order:
                order.append(it.ident)
        items.sort(key=lambda it: order.index(it.ident))
        hidden = set(self.cfg["tray_hidden"])
        if self.tdrag:                  # rebuilt once the drag is over
            if time.monotonic() - self.tdrag.get("moved_at", 0) < 10:
                return False
            self.tray_drag_end({}, False)  # a drag that never ended
            return False
        bar, ov = [], []
        for it in items:
            (ov if it.ident in hidden else bar).append(self.tray_button(it))
        self.tray_box.set_items(bar)
        self.overflow_grid.set_items(ov)
        any_hidden = bool(ov)
        self.overflow_empty.set_visible(not any_hidden)
        self.overflow_grid.set_visible(any_hidden)
        self.tray_arrow.set_visible(any_hidden or self.dragging_tray)
        if not any_hidden and not self.dragging_tray:
            self.close_overflow()
        elif self.ov_win.get_visible():
            GLib.idle_add(self.place_overflow)
        return False

    def tray_button(self, item):
        pic = item.paintable(16)
        if isinstance(pic, str):
            img = Gtk.Image.new_from_icon_name(pic)
        else:
            img = Gtk.Image.new_from_paintable(pic)
        img.set_pixel_size(16)
        b = Gtk.Button(child=img)
        b.add_css_class("tb-tray-item")
        b.set_tooltip_text(item.title)
        b.ident = item.ident
        tray = self.shell.tray
        b.connect("clicked", lambda *_: tray.activate(item, b))
        for num, fn in ((Gdk.BUTTON_SECONDARY, lambda: tray.menu(item, b)),
                        (Gdk.BUTTON_MIDDLE, lambda: tray.secondary(item))):
            g = Gtk.GestureClick(button=num)
            g.connect("released", lambda *_a, f=fn: f())
            b.add_controller(g)
        sc = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL |
                                       Gtk.EventControllerScrollFlags.DISCRETE)
        sc.connect("scroll", lambda _c, _dx, dy: (tray.scroll(item, -dy), True)[1])
        b.add_controller(sc)
        return b

    # Tray dragging. Two places: "bar" (taskbar) and "ov" (hidden-icons
    # flyout, its own surface). The pointer position is turned into taskbar
    # coordinates to tell which one it is over.
    def tray_box_of(self, where):
        return self.tray_box if where == "bar" else self.overflow_grid

    def ov_origin(self):
        """Top-left corner of the flyout in taskbar coordinates (the flyout
        sits on top of the taskbar, anchored bottom right)."""
        x = self.win.get_width() - LS.get_margin(self.ov_win, LS.Edge.RIGHT) - self.ov_win.get_width()
        y = -(LS.get_margin(self.ov_win, LS.Edge.BOTTOM) + self.ov_win.get_height())
        return x, y

    @staticmethod
    def point(src, dst, x, y):
        ok_, p = src.compute_point(dst, Graphene.Point().init(x, y))
        return (p.x, p.y) if ok_ else (x, y)

    def tray_drag_start(self, st):
        btn = st["w"]
        item = next((it for it in self.shell.tray.items.values() if it.ident == btn.ident), None)
        src = "bar" if st["box"] is self.tray_box else "ov"
        self.dragging_tray = True
        self.tdrag = {"btn": btn, "src": src, "target": src, "index": 0, "item": item,
                      "ghost": {"bar": None, "ov": None}, "moved_at": time.monotonic()}
        GLib.timeout_add_seconds(11, self.tray_changed)     # in case it never ends
        self.tray_arrow.set_visible(True)

    def ghost(self, where):
        """The dragged icon shown in the other place while it hovers there."""
        d = self.tdrag
        if d["ghost"][where] is None and d["item"]:
            g = self.tray_button(d["item"])
            g.set_can_target(False)
            g.set_opacity(0.75)
            d["ghost"][where] = g
        return d["ghost"][where]

    def tray_drag_move(self, st, x, y):
        d = self.tdrag
        if not d:
            return
        # pointer in taskbar coordinates
        if d["src"] == "bar":
            tx, ty = self.point(self.tray_box, self.win, x, y)
        else:
            fx, fy = self.point(self.overflow_grid, self.ov_win, x, y)
            ox, oy = self.ov_origin()
            tx, ty = fx + ox, fy + oy
        where, lx, ly = None, 0, 0
        ok_, ar = self.tray_arrow.compute_bounds(self.win)
        over_arrow = ok_ and self.tray_arrow.get_visible() and ar.contains_point(Graphene.Point().init(tx, ty))
        if over_arrow and not self.ov_win.get_visible():
            self.open_overflow()
        if self.ov_win.get_visible():
            ox, oy = self.ov_origin()
            fx, fy = tx - ox, ty - oy
            if over_arrow or (0 <= fx <= self.ov_win.get_width() and 0 <= fy <= self.ov_win.get_height()):
                where = "ov"
                lx, ly = (10 ** 6, 10 ** 6) if over_arrow else self.point(self.ov_win, self.overflow_grid, fx, fy)
        if where is None and ty >= 0:
            where = "bar"
            lx, ly = self.point(self.win, self.tray_box, tx, ty)
        if where is None and ty < 0 and d["target"] == "ov" and self.ov_win.get_visible():
            # between the taskbar and the flyout (or just beside it): stay with
            # the flyout – letting go of it there made the flyout shrink and
            # grow again under the pointer (flicker)
            where = "ov"
            lx = ly = 10 ** 6 if d["index"] >= 10 ** 6 else None
        d["moved_at"] = time.monotonic()
        if where and lx is not None:
            box = self.tray_box_of(where)
            moving = d["btn"] if d["src"] == where else self.ghost(where)
            d["index"] = box.index_at(lx, ly, exclude=moving) if lx < 10 ** 6 else 10 ** 6
        self.tray_preview(where, d["index"])
        # the dragged icon follows the pointer while it is over its own place
        src_box = self.tray_box_of(d["src"])
        if where == d["src"]:
            gx, gy = st["grab"]
            src_box.set_float(d["btn"], x - gx, y - gy)
            d["btn"].set_opacity(1)
        else:
            src_box.drop_float()
            d["btn"].set_opacity(0 if where else 0.4)

    def tray_preview(self, where, index=0):
        """Show where the dragged icon would land: there the others glide
        aside; where it came from, its place stays free."""
        d = self.tdrag
        if not d:
            return
        d["target"] = where
        for name in ("bar", "ov"):
            box, btn = self.tray_box_of(name), d["btn"]
            ghost = d["ghost"][name]
            items = [w for w in box.items if w is not ghost]
            if where == name:
                moving = btn if d["src"] == name else self.ghost(name)
                if moving is None:
                    continue
                items = [w for w in items if w is not moving]
                items.insert(max(0, min(index, len(items))), moving)
                box.set_items(items)
                if d["src"] == name:
                    box.set_collapsed(btn, False)
            else:
                # where it came from, its place stays free until it is let go:
                # closing it moved "^" away under the pointer and back, 60
                # times a second (the flicker)
                box.set_items(items)
        has_ov = any(w.get_visible() and w not in self.overflow_grid.collapsed for w in self.overflow_grid.items)
        self.overflow_empty.set_visible(not has_ov)
        # an icon dragged out of the flyout: its grid holds the drag – hiding
        # it would drop the drag without a word (the tray then got stuck)
        self.overflow_grid.set_visible(has_ov or d["src"] == "ov")
        if self.ov_win.get_visible():
            GLib.idle_add(self.place_overflow)

    def tray_drag_end(self, st, ok_):
        d, self.tdrag = self.tdrag, None
        self.dragging_tray = False
        if not d:
            return
        self.tray_box_of(d["src"]).drop_float()
        d["btn"].set_opacity(1)
        if self.ov_win.get_visible():           # clicking outside closes it again
            self.shell.qs.catcher_for("tray")
        where = d["target"] if ok_ else None
        if where:
            ident, row = d["btn"].ident, []
            for w in self.tray_box_of(where).items:
                if w is d["ghost"][where] or (w is d["btn"] and d["src"] == where):
                    row.append(ident)
                elif getattr(w, "ident", None) and w.ident != ident and w not in self.tray_box_of(where).collapsed:
                    row.append(w.ident)
            if ident not in row:
                row.append(ident)
            self.place_tray(ident, where == "ov", row)
        else:
            GLib.idle_add(self.tray_changed)

    def place_tray(self, ident, hidden, row):
        """Hide/show a tray icon; `row` = icons of that place in their new order."""
        hid = [i for i in self.cfg["tray_hidden"] if i != ident]
        if hidden:
            hid.append(ident)
        self.cfg["tray_hidden"] = hid
        order = [i for i in self.cfg["tray_order"] if i != ident]
        if row:
            pos = row.index(ident)
            after = next((i for i in row[pos + 1:] if i in order), None)
            before = next((i for i in reversed(row[:pos]) if i in order), None)
            if after is not None:
                order.insert(order.index(after), ident)
            elif before is not None:
                order.insert(order.index(before) + 1, ident)
            else:
                order.append(ident)
        else:
            order.append(ident)
        self.cfg["tray_order"] = order
        self.save_config()
        GLib.idle_add(self.tray_changed)

    def move_tray(self, value, hidden, x=None):
        """winshell-ctl tray hide|show <id>"""
        if not isinstance(value, str) or not value.startswith("tray:"):
            return False
        ident = value[5:]
        box = self.overflow_grid if hidden else self.tray_box
        self.place_tray(ident, hidden, [w.ident for w in box.items if getattr(w, "ident", None)] + [ident])
        return True

    # hidden-icons flyout: its own layer surface (a Gtk.Popover grabs the
    # pointer and closes as soon as an icon is dragged out of it)
    def build_overflow(self):
        self.ov_win = layer_window(self.app, "winshell-tray", LS.Layer.OVERLAY,
                                   anchors=(LS.Edge.BOTTOM, LS.Edge.RIGHT),
                                   margins={LS.Edge.BOTTOM: 8, LS.Edge.RIGHT: 8})
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("tray-flyout")
        panel.add_css_class("flyout")
        self.ov_panel = panel
        self.overflow_grid = SlideBox(columns=5)
        self.overflow_empty = label("Drop here to hide", "overflow-empty", xalign=0.5, ellipsize=False)
        panel.append(self.overflow_grid)
        panel.append(self.overflow_empty)
        self.drag_gesture(self.overflow_grid, "ov")
        self.ov_win.set_child(panel)

    def place_overflow(self):
        """Center the flyout above the "^" button."""
        self.ov_win.set_default_size(1, 1)      # shrink with its content again
        ok_, r = self.tray_arrow.compute_bounds(self.win)
        if ok_:
            width = self.ov_panel.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
            center = r.get_x() + r.get_width() / 2
            LS.set_margin(self.ov_win, LS.Edge.RIGHT, max(8, int(self.win.get_width() - center - width / 2)))
        return False

    def toggle_overflow(self):
        if self.ov_win.get_visible():
            self.close_overflow()
        else:
            self.open_overflow()

    def open_overflow(self):
        if self.ov_win.get_visible():
            return
        for other in (self.shell.start, self.shell.calendar):
            if other.visible:
                other.hide()
        if self.shell.qs.visible:
            self.shell.qs.hide()
        self.place_overflow()
        if not self.dragging_tray:
            self.shell.qs.catcher_for("tray")
        slide_in(self.ov_panel)
        self.ov_win.present()
        self.tray_arrow.add_css_class("open")

    def close_overflow(self):
        if not getattr(self, "ov_win", None) or not self.ov_win.get_visible():
            return
        self.ov_win.set_visible(False)
        self.tray_arrow.remove_css_class("open")
        if self.shell.qs.backdrop_for == "tray":
            self.shell.qs.catcher_for(None)


def popup_menu(anchor, items):
    """Small Windows-style context menu above a taskbar button."""
    pop = Gtk.Popover(has_arrow=False, position=Gtk.PositionType.TOP)
    pop.add_css_class("tray-menu")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
    for text, fn in items:
        b = button(label(text, xalign=0.0), "tray-menu-item")
        b.connect("clicked", lambda _b, f=fn: (pop.popdown(), f()))
        box.append(b)
    pop.set_child(box)
    pop.set_parent(anchor)
    pop.connect("closed", lambda p: GLib.idle_add(lambda: p.unparent() and False))
    pop.popup()


# ── application ──────────────────────────────────────────────────────────
class WinShell(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.noe1.winshell",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.switcher = self.qs = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        crisp_text()
        # winshell-ctl starts us with LD_PRELOAD=libgtk4-layer-shell and
        # LC_TIME=en_GB. Both are in effect now (GTK has set the locale), so
        # drop them from the environment: apps launched from the start menu
        # or quick settings must not inherit them – the preload drags GTK 4
        # into GTK 3 programs such as Discord, which then abort on start.
        for var in ("LD_PRELOAD", "LC_TIME"):
            os.environ.pop(var, None)
        display = Gdk.Display.get_default()
        Gtk.IconTheme.get_for_display(display).add_search_path(ICONS)
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(HERE, "style.css"))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        global SHELL
        SHELL = self
        self.taskbar = None
        icons = AppIcons()
        self.switcher = Switcher(self, icons)
        self.qs = QuickSettings(self)
        self.osd = OSD(self)
        self.snap = SnapPreview(self)
        self.desk = DesktopAnim(self, self)
        self.clip = ClipboardPanel(self, self)
        self.shutdown = ShutdownDialog(self, self)
        self.start = StartMenu(self, self)
        self.calendar = CalendarFlyout(self, self)
        self.taskbar = Taskbar(self, self)
        import tray as tray_mod
        self.tray = tray_mod.Tray(lambda: GLib.idle_add(self.taskbar.tray_changed))

        if os.path.exists(FIFO) and not os.path.isfile(FIFO):
            os.unlink(FIFO)
        if not os.path.exists(FIFO):
            os.mkfifo(FIFO, 0o600)
        fd = os.open(FIFO, os.O_RDWR | os.O_NONBLOCK)
        self.buf = ""
        GLib.unix_fd_add_full(GLib.PRIORITY_HIGH, fd, GLib.IOCondition.IN, self.on_fifo)
        open(READY, "w").write(str(os.getpid()))

    def do_activate(self):
        pass

    # At most POPUP_LIMIT notification popups on screen: when another one
    # arrives, the oldest one still showing is deleted. swaync can't do this
    # itself, so its "popup-limit" script reports every new notification.
    POPUP_LIMIT = 3
    POPUP_TIMEOUT = {"low": 4, "normal": 6}         # seconds, as in swaync's config

    def limit_popups(self, nid, urgency):
        if not nid.isdigit():
            return
        if not hasattr(self, "popups"):
            self.popups = []                        # (id, shown until) oldest first
        try:
            dnd, cc_open = self.swaync_call("GetSubscribeData").unpack()[0][:2]
        except Exception:
            dnd = cc_open = False
        if dnd or cc_open:                          # no popups are shown right now
            return
        now = time.monotonic()
        life = self.POPUP_TIMEOUT.get(urgency.lower())
        until = now + life if life else float("inf")   # critical: stays until closed
        self.popups = [(i, u) for i, u in self.popups if u > now and i != int(nid)]
        self.popups.append((int(nid), until))
        while len(self.popups) > self.POPUP_LIMIT:
            oldest, _ = self.popups.pop(0)
            try:
                self.swaync_call("CloseNotification", GLib.Variant("(u)", (oldest,)))
            except Exception as e:
                print("winshell: can't close notification:", e, file=sys.stderr)

    @staticmethod
    def swaync_call(method, args=None):
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        return bus.call_sync("org.erikreider.swaync.cc", "/org/erikreider/swaync/cc",
                             "org.erikreider.swaync.cc", method, args, None,
                             Gio.DBusCallFlags.NONE, 1000, None)

    def on_fifo(self, fd, _cond):
        try:
            self.buf += os.read(fd, 4096).decode(errors="replace")
        except BlockingIOError:
            return True
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            try:
                self.command(line.split())
            except Exception as e:
                print("winshell:", e, file=sys.stderr)
        return True

    def command(self, args):
        if not args:
            return
        what, arg = args[0], (args[1] if len(args) > 1 else "")
        if what == "switch":
            if self.qs.visible:
                self.qs.hide()
            if self.start.visible:
                self.start.hide()
            {"next": lambda: self.switcher.step(1), "prev": lambda: self.switcher.step(-1),
             "release": self.switcher.alt_released,
             "swipe-begin": self.switcher.swipe_begin,
             "swipe-next": lambda: self.switcher.swipe_step(1), "swipe-prev": lambda: self.switcher.swipe_step(-1),
             "swipe-down": lambda: self.switcher.swipe_step(0, 1), "swipe-up": lambda: self.switcher.swipe_step(0, -1),
             "swipe-end": self.switcher.swipe_end, "swipe-cancel": lambda: self.switcher.swipe_end(True),
             "view": self.switcher.view,
             "commit": self.switcher.commit,
             "cancel": lambda: self.switcher.visible and self.switcher.hide()}.get(arg, lambda: None)()
        elif what == "qs":
            if arg == "hide":
                self.qs.hide()
            elif arg in ("main", "wifi", "bluetooth", "sound"):
                self.qs.show(arg)
            else:
                self.qs.toggle()
        elif what == "shutdown-dialog":         # Alt + F4 on the desktop
            self.shutdown.hide() if arg == "hide" else self.shutdown.toggle()
        elif what == "clipboard":               # Win + V
            self.clip.hide() if arg == "hide" else self.clip.toggle()
        elif what == "desk":                    # hyprland.lua: minimize / show desktop animation
            if arg in ("out", "in") and len(args) > 3 and args[2].isidentifier() and args[3].isdigit():
                self.desk.run(arg, args[2], int(args[3]), args[4:])
            else:
                self.desk.stop()
        elif what == "thumbs":                  # hyprland.lua: a window is about to change
            self.switcher.pause_thumbs()
        elif what == "snap":                    # hyprland.lua, while a window is dragged
            if arg == "show" and len(args) >= 8:
                self.snap.show(*(float(v) for v in args[2:8]))
            else:
                self.snap.hide()
        elif what == "osd":
            self.osd.show("brightness" if arg == "brightness" else "volume")
            if self.qs.visible:
                self.qs.refresh()
        elif what == "calendar":
            self.calendar.hide() if arg == "hide" else self.calendar.toggle()
        elif what == "menu":
            self.start.hide() if arg == "hide" else self.start.toggle()
        elif what == "tray":                    # hidden-icons flyout / an icon's menu / hide|show <id>
            if arg in ("hide", "show") and len(args) > 2:
                self.taskbar.move_tray("tray:" + args[2], hidden=arg == "hide")
            elif arg == "menu":
                child = (self.taskbar.tray_box.items or [None])[0]
                items = [it for it in self.tray.items.values() if it.title == (child.get_tooltip_text() if child else None)]
                if child and items:
                    self.tray.menu(items[0], child)
            else:
                self.taskbar.toggle_overflow()
        elif what == "search":                  # open the start menu with text typed in
            if not self.start.visible:
                self.start.show()
            self.start.search.set_text(" ".join(args[1:]))
            self.start.search.set_position(-1)
            self.start.filter()
        elif what == "backdrop":
            self.qs.backdrop_rofi(arg == "rofi")
        elif what == "notified":                # swaync script: a notification arrived
            self.limit_popups(args[1] if len(args) > 1 else "", args[2] if len(args) > 2 else "")
        elif what == "debug":
            b = self.qs.backdrop
            print(f"winshell: backdrop visible={b.get_visible()} mapped={b.get_mapped()} "
                  f"size={b.get_width()}x{b.get_height()} for={self.qs.backdrop_for}", file=sys.stderr, flush=True)
        elif what == "quit":
            self.quit()


def main():
    lock = open(LOCK, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("winshell is already running", file=sys.stderr)
        return 0
    try:
        return WinShell().run([])
    finally:
        for p in (READY,):
            try:
                os.unlink(p)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
