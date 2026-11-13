#!/usr/bin/env python3
"""winshell – Windows 11 style shell pieces for Hyprland.

  * Alt+Tab switcher with live window previews
  * Quick settings flyout (Wi-Fi, Bluetooth, airplane mode, do not disturb,
    brightness, volume, battery) with Wi-Fi and Bluetooth pages

Runs as a background daemon. Control it with winshell-ctl:
  winshell-ctl switch next|prev|commit|cancel
  winshell-ctl qs [toggle|wifi|bluetooth|hide]
"""
import datetime
from shutil import which as shutil_which
import fcntl
import json
import types
import math
import os
import signal
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
import displays                 # noqa: E402  which screen is the main one
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


def search_box(entry, cls=None):
    """A search field like Windows 11's: a round pill with the colored
    magnifier on the left, no clear (×) button (style.css .search-box –
    GTK's own icons of the entry are hidden there)."""
    box = Gtk.Box(spacing=6)            # (the entry's hidden icon still adds a little)
    box.add_css_class("search-box")
    if cls:
        box.add_css_class(cls)
    box.append(icon("win11-search", 20))
    box.append(entry)
    click = Gtk.GestureClick()          # a click anywhere in the pill types into it
    click.connect("pressed", lambda *_: entry.grab_focus())
    box.add_controller(click)
    return box


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


SLIDE_WAIT = 3       # frames (at 60 Hz) until a freshly shown window is on screen


def slide_in(widget):
    """Windows 11 flyout entrance: glide up out of the taskbar and fade in.

    The motion starts only once the window's first frame is on screen: a
    freshly shown layer window takes ~50-70 ms until then (measured), and
    the transition (started after a fixed 16 ms) was half done by the first
    visible frame – it looked like a jump halfway, then the rest, laggy.
    GTK can't tell when that frame is shown (the panel is still outside
    the window, nothing is drawn), so: SLIDE_WAIT frames after mapping."""
    leave = getattr(widget, "_leave_id", 0)
    if leave:                           # it was just going away: comes back instead
        GLib.source_remove(leave)
        widget._leave_id = 0
    widget.remove_css_class("leaving")
    widget.remove_css_class("shown")
    old = getattr(widget, "_slide_tick", 0)
    if old:
        widget.remove_tick_callback(old)
    left = [SLIDE_WAIT]

    def tick(w, _clock):
        left[0] -= 1
        if left[0] > 0:
            return True
        w._slide_tick = 0
        w.add_css_class("shown")
        return False
    widget._slide_tick = widget.add_tick_callback(tick)


def slide_out(widget, win, ms=150, gone_cb=None):
    """…and the way out: glide back down a little while fading (style.css
    .flyout.leaving), then the window goes – not gone from one frame to the next.
    gone_cb: called instead of hiding the window (StartMenu keeps it mapped)."""
    if getattr(widget, "_leave_id", 0):
        return
    tick = getattr(widget, "_slide_tick", 0)
    if tick:                            # closed before it even started gliding in
        widget.remove_tick_callback(tick)
        widget._slide_tick = 0
    widget.add_css_class("leaving")
    widget.remove_css_class("shown")

    def gone():
        widget._leave_id = 0
        widget.remove_css_class("leaving")
        (gone_cb or (lambda: win.set_visible(False)))()
        return False
    widget._leave_id = GLib.timeout_add(ms, gone)


def clear(box):
    while child := box.get_first_child():
        box.remove(child)


def gdk_monitor(connector):
    """The GdkMonitor of a screen by its connector name ("eDP-1", "DP-2")."""
    mons = Gdk.Display.get_default().get_monitors()
    for i in range(mons.get_n_items()):
        m = mons.get_item(i)
        if m.get_connector() == connector:
            return m
    return None


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


def dialog_titlebar(win, title):
    """The title bar of winshell's settings dialogs, looking like the others
    (hyprbars: 32 px, 46 px wide caption buttons, close turns red) – but
    like a fixed-size Windows dialog only – and X, no maximize. Hyprland
    gives these windows no hyprbars bar (hyprland.lua); dragging this one
    moves the window without snapping it to an edge or maximizing it."""
    row = Gtk.Box()
    row.add_css_class("dlg-bar")
    t = label(title, "dlg-title")
    t.set_hexpand(True)
    row.append(t)
    for glyph, cls, act in (("\ueaba", "dlg-min", win.minimize),
                            ("\ueab8", "dlg-close", win.close)):
        b = Gtk.Button(label=glyph)
        b.add_css_class("dlg-btn")
        b.add_css_class(cls)
        b.set_focusable(False)
        b.connect("clicked", lambda *_, f=act: f())
        row.append(b)
    handle = Gtk.WindowHandle()             # drag it to move the window
    handle.set_child(row)
    win.set_titlebar(handle)


def clock_with_dnd(bar):
    """The taskbar clock, and right of it a "Zzz" while do not disturb is on (part of the clock button: a click opens
    the calendar and the notifications). Sets bar.dnd_img."""
    box = Gtk.Box(spacing=10)
    box.append(bar.clock_lbl)
    bar.dnd_img = icon("win11-dnd", 16)
    bar.dnd_img.add_css_class("tb-dnd")
    bar.dnd_img.set_valign(Gtk.Align.CENTER)
    bar.dnd_img.set_visible(bool(SHELL and getattr(SHELL, "dnd_on", False)))
    bar.dnd_img.set_tooltip_text("Do not disturb is on")
    box.append(bar.dnd_img)
    return box


# Our own icons for some apps – the same in the start menu as on the taskbar
APP_ICONS = {"winterm.desktop": "win11-terminal",
             "org.kde.dolphin.desktop": "win11-explorer"}


def app_gicon(app):
    own = APP_ICONS.get(app.get_id() or "")
    if own:
        return Gio.ThemedIcon.new(own)
    return app.get_icon() or Gio.ThemedIcon.new("application-x-executable")


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

    OVERRIDE = {"io.github.noe1.winterm": "win11-terminal",
                "io.github.noe1.winshell": "win11-settings-app",
                "org.kde.dolphin": "win11-explorer"}

    def get(self, cls):
        c = (cls or "").lower()
        if c in self.OVERRIDE:              # Alt+Tab, task view: the same icons as the taskbar
            return Gio.ThemedIcon.new(self.OVERRIDE[c])
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
        self.on_thumb = None        # the task view wants new previews too
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

    # every open window of this desktop (like Windows), minimized ones too –
    # most recent first. Minimized windows count for the desktop they were on.
    def collect(self):
        clients = hypr_json("clients") or []
        cur = current_desktop()
        wins = []
        for c in clients:
            name = (c.get("workspace") or {}).get("name", "")
            if not c.get("mapped") or c.get("hidden") or not c.get("address"):
                continue
            if name.startswith("special:") and name != "special:minimized":
                continue                    # scratchpads and the like
            if desktop_of(c) not in (cur, None):
                continue
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
        # as many rows as needed anyway, but spread the windows evenly over
        # them (7 windows with room for 6: 4 + 3, not 6 + 1)
        n = max(1, len(self.items))
        rows = -(-n // fit)
        per_line = max(1, -(-n // rows))
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
            # × top right (on hover and on the selected card): close that window
            # right here, while Alt is still held – the switcher stays open
            x = Gtk.Button(child=icon("win11-close", 12), valign=Gtk.Align.CENTER)
            x.add_css_class("card-close")
            x.set_focusable(False)
            x.set_tooltip_text("Close window")
            x.connect("clicked", lambda *_a, addr=c["address"]: self.close_item(addr))
            head.append(x)
            card.append(head)

            stack = Gtk.Stack()
            stack.set_size_request(self.W, self.H)
            stack.add_css_class("thumb")
            stack.set_overflow(Gtk.Overflow.HIDDEN)   # clip the preview to the rounded corners
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
        if surf is not None and self.win.get_mapped():
            mon = disp.get_monitor_at_surface(surf)
        if mon is None:
            # not shown yet: it opens on the screen you're on – not on the
            # first one (with the wider screen on the left, the cards were
            # laid out for that one and ran off the laptop's screen)
            focused = next((m["name"] for m in hypr_json("monitors") or [] if m.get("focused")), None)
            mon = gdk_monitor(focused) if focused else None
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
        if getattr(self, "closing_item", False):
            self.closing_item = False           # that click was the ×
            return
        self.sel = i
        self.commit()

    def close_item(self, addr):
        """The × on a card: the window closes, its card goes, the rest stay."""
        self.closing_item = True
        GLib.timeout_add(150, lambda: setattr(self, "closing_item", False) or False)
        dispatch(f'hl.dsp.window.close({{ window = "address:{addr}" }})')
        i = next((k for k, c in enumerate(self.items) if c["address"] == addr), None)
        if i is None:
            return
        self.items.pop(i)
        if not self.items:
            self.hide()
            return
        if i < self.sel or self.sel >= len(self.items):
            self.sel = max(0, self.sel - 1)
        self.build()
        self.update_selection()

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
                if self.on_thumb:
                    self.on_thumb(addr, tex)
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
                elif ev == "workspacev2" and data and SHELL and getattr(SHELL, "deskslide", None):
                    # a desktop spans all screens: the others follow (hyprland.lua)
                    GLib.idle_add(lambda: hypr_eval("DesktopSync()") and False)
                    wid = data.split(",")[0]
                    if wid.lstrip("-").isdigit():
                        GLib.idle_add(SHELL.deskslide.changed, int(wid),
                                      GLib.get_monotonic_time() / 1e6)
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
        click.connect("pressed", lambda _g, _n, x, y: (self.press_through(x, y), self.outside_click()))
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
            ("dnd", "win11-dnd", "Do not disturb", self.toggle_dnd, None),
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
        sliders = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)   # as far apart as Windows'
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
            more = button(icon("win11-chevron-right", 16), "tile-more", on_click=on_more)
            more.set_hexpand(True)
            row.append(more)
            row.set_homogeneous(True)       # the divider right in the middle of the tile
        lbl = label(text, "tile-label", xalign=0.5)
        lbl.set_wrap(True)
        lbl.set_lines(2)
        lbl.set_justify(Gtk.Justification.CENTER)
        lbl.set_yalign(0.0)
        lbl.set_width_chars(1)              # wrap inside the tile's width
        outer.append(row)
        outer.append(lbl)
        return outer, row, lbl, img

    @staticmethod
    def value_tip(scale):
        """Like Windows 11: while the knob is held and dragged, a small grey
        bubble above it shows the value (e.g. 74) and moves along with it."""
        tip = Gtk.Popover(has_arrow=False, autohide=False, position=Gtk.PositionType.TOP)
        tip.add_css_class("slider-tip")
        tip.set_can_target(False)
        lbl = Gtk.Label()
        tip.set_child(lbl)
        tip.set_parent(scale)
        tip.set_offset(0, -6)
        held = [False]

        def place():
            lbl.set_text(str(int(round(scale.get_value()))))
            start, end = scale.get_slider_range()
            r = Gdk.Rectangle()
            r.x, r.y, r.width, r.height = (start + end) // 2, scale.get_height() // 2 - 7, 1, 1
            tip.set_pointing_to(r)

        def on_event(c, *_a):
            ev = c.get_current_event()      # (the signal's own argument comes as None)
            if ev is None:
                return False
            t = ev.get_event_type()
            if t in (Gdk.EventType.BUTTON_PRESS, Gdk.EventType.TOUCH_BEGIN):
                held[0] = True
                # a frame later: the knob has jumped to the pointer by then
                GLib.idle_add(lambda: held[0] and (place(), tip.popup()) and False)
            elif t in (Gdk.EventType.BUTTON_RELEASE, Gdk.EventType.TOUCH_END, Gdk.EventType.TOUCH_CANCEL):
                held[0] = False
                tip.popdown()
            return False
        legacy = Gtk.EventControllerLegacy()
        legacy.connect("event", on_event)
        scale.add_controller(legacy)
        scale.connect("value-changed", lambda _s: held[0] and place())
        scale.connect("unmap", lambda _s: (held.__setitem__(0, False), tip.popdown()))

    def slider(self, ic, on_change, on_icon=None, on_more=None, more_icon=None):
        row = Gtk.Box(spacing=6)
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
        self.value_tip(scale)
        # Like Windows, both sliders are equally long: the brightness row gets
        # an invisible stand-in for the volume row's ">" (output devices)
        # "select a sound output": output icon + ">", one button like Windows
        more_box = Gtk.Box(spacing=2)
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
            st = getattr(self.shell, "settings", None)
            if st and st.visible:
                st.hide()
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
        if self.win.get_visible():
            slide_out(self.panel, self.win)     # glides back down while fading, like the calendar
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
        st = getattr(self.shell, "settings", None)
        if who and who != "settings" and st and st.visible:
            st.hide()                       # one flyout at a time, like Windows
        tb = getattr(self.shell, "taskbar", None)
        if who and who != "tray" and tb:
            tb.close_overflow()
        if who:
            self.backdrop_for = who
            self.backdrop.present()
        elif self.backdrop_for not in (None, "qs"):
            self.backdrop_for = None
            self.backdrop.set_visible(False)

    def press_through(self, x, y):
        """A click on the taskbar while a flyout is open lands on this
        catcher, not on the button below: let that button look pressed
        anyway (the start logo / an app icon dips like on a normal click)."""
        sh = self.shell
        con = getattr(sh, "flyout_screen", None) or getattr(sh, "primary", None)
        bar = sh.desk.bar_of(con) if getattr(sh, "desk", None) else getattr(sh, "taskbar", None)
        if not bar or not bar.win.get_mapped():
            return
        top = self.backdrop.get_height() - bar.win.get_height()
        if y < top:
            return
        w = bar.win.pick(x, y - top, Gtk.PickFlags.DEFAULT)
        while w and not isinstance(w, Gtk.Button):
            w = w.get_parent()
        if w:
            w.add_css_class("pressing")
            GLib.timeout_add(120, lambda: w.remove_css_class("pressing") and False)

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
        elif self.backdrop_for == "settings":
            self.shell.settings.hide()
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

    def live_speed(self, lbl, base):
        """The link speed next to "Connected" (same as the taskbar tooltip)."""
        def done(speed):
            if speed and lbl.get_root():
                lbl.set_text(f"{base} · {speed}")
        bg(Taskbar.link_speed, done)

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
        if n["active"]:
            self.live_speed(st_lbl, status)     # "Connected, secured · 866 Mbit/s"
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
def rgba(r, g, b, a):
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = r, g, b, a
    return c


class StartLogo(Gtk.Widget):
    """The start button's logo (icons/win11-start.svg, drawn here): four
    rounded squares with a light-to-deep blue gradient. Opened with the
    Windows key it flickers like Windows 11 – a flash of light runs across
    the squares from top left to bottom right, each lights up and goes back."""
    LIGHT = (0x6c / 255, 0xcf / 255, 1.0)       # gradient top left
    DEEP = (0x1f / 255, 0x8f / 255, 0xe0 / 255)  # … bottom right
    FLASH = (0.86, 0.96, 1.0)                    # the squares light up to this
    SQUARES = ((2, 2, 0), (12.4, 2, 1), (2, 12.4, 1), (12.4, 12.4, 2))   # x, y, diagonal
    DELAY = 0.07                # s between the diagonals
    UP, DOWN = 0.06, 0.22       # s: lighting up, going back

    def __init__(self, size=26):
        super().__init__()
        self.size = size
        self.set_size_request(size, size)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.add_css_class("start-logo")
        self.t0 = None
        self.t = 0.0
        self.tick = 0

    def flicker(self):
        self.t0 = None
        if not self.tick:
            self.tick = self.add_tick_callback(self.on_tick)

    def on_tick(self, _w, clock):
        now = clock.get_frame_time() / 1e6
        if self.t0 is None:
            self.t0 = now
        self.t = now - self.t0
        self.queue_draw()
        if self.t >= 2 * self.DELAY + self.UP + self.DOWN:
            self.t0, self.t, self.tick = None, 0.0, 0
            return False
        return True

    def glow(self, diag):
        """0..1: how far that square is lit up right now."""
        if not self.tick:
            return 0.0
        t = self.t - diag * self.DELAY
        if t <= 0:
            return 0.0
        if t < self.UP:
            return t / self.UP
        t -= self.UP
        return max(0.0, 1 - t / self.DOWN) ** 2

    def mix(self, c, k):
        f = self.FLASH
        return rgba(c[0] + (f[0] - c[0]) * k, c[1] + (f[1] - c[1]) * k, c[2] + (f[2] - c[2]) * k, 1)

    def do_snapshot(self, snap):
        k = self.size / 24
        ox = (self.get_width() - self.size) / 2
        oy = (self.get_height() - self.size) / 2
        side = 9.6 * k
        for x, y, diag in self.SQUARES:
            g = self.glow(diag)
            rect = Graphene.Rect().init(ox + x * k, oy + y * k, side, side)
            clip = Gsk.RoundedRect()
            clip.init_from_rect(rect, 1 * k)
            snap.push_rounded_clip(clip)
            stops = [Gsk.ColorStop(), Gsk.ColorStop()]
            stops[0].offset, stops[0].color = 0.0, self.mix(self.LIGHT, g)
            stops[1].offset, stops[1].color = 1.0, self.mix(self.DEEP, g * 0.85)
            snap.append_linear_gradient(rect, Graphene.Point().init(ox + x * k, oy + y * k),
                                        Graphene.Point().init(ox + x * k + side, oy + y * k + side), stops)
            snap.pop()


class WeekScroller(Gtk.Widget):
    """The calendar's days as one endless sheet of weeks (like Windows 11):
    it scrolls smoothly – a touchpad moves it with the fingers (and on with
    momentum), the wheel and the arrows glide – and settles on a whole row.
    The month in the middle of the view is the bright one, its name is in
    the header; the days of the months around it are dimmed."""
    ROWS = 6
    CELL = 40                   # px: the round day cell
    GAP = 2                     # px between rows / columns
    EPOCH = datetime.date(2001, 1, 1)       # a Monday: rows count from its week
    WHEEL_ROWS = 1              # one wheel notch
    WHEEL_GLIDE = 0.34          # s: the numbers glide over slowly, no jump
    FRICTION = 5.0              # momentum: 1/s (higher stops sooner)

    def __init__(self, on_month):
        super().__init__()
        self.on_month = on_month            # (year, month): the month in view changed
        self.row_h = self.CELL + self.GAP
        self.set_size_request(-1, self.ROWS * self.CELL + (self.ROWS - 1) * self.GAP)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.top = 0.0                      # rows from EPOCH's week at the top edge
        self.focus = None                   # (year, month) shown bright
        self.prev_focus = None              # … fading out
        self.focus_t0 = 0.0
        self.hover = None                   # (x, y) of the pointer
        self.anim = None                    # glide: (from, to, t0, duration)
        self.velocity = 0.0                 # momentum: rows per second
        self.tick = 0
        self.last_frame = 0.0
        self.touching = False
        self.settle_id = 0
        self._layouts = {}                  # (text, size, weight) -> Pango.Layout

        sc = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL
                                       | Gtk.EventControllerScrollFlags.KINETIC)
        sc.connect("scroll-begin", self.on_scroll_begin)
        sc.connect("scroll", self.on_scroll)
        sc.connect("scroll-end", self.on_scroll_end)
        sc.connect("decelerate", self.on_decelerate)
        self.add_controller(sc)
        mo = Gtk.EventControllerMotion()
        mo.connect("motion", lambda _c, x, y: self.set_hover((x, y)))
        mo.connect("leave", lambda _c: self.set_hover(None))
        self.add_controller(mo)

    # ── positions ──
    @classmethod
    def row_of(cls, date):
        return (date - cls.EPOCH).days // 7

    @staticmethod
    def first_row_month(row):
        """The (year, month) of the Thursday of that row (ISO: the week's month)."""
        d = WeekScroller.EPOCH + datetime.timedelta(days=7 * row + 3)
        return d.year, d.month

    def middle_month(self):
        return self.first_row_month(math.floor(self.top + self.ROWS / 2))

    def show_month(self, year, month, animate=False):
        """That month's first week at the top."""
        row = self.row_of(datetime.date(year, month, 1))
        if animate:
            self.glide_to(row, 0.26)
        else:
            self.stop()
            self.top = float(row)
            self.update_focus(fade=False)
            self.queue_draw()

    def step_month(self, delta):
        base = self.anim[1] if self.anim else self.top       # arrows pressed quickly: from where it is heading
        y, m = self.first_row_month(math.floor(base + self.ROWS / 2))
        m += delta
        y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
        self.show_month(y, m, animate=True)

    # ── motion ──
    def ensure_tick(self):
        if not self.tick:
            self.last_frame = 0.0
            self.tick = self.add_tick_callback(self.on_tick)

    def stop(self):
        self.anim = None
        self.velocity = 0.0
        if self.settle_id:
            GLib.source_remove(self.settle_id)
            self.settle_id = 0

    def glide_to(self, row, duration=0.2):
        start = self.top
        self.stop()
        if abs(row - start) < 1e-3:
            self.top = float(row)
            self.queue_draw()
            return
        self.anim = (start, float(row), None, duration)
        self.ensure_tick()

    def settle(self):
        """Come to rest on a whole row."""
        self.settle_id = 0
        if not self.touching and self.anim is None and not self.velocity:
            self.glide_to(round(self.top), 0.18)
        return False

    def on_tick(self, _w, clock):
        now = clock.get_frame_time() / 1e6
        dt = min(0.05, now - self.last_frame) if self.last_frame else 1 / 60
        self.last_frame = now
        busy = False
        if self.anim:
            a, b, t0, dur = self.anim
            if t0 is None:
                t0 = now
                self.anim = (a, b, t0, dur)
            t = min(1.0, (now - t0) / dur)
            e = 1 - (1 - t) ** 3                    # ease out
            self.top = a + (b - a) * e
            if t >= 1.0:
                self.anim = None
            else:
                busy = True
        elif self.velocity:
            self.top += self.velocity * dt
            self.velocity *= math.exp(-self.FRICTION * dt)
            if abs(self.velocity) < 1.5:            # rows/s: slow enough – settle
                self.velocity = 0.0
                self.settle()
            busy = True
        if self.focus_t0:                           # months still crossfading
            busy = True
        self.update_focus()
        self.queue_draw()
        if not busy and self.anim is None:
            self.tick = 0
            return False
        return True

    # ── input ──
    def on_scroll_begin(self, _c):
        self.touching = True
        self.stop()

    def on_scroll(self, c, _dx, dy):
        if not dy:
            return True
        if c.get_unit() == Gdk.ScrollUnit.SURFACE:      # touchpad: follows the fingers
            self.stop()
            self.top += dy / self.row_h
            self.update_focus()
            self.queue_draw()
            if not self.touching:                       # no begin/end (some devices)
                self.settle_id = GLib.timeout_add(120, self.settle)
        else:                                           # wheel: glides one row per notch
            # (Hyprland's scroll factor makes a notch 1.67: that was 2 rows
            # at once; fine-grained wheels send parts of a notch – summed up)
            if abs(dy) >= 0.5:
                steps = 1 if dy > 0 else -1
                self.wheel_acc = 0.0
            else:
                self.wheel_acc = getattr(self, "wheel_acc", 0.0) + dy
                steps = int(self.wheel_acc)
                self.wheel_acc -= steps
            if steps:
                target = (self.anim[1] if self.anim else round(self.top)) + steps * self.WHEEL_ROWS
                self.glide_to(round(target), self.WHEEL_GLIDE)
        return True

    def on_scroll_end(self, _c):
        self.touching = False
        # momentum (decelerate) follows right away if the fingers were moving
        self.settle_id = GLib.timeout_add(40, self.settle)

    def on_decelerate(self, _c, _vx, vy):
        if self.settle_id:
            GLib.source_remove(self.settle_id)
            self.settle_id = 0
        self.velocity = vy / self.row_h
        if abs(self.velocity) < 1.5:
            self.velocity = 0.0
            self.settle()
            return
        self.ensure_tick()

    def set_hover(self, pos):
        self.hover = pos
        self.queue_draw()

    # ── drawing ──
    def update_focus(self, fade=True):
        f = self.middle_month()
        if f != self.focus:
            self.prev_focus = self.focus if fade else None
            self.focus = f
            self.focus_t0 = time.monotonic() if fade and self.prev_focus else 0.0
            if fade and self.prev_focus:
                self.ensure_tick()
            self.on_month(*f)

    def brightness(self, ym):
        """1 for the month in view, dim for the others – crossfading."""
        k = 1.0
        if self.focus_t0:
            k = min(1.0, (time.monotonic() - self.focus_t0) / 0.18)
            if k >= 1.0:
                self.focus_t0 = 0.0
        if ym == self.focus:
            return 0.38 + 0.62 * k
        if ym == self.prev_focus and k < 1.0:
            return 1.0 - 0.62 * k
        return 0.38

    def layout(self, text, size, weight=Pango.Weight.NORMAL):
        key = (text, size, weight)
        lay = self._layouts.get(key)
        if lay:
            return lay
        lay = self.create_pango_layout(text)
        desc = lay.get_context().get_font_description().copy()
        desc.set_absolute_size(size * Pango.SCALE)
        desc.set_weight(weight)
        lay.set_font_description(desc)
        self._layouts[key] = lay
        return lay

    def text(self, snap, lay, cx, cy, rgba):
        _ink, logical = lay.get_pixel_extents()
        snap.save()
        snap.translate(Graphene.Point().init(cx - logical.width / 2, cy - logical.height / 2))
        snap.append_layout(lay, rgba)
        snap.restore()

    def circle(self, snap, cx, cy, rgba):
        r = self.CELL / 2
        rect = Graphene.Rect().init(cx - r, cy - r, self.CELL, self.CELL)
        clip = Gsk.RoundedRect()
        clip.init_from_rect(rect, r)
        snap.push_rounded_clip(clip)
        snap.append_color(rgba, rect)
        snap.pop()

    def do_snapshot(self, snap):
        w = self.get_width()
        col_w = (w - 7 * self.GAP) / 8
        today = datetime.date.today()
        first = math.floor(self.top)
        hover_cell = None
        if self.hover:
            hx, hy = self.hover
            hcol = int(hx // (col_w + self.GAP))
            hrow = math.floor(self.top + hy / self.row_h)
            if 1 <= hcol <= 7:
                hover_cell = (hrow, hcol)
        white = lambda a: rgba(1, 1, 1, a)
        for row in range(first, first + self.ROWS + 1):
            y = (row - self.top) * self.row_h
            cy = y + self.CELL / 2
            monday = self.EPOCH + datetime.timedelta(days=7 * row)
            # calendar week (ISO 8601, as used in Switzerland/Germany)
            self.text(snap, self.layout(str(monday.isocalendar()[1]), 11),
                      col_w / 2, cy, white(0.45))
            for c in range(7):
                day = monday + datetime.timedelta(days=c)
                cx = (c + 1) * (col_w + self.GAP) + col_w / 2
                if day == today:
                    self.circle(snap, cx, cy, rgba(0x4c / 255, 0xc2 / 255, 1, 1))
                    self.text(snap, self.layout(str(day.day), 13, Pango.Weight.SEMIBOLD),
                              cx, cy, rgba(0, 0, 0, 1))
                    continue
                if hover_cell == (row, c + 1):
                    self.circle(snap, cx, cy, white(0.06))
                self.text(snap, self.layout(str(day.day), 13), cx, cy,
                          white(self.brightness((day.year, day.month))))


class CalendarFlyout:
    """Own month grid (GTK's calendar scrolls on the mouse wheel and redraws badly)."""

    def __init__(self, app, shell):
        self.shell = shell
        self.visible = False
        self.win = layer_window(app, "winshell-calendar", LS.Layer.OVERLAY,
                                anchors=(LS.Edge.BOTTOM, LS.Edge.RIGHT),
                                margins={LS.Edge.BOTTOM: 12, LS.Edge.RIGHT: 12},
                                keyboard=LS.KeyboardMode.NONE)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", lambda _c, k, *_: (self.hide(), True)[1] if k == Gdk.KEY_Escape else False)
        self.win.add_controller(keys)

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("cal")
        # no .flyout glide out of the taskbar: like Windows 11 it slides in
        # from the right screen edge and back out – Hyprland's layer
        # animation (hyprland.lua), in step with the notification center above
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

        # day names (column 0: calendar week, as used in Switzerland/Germany);
        # the days below are one endless sheet of weeks that scrolls smoothly
        grid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=WeekScroller.GAP)
        grid.add_css_class("cal-grid")
        names = Gtk.Grid(column_homogeneous=True, column_spacing=WeekScroller.GAP)
        names.set_size_request(-1, WeekScroller.CELL)
        names.attach(label("KW", "cal-weekname", xalign=0.5, ellipsize=False), 0, 0, 1, 1)
        for i, d in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            names.attach(label(d, "cal-dayname", xalign=0.5, ellipsize=False), i + 1, 0, 1, 1)
        for ch in (names.get_child_at(i, 0) for i in range(8)):
            ch.set_valign(Gtk.Align.CENTER)
        grid.append(names)
        self.days = WeekScroller(lambda y, m: self.month_lbl.set_text(
            GLib.DateTime.new_local(y, m, 1, 0, 0, 0).format("%B %Y")))
        grid.append(self.days)
        month = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        month.add_css_class("cal-body")
        month.append(nav)
        month.append(grid)
        self.month_box = month
        self.body = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                                 transition_duration=200, reveal_child=not self.folded)
        self.body.set_child(month)
        self.body.connect("notify::child-revealed", lambda *_: self.folded_done())
        panel.append(self.body)
        self.win.set_child(panel)


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
                    dnd = bool(info.get("dnd"))
                    if dnd != getattr(self.shell, "dnd_on", None):
                        GLib.idle_add(self.shell.show_dnd, dnd)
                    empty = not info.get("count")
                    if empty != self.nc_empty:
                        # nothing to show: smaller panel; something arrived: full size
                        if empty and self.nc_empty is not None:
                            # "Clear all" / the last one closed: let swaync's
                            # slide-out play first – reloading its config
                            # rebuilds the panel and cut the animation off
                            # (a new one meanwhile: the next line sets it back)
                            time.sleep(0.45)
                        self.nc_empty = empty
                        ncplace.save(empty=empty)
                        ncplace.sync()
                    if self.nc_open and not visible and not getattr(self, "nc_moving", False):
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
        if not self.visible:
            bg(ncplace.sync)
            return
        # swaync takes a new place only with a reload and only once it is
        # opened again; it can't move while open. So while the calendar
        # folds (200 ms) it fades out where it was and fades back in at its
        # new place once the calendar has arrived – no jump, no hanging in
        # the air. (The reload sometimes closes it by itself: expected, the
        # calendar stays open.)
        self.nc_moving = True
        self.nc_anim("fade")
        self.notification_center(False)
        bg(ncplace.sync)
        GLib.timeout_add(self.body.get_transition_duration() + 20, self.nc_reopen)

    @staticmethod
    def nc_anim(style):
        """Hyprland's animation of the notification center (hyprland.lua
        rule "slide-swaync-cc"): "slide right" normally, "fade" while it moves."""
        hypr_eval('hl.layer_rule({ name = "slide-swaync-cc", match = { namespace = '
                  f'"^swaync-control-center$" }}, animation = "{style}" }})')

    def nc_reopen(self):
        if self.visible:
            self.notification_center(True)
            # the keyboard back to the calendar (Escape closes both)
            GLib.timeout_add(40, lambda: self.visible and LS.set_keyboard_mode(
                self.win, LS.KeyboardMode.EXCLUSIVE) and False)

        def done():
            self.nc_moving = False
            self.nc_anim("slide right")
            return False
        GLib.timeout_add(450, done)
        return False

    def shift(self, months):
        """The arrows: the previous / next month glides into view."""
        self.days.step_month(months)

    def toggle(self):
        self.hide() if self.visible else self.show()

    def show(self):
        for other in (self.shell.qs, self.shell.start):
            if other.visible:
                other.hide()
        now = GLib.DateTime.new_now_local()
        self.head.set_text(now.format("%A, %B %-d"))
        self.days.show_month(now.get_year(), now.get_month())
        self.visible = True
        self.shell.qs.catcher_for("calendar")
        # the notification center sits right above: opened over D-Bus (not
        # by starting swaync-client, which came a beat late). Both slide in
        # from the right (hyprland.lua) – the calendar only appears once
        # it is about to be (NC_DELAY) – it came ~20 ms later and lagged behind.
        # (Waiting for Hyprland's "openlayer" event was too late: +35 ms.)
        self.notification_center(True)
        self.waiting_nc = True
        GLib.timeout_add(self.NC_DELAY, self.nc_opened)

    # ms: swaync's control center maps ~20 ms after the D-Bus call, but its
    # contents show up on screen only ~65 ms after the calendar's (measured
    # frame by frame) – the notifications slid in behind the calendar. The
    # calendar waits that long, so both slide in as one.
    NC_DELAY = 80

    def nc_opened(self):
        """swaync's control center is (about to be) on screen: now the
        calendar, so both slide in together."""
        if not (self.visible and getattr(self, "waiting_nc", False)):
            return False
        self.waiting_nc = False
        # measured once the window is up: before its first showing the
        # calendar reported a far too small height, and the notification
        # center was placed over it
        GLib.timeout_add(60, lambda: self.visible and self.place_notifications(
            self.height(open_=not self.folded)) and False)
        # take the keyboard (after swaync opened) so Escape closes both
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.EXCLUSIVE)
        self.win.present()
        return False

    def hide(self):
        if not self.visible:
            return
        self.visible = False
        self.waiting_nc = False
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.NONE)
        self.win.set_visible(False)             # slides out to the right (hyprland.lua), the notifications with it
        self.shell.qs.catcher_for(None)
        self.notification_center(False)

    @staticmethod
    def notification_center(open_):
        def fallback(conn, res):
            try:
                conn.call_finish(res)
            except GLib.Error:
                spawn("swaync-client -op" if open_ else "swaync-client -cp")
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call("org.erikreider.swaync.cc", "/org/erikreider/swaync/cc", "org.erikreider.swaync.cc",
                     "SetVisibility", GLib.Variant("(b)", (open_,)), None,
                     Gio.DBusCallFlags.NONE, 1000, None, fallback)
        except GLib.Error:
            spawn("swaync-client -op" if open_ else "swaync-client -cp")


# ── Start menu: files and folders in the home folder ───────────────────────
class FileIndex:
    """The names of the files and folders in your home folder, kept in
    memory for the start menu search. Hidden ones and build/cache folders
    are left out. Rebuilt in the background when the menu opens (a walk
    over the home folder takes a few ms)."""
    SKIP = {"node_modules", "__pycache__", "target", "build", "dist", "venv", "site-packages"}
    MAX = 50000
    FRESH = 30                  # s: an index this young is used as it is
    PHOTO = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp", ".svg", ".tif", ".tiff", ".avif")

    def __init__(self):
        self.items = []         # (name lower, path, is_dir, mtime)
        self.built = 0.0
        self.busy = False

    def refresh(self, done=None):
        if self.busy or time.monotonic() - self.built < self.FRESH:
            return
        self.busy = True

        def walk():
            home = os.path.expanduser("~")
            out = []
            for root, dirs, files in os.walk(home):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in self.SKIP]
                for name, is_dir in [(d, True) for d in dirs] + [(f, False) for f in files]:
                    if name.startswith("."):
                        continue
                    path = os.path.join(root, name)
                    try:
                        mtime = os.lstat(path).st_mtime
                    except OSError:
                        continue
                    out.append((name.lower(), path, is_dir, mtime))
                if len(out) >= self.MAX:
                    break
            return out

        def finished(items):
            self.items, self.built, self.busy = items, time.monotonic(), False
            if done:
                done()
        bg(walk, finished)

    @classmethod
    def kind(cls, path, is_dir):
        if is_dir:
            return "folder"
        return "photo" if path.lower().endswith(cls.PHOTO) else "doc"

    def search(self, query, limit):
        """Best first: the whole name, its start, a word's start, anywhere;
        every word of the query has to be in the name. Newer files win ties."""
        terms = query.lower().split()
        if not terms:
            return []
        q = " ".join(terms)
        hits = []
        for name, path, is_dir, mtime in self.items:
            if not all(t in name for t in terms):
                continue
            stem = name if is_dir else os.path.splitext(name)[0]
            words = _words(name)
            if stem == q or name == q:
                sc = 100
            elif name.startswith(q):
                sc = 80
            elif all(any(w.startswith(t) for w in words) for t in terms):
                sc = 60
            else:
                sc = 40
            hits.append((-sc, -mtime, path, is_dir))
        hits.sort()
        return [(p, d) for _sc, _m, p, d in hits[:limit]]


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
        # window right on top of the taskbar (the 12 px gap is the panel's
        # margin, style.css .start): the menu slides out of the taskbar,
        # clipped at its top edge
        self.win = layer_window(app, "winshell-start", LS.Layer.OVERLAY,
                                anchors=(LS.Edge.BOTTOM,), margins={LS.Edge.BOTTOM: 0})
        self.win.add_css_class("start-window")     # style.css: so it is on screen while empty
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
        self.search = Gtk.SearchEntry(placeholder_text="Search for apps and documents")
        self.search.set_hexpand(True)
        if hasattr(self.search.props, "search_delay"):
            self.search.props.search_delay = 40      # default 150 ms feels laggy
        self.search.connect("search-changed", lambda *_: self.filter())
        self.search.connect("activate", lambda *_: self.launch_selected() if self.query else self.launch_first())
        body.append(search_box(self.search, "start-search"))

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
        # like Windows the menu is always ready: its window stays on screen,
        # empty (the panel sits below its edge) and click-through (park).
        # A freshly shown window took ~35-50 ms until Hyprland put it on
        # screen, and only then could the menu start sliding – the Windows
        # key felt slow. Now the slide starts with the key press.
        GLib.idle_add(self.premap)

    def premap(self):
        self.win.present()
        self.park()
        return False

    def repark(self):
        """Out and straight back in (premap). Merely staying on screen after
        closing, Hyprland kept the pointer on it and the next click went
        nowhere; a fresh, empty window doesn't take it."""
        self.win.set_visible(False)
        GLib.timeout_add(30, lambda: (not self.visible and self.premap()) and False)

    def park(self):
        """Mapped but invisible: no input, no keyboard (clicks go through)."""
        import cairo
        surf = self.win.get_surface()
        if surf:
            surf.set_input_region(cairo.Region())

    def unpark(self):
        import cairo
        surf = self.win.get_surface()
        if surf:
            surf.set_input_region(cairo.Region(cairo.RectangleInt(0, 0, 100000, 100000)))

    # ── search results (Windows 11: best match + list left, details right) ──
    # apps always come first, then files: documents, photos, folders
    MAX_APPS, MAX_FILES = 6, 8
    GROUPS = {"app": "Apps", "doc": "Documents", "photo": "Photos", "folder": "Folders"}
    KIND_NAME = {"app": "App", "doc": "Document", "photo": "Photo", "folder": "Folder"}

    def build_search_page(self):
        page = Gtk.Box(spacing=12)
        page.add_css_class("search-page")
        left = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        left.set_size_request(300, -1)
        self.results_box = left
        self.results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.results.add_css_class("results")
        self.results.set_header_func(self.result_header)
        self.results.connect("row-activated", lambda _l, row: self.open_row(row))
        self.results.connect("row-selected", lambda _l, row: self.show_details(row))
        left.set_child(self.results)
        page.append(left)
        # nothing found: in the middle of the whole page
        self.no_results = label("No results", "empty-text", xalign=0.5)
        self.no_results.set_hexpand(True)
        self.no_results.set_halign(Gtk.Align.CENTER)
        self.no_results.set_valign(Gtk.Align.START)
        page.append(self.no_results)
        self.files = FileIndex()

        self.details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.details.add_css_class("details")
        self.details.set_hexpand(True)
        page.append(self.details)
        return page

    @classmethod
    def result_header(cls, row, before):
        """"Best match" above the first result, then a heading wherever a
        group starts: Apps, Documents, Photos, Folders."""
        if before is None:
            row.set_header(label("Best match", "results-head"))
        elif before.get_index() == 0 or before.kind != row.kind:
            row.set_header(label(cls.GROUPS[row.kind], "results-head"))
        else:
            row.set_header(None)

    # like Windows 11: folders show the Explorer icon (as on the taskbar),
    # photos the Photos one, every other file the Notepad one (make-icons.py)
    KIND_ICON = {"folder": "win11-explorer", "photo": "win11-photo", "doc": "win11-document"}

    @classmethod
    def file_gicon(cls, path, is_dir):
        return Gio.ThemedIcon.new(cls.KIND_ICON[FileIndex.kind(path, is_dir)])

    def result_row(self, app, name, best, kind="app", path=None):
        box = Gtk.Box(spacing=12)
        box.add_css_class("result")
        gicon = app_gicon(app) if kind == "app" else self.file_gicon(path, kind == "folder")
        img = Gtk.Image.new_from_gicon(gicon)
        img.set_pixel_size(32 if best else 20)
        box.append(img)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        text.set_hexpand(True)
        text.append(label(name, "result-name"))
        if best:
            text.append(label(self.KIND_NAME[kind], "result-kind"))
        box.append(text)
        chev = icon("win11-chevron-right", 12)
        chev.add_css_class("result-chevron")
        box.append(chev)
        row = Gtk.ListBoxRow(child=box)
        if best:
            row.add_css_class("best")
        row.app, row.name, row.kind, row.path, row.gicon = app, name, kind, path, gicon
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
        rows = [self.result_row(c.app, c.name, i == 0) for i, c in enumerate(ranked[:self.MAX_APPS])]
        files = self.files.search(self.query, self.MAX_FILES)
        order = {"doc": 0, "photo": 1, "folder": 2}
        # the best file stays on top of its group, the groups in a fixed order
        firsts = files[:1] if not rows else []
        rest = sorted(files[len(firsts):], key=lambda f: order[FileIndex.kind(*f)])
        for path, is_dir in firsts + rest:
            rows.append(self.result_row(None, os.path.basename(path), not rows,
                                        FileIndex.kind(path, is_dir), path))
        for r in rows:
            self.results.append(r)
        self.no_results.set_visible(not rows)
        self.results_box.set_visible(bool(rows))
        self.details.set_visible(bool(rows))
        if rows:
            self.results.select_row(self.results.get_row_at_index(0))
        self.results_box.get_vadjustment().set_value(0)

    def show_details(self, row):
        clear(self.details)
        if row is None:
            return
        if row.kind != "app":
            self.show_file_details(row)
            return
        app = row.app
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)
        top.add_css_class("details-top")
        img = Gtk.Image.new_from_gicon(app_gicon(app))
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

    def show_file_details(self, row):
        # long names are cut short (…) instead of widening the menu
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        top.add_css_class("details-top")
        img = Gtk.Image.new_from_gicon(row.gicon)
        img.set_pixel_size(64)
        top.append(img)
        name = label(row.name, "details-name", xalign=0.5)
        name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        name.set_max_width_chars(1)
        name.set_hexpand(True)
        top.append(name)
        top.append(label(self.KIND_NAME[row.kind], "details-kind", xalign=0.5))
        where = label(os.path.dirname(row.path).replace(os.path.expanduser("~"), "~", 1),
                      "details-path", xalign=0.5)
        where.set_max_width_chars(1)
        where.set_hexpand(True)
        where.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        top.append(where)
        self.details.append(top)
        sep = Gtk.Separator()
        sep.add_css_class("details-sep")
        self.details.append(sep)
        path = row.path
        actions = [("win11-open", "Open", lambda: self.open_row(row))]
        if row.kind != "folder":
            actions.append(("win11-folder", "Open file location", lambda: self.show_in_folder(path)))
        actions.append(("win11-copy", "Copy path", lambda: (
            subprocess.Popen(["wl-copy", "--", path], start_new_session=True, env=child_env(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL), self.hide())))
        for ic, text, fn in actions:
            line = Gtk.Box(spacing=12)
            line.append(icon(ic, 16))
            line.append(label(text, "action-text"))
            self.details.append(button(line, "action", on_click=fn))

    def open_row(self, row):
        if row.kind == "app":
            self.launch(row.app)
            return
        self.hide()
        try:
            ctx = Gdk.Display.get_default().get_app_launch_context()
            for var in OWN_ENV:
                ctx.unsetenv(var)
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(row.path).get_uri(), ctx)
        except Exception as e:
            print("winshell: open failed:", e, file=sys.stderr)

    def show_in_folder(self, path):
        """The folder it is in, with the file selected (FileManager1: Dolphin)."""
        self.hide()
        uri = Gio.File.new_for_path(path).get_uri()

        def fallback(conn, res):
            try:
                conn.call_finish(res)
            except GLib.Error:
                Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(os.path.dirname(path)).get_uri(), None)
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                 "org.freedesktop.FileManager1", "ShowItems", GLib.Variant("(ass)", ([uri], "")),
                 None, Gio.DBusCallFlags.NONE, 3000, None, fallback)

    def move_selection(self, delta):
        row = self.results.get_selected_row()
        i = (row.get_index() if row else -1) + delta
        target = self.results.get_row_at_index(max(0, i))
        if target:
            self.results.select_row(target)

    def launch_selected(self):
        row = self.results.get_selected_row()
        if row:
            self.open_row(row)

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
        # fade in while the entries glide up (style.css popover.start-menu),
        # not there from one frame to the next; reset on close for next time
        pop.connect("map", lambda p: slide_in(p))
        pop.connect("unmap", lambda p: p.remove_css_class("shown"))
        return pop

    def power(self, action):
        self.hide()
        spawn(f"sleep 0.2; {SCRIPTS}/powermenu.sh '{action}'")

    # apps -----------------------------------------------------------------
    # kitty is the terminal of this setup: it shows up as "Terminal", and the
    # other terminal apps that came with the system stay out of the menu
    RENAME = {"winterm.desktop": "Terminal"}
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
            img = Gtk.Image.new_from_gicon(app_gicon(a))
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
        # files for the search: fresh in the background (results update when ready)
        self.files.refresh(lambda: self.query and self.show_results())
        if self.search.get_text():
            self.search.set_text("")
            self.filter()
        self.visible = True
        self.mark_open(True)
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.EXCLUSIVE)
        self.shell.qs.catcher_for("start")
        if self.win.get_mapped():
            # already on screen (premap): slide right away
            leave = getattr(self.panel, "_leave_id", 0)
            if leave:                   # it was just going away: comes back instead
                GLib.source_remove(leave)
                self.panel._leave_id = 0
            self.panel.remove_css_class("leaving")
            self.unpark()
            self.panel.add_css_class("shown")
        else:
            slide_in(self.panel)
            self.win.present()
            self.unpark()
        self.search.grab_focus()
        adj = self.flow.get_parent().get_parent().get_vadjustment() if self.flow.get_parent() else None
        if adj:
            adj.set_value(0)

    def hide(self):
        if not self.visible:
            return
        self.visible = False
        # slides back down into the taskbar (style.css .start.flyout), then
        # the window goes and comes straight back, empty and click-through,
        # ready for the next time (repark)
        self.park()
        slide_out(self.panel, self.win, 180, gone_cb=self.repark)
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
    """The flyout at the bottom: volume, brightness, … only on the main
    screen (it sits there, arrange_bars); "Desktop 2" on every screen – a
    desktop spans all of them."""
    def __init__(self, app):
        self.app = app
        self.timer = None
        self.main = self.pill()
        self.win = self.main.win
        self.others = {}                    # connector -> pill on the other screens
        self.watch_kbd_backlight()

    def pill(self):
        p = types.SimpleNamespace()
        p.win = layer_window(self.app, "winshell-osd", LS.Layer.OVERLAY,
                             anchors=(LS.Edge.BOTTOM,), margins={LS.Edge.BOTTOM: 24})
        p.box = Gtk.Box(spacing=14)
        p.box.add_css_class("osd")
        p.img = icon("win11-volume-3", 18)
        p.bar = Gtk.ProgressBar(hexpand=True, valign=Gtk.Align.CENTER)
        p.lbl = label("", "osd-value", xalign=1.0, ellipsize=False)
        p.lbl.set_width_chars(4)            # "100", "High": same width
        # microphone on/off: a text instead of the bar ("Microphone muted")
        p.msg = label("", "osd-message", xalign=0.5, ellipsize=False)
        for w in (p.img, p.bar, p.lbl, p.msg):
            p.box.append(w)
        p.win.set_child(p.box)
        p.mon = None
        return p

    def other_pills(self):
        """A pill for every screen besides the main one (made on demand,
        dropped when the screen is gone)."""
        primary = getattr(self.app, "primary", None)
        mons = Gdk.Display.get_default().get_monitors()
        out, seen = [], set()
        for i in range(mons.get_n_items()):
            m = mons.get_item(i)
            c = m.get_connector()
            if not c or c == primary:
                continue
            seen.add(c)
            p = self.others.get(c)
            if p and p.mon is not m:            # off and on again: a new screen for GTK
                p.win.destroy()
                p = None
            if not p:
                p = self.pill()
                p.mon = m
                LS.set_monitor(p.win, m)
                self.others[c] = p
            out.append(p)
        for c in list(self.others):
            if c not in seen:
                self.others.pop(c).win.destroy()
        return out

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
        if kind == "mic":
            muted = "MUTED" in sh("wpctl get-volume @DEFAULT_AUDIO_SOURCE@")
            return ("win11-mic-off" if muted else "win11-mic"), None, muted
        out = sh("wpctl get-volume @DEFAULT_AUDIO_SINK@")
        mv = re.search(r"([\d.]+)", out)
        v = round(float(mv.group(1)) * 100) if mv else 0
        muted = "MUTED" in out
        return QuickSettings.volume_icon(v, muted), v, muted

    def show(self, kind):
        bg(lambda: self.read(kind), self.update)

    def update(self, res, text=None, everywhere=False):
        """everywhere: on every screen (the desktop changed), else only on
        the main one."""
        pills = [self.main] + (self.other_pills() if everywhere else [])
        for p in self.others.values():
            if p not in pills:
                p.win.set_visible(False)
        for p in pills:
            self.fill(p, res, text)
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(1600, self.hide)

    @staticmethod
    def fill(p, res, text):
        name, value, muted = res
        p.img.set_from_icon_name(name)
        message = value is None             # microphone: on / muted, no level
        p.bar.set_visible(not message)
        p.lbl.set_visible(not message)
        p.msg.set_visible(message)
        # a text pill is only as wide as icon + text: even room on both sides
        (p.box.add_css_class if message else p.box.remove_css_class)("osd-text")
        p.win.set_default_size(1, 1)        # shrink again after the wider volume pill
        if message:
            p.msg.set_text(text if text is not None else
                           ("Microphone muted" if muted else "Microphone on"))
        else:
            p.bar.set_fraction(max(0, min(100, value)) / 100)
            (p.bar.add_css_class if muted else p.bar.remove_css_class)("muted")
            p.lbl.set_text(text if text is not None else str(value))
        p.win.present()

    def hide(self):
        self.timer = None
        for p in [self.main] + list(self.others.values()):
            p.win.set_visible(False)
        return False



# ── Long notifications ──────────────────────────────────────────────────────
class NotifyGuard:
    """Keeps a notification from filling the screen. swaync cuts every line
    to one line, but not the number of lines: a chat message made of
    hundreds of line breaks (Discord) became a popup as high as the screen.
    Like Windows, a notification shows its first few lines only.

    winshell listens to the session bus (a D-Bus monitor) for notifications
    going to swaync; a long one is replaced right away (same id) by an exact
    copy – icon, the sender's picture, actions, hints – with only the text
    cut to MAX_LINES lines and "…"."""
    MAX_LINES = 3
    MAX_CHARS = 280

    def __init__(self):
        self.calls = {}                 # (sender, serial) -> Notify arguments (GLib.Variant)
        self.conn = None
        try:
            addr = Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None)
            self.conn = Gio.DBusConnection.new_for_address_sync(
                addr, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
                Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
            self.conn.add_filter(self.on_message)
            # asynchronous: the filter runs in GDBus' own thread and needs
            # Python meanwhile – a blocking call here would wait for itself
            self.conn.call(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus.Monitoring",
                "BecomeMonitor", GLib.Variant("(asu)", ([
                    "type='method_call',interface='org.freedesktop.Notifications',member='Notify'",
                    "type='method_return',sender='org.freedesktop.Notifications'"], 0)),
                None, Gio.DBusCallFlags.NONE, 5000, None, self.on_monitor)
        except GLib.Error as e:
            print("winshell: notification guard:", e.message, file=sys.stderr, flush=True)

    def on_monitor(self, conn, res):
        try:
            conn.call_finish(res)
        except GLib.Error as e:
            print("winshell: notification guard:", e.message, file=sys.stderr, flush=True)

    @classmethod
    def trimmed(cls, body):
        lines = [ln.strip() for ln in body.splitlines()]
        lines = [ln for ln in lines if ln]
        if (len(lines) <= cls.MAX_LINES and len(body) <= cls.MAX_CHARS * 2
                and body.count("\n") < 2 * cls.MAX_LINES):
            return None                 # short enough: leave it alone
        text = "\n".join(lines[:cls.MAX_LINES])
        if len(text) > cls.MAX_CHARS:
            text = text[:cls.MAX_CHARS].rstrip()
        return text + " …"

    def on_message(self, _conn, msg, incoming):
        # (a GDBus worker thread: only note things down here)
        if not incoming or msg.get_sender() == "org.freedesktop.DBus":
            return msg                  # our own call and the bus' answer to it go through
        try:
            kind = msg.get_message_type()
            if kind == Gio.DBusMessageType.METHOD_CALL:
                args = msg.get_body()
                if args is not None and args.n_children() == 8:
                    if len(self.calls) > 64:
                        self.calls.clear()
                    self.calls[(msg.get_sender(), msg.get_serial())] = args
            elif kind == Gio.DBusMessageType.METHOD_RETURN:
                args = self.calls.pop((msg.get_destination(), msg.get_reply_serial()), None)
                body = msg.get_body()
                if args is not None and body is not None:
                    short = self.trimmed(args.get_child_value(4).get_string())
                    if short is not None:
                        nid = body.get_child_value(0).get_uint32()
                        GLib.idle_add(self.replace, args, nid, short)
        except Exception as e:          # noqa: BLE001 – never break the bus connection
            print("winshell: notification guard:", e, file=sys.stderr, flush=True)
        return None                     # a monitor only listens

    @staticmethod
    def replace(args, nid, short):
        kids = [args.get_child_value(i) for i in range(8)]
        kids[1] = GLib.Variant("u", nid)
        kids[4] = GLib.Variant("s", short)
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                     "org.freedesktop.Notifications", "Notify", GLib.Variant.new_tuple(*kids),
                     None, Gio.DBusCallFlags.NONE, 3000, None, None)
        except GLib.Error as e:
            print("winshell: notification guard:", e.message, file=sys.stderr, flush=True)
        return False


# ── Desktops (Hyprland workspaces) ──────────────────────────────────────────
# Minimized windows sit in "special:minimized", so they don't tell which
# desktop they belong to: remember the last real desktop of every window
# (kept in a file, so it survives a winshell restart).
DESKTOPS_FILE = os.path.join(RUNTIME, "winshell-desktops.json")
try:
    with open(DESKTOPS_FILE) as _f:
        WINDOW_DESKTOP = {k: int(v) for k, v in json.load(_f).items()}
except (OSError, ValueError, TypeError, AttributeError):
    WINDOW_DESKTOP = {}


# Like Windows a desktop spans all screens: desktop d is workspace
# slot * DESK_STRIDE + d on every screen (hyprland.lua).
DESK_STRIDE = 100
HYPR_DESKTOPS = os.path.join(RUNTIME, "hypr-desktops")


def desk(ws_id):
    """Workspace id -> desktop number (None for special / unknown)."""
    if not isinstance(ws_id, int) or ws_id <= 0:
        return None
    return ws_id % DESK_STRIDE or None


def desktop_list():
    """The desktops there are (hyprland.lua keeps the list), ascending."""
    try:
        with open(HYPR_DESKTOPS) as f:
            ids = sorted({int(x) for x in f.read().split() if x.isdigit() and int(x) > 0})
    except OSError:
        ids = []
    if not ids:
        ids = sorted({desk(w.get("id")) for w in hypr_json("workspaces") or []} - {None})
    return ids


def desktop_of(c):
    """The desktop a window belongs to – for a minimized window the one it
    was on. None if unknown."""
    ws = c.get("workspace") or {}
    d = desk(ws.get("id")) if not ws.get("name", "").startswith("special:") else None
    if d is not None:
        if WINDOW_DESKTOP.get(c["address"]) != d:
            WINDOW_DESKTOP[c["address"]] = d
            try:
                with open(DESKTOPS_FILE, "w") as f:
                    json.dump(WINDOW_DESKTOP, f)
            except OSError:
                pass
        return d
    return WINDOW_DESKTOP.get(c["address"])


def current_desktop():
    return desk((hypr_json("activeworkspace") or {}).get("id"))


class FixedPicture(Gtk.Picture):
    """A picture that always asks for the same size, whatever the texture."""
    def __init__(self, w, h, **kw):
        super().__init__(**kw)
        self.fixed = (w, h)

    def do_measure(self, orientation, for_size):
        n = self.fixed[0] if orientation == Gtk.Orientation.HORIZONTAL else self.fixed[1]
        return n, n, -1, -1


# ── Task view (Win + Tab) ──────────────────────────────────────────────────
class TaskView:
    """Win + Tab like Windows 11: the windows of this desktop big in the
    middle, every desktop in a row at the bottom, "+ New desktop" last.
    Pointing at a desktop shows its windows; a click on a window or a desktop
    goes there. Esc, Win + Tab again or a click on the background close it."""
    CARD_W, CARD_H = 288, 180
    DESK_W, DESK_H = 176, 99
    MAX_DESKTOPS = 6            # as in hyprland.lua: no "New desktop" beyond this

    def __init__(self, app, shell):
        self.shell = shell
        self.visible = False
        self.items, self.cards, self.sel = [], [], 0
        self.shown_ws = None
        self.wall = (None, None)                # (path, texture)
        self.win = layer_window(app, "winshell-taskview", LS.Layer.OVERLAY,
                                anchors=(LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT),
                                exclusive=-1)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class("taskview")
        self.root = root
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                column_spacing=20, row_spacing=20,
                                halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.empty = label("No open windows on this desktop", "tv-empty", xalign=0.5)
        middle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER,
                         halign=Gtk.Align.CENTER)
        middle.append(self.flow)
        middle.append(self.empty)
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         vscrollbar_policy=Gtk.PolicyType.AUTOMATIC, vexpand=True)
        self.scroll.set_child(middle)
        root.append(self.scroll)
        self.desks = Gtk.Box(spacing=14, halign=Gtk.Align.CENTER)
        self.desks.add_css_class("tv-desks")
        root.append(self.desks)
        self.win.set_child(root)
        # a click that no card took: on the background – close
        back = Gtk.GestureClick()
        back.connect("released", lambda *_: self.visible and self.hide())
        root.add_controller(back)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.win.add_controller(keys)

    # ── open / close ──
    def toggle(self):
        self.hide() if self.visible else self.show()

    def show(self):
        self.shell.close_flyouts()
        if self.shell.switcher.visible:
            self.shell.switcher.hide()
        self.visible = True
        self.shell.switcher.on_thumb = self.on_thumb
        self.cur = current_desktop()
        self.build_desks()
        self.show_desktop(self.cur, select_active=True)
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.EXCLUSIVE)
        # Windows-like opening: Hyprland fades the whole view in (layer rule
        # in hyprland.lua), the windows grow into place and the desktops rise
        # from the bottom – CSS transitions, started one frame after showing
        self.root.remove_css_class("open")
        self.win.present()
        GLib.timeout_add(30, lambda: self.visible and self.root.add_css_class("open") and False)
        # fresh previews of this desktop's windows (only visible windows can
        # be captured; the others keep their last picture)
        for c in self.items:
            self.shell.switcher.capture(c, update=False)

    def hide(self):
        self.visible = False
        self.shell.switcher.on_thumb = None
        self.win.set_visible(False)
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.NONE)

    def close_then(self, fn):
        """Give the keyboard back first (Hyprland refocuses right then), then
        act, then hide – like the Alt+Tab switcher."""
        self.visible = False
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.NONE)

        def later():
            fn()
            GLib.timeout_add(40, lambda: self.hide() and False)
            return False
        GLib.timeout_add(20, later)

    # ── data ──
    @staticmethod
    def windows():
        wins = []
        for c in hypr_json("clients") or []:
            name = (c.get("workspace") or {}).get("name", "")
            if not c.get("mapped") or c.get("hidden") or not c.get("address"):
                continue
            if name.startswith("special:") and name != "special:minimized":
                continue
            wins.append(c)
        wins.sort(key=lambda c: c.get("focusHistoryID", 99))
        return wins

    def desktop_ids(self):
        ids = set(desktop_list())
        if self.cur:
            ids.add(self.cur)
        return sorted(ids)

    def wallpaper(self):
        try:
            with open(os.path.expanduser("~/.config/hypr/.current_wallpaper")) as f:
                path = f.read().strip()
        except OSError:
            return None
        if re.search(r"\.(mp4|webm|mkv|gif)$", path, re.I):
            path = os.path.splitext(path)[0] + ".poster.jpg"
        if self.wall[0] == path:
            return self.wall[1]
        tex = None
        try:
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, self.DESK_W * 2, -1, True)
            tex = Gdk.Texture.new_for_pixbuf(pb)
        except Exception:
            pass
        self.wall = (path, tex)
        return tex

    # ── windows in the middle ──
    def show_desktop(self, ws, select_active=False):
        self.shown_ws = ws
        wins = self.windows()
        self.items = [c for c in wins if desktop_of(c) == ws or
                      (desktop_of(c) is None and ws == self.cur)]
        self.cards = []
        clear(self.flow)
        mon_w = self.shell.switcher.monitor_size()[0]
        per_line = max(1, min(len(self.items) or 1, int((mon_w * 0.9) // (self.CARD_W + 44))))
        self.flow.set_min_children_per_line(per_line)
        self.flow.set_max_children_per_line(per_line)
        for i, c in enumerate(self.items):
            self.flow.append(self.make_card(i, c))
        self.flow.set_visible(bool(self.items))
        self.empty.set_visible(not self.items)
        self.sel = 0
        self.update_selection(select_active)
        for d in self.desk_cards:
            (d.add_css_class if d.ws == ws else d.remove_css_class)("shown")

    def make_card(self, i, c):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.add_css_class("tv-card")
        head = Gtk.Box(spacing=8)
        img = Gtk.Image.new_from_gicon(self.shell.switcher.icons.get(c["class"]))
        img.set_pixel_size(16)
        head.append(img)
        t = label(c["title"] or c["class"], "card-title")
        t.set_hexpand(True)
        t.set_width_chars(1)
        head.append(t)
        x = Gtk.Button(child=icon("win11-close", 12))
        x.add_css_class("tv-close")
        x.set_tooltip_text("Close")
        x.connect("clicked", lambda *_: self.close_window(c))
        head.append(x)
        head.set_size_request(self.CARD_W, -1)
        card.append(head)
        stack = Gtk.Stack()
        stack.add_css_class("thumb")
        big = Gtk.Image.new_from_gicon(self.shell.switcher.icons.get(c["class"]))
        big.set_pixel_size(56)
        stack.add_named(big, "icon")
        pic = FixedPicture(self.CARD_W, self.CARD_H, content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
        stack.add_named(pic, "pic")
        tex = self.shell.switcher.thumbs.get(c["address"])
        if tex:
            pic.set_paintable(tex)
            stack.set_visible_child_name("pic")
        else:
            stack.set_size_request(self.CARD_W, self.CARD_H)
        card.append(stack)
        card.pic, card.stack, card.addr = pic, stack, c["address"]
        click = Gtk.GestureClick()
        click.connect("released", lambda g, *_a, i=i: (g.set_state(Gtk.EventSequenceState.CLAIMED),
                                                       self.open(i)))
        stack.add_controller(click)
        self.cards.append(card)
        return card

    def on_thumb(self, addr, tex):
        for card in self.cards:
            if card.addr == addr:
                card.pic.set_paintable(tex)
                card.stack.set_visible_child_name("pic")

    def update_selection(self, select_active=False):
        if select_active:
            self.sel = 0                        # most recent window first
        for i, card in enumerate(self.cards):
            (card.add_css_class if i == self.sel else card.remove_css_class)("selected")

    def open(self, i):
        if not (0 <= i < len(self.items)):
            return
        c = self.items[i]
        ws = self.shown_ws
        minimized = (c.get("workspace") or {}).get("name") == "special:minimized"

        def go():
            if minimized and ws and ws != self.cur:
                # restored onto the desktop it belongs to, not this one
                hypr_eval(f"DesktopShow({ws})")
                GLib.timeout_add(60, lambda: self.shell.switcher.focus(c["address"], c["class"], 4) and False)
            else:
                self.shell.switcher.focus(c["address"], c["class"], tries=4)
        self.close_then(go)

    def close_window(self, c):
        dispatch(f'hl.dsp.window.close({{ window = "address:{c["address"]}" }})')
        GLib.timeout_add(250, lambda: self.visible and (self.build_desks(),
                                                        self.show_desktop(self.shown_ws)) and False)

    # ── desktops at the bottom ──
    def build_desks(self):
        clear(self.desks)
        self.desk_cards = []
        wins = self.windows()
        ids = self.desktop_ids()
        wall = self.wallpaper()
        for n, ws in enumerate(ids, 1):
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            card.add_css_class("tv-desk")
            card.ws = ws
            if ws == self.cur:
                card.add_css_class("current")
            over = Gtk.Overlay()
            pic = FixedPicture(self.DESK_W, self.DESK_H, content_fit=Gtk.ContentFit.COVER, can_shrink=True)
            pic.add_css_class("tv-desk-pic")
            if wall:
                pic.set_paintable(wall)
            over.set_child(pic)
            row = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            seen = []
            for c in wins:
                if desktop_of(c) == ws and c["class"] not in seen and len(seen) < 5:
                    seen.append(c["class"])
                    im = Gtk.Image.new_from_gicon(self.shell.switcher.icons.get(c["class"]))
                    im.set_pixel_size(28)
                    row.append(im)
            over.add_overlay(row)
            card.append(over)
            foot = Gtk.Box(spacing=4)
            name = label(f"Desktop {n}", "tv-desk-name")
            name.set_hexpand(True)
            foot.append(name)
            if len(ids) > 1:
                x = Gtk.Button(child=icon("win11-close", 10))
                x.add_css_class("tv-close")
                x.set_tooltip_text("Close desktop")
                x.connect("clicked", lambda *_a, ws=ws: self.close_desktop(ws))
                foot.append(x)
            else:
                foot.set_size_request(-1, 24)   # same height as with the X
            card.append(foot)
            self.hook_desk(card, lambda ws=ws: self.go_desktop(ws))
            self.desks.append(card)
            self.desk_cards.append(card)
        if len(ids) >= self.MAX_DESKTOPS:
            return                              # the limit: no "New desktop" tile
        # "New desktop": a tile like a desktop, a "+" in the middle
        new = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        new.add_css_class("tv-desk")
        new.add_css_class("tv-new")
        tile = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        tile.set_size_request(self.DESK_W, self.DESK_H)
        tile.add_css_class("tv-new-tile")
        plus = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=True)
        plus.add_css_class("tv-plus")
        plus.append(icon("win11-add", 20))
        tile.append(plus)
        new.append(tile)
        foot = Gtk.Box()
        foot.set_size_request(-1, 24)
        foot.append(label("New desktop", "tv-desk-name"))
        new.append(foot)
        new.ws = None
        self.hook_desk(new, self.new_desktop, hover=False)
        self.desks.append(new)

    def hook_desk(self, card, on_click, hover=True):
        click = Gtk.GestureClick()
        click.connect("released", lambda g, *_: (g.set_state(Gtk.EventSequenceState.CLAIMED), on_click()))
        card.add_controller(click)
        if hover:
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda *_: self.visible and self.shown_ws != card.ws
                           and self.show_desktop(card.ws))
            card.add_controller(motion)

    def go_desktop(self, ws):
        if ws == self.cur:
            self.close_then(lambda: None)
        else:
            self.close_then(lambda: hypr_eval(f"DesktopShow({ws})"))

    def new_desktop(self):
        """A new, empty desktop after the last one (hyprland.lua DesktopAdd);
        you stay where you are and the view stays open, like Windows."""
        hypr_eval("DesktopAdd(false)")

        def again():
            if self.visible:
                self.build_desks()
                for d in self.desk_cards:
                    (d.add_css_class if d.ws == self.shown_ws else d.remove_css_class)("shown")
            return False
        GLib.timeout_add(120, again)

    def close_desktop(self, ws):
        """Like Windows: its windows move to the desktop on the left (or the
        right one, for the first)."""
        ids = self.desktop_ids()
        if ws not in ids or len(ids) < 2:
            return
        i = ids.index(ws)
        target = ids[i - 1] if i > 0 else ids[1]
        for c in self.windows():             # minimized ones: winshell keeps their desktop
            if desktop_of(c) == ws and (c.get("workspace") or {}).get("name") == "special:minimized":
                WINDOW_DESKTOP[c["address"]] = target
        hypr_eval(f"DesktopClose({ws})")       # moves the others, drops the desktop

        def again():
            if self.visible:
                self.cur = current_desktop()
                self.build_desks()
                self.show_desktop(self.cur)
            return False
        GLib.timeout_add(150, again)

    # ── keyboard ──
    def on_key(self, _c, keyval, _code, state):
        n = len(self.cards)
        if keyval == Gdk.KEY_Escape:
            self.hide()
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            if n:
                self.open(self.sel)
            else:
                self.go_desktop(self.shown_ws)
        elif n and keyval in (Gdk.KEY_Right, Gdk.KEY_Tab, Gdk.KEY_Down):
            per = self.flow.get_max_children_per_line()
            self.sel = min(n - 1, self.sel + (per if keyval == Gdk.KEY_Down else 1))
            self.update_selection()
        elif n and keyval in (Gdk.KEY_Left, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_Up):
            per = self.flow.get_max_children_per_line()
            self.sel = max(0, self.sel - (per if keyval == Gdk.KEY_Up else 1))
            self.update_selection()
        return True


# ── Desktop switch: the wallpaper slides along ─────────────────────────────
class DesktopSlide(Gtk.Widget):
    """Hyprland slides only the windows when you change desktops; the
    wallpaper is its own layer and stands still – between two empty desktops
    nothing moved at all. Like Windows, the wallpaper slides along: this
    layer lies right above the wallpaper (below every window) and moves the
    old picture out and the new one in – in step with Hyprland's slide: same
    curve ("winOut"), same length, same distance, timed from the moment
    Hyprland reports the switch. A "Desktop 2" pill (the OSD) says where you are.
    The layer stays mapped (empty and click-through), so nothing has to
    appear first when a switch starts."""
    DURATION = 0.4              # s: Hyprland's "workspaces" animation, speed 4 (× 100 ms)
    CURVE = (0.1, 0.9, 0.1, 1.0)    # its bezier "winOut" (hyprland.lua)
    SEAM = 28                   # px of shadow on the edge between the two desktops
    LEAD = 0.025                # s ahead: the switch reaches us a moment late and our
                                # picture shows a frame later – measured ~15–30 ms behind

    def __init__(self, app, shell):
        super().__init__()
        self.shell = shell
        self.tex = None
        self.tex_path = None
        self.dir = 1
        self.t0 = None              # monotonic s when Hyprland switched; None: idle
        self.tick = 0
        self.prev = current_desktop()
        self.win = layer_window(app, "winshell-deskslide", LS.Layer.BOTTOM,
                                anchors=(LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT),
                                exclusive=-1)
        self.win.add_css_class("deskslide-window")
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.win.set_child(self)
        self.win.connect("realize", lambda w: self.passthrough())
        self.win.present()
        GLib.idle_add(lambda: self.wallpaper() and False)      # decoded before the first switch

    def passthrough(self):
        import cairo
        surf = self.win.get_surface()
        if surf:
            surf.set_input_region(cairo.Region())

    def wallpaper(self):
        try:
            with open(os.path.expanduser("~/.config/hypr/.current_wallpaper")) as f:
                path = f.read().strip()
        except OSError:
            return None
        if re.search(r"\.(mp4|webm|mkv|gif)$", path, re.I):
            path = os.path.splitext(path)[0] + ".poster.jpg"
        if path != self.tex_path:
            try:
                self.tex = Gdk.Texture.new_from_filename(path)
            except GLib.Error:
                self.tex = None
            self.tex_path = path
        return self.tex

    def changed(self, ws, when):
        """workspacev2 from Hyprland (when: monotonic s it arrived)."""
        ws = desk(ws)               # every screen reports its own workspace of the desktop
        if ws is None:
            return False
        prev, self.prev = self.prev, ws
        if not prev or ws == prev or ws <= 0:
            return False
        if time.monotonic() < getattr(self.shell, "screens_busy", 0):
            return False            # screens are being switched: not a desktop change by you
        if self.shell.taskview.visible:
            return False            # the task view switches behind its own picture
        ids = desktop_list()
        name = f"Desktop {ids.index(ws) + 1}" if ws in ids else f"Desktop {ws}"
        self.shell.osd.update(("win11-desktops", None, False), text=name, everywhere=True)
        if not self.wallpaper():
            return False
        self.dir = 1 if ws > prev else -1    # to the right: the new one comes from the right
        self.t0 = when
        self.queue_draw()
        if not self.tick:
            self.tick = self.add_tick_callback(self.on_tick)
        return False

    def on_tick(self, _w, _clock):
        self.queue_draw()
        if self.t0 is None or GLib.get_monotonic_time() / 1e6 - self.t0 + self.LEAD >= self.DURATION:
            self.t0 = None
            self.tick = 0
            self.queue_draw()           # back to empty
            return False
        return True

    @classmethod
    def ease(cls, x):
        """y of the bezier at x (Hyprland's curves are x → y like CSS)."""
        x1, y1, x2, y2 = cls.CURVE
        lo, hi = 0.0, 1.0
        for _ in range(30):             # find t for x by halving
            t = (lo + hi) / 2
            bx = 3 * (1 - t) ** 2 * t * x1 + 3 * (1 - t) * t * t * x2 + t ** 3
            lo, hi = (t, hi) if bx < x else (lo, t)
        t = (lo + hi) / 2
        return 3 * (1 - t) ** 2 * t * y1 + 3 * (1 - t) * t * t * y2 + t ** 3

    def do_snapshot(self, snap):
        w, h = self.get_width(), self.get_height()
        tex = self.tex
        if self.t0 is None or not tex or not w or not h:
            return                      # idle: draws nothing, the wallpaper shows through
        x = min(1.0, max(0.0, (GLib.get_monotonic_time() / 1e6 - self.t0 + self.LEAD) / self.DURATION))
        shift = w * self.ease(x)
        scale = max(w / tex.get_width(), h / tex.get_height())      # "cover", like the wallpaper
        tw, th = tex.get_width() * scale, tex.get_height() * scale
        old_x, new_x = -self.dir * shift, self.dir * (w - shift)
        for x0 in (old_x, new_x):
            snap.push_clip(Graphene.Rect().init(x0, 0, w, h))
            snap.append_texture(tex, Graphene.Rect().init(x0 + (w - tw) / 2, (h - th) / 2, tw, th))
            snap.pop()
        # a soft shadow on the incoming desktop's leading edge marks the seam
        edge = new_x if self.dir > 0 else new_x + w - self.SEAM
        dark, clear_ = Gdk.RGBA(), Gdk.RGBA()
        dark.parse("rgba(0,0,0,0.55)")
        clear_.parse("rgba(0,0,0,0)")
        stops = [Gsk.ColorStop(), Gsk.ColorStop()]
        stops[0].offset, stops[0].color = 0.0, dark
        stops[1].offset, stops[1].color = 1.0, clear_
        rect = Graphene.Rect().init(edge, 0, self.SEAM, h)
        a = Graphene.Point().init(edge if self.dir > 0 else edge + self.SEAM, 0)
        b = Graphene.Point().init(edge + self.SEAM if self.dir > 0 else edge, 0)
        snap.append_linear_gradient(rect, a, b, stops)


# ── Frozen screen while a screenshot region is picked ──────────────────────
class Freeze:
    """screenshot.sh takes a picture of every screen first, then shows it
    here, full screen above everything, while slurp lets you pick the region
    – so a running video (or anything else) holds still until you're done.
    Underneath, everything keeps running.
      freeze <monitor>=<png> ...   show the pictures
      freeze off                   remove them"""
    def __init__(self, app):
        self.app = app
        self.wins = []

    def show(self, pairs):
        self.hide()
        display = Gdk.Display.get_default()
        mons = display.get_monitors()
        by_name = {mons.get_item(i).get_connector(): mons.get_item(i) for i in range(mons.get_n_items())}
        for pair in pairs:
            name, _, path = pair.partition("=")
            mon = by_name.get(name)
            if not mon or not os.path.exists(path):
                continue
            w = layer_window(self.app, "winshell-freeze", LS.Layer.OVERLAY,
                             anchors=(LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT),
                             exclusive=-1)
            LS.set_monitor(w, mon)
            w.add_css_class("freeze-window")
            pic = Gtk.Picture.new_for_filename(path)
            pic.set_content_fit(Gtk.ContentFit.FILL)
            pic.set_can_shrink(True)
            w.set_child(pic)
            w.present()
            self.wins.append(w)
        # it covers the whole screen and takes the clicks: if screenshot.sh
        # were killed before it says "freeze off", it must still go away
        if self.wins:
            gen = self.gen = getattr(self, "gen", 0) + 1
            GLib.timeout_add_seconds(60, lambda: gen == self.gen and self.hide() and False)

    def hide(self):
        for w in self.wins:
            w.destroy()
        self.wins = []


# ── Fingerprint pop-up (sudo, polkit … ask for a finger) ───────────────────
class FingerprintPopup:
    """Like Windows Hello: when something asks for your fingerprint (sudo in
    the terminal, an admin prompt), a small pop-up with the fingerprint shows
    at the bottom in the middle – it pulses while it waits, shakes red when
    the finger isn't recognized and turns into a green check when it is.
    Only while a finger is wanted: fprintd says so on the system bus
    (finger-needed, VerifyStatus). The lock screen has its own."""
    def __init__(self, app):
        self.hide_id = 0
        self.busy = False               # the check or the shake is playing
        self.win = layer_window(app, "winshell-fingerprint", LS.Layer.OVERLAY,
                                anchors=(LS.Edge.BOTTOM,), margins={LS.Edge.BOTTOM: 72})
        box = Gtk.Box(halign=Gtk.Align.CENTER)
        box.add_css_class("fp-pop")
        self.box = box
        self.img = Gtk.Image(icon_name="win11-fingerprint", pixel_size=36)
        self.img.add_css_class("fp")
        box.append(self.img)
        self.win.set_child(box)
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except GLib.Error:
            return
        self.bus = bus
        bus.signal_subscribe("net.reactivated.Fprint", "org.freedesktop.DBus.Properties",
                             "PropertiesChanged", None, "net.reactivated.Fprint.Device",
                             Gio.DBusSignalFlags.NONE, self.on_props)
        bus.signal_subscribe("net.reactivated.Fprint", "net.reactivated.Fprint.Device",
                             "VerifyStatus", None, None, Gio.DBusSignalFlags.NONE, self.on_status)

    LOCKED = os.path.join(RUNTIME, "lockscreen.active")    # lockscreen.py while it locks

    def on_props(self, _c, _s, _p, _i, _sig, params):
        _iface, changed, _inv = params.unpack()
        if "finger-needed" not in changed:
            return
        if os.path.exists(self.LOCKED):
            # the lock screen asks for the finger itself (and shows it): no
            # pop-up – it was still visible for a moment after unlocking
            if self.win.get_visible():
                self.hide()
            return
        if changed["finger-needed"]:
            self.show()
        elif not self.busy:
            self.later_hide(400)

    def on_status(self, _c, _s, _p, _i, _sig, params):
        result, _done = params.unpack()
        if not self.win.get_visible() or os.path.exists(self.LOCKED):
            return
        if result == "verify-match":
            self.state("win11-fingerprint-ok", "ok")
            self.busy = True
            self.later_hide(700)
        elif result == "verify-no-match":
            self.state("win11-fingerprint-fail", "shake")
            self.busy = True

            def back():
                self.busy = False
                if self.win.get_visible() and not self.hide_id:
                    self.state("win11-fingerprint", "waiting")
                return False
            GLib.timeout_add(900, back)

    def state(self, icon_name, css):
        self.img.set_from_icon_name(icon_name)
        for c in ("waiting", "shake", "ok"):
            self.img.remove_css_class(c)
        GLib.timeout_add(16, lambda: self.img.add_css_class(css) and False)

    def show(self):
        if self.hide_id:
            GLib.source_remove(self.hide_id)
            self.hide_id = 0
        self.busy = False
        self.state("win11-fingerprint", "waiting")
        self.box.remove_css_class("shown")
        self.win.present()
        GLib.timeout_add(16, lambda: self.box.add_css_class("shown") and False)

    def later_hide(self, ms):
        if self.hide_id:
            GLib.source_remove(self.hide_id)
        self.hide_id = GLib.timeout_add(ms, self.hide)

    def hide(self):
        self.hide_id = 0
        self.busy = False
        self.box.remove_css_class("shown")          # slides down and fades (CSS)
        GLib.timeout_add(200, lambda: (not self.box.has_css_class("shown")
                                       and self.win.set_visible(False)) and False)
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
        # dicts: addr, tex, a (from rect), b (to rect), alpha0 (alpha at the
        # start – a picture that turns around mid-flight keeps its own)
        self.items = []
        self.t = 0.0
        self.rising = False
        self.fade = 1.0         # whole drawing (fades out once the windows are back)
        self.bar_h = 0          # the taskbar's height: the pictures never cover it

    def rect_of(self, it):
        """Where that picture is right now (x, y, w, h)."""
        t = self.t
        e = 1 - (1 - t) ** 3 if self.rising else t * t * (3 - 2 * t)
        ax, ay, aw, ah = it["a"]
        bx, by, bw, bh = it["b"]
        return (ax + (bx - ax) * e, ay + (by - ay) * e, aw + (bw - aw) * e, ah + (bh - ah) * e)

    def alpha_of(self, it):
        t = self.t
        if self.rising:                                  # out of the taskbar
            a0 = it.get("alpha0", 0.0)
            return a0 + (1.0 - a0) * min(1.0, t / 0.35)
        # into the taskbar: opaque from the first frame – the pictures must
        # cover the real windows (hidden a moment later), or the ones behind
        # show through
        return it.get("alpha0", 1.0) * (1.0 - max(0.0, (t - 0.25) / 0.75) ** 1.5)

    def do_snapshot(self, snap):
        # the final fade-away applies to all pictures together, as one
        # surface: fading each one by itself let the pictures of the windows
        # behind shine through the front one (Discord through Brave)
        snap.push_opacity(self.fade)
        # the taskbar stays on top: the windows slip under it into their
        # icon (and come out from under it), they never cover it
        snap.push_clip(Graphene.Rect().init(0, 0, self.get_width(), max(0, self.get_height() - self.bar_h)))
        for it in self.items:
            x, y, w, h = self.rect_of(it)
            full_w = max(it["a"][2], it["b"][2], 1.0)
            rect = Graphene.Rect().init(x, y, w, h)        # the window's content only
            radius = 8 * min(1.0, w / full_w) + 2
            snap.push_opacity(max(0.0, min(1.0, self.alpha_of(it))))
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
        snap.pop()
        snap.pop()


class DesktopAnim:
    """Like Windows: on "show desktop" every window shrinks into its taskbar
    button while it fades, and grows back out of it when it returns.
    Hyprland can't scale windows, so pictures of them do it on a full-screen
    layer above everything (the windows themselves are hidden meanwhile).
    A new run while one is still going (Win + D pressed again mid-way) turns
    the pictures around from where they are. The taskbar icon dips when its
    window lands in it and hops when the window leaves it, like Windows 11.

    hyprland.lua:  desk out <callback> <gen> <window>…   → callback(gen) once shown
                   desk in  <callback> <gen> <window>…   → callback(gen) at the end
                   desk stop
    <window> = address|x|y|width|height|title bar height|class"""
    OUT = 0.24                  # seconds
    IN = 0.28

    def __init__(self, app, shell):
        self.app = app
        self.shell = shell
        # one full-screen layer per screen: every window flies into the
        # taskbar of its own screen (Win + D takes all screens, like Windows)
        self.layers = {}            # connector -> layer (win, canvas, mon)
        self.active = []            # the layers of the current run
        self.tick = 0
        self.tick_canvas = None
        self.fade_tick = 0
        self.fade_canvas = None
        self.t0 = 0
        self.dur = self.OUT
        self.done = None
        self.gen = 0
        self.serial = 0             # counts runs: late timers of an old run do nothing
        self.wait = 0               # frames until a just-shown layer is on screen
        self.hide_now = None        # "out": hides the real windows once the pictures cover them

    @property
    def win(self):
        main = self.layers.get(getattr(self.shell, "primary", None))
        return main.win if main else None

    def layer(self, connector):
        mon = gdk_monitor(connector)
        if not mon:
            return None
        ly = self.layers.get(connector)
        if ly and ly.mon is not mon:            # off and on again: a new screen for GTK
            ly.win.destroy()
            ly = None
        if not ly:
            ly = types.SimpleNamespace(connector=connector, mon=mon)
            ly.win = layer_window(self.app, "winshell-desk", LS.Layer.OVERLAY,
                                  anchors=(LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT),
                                  exclusive=-1)
            LS.set_monitor(ly.win, mon)
            ly.win.add_css_class("snap-window")         # transparent
            ly.canvas = DeskCanvas()
            ly.canvas.set_hexpand(True)
            ly.canvas.set_vexpand(True)
            ly.win.set_child(ly.canvas)
            ly.win.connect("realize", lambda *_, l=ly: self.passthrough(l))
            self.layers[connector] = ly
        return ly

    @staticmethod
    def passthrough(ly):
        import cairo
        surf = ly.win.get_surface()
        if surf:
            surf.set_input_region(cairo.Region())

    def bar_of(self, connector):
        if connector == getattr(self.shell, "primary", None):
            return self.shell.taskbar
        return getattr(self.shell, "secondaries", {}).get(connector) or self.shell.taskbar

    def button(self, cls, connector=None):
        tb = self.shell.taskbar
        bar = self.bar_of(connector) if connector else tb
        if tb and bar:
            b = bar.buttons.get(tb.key_for({"class": cls}))
            if b and b.get_mapped():
                return b
        return None

    def target(self, cls, connector, mon_w, mon_h):
        """The taskbar button of that app on that screen (screen coordinates)."""
        bar, b = self.bar_of(connector), self.button(cls, connector)
        if b and bar:
            ok_, r = b.compute_bounds(bar.win)
            if ok_:
                top = mon_h - bar.win.get_height()
                cx, cy = r.get_x() + r.get_width() / 2, top + r.get_y() + r.get_height() / 2
                return (cx - 20, cy - 14, 40, 28)
        return (mon_w / 2 - 20, mon_h - 34, 40, 28)

    def bounce(self, classes, kind, delay):
        """The taskbar icons of these apps dip ("dip": a window lands in
        them) or hop ("hop": a window comes out) – CSS animation, style.css.
        Only on the taskbar of the screen the window is (was) on."""
        buttons = {b for con, c in classes for b in [self.button(c, con)] if b}

        def start():
            for b in buttons:
                b.remove_css_class("tb-dip")
                b.remove_css_class("tb-hop")
            # a frame later, so the same animation can start again
            GLib.timeout_add(20, lambda: [b.add_css_class("tb-" + kind) for b in buttons] and False)
            GLib.timeout_add(480, lambda: [b.remove_css_class("tb-" + kind) for b in buttons] and False)
            return False
        GLib.timeout_add(max(1, int(delay * 1000)), start)

    def parse(self, tokens):
        """Items per screen: {connector: [item]} – the screen the middle of
        the window is on, coordinates relative to that screen."""
        mons = [m for m in hypr_json("monitors") or [] if not m.get("disabled")]
        thumbs = self.shell.switcher.thumbs if self.shell.switcher else {}
        out = {}
        for tok in tokens:
            p = tok.split("|", 6)
            if len(p) < 7:
                continue
            addr, x, y, w, h, bar, cls = p[0], *map(float, p[1:6]), p[6]
            cx, cy = x + w / 2, y + h / 2
            mon = None
            for m in mons:
                sc = m.get("scale") or 1
                mw, mh = m["width"] / sc, m["height"] / sc
                if m["x"] <= cx < m["x"] + mw and m["y"] <= cy < m["y"] + mh:
                    mon = (m, mw, mh)
                    break
            if not mon:
                m = next((m for m in mons if m["name"] == getattr(self.shell, "primary", None)),
                         mons[0] if mons else None)
                if not m:
                    continue
                sc = m.get("scale") or 1
                mon = (m, m["width"] / sc, m["height"] / sc)
            m, mw, mh = mon
            out.setdefault(m["name"], []).append(
                {"addr": addr, "cls": cls, "tex": thumbs.get(addr), "bar": bar,
                 "win": (x - m["x"], y - m["y"], w, h), "icon": self.target(cls, m["name"], mw, mh)})
        return out

    def run(self, kind, callback, gen, tokens):
        self.gen = gen
        self.serial += 1
        self.shell.switcher.pause_thumbs(self.OUT + self.IN + 0.4)
        groups = self.parse(tokens)
        layers = {}
        for con, items in groups.items():
            ly = self.layer(con)
            if ly:
                layers[con] = (ly, items)
        if not layers:
            hypr_eval(f"{callback}({gen})")
            return
        rising = kind == "in"
        # pictures still on screen (a run in progress, or just landed): the
        # same windows go on from where they are instead of starting over
        now = {}
        for ly in self.active:
            for it in ly.canvas.items:
                now[it["addr"]] = (ly.connector, ly.canvas.rect_of(it),
                                   ly.canvas.alpha_of(it) * ly.canvas.fade)
        self.stop_ticks()
        for ly in self.active:                  # screens without windows this time
            if ly.connector not in layers:
                ly.canvas.items = []
                ly.win.set_visible(False)
        self.active = []
        classes = set()
        fresh = False               # a layer just shown: on screen only a few frames later
        for con, (ly, items) in layers.items():
            for it in items:
                it["a"], it["b"] = (it["icon"], it["win"]) if rising else (it["win"], it["icon"])
                old = now.get(it["addr"])
                if old and old[0] == con:
                    it["a"], it["alpha0"] = old[1], old[2]
                classes.add((con, it["cls"]))
            c = ly.canvas
            c.items, c.rising, c.t, c.fade = items, rising, 0.0, 1.0
            bar = self.bar_of(con)
            c.bar_h = bar.win.get_height() if bar and bar.win.get_mapped() else 0
            if not ly.win.get_visible():
                fresh = True
            ly.win.present()
            self.passthrough(ly)
            c.queue_draw()
            self.active.append(ly)
        self.dur = self.IN if rising else self.OUT
        self.done = None
        # the clock starts once a freshly shown layer is really on screen
        # (see slide_in) – else the first part of the flight was skipped
        self.wait = SLIDE_WAIT if fresh else 0
        if not rising:
            # the real windows are hidden in the frame the pictures cover
            # them (then the pictures fly): earlier, the desktop flashed
            # through; later, the big window stood behind the shrinking
            # picture (small and big at the same time). No fresh screenshots
            # first – they made it feel slow; the Alt+Tab previews do.
            self.hide_now = lambda: hypr_eval(f"{callback}({gen})")
            # together with the windows: the icon goes down as they fly
            # into it, in step, and back up once they have landed (tb-dip)
            # (40 ms early: the class goes on a frame later and GTK starts
            # the animation with the next frame – it lagged behind a little)
            self.bounce(classes, "dip", max(0.001, self.wait / 60 - 0.04))
        else:
            self.hide_now = None
            self.done = lambda: hypr_eval(f"{callback}({gen})")
            self.bounce(classes, "hop", self.wait / 60)     # as they take off
        self.t0 = 0
        self.tick_canvas = self.active[0].canvas      # one clock drives all screens
        self.tick = self.tick_canvas.add_tick_callback(self.on_tick)

    def stop_ticks(self):
        if self.tick and self.tick_canvas:
            self.tick_canvas.remove_tick_callback(self.tick)
        if self.fade_tick and self.fade_canvas:
            self.fade_canvas.remove_tick_callback(self.fade_tick)
        self.tick = self.fade_tick = 0

    def on_tick(self, _w, clock):
        now = clock.get_frame_time() / 1e6
        if self.wait > 0:
            self.wait -= 1
            return True
        if self.hide_now:               # pictures on screen: the windows go (see run)
            hide, self.hide_now = self.hide_now, None
            hide()
        if not self.t0:
            self.t0 = now
        t = min(1.0, (now - self.t0) / self.dur)
        for ly in self.active:
            ly.canvas.t = t
            ly.canvas.queue_draw()
        if t < 1.0:
            return True
        self.tick = 0
        if self.done:                   # windows come back right under the pictures
            done, self.done = self.done, None
            done()
            # then the pictures fade away (instead of vanishing at once) –
            # but only once the real windows are fully there: Hyprland fades
            # a window in for ~100 ms when it returns from the hidden
            # workspace; fading the picture during that let the blurred
            # background shine through a little (the window looked a touch
            # brighter for a moment)
            GLib.timeout_add(160, self.fade_away, self.serial)
        else:
            self.close()
        return False

    def fade_away(self, serial):
        if serial != self.serial:
            return False                # another run started meanwhile
        t0 = []

        def step(_w, clock):
            now = clock.get_frame_time() / 1e6
            if not t0:
                t0.append(now)
            fade = max(0.0, 1.0 - (now - t0[0]) / 0.12)
            for ly in self.active:
                ly.canvas.fade = fade
                ly.canvas.queue_draw()
            if fade > 0:
                return True
            self.fade_tick = 0
            self.close()
            return False
        if self.active and any(ly.canvas.items for ly in self.active) and not self.fade_tick:
            self.fade_canvas = self.active[0].canvas
            self.fade_tick = self.fade_canvas.add_tick_callback(step)
        return False

    def close(self):
        if self.fade_tick and self.fade_canvas:
            self.fade_canvas.remove_tick_callback(self.fade_tick)
        self.fade_tick = 0
        for ly in self.layers.values():
            ly.canvas.fade = 1.0
            ly.canvas.items = []
            ly.win.set_visible(False)
        self.active = []
        return False

    def stop(self):
        self.done = None
        self.hide_now = None
        self.serial += 1
        if self.tick and self.tick_canvas:
            self.tick_canvas.remove_tick_callback(self.tick)
        self.tick = 0
        self.close()


# ── Clipboard history (Win + V) ─────────────────────────────────────────────
CLIP_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "winshell", "clipboard")
TERMINALS = {"kitty", "io.github.noe1.winterm", "foot", "alacritty", "org.wezfurlong.wezterm", "com.mitchellh.ghostty",
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


class SettingsMenu:
    """Win + I: the settings, like a Windows 11 flyout in the middle of the
    screen – a search field, then one row per setting (icon, name, what it
    is about). Arrow keys or the mouse pick one, Enter or a click opens it;
    the wheel doesn't move the selection. A click outside, Escape or opening
    another flyout (start menu, …) closes it."""
    SCRIPTS = os.path.expanduser("~/.config/hypr/scripts")

    def __init__(self, app, shell):
        self.shell = shell
        self.visible = False
        self.rows = []
        self.sel = 0
        self.win = layer_window(app, "winshell-settings", LS.Layer.OVERLAY,
                                keyboard=LS.KeyboardMode.EXCLUSIVE)
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        panel.add_css_class("settings-menu")
        panel.add_css_class("flyout")
        self.panel = panel
        panel.append(label("Settings", "sm-title"))
        self.search = Gtk.SearchEntry(placeholder_text="Find a setting")
        self.search.set_hexpand(True)
        if hasattr(self.search.props, "search_delay"):
            self.search.props.search_delay = 0
        self.search.connect("search-changed", lambda *_: self.filter())
        self.search.connect("activate", lambda *_: self.open_selected())
        bar = search_box(self.search, "sm-search")
        panel.append(bar)
        self.list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.list.add_css_class("sm-list")
        panel.append(self.list)
        self.empty = label("No settings found", "sm-empty", xalign=0.5)
        panel.append(self.empty)
        self.win.set_child(panel)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.win.add_controller(keys)

    def items(self):
        has = lambda cmd: shutil_which(cmd) is not None
        sh = self.shell
        out = [
            ("win11-wifi-4", "Wi-Fi", "Networks nearby, connect", lambda: sh.qs.show("wifi")),
            ("win11-bluetooth", "Bluetooth", "Pair and manage devices", lambda: spawn("blueman-manager")),
            ("win11-speaker", "Sound", "Volume, output and input devices", lambda: spawn("pavucontrol")),
            ("win11-monitor", "Displays", "Arrangement, scale, main display", lambda: sh.display_settings.show()),
            ("win11-mouse", "Mouse", "Pointer speed, scrolling, buttons", lambda: sh.mouse_settings.show()),
            ("win11-background", "Background", "Wallpaper", lambda: spawn(f"'{self.SCRIPTS}/wallpaper.sh' pick")),
        ]
        if has("nwg-look"):
            out.append(("win11-palette", "Appearance", "Colors, fonts, themes", lambda: spawn("nwg-look")))
        out += [
            ("win11-ethernet", "Network connections", "Ethernet, VPN, advanced settings",
             lambda: spawn("nm-connection-editor")),
            ("win11-activity", "Task Manager", "Running apps and performance",
             lambda: spawn("gnome-system-monitor")),
            ("win11-code", "Edit Hyprland config", "hyprland.lua in your editor",
             lambda: spawn("xdg-open ~/.config/hypr/hyprland.lua")),
        ]
        return out

    def build(self):
        clear(self.list)
        self.rows = []
        for ic, title, sub, fn in self.items():
            row = Gtk.Box(spacing=0)
            row.add_css_class("sm-row")
            pill = Gtk.Box(valign=Gtk.Align.CENTER)       # Windows 11's blue mark of the selected row
            pill.add_css_class("sm-pill")
            row.append(pill)
            img = icon(ic, 20)
            img.add_css_class("sm-icon")
            img.set_valign(Gtk.Align.CENTER)
            row.append(img)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
            text.set_hexpand(True)
            text.append(label(title, "sm-name"))
            text.append(label(sub, "sm-sub"))
            row.append(text)
            chev = icon("win11-chevron-right", 12)
            chev.add_css_class("sm-chevron")
            chev.set_valign(Gtk.Align.CENTER)
            row.append(chev)
            row.fn, row.words = fn, f"{title} {sub}".lower()
            click = Gtk.GestureClick()
            click.connect("released", lambda *_a, r=row: self.open(r))
            row.add_controller(click)
            motion = Gtk.EventControllerMotion()
            motion.connect("enter", lambda *_a, r=row: self.select(r))
            row.add_controller(motion)
            self.list.append(row)
            self.rows.append(row)
        self.filter()

    def visible_rows(self):
        return [r for r in self.rows if r.get_visible()]

    def filter(self):
        terms = self.search.get_text().lower().split()
        for r in self.rows:
            r.set_visible(all(t in r.words for t in terms))
        shown = self.visible_rows()
        self.empty.set_visible(not shown)
        self.sel = 0
        self.mark()

    def select(self, row):
        shown = self.visible_rows()
        if row in shown:
            self.sel = shown.index(row)
            self.mark()

    def mark(self):
        shown = self.visible_rows()
        for i, r in enumerate(shown):
            (r.add_css_class if i == self.sel else r.remove_css_class)("selected")
        for r in self.rows:
            if r not in shown:
                r.remove_css_class("selected")

    def on_key(self, _c, keyval, _code, _state):
        shown = self.visible_rows()
        if keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up) and shown:
            step = 1 if keyval == Gdk.KEY_Down else -1
            self.sel = (self.sel + step) % len(shown)     # past the last: back to the first (and up)
            self.mark()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.open_selected()
            return True
        return False

    def open_selected(self):
        shown = self.visible_rows()
        if shown:
            self.open(shown[min(self.sel, len(shown) - 1)])

    def open(self, row):
        self.hide()
        GLib.timeout_add(60, lambda: (row.fn(), False)[1])     # once the keyboard is given back

    def toggle(self):
        self.hide() if self.visible else self.show()

    def show(self):
        for other in (self.shell.start, self.shell.calendar, self.shell.qs, self.shell.clip):
            if getattr(other, "visible", False):
                other.hide()
        spawn("pkill -x rofi")
        self.search.set_text("")
        self.build()
        self.visible = True
        self.shell.qs.catcher_for("settings")
        slide_in(self.panel)
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.EXCLUSIVE)
        self.win.present()
        self.search.grab_focus()

    def hide(self):
        if not self.visible:
            return
        self.visible = False
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.NONE)
        slide_out(self.panel, self.win)     # glides down a little while fading (.flyout.leaving)
        self.shell.qs.catcher_for(None)


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


# ── Display settings (Win + I → Displays) ──────────────────────────────────
class DisplaySettings:
    """Like Windows' Settings → Display: the screens as numbered rectangles,
    drawn by their real resolution (like Windows – a big screen at 200 %
    doesn't look tiny), with name and resolution; drag them into place (they
    snap together edge to edge). "Identify" shows the numbers on the real
    screens, per screen: scale (100 % … 300 %) and "Make this my main
    display". Apply sets it in Hyprland right away and keeps it in
    ~/.config/hypr/monitors.lua (hyprland.lua loads that file)."""
    W, H = 560, 300
    SNAP = 0.12                     # of the smaller screen's size: closer than this snaps

    def __init__(self, app, shell):
        self.app, self.shell = app, shell
        self.win = None
        self.items = []
        self.sel = 0
        self.drag = None
        self.updating = False
        self.confirming = None          # after Apply, until kept or reverted

    # ── window ──
    def show(self):
        if self.win:
            self.win.present()
            return
        self.load()
        win = Gtk.Window(application=self.app, title="Display settings")
        dialog_titlebar(win, "Settings")
        win.set_default_size(620, 640)
        win.set_resizable(False)            # a dialog: no maximize / full screen (see hyprland.lua)
        win.add_css_class("display-settings")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.add_css_class("ds-body")
        box.append(label("Display", "ds-title"))
        hint = label("Drag the displays to arrange them the way they stand on your desk. "
                     "Win + P switches between extend, duplicate and one screen.", "ds-hint", ellipsize=False)
        hint.set_wrap(True)
        box.append(hint)
        frame = Gtk.Box()
        frame.add_css_class("ds-canvas")
        self.area = Gtk.DrawingArea(content_width=self.W, content_height=self.H, hexpand=True)
        self.area.set_draw_func(self.draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self.on_begin)
        drag.connect("drag-update", self.on_update)
        drag.connect("drag-end", self.on_end)
        self.area.add_controller(drag)
        frame.append(self.area)
        box.append(frame)
        row = Gtk.Box(spacing=8)
        ident = Gtk.Button(label="Identify")
        ident.add_css_class("ds-button")
        ident.connect("clicked", lambda *_: self.identify())
        row.append(ident)
        box.append(row)
        self.info = label("", "ds-info", ellipsize=False)
        box.append(self.info)
        scale_row = Gtk.Box(spacing=12)
        scale_row.append(label("Scale", "ds-info"))
        self.scales = []
        self.scale_dd = Gtk.DropDown.new_from_strings(["100%"])
        self.scale_dd.add_css_class("ds-drop")
        self.scale_dd.connect("notify::selected", self.on_scale)
        scale_row.append(self.scale_dd)
        box.append(scale_row)
        rate_row = Gtk.Box(spacing=12)
        rate_row.append(label("Refresh rate", "ds-info"))
        self.rates = []
        self.rate_dd = Gtk.DropDown.new_from_strings(["60 Hz"])
        self.rate_dd.add_css_class("ds-drop")
        self.rate_dd.connect("notify::selected", self.on_rate)
        rate_row.append(self.rate_dd)
        box.append(rate_row)
        self.main_chk = Gtk.CheckButton(label="Make this my main display")
        self.main_chk.add_css_class("ds-check")
        self.main_chk.connect("toggled", self.on_main)
        box.append(self.main_chk)
        # bottom: "Apply" – or, after applying, Windows' question with a countdown
        self.bottom = Gtk.Stack(vexpand=True, valign=Gtk.Align.END,
                                transition_type=Gtk.StackTransitionType.SLIDE_UP_DOWN,
                                transition_duration=220)       # the question slides in
        actions = Gtk.Box(halign=Gtk.Align.END, spacing=8)
        self.apply_btn = Gtk.Button(label="Apply")
        self.apply_btn.add_css_class("ds-button")
        self.apply_btn.add_css_class("ds-accent")
        self.apply_btn.connect("clicked", lambda *_: self.apply())
        self.apply_btn.set_sensitive(False)
        actions.append(self.apply_btn)
        self.bottom.add_named(actions, "apply")
        conf = Gtk.Box(spacing=10)
        conf.add_css_class("ds-confirm")
        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        txt.append(label("Keep these display settings?", "ds-confirm-title"))
        self.count_lbl = label("", "ds-hint")
        txt.append(self.count_lbl)
        conf.append(txt)
        keep = Gtk.Button(label="Keep changes", valign=Gtk.Align.CENTER)
        keep.add_css_class("ds-button")
        keep.add_css_class("ds-accent")
        keep.add_css_class("ds-keep")       # the countdown runs down inside it (style.css)
        self.keep_btn = keep
        keep.connect("clicked", lambda *_: self.keep())
        conf.append(keep)
        rev = Gtk.Button(label="Revert", valign=Gtk.Align.CENTER)
        rev.add_css_class("ds-button")
        rev.connect("clicked", lambda *_: self.revert())
        conf.append(rev)
        self.bottom.add_named(conf, "confirm")
        box.append(self.bottom)
        win.set_child(box)
        win.connect("close-request", self.closed)
        self.win = win
        self.update_info()
        win.present()

    def closed(self, *_):
        if self.confirming:                 # closed without "Keep changes": back, like Windows
            self.revert()
        self.win = None
        return False

    def load(self):
        mons = displays.monitors()
        self.primary = displays.primary_name(mons)
        self.items = displays.to_real(displays.items_from(mons))
        for m in self.items:
            m["scales"] = displays.valid_scales(m["pw"], m["ph"], m["scale"])
        # how it is now: what "Apply" compares with and "Revert" goes back to
        self.before = {m["name"]: displays.spec(m) for m in self.items}
        self.before_primary = self.primary
        self.orig = self.signature()
        self.sel = next((i for i, m in enumerate(self.items) if m["name"] == self.primary), 0)

    def signature(self):
        return (tuple(sorted((m["name"], m["px"], m["py"], round(m["scale"], 4), round(m["rate"], 3))
                             for m in self.items)), self.primary)

    def changed(self):
        """Apply only when something really differs from how it is now."""
        self.apply_btn.set_sensitive(not self.confirming and self.signature() != self.orig)

    # ── drawing (layout by real pixels → canvas) ──
    def view(self):
        aw, ah = self.area.get_width() or self.W, self.area.get_height() or self.H
        x0 = min(m["px"] for m in self.items)
        y0 = min(m["py"] for m in self.items)
        x1 = max(m["px"] + m["pw"] for m in self.items)
        y1 = max(m["py"] + m["ph"] for m in self.items)
        f = min((aw - 60) / max(1, x1 - x0), (ah - 60) / max(1, y1 - y0))
        ox = (aw - (x1 - x0) * f) / 2 - x0 * f
        oy = (ah - (y1 - y0) * f) / 2 - y0 * f
        return f, ox, oy

    def draw(self, _a, cr, _w, _h):
        if not self.items:
            return
        f, ox, oy = self.view()
        for i, m in enumerate(self.items):
            x, y, w, h = ox + m["px"] * f + 2, oy + m["py"] * f + 2, m["pw"] * f - 4, m["ph"] * f - 4
            r = 6
            cr.new_sub_path()
            cr.arc(x + w - r, y + r, r, -1.5708, 0)
            cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
            cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
            cr.arc(x + r, y + r, r, 3.1416, 4.7124)
            cr.close_path()
            sel = i == self.sel
            cr.set_source_rgba(0.30, 0.76, 1.0, 0.95) if sel else cr.set_source_rgba(1, 1, 1, 0.12)
            cr.fill_preserve()
            cr.set_source_rgba(1, 1, 1, 0 if sel else 0.35)
            cr.set_line_width(1)
            cr.stroke()
            ink = (0, 0, 0, 0.88) if sel else (1, 1, 1, 0.92)
            # the number, and the name and resolution under it
            cr.set_source_rgba(*ink)
            cr.select_font_face("Inter", 0, 1)
            cr.set_font_size(min(44, h * 0.34))
            t = str(i + 1)
            ext = cr.text_extents(t)
            cy = y + h * 0.42
            cr.move_to(x + w / 2 - ext.width / 2 - ext.x_bearing, cy - ext.height / 2 - ext.y_bearing)
            cr.show_text(t)
            cr.select_font_face("Inter", 0, 0)
            for k, line in enumerate((m["label"], f"{m['pw']} × {m['ph']}  ·  {int(round(m['scale'] * 100))}%")):
                size = max(9, min(13, w / 16))
                cr.set_font_size(size)
                e = cr.text_extents(line)
                if e.width > w - 10:                # too long for a small box: shorter
                    line = line[: max(4, int(len(line) * (w - 14) / e.width))] + "…"
                    e = cr.text_extents(line)
                cr.set_source_rgba(ink[0], ink[1], ink[2], 0.8)
                cr.move_to(x + w / 2 - e.width / 2 - e.x_bearing, cy + ext.height / 2 + 14 + k * (size + 4))
                cr.show_text(line)

    def at(self, px, py):
        f, ox, oy = self.view()
        for i, m in enumerate(self.items):
            if ox + m["px"] * f <= px <= ox + (m["px"] + m["pw"]) * f and \
                    oy + m["py"] * f <= py <= oy + (m["py"] + m["ph"]) * f:
                return i
        return None

    # ── dragging ──
    def on_begin(self, _g, px, py):
        i = None if self.confirming else self.at(px, py)
        if i is None:
            self.drag = None
            return
        self.sel = i
        self.drag = (i, self.items[i]["px"], self.items[i]["py"], self.view()[0])
        self.update_info()
        self.area.queue_draw()

    def on_update(self, _g, dx, dy):
        if not self.drag or len(self.items) < 2:
            return
        i, x, y, f = self.drag
        self.items[i]["px"], self.items[i]["py"] = round(x + dx / f), round(y + dy / f)
        self.area.queue_draw()

    def on_end(self, *_):
        if not self.drag or len(self.items) < 2:
            return
        self.snap(self.drag[0])
        self.drag = None
        self.changed()
        self.area.queue_draw()

    def snap(self, i):
        """Put the dragged screen right next to the closest other one, edge
        to edge (no gaps, no overlap – Hyprland needs that), and line it up
        with that screen's top / bottom / left / right / middle when close."""
        m = self.items[i]
        best = None
        for j, o in enumerate(self.items):
            if j == i:
                continue
            th_x = self.SNAP * min(m["pw"], o["pw"])
            th_y = self.SNAP * min(m["ph"], o["ph"])

            def clamp_y(y):
                y = max(o["py"] - m["ph"] + 1, min(o["py"] + o["ph"] - 1, y))
                for a in (o["py"], o["py"] + o["ph"] - m["ph"]):
                    if abs(y - a) < th_y:
                        return a
                return y

            def clamp_x(x):
                x = max(o["px"] - m["pw"] + 1, min(o["px"] + o["pw"] - 1, x))
                for a in (o["px"], o["px"] + o["pw"] - m["pw"], o["px"] + (o["pw"] - m["pw"]) // 2):
                    if abs(x - a) < th_x:
                        return a
                return x
            for cx, cy in ((o["px"] + o["pw"], clamp_y(m["py"])), (o["px"] - m["pw"], clamp_y(m["py"])),
                           (clamp_x(m["px"]), o["py"] + o["ph"]), (clamp_x(m["px"]), o["py"] - m["ph"])):
                d = (cx - m["px"]) ** 2 + (cy - m["py"]) ** 2
                if best is None or d < best[0]:
                    best = (d, cx, cy)
        if best:
            m["px"], m["py"] = best[1], best[2]
        x0 = min(o["px"] for o in self.items)
        y0 = min(o["py"] for o in self.items)
        for o in self.items:
            o["px"] -= x0
            o["py"] -= y0

    # ── per screen: scale, main display ──
    def update_info(self):
        if not self.items:
            return
        m = self.items[self.sel]
        self.updating = True
        self.info_text()
        is_main = m["name"] == self.primary
        self.main_chk.set_active(is_main)
        self.main_chk.set_sensitive(not is_main and len(self.items) > 1 and not self.confirming)
        # only the scales this screen can do (see displays.valid_scales)
        if self.scales != m["scales"]:
            self.scales = m["scales"]
            self.scale_dd.set_model(Gtk.StringList.new([f"{round(v * 100)}%" for v in self.scales]))
        near = min(range(len(self.scales)), key=lambda k: abs(self.scales[k] - m["scale"]))
        self.scale_dd.set_selected(near)
        self.scale_dd.set_sensitive(not self.confirming)
        # the refresh rates it offers at this resolution
        if self.rates != m["rates"]:
            self.rates = m["rates"]
            self.rate_dd.set_model(Gtk.StringList.new([displays.rate_label(r) for r in self.rates]))
        self.rate_dd.set_selected(min(range(len(self.rates)), key=lambda k: abs(self.rates[k] - m["rate"])))
        self.rate_dd.set_sensitive(not self.confirming and len(self.rates) > 1)
        self.updating = False

    def on_rate(self, dd, _p):
        if self.updating or not self.items or dd.get_selected() >= len(self.rates):
            return
        rate = self.rates[dd.get_selected()]
        if rate == self.items[self.sel]["rate"]:
            return
        self.items[self.sel]["rate"] = rate
        self.changed()
        # only the text: update_info() would rebuild this drop-down's list
        # from inside its own "selected" signal – GTK then reported the
        # selection again, and again … winshell hung in that loop
        self.info_text()

    def info_text(self):
        m = self.items[self.sel]
        self.info.set_text(f"Display {self.sel + 1}  ·  {m['label']}  ·  {m['pw']} × {m['ph']}  ·  "
                           f"{displays.rate_label(m['rate'])}  ·  {m['name']}")

    def on_scale(self, dd, _p):
        if self.updating or not self.items or dd.get_selected() >= len(self.scales):
            return
        if self.scales[dd.get_selected()] == self.items[self.sel]["scale"]:
            return
        self.items[self.sel]["scale"] = self.scales[dd.get_selected()]
        self.changed()
        self.area.queue_draw()

    def on_main(self, chk):
        if self.updating:
            return
        if chk.get_active():
            self.primary = self.items[self.sel]["name"]
            self.main_chk.set_sensitive(False)
        self.changed()

    # ── identify: the number big on each real screen ──
    def identify(self):
        wins = []
        for i, m in enumerate(self.items):
            mon = gdk_monitor(m["name"])
            if not mon:
                continue
            w = layer_window(self.app, "winshell-identify", LS.Layer.OVERLAY,
                             anchors=(LS.Edge.BOTTOM, LS.Edge.LEFT),
                             margins={LS.Edge.BOTTOM: 72, LS.Edge.LEFT: 32})
            LS.set_monitor(w, mon)
            w.set_child(label(str(i + 1), "ds-identify", xalign=0.5, ellipsize=False))
            w.present()
            wins.append(w)
        GLib.timeout_add(2500, lambda: [w.destroy() for w in wins] and False)

    # ── apply → "Keep these display settings?" (10 s) → keep or revert ──
    CONFIRM = 10                    # s

    def apply(self):
        if self.confirming or self.signature() == self.orig:
            return
        displays.to_logical(self.items)
        new = {m["name"]: displays.spec(m) for m in self.items}
        # only the screens that really change: setting a screen again that
        # stays as it is made it flicker and Hyprland shuffle the desktops
        todo = [n for n in new if new[n] != self.before.get(n)]
        snap = self.shell.screens_snapshot()
        self.confirming = {"old": {n: self.before[n] for n in todo if n in self.before},
                           "old_primary": self.before_primary, "new": new,
                           "left": self.CONFIRM, "snap": snap, "timer": 0}
        cycled = self.set_screens({n: new[n] for n in todo}, self.before)
        if self.primary != self.before_primary:
            displays.save(primary=self.primary)
        self.settle(snap, 1900 if cycled else 700)
        self.bottom.set_visible_child_name("confirm")
        self.start_countdown()
        self.apply_btn.set_sensitive(False)
        self.update_info()
        self.count_lbl.set_text(f"Reverting in {self.CONFIRM} seconds")

        def tick():
            c = self.confirming
            if not c:
                return False
            c["left"] -= 1
            if c["left"] <= 0:
                c["timer"] = 0
                self.revert()
                return False
            self.count_lbl.set_text(f"Reverting in {c['left']} second{'s' if c['left'] != 1 else ''}")
            return True
        self.confirming["timer"] = GLib.timeout_add(1000, tick)

    def set_screens(self, specs, old):
        """Apply several screens – in an order in which they never lie on
        top of each other (displays.safe_order). True if one was cycled."""
        cycled = False
        for sp in displays.safe_order(list(specs.values())):
            name = re.search(r'output = "([^"]+)"', sp).group(1)
            cycled = self.set_screen(name, sp, old.get(name)) or cycled
        return cycled

    cycling = {}                        # screen -> the settings it gets when it's back on

    @classmethod
    def set_screen(cls, name, spec, old):
        """Apply a screen's settings. When its mode (refresh rate) changes it
        is switched off and on again around it: changed on the fly, the
        screen froze (a still picture) until it was switched off and on.
        Returns True when it was cycled (takes a moment longer)."""
        if name in cls.cycling:             # off for a moment: it comes back with this
            cls.cycling[name] = spec
            return True
        mode = re.search(r'mode = "([^"]*)"', spec)
        old_mode = re.search(r'mode = "([^"]*)"', old or "")
        if old_mode and mode and mode.group(1) != old_mode.group(1):
            if SHELL:
                SHELL.drop_bar(name)        # before the screen goes: no stray taskbar
            hypr_eval(f'hl.monitor({{ output = "{name}", disabled = true }})')
            cls.cycling[name] = spec

            def back():
                hypr_eval(f"hl.monitor({cls.cycling.pop(name, spec)})")
                return False
            GLib.timeout_add(700, back)
            return True
        hypr_eval(f"hl.monitor({spec})")
        return False

    def settle(self, snap, delay=700):
        """After screens changed: desktops back on their screens, taskbars."""
        self.shell.flyout_screen = None

        def after():
            self.shell.restore_screens(snap)
            self.shell.arrange_bars()
            if self.win:
                self.win.present()
            return False
        GLib.timeout_add(delay, after)

    def start_countdown(self):
        """The fill in "Keep changes" empties over the 10 s (CSS animation,
        restarted a frame later so it runs from the start every time)."""
        b = self.keep_btn
        b.remove_css_class("counting")
        GLib.timeout_add(30, lambda: b.add_css_class("counting") and False)

    def preview_confirm(self):
        """Only the question with its countdown, nothing applied (to look at it)."""
        self.show()
        self.bottom.set_visible_child_name("confirm")
        self.count_lbl.set_text(f"Reverting in {self.CONFIRM} seconds")
        self.start_countdown()
        GLib.timeout_add(self.CONFIRM * 1000, lambda: self.win and not self.confirming
                         and self.bottom.set_visible_child_name("apply") and False)

    def end_confirm(self):
        c, self.confirming = self.confirming, None
        if c and c["timer"]:
            GLib.source_remove(c["timer"])

        def reload():
            self.load()                     # how it is now
            if self.win:
                self.bottom.set_visible_child_name("apply")
                self.update_info()
                self.changed()
                self.area.queue_draw()
            return False
        GLib.timeout_add(900, reload)
        return c

    def keep(self):
        """Kept: now it is written down for the next start (monitors.lua)."""
        c = self.end_confirm()
        if not c:
            return
        lines = ["-- written by winshell's Display settings (Win + I → Displays)",
                 "-- where the screens stand; delete this file to go back to automatic"]
        layout = {}
        for name, spec in c["new"].items():
            lines.append(f"hl.monitor({spec})")
            layout[name] = spec.split(",", 1)[1].rstrip(" }").strip()     # mode, position, scale
        try:
            with open(MONITORS_LUA, "w") as f:
                f.write("\n".join(lines) + "\n")
        except OSError as e:
            print("winshell: can't save monitors.lua:", e, file=sys.stderr)
        displays.save(primary=self.primary, layout=layout)
        ncplace.save(output=None)

    def revert(self):
        """Not confirmed in time (or "Revert"): everything as it was."""
        c = self.end_confirm()
        if not c:
            return
        cycled = self.set_screens(c["old"], c["new"])
        if self.primary != c["old_primary"]:
            displays.save(primary=c["old_primary"])
        self.settle(c["snap"], 1900 if cycled else 700)


# ── Mouse settings ──────────────────────────────────────────────────────────
MOUSE_LUA = os.path.expanduser("~/.config/hypr/mouse.lua")
MOUSE_JSON = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
                          "winshell", "mouse.json")


class MouseSettings:
    """Like Windows' Settings → Bluetooth & devices → Mouse / Touchpad:
    primary button, pointer speed (1–20, Windows' 10 is the default), lines
    to scroll at a time (3 by default) and the touchpad's cursor and scroll
    speed. Changes apply right away and are kept in ~/.config/hypr/mouse.lua
    (hyprland.lua loads it; per machine, not in the repo)."""
    DEFAULTS = {"right": False, "speed": 10, "lines": 3, "tp_speed": 5, "tp_scroll": 5}

    def __init__(self, app, shell):
        self.app, self.shell = app, shell
        self.win = None
        self.timer = 0
        self.cfg = dict(self.DEFAULTS)
        try:
            with open(MOUSE_JSON) as f:
                self.cfg.update({k: v for k, v in json.load(f).items() if k in self.DEFAULTS})
        except (OSError, ValueError, AttributeError):
            pass

    # Windows' scales → Hyprland's numbers (10 / 3 / 5 / 5 = how it was set up)
    @staticmethod
    def sensitivity(v):             # pointer speed 1..20 → libinput -1..1 (10 → -0.3)
        return round(-0.3 - (10 - v) * 0.7 / 9 if v <= 10 else -0.3 + (v - 10) * 0.1, 3)

    @staticmethod
    def tp_sensitivity(v):          # touchpad cursor speed 1..10 → -0.6..0.75 (5 → 0)
        return round((v - 5) * 0.15, 3)

    @staticmethod
    def touchpads():
        mice = (hypr_json("devices") or {}).get("mice", [])
        return [m["name"] for m in mice if "touchpad" in m.get("name", "").lower()]

    def lua(self):
        c = self.cfg
        out = [f'hl.config({{ input = {{ left_handed = {"true" if c["right"] else "false"}, '
               f'sensitivity = {self.sensitivity(c["speed"])}, '
               f'scroll_factor = {round(c["lines"] / 3, 3)}, '
               f'touchpad = {{ scroll_factor = {round(c["tp_scroll"] * 0.14, 3)} }} }} }})']
        for name in self.touchpads():
            out.append(f'hl.device({{ name = "{name}", sensitivity = {self.tp_sensitivity(c["tp_speed"])} }})')
        return out

    def apply(self):
        self.timer = 0
        lines = self.lua()
        for ln in lines:
            hypr_eval(ln)
        try:
            os.makedirs(os.path.dirname(MOUSE_JSON), exist_ok=True)
            with open(MOUSE_JSON, "w") as f:
                json.dump(self.cfg, f, indent=2)
            with open(MOUSE_LUA + ".tmp", "w") as f:
                f.write("-- written by winshell's Mouse settings (Win + I → Mouse)\n"
                        "-- delete this file to go back to the defaults\n" + "\n".join(lines) + "\n")
            os.replace(MOUSE_LUA + ".tmp", MOUSE_LUA)
        except OSError as e:
            print("winshell: mouse settings:", e, file=sys.stderr, flush=True)
        return False

    def changed(self, key, value):
        if self.cfg.get(key) == value:
            return
        self.cfg[key] = value
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(150, self.apply)     # while dragging: not on every step

    # ── window ──
    def slider(self, box, title, key, lo, hi, note=None):
        box.append(label(title, "ds-info"))
        row = Gtk.Box(spacing=12)
        sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
        sc.set_hexpand(True)
        sc.set_draw_value(False)
        sc.set_round_digits(0)
        sc.set_value(self.cfg[key])
        sc.add_css_class("ms-scale")
        # the mouse wheel / touchpad doesn't move it: scrolling over the window
        # changed the speeds while you scrolled (the scroll speed itself too)
        # and it jumped around; drag or click it instead
        no_wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES,
                                             propagation_phase=Gtk.PropagationPhase.CAPTURE)
        no_wheel.connect("scroll", lambda *_: True)
        sc.add_controller(no_wheel)
        val = label(str(self.cfg[key]), "ds-info")
        val.set_width_chars(3)

        def moved(s):
            v = int(round(s.get_value()))
            val.set_text(str(v))
            self.changed(key, v)
        sc.connect("value-changed", moved)
        row.append(sc)
        row.append(val)
        box.append(row)
        if note:
            box.append(label(note, "ds-hint"))

    def show(self):
        if self.win:
            self.win.present()
            return
        win = Gtk.Window(application=self.app, title="Mouse settings")
        dialog_titlebar(win, "Settings")
        win.set_default_size(520, 470)
        win.set_resizable(False)            # a dialog: no maximize / full screen (see hyprland.lua)
        win.add_css_class("display-settings")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.add_css_class("ds-body")
        box.append(label("Mouse", "ds-title"))
        row = Gtk.Box(spacing=12)
        row.append(label("Primary mouse button", "ds-info"))
        dd = Gtk.DropDown.new_from_strings(["Left", "Right"])
        dd.add_css_class("ds-drop")
        dd.set_halign(Gtk.Align.END)
        dd.set_hexpand(True)
        dd.set_selected(1 if self.cfg["right"] else 0)
        dd.connect("notify::selected", lambda d, _p: self.changed("right", d.get_selected() == 1))
        row.append(dd)
        box.append(row)
        self.slider(box, "Mouse pointer speed", "speed", 1, 20)
        self.slider(box, "Lines to scroll at a time", "lines", 1, 15)
        if self.touchpads():
            t = label("Touchpad", "ds-title")
            t.set_margin_top(12)
            box.append(t)
            self.slider(box, "Cursor speed", "tp_speed", 1, 10)
            self.slider(box, "Scroll speed", "tp_scroll", 1, 10)
        actions = Gtk.Box(halign=Gtk.Align.END, spacing=8, vexpand=True, valign=Gtk.Align.END)
        reset = Gtk.Button(label="Reset to defaults")
        reset.add_css_class("ds-button")

        def do_reset(*_):
            self.cfg = dict(self.DEFAULTS)
            self.apply()
            win.close()
            GLib.timeout_add(50, lambda: self.show() and False)
        reset.connect("clicked", do_reset)
        actions.append(reset)
        box.append(actions)
        win.set_child(box)
        win.connect("close-request", self.closed)
        self.win = win
        win.present()

    def closed(self, *_):
        if self.timer:
            GLib.source_remove(self.timer)
            self.apply()
        self.win = None
        return False


# ── Win + P: project ────────────────────────────────────────────────────────
class ProjectMenu:
    """Win + P like Windows: PC screen only, Duplicate, Extend, Second screen
    only. Arrows or Win + P again move the choice, Enter or a click takes it,
    Escape closes. The arrangement itself is in Display settings."""
    MODES = (("pc", "PC screen only", "win11-project-pc"),
             ("duplicate", "Duplicate", "win11-project-duplicate"),
             ("extend", "Extend", "win11-project-extend"),
             ("second", "Second screen only", "win11-project-second"))

    def __init__(self, app, shell):
        self.shell = shell
        self.visible = False
        self.sel = 0
        self.moved = 0.0
        self.win = layer_window(app, "winshell-project", LS.Layer.OVERLAY,
                                anchors=(LS.Edge.BOTTOM, LS.Edge.RIGHT),
                                margins={LS.Edge.BOTTOM: 12, LS.Edge.RIGHT: 12})
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        panel.add_css_class("qs")
        panel.add_css_class("flyout")
        panel.add_css_class("project")
        self.panel = panel
        panel.append(label("Project", "project-title"))
        grid = Gtk.Grid(column_spacing=12, row_spacing=10, column_homogeneous=True)
        self.tiles = []
        for n, (mode, text, ic) in enumerate(self.MODES):
            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            tile = button(icon(ic, 24), "project-tile", on_click=lambda m=mode: self.choose(m))
            tile.set_size_request(-1, 56)
            outer.append(tile)
            lbl = label(text, "tile-label", xalign=0.5)
            lbl.set_wrap(True)
            lbl.set_lines(2)
            lbl.set_justify(Gtk.Justification.CENTER)
            outer.append(lbl)
            # the whole cell is the target, the text below the tile too
            click = Gtk.GestureClick()
            click.connect("released", lambda g, *_a, m=mode: (g.set_state(Gtk.EventSequenceState.CLAIMED),
                                                              self.choose(m)))
            lbl.add_controller(click)
            # the white frame follows the pointer: Enter then takes the tile
            # the mouse is on (it took the current mode before, whatever the
            # mouse pointed at)
            hover = Gtk.EventControllerMotion()
            hover.connect("enter", lambda *_a, n=n: (setattr(self, "sel", n), self.update()))
            outer.add_controller(hover)
            grid.attach(outer, n % 2, n // 2, 1, 1)
            self.tiles.append(tile)
        panel.append(grid)
        self.win.set_child(panel)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.on_key)
        self.win.add_controller(keys)

    def toggle(self):
        """Win + P: open, or (already open) move to the next choice."""
        if self.visible:
            self.move(1)
            return
        self.shell.close_flyouts()
        mon = gdk_monitor(self.shell.primary)
        if mon:
            LS.set_monitor(self.win, mon)
        mode = displays.project_mode()
        self.sel = next((i for i, m in enumerate(self.MODES) if m[0] == mode), 2)
        self.current = mode
        self.update()
        self.visible = True
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.EXCLUSIVE)
        slide_in(self.panel)
        self.win.present()

    def hide(self):
        self.visible = False
        self.win.set_visible(False)
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.NONE)

    def update(self):
        for i, (mode, _t, _ic) in enumerate(self.MODES):
            t = self.tiles[i]
            (t.add_css_class if mode == self.current else t.remove_css_class)("on")
            (t.add_css_class if i == self.sel else t.remove_css_class)("selected")

    def move(self, d):
        # Win + P while the menu is open can arrive twice (Hyprland's key
        # binding and the key itself): one step, not two – that jumped from
        # "Extend" past "Second screen only" to "PC screen only"
        now = time.monotonic()
        if now - self.moved < 0.12:
            return
        self.moved = now
        self.sel = (self.sel + d) % len(self.MODES)
        self.update()

    def on_key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.hide()
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            print(f"winshell: project key {keyval}", file=sys.stderr, flush=True)
            self.choose(self.MODES[self.sel][0])
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_Down, Gdk.KEY_Tab, Gdk.KEY_p, Gdk.KEY_P):
            self.move(1 if keyval != Gdk.KEY_Down else 2)
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_Up, Gdk.KEY_ISO_Left_Tab):
            self.move(-1 if keyval != Gdk.KEY_Up else -2)
        return True

    def choose(self, mode):
        self.hide()
        specs = displays.project_specs(mode)
        print(f"winshell: project {mode} (sel {self.sel}): {specs}", file=sys.stderr, flush=True)
        if not specs:
            return                          # only one screen, or it is like that already
        displays.save(project=mode)
        snap = self.shell.screens_snapshot()
        mirrored = {m["name"] for m in displays.all_monitors()
                    if (m.get("mirrorOf") or "none") != "none"}
        for sp in displays.safe_order(specs):     # never two screens on top of each other
            name = re.match(r'\{ output = "([^"]+)"', sp).group(1)
            if re.match(r'\{ output = "[^"]+", disabled = true', sp):
                self.shell.drop_bar(name)
            elif name in mirrored and 'mirror = ""' in sp:
                # a duplicated screen that stands on its own again: off and
                # on – just un-mirrored, Hyprland doesn't announce it to the
                # programs again (no taskbar, no windows on it)
                hypr_eval(f'hl.monitor({{ output = "{name}", disabled = true }})')
                GLib.timeout_add(700, lambda sp=sp: hypr_eval(f"hl.monitor({sp})") and False)
                continue
            hypr_eval(f"hl.monitor({sp})")
        self.shell.flyout_screen = None
        GLib.timeout_add(1800 if mirrored else 900,
                         lambda: (self.shell.restore_screens(snap), self.shell.arrange_bars(), False)[-1])


MONITORS_LUA = os.path.expanduser("~/.config/hypr/monitors.lua")


class SecondaryTaskbar:
    """The taskbar on every other screen, like Windows: start, the apps and
    the clock. The tray and Wi-Fi / volume / battery are only on the main
    display's taskbar. Start menu and calendar open on this screen."""

    def __init__(self, app, shell, connector):
        self.shell, self.connector = shell, connector
        self.keys = None
        self.buttons = {}
        self.win = layer_window(app, "winshell-taskbar", LS.Layer.TOP,
                                anchors=(LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT), exclusive=48)
        self.monitor = gdk_monitor(connector)
        LS.set_monitor(self.win, self.monitor)
        # the screen goes away (switched off, unplugged, its mode changes):
        # close this taskbar at once – otherwise it slips onto another screen
        # for a moment (two taskbars on the main display)
        if self.monitor:
            self.monitor.connect("invalidate", lambda *_: self.shell.drop_bar(self.connector))
        self.win.add_css_class("taskbar-window")
        self.win.set_size_request(-1, 48)
        bar = Gtk.CenterBox()
        bar.add_css_class("taskbar")
        center = Gtk.Box()
        center.append(button(StartLogo(26), "tb-start", tooltip="Start",
                             on_click=lambda: (self.shell.flyouts_on(self.connector),
                                               setattr(self.shell, "start_click_at", time.monotonic()),
                                               self.shell.start.toggle())))
        self.app_box = Gtk.Box()
        center.append(self.app_box)
        bar.set_center_widget(center)
        right = Gtk.Box()
        right.add_css_class("tb-right")
        self.clock_lbl = Gtk.Label(justify=Gtk.Justification.RIGHT, xalign=1.0)
        self.clock_lbl.add_css_class("tb-clock-text")
        self.clock_lbl.set_text(self.shell.taskbar.clock_lbl.get_text())
        right.append(button(clock_with_dnd(self), "tb-clock",
                            on_click=lambda: (self.shell.flyouts_on(self.connector),
                                              self.shell.calendar.toggle())))
        right.append(button(Gtk.Box(), "tb-peek", on_click=lambda: spawn(f"{SCRIPTS}/wallpaper.sh next")))
        bar.set_end_widget(right)
        self.win.set_child(bar)
        self.refresh()
        self.win.present()

    def refresh(self):
        """The same apps as the main taskbar (Windows: "all taskbars")."""
        tb = self.shell.taskbar
        keys = list(tb.shown)
        if keys != self.keys:
            clear(self.app_box)
            self.buttons = {k: tb.make_app_button(k) for k in keys}
            for k in keys:
                self.app_box.append(self.buttons[k])
            self.keys = keys
        for k, b in self.buttons.items():
            main = tb.buttons.get(k)
            for c in ("running", "active", "multi"):
                (b.add_css_class if main is not None and main.has_css_class(c) else b.remove_css_class)(c)
            if main is not None:
                b.set_tooltip_text(main.get_tooltip_text())

    def destroy(self):
        self.win.destroy()


class TaskPreview:
    """Like Windows 11: rest the pointer on a taskbar app that has windows
    open and its windows show as pictures side by side above the button –
    with several terminals open, pick the one you want instead of clicking
    the button again and again. A click brings that window up, the X (on
    hover) closes it. The pictures are the Alt+Tab ones (Switcher.capture),
    renewed while the preview is open."""
    W, H = 200, 120
    SHOW_MS, HIDE_MS = 350, 300

    def __init__(self, app, shell):
        self.shell = shell
        # right above the taskbar (its exclusive zone already keeps it clear)
        self.win = layer_window(app, "winshell-preview", LS.Layer.OVERLAY,
                                anchors=(LS.Edge.BOTTOM, LS.Edge.LEFT), margins={LS.Edge.BOTTOM: 8})
        self.box = Gtk.Box(spacing=6)
        self.box.add_css_class("tb-preview")
        self.win.set_child(self.box)
        m = Gtk.EventControllerMotion()
        m.connect("enter", lambda *_: self.cancel_hide())
        m.connect("leave", lambda *_: self.hide_soon())
        self.win.add_controller(m)
        self.key = None
        self.show_id = self.hide_id = self.live_id = self.watch_id = 0
        self.mon = None
        self.cards = {}             # address -> card

    # ── the pointer on the taskbar ──
    def hover(self, key, button, bar):
        self.cancel_hide()
        if self.show_id:
            GLib.source_remove(self.show_id)
            self.show_id = 0
        if self.win.get_visible():
            if key != self.key:     # moving along the taskbar: the next one right away
                self.open(key, button, bar)
            return
        self.show_id = GLib.timeout_add(self.SHOW_MS, lambda: self.open(key, button, bar) and False)

    def unhover(self):
        if self.show_id:
            GLib.source_remove(self.show_id)
            self.show_id = 0
        self.hide_soon()

    def hide_soon(self):
        if not self.hide_id and self.win.get_visible():
            self.hide_id = GLib.timeout_add(self.HIDE_MS, self.hide)

    def cancel_hide(self):
        if self.hide_id:
            GLib.source_remove(self.hide_id)
            self.hide_id = 0

    def hide(self):
        self.hide_id = 0
        if self.show_id:
            GLib.source_remove(self.show_id)
            self.show_id = 0
        if self.live_id:
            GLib.source_remove(self.live_id)
            self.live_id = 0
        if self.watch_id:
            GLib.source_remove(self.watch_id)
            self.watch_id = 0
        self.box.remove_css_class("shown")
        self.win.set_visible(False)
        self.key = None
        return False

    # ── the preview ──
    def open(self, key, button, bar):
        self.show_id = 0
        tb = self.shell.taskbar
        wins = tb.groups.get(key, []) if tb else []
        if not wins or not button.get_mapped():
            self.hide()
            return False
        self.key = key
        sw = self.shell.switcher
        active = (hypr_json("activewindow") or {}).get("address")
        clear(self.box)
        self.cards = {}
        for c in wins:
            self.box.append(self.card(c, sw, c["address"] == active))
        # above the button, on that taskbar's screen
        mon = getattr(bar, "monitor", None) or gdk_monitor(getattr(self.shell, "primary", "") or "")
        if mon:
            LS.set_monitor(self.win, mon)
        self.mon = mon
        mon_w = mon.get_geometry().width if mon else 1920
        width = self.box.measure(Gtk.Orientation.HORIZONTAL, -1)[1]
        ok_, r = button.compute_bounds(bar.win)
        cx = (r.get_x() + r.get_width() / 2) if ok_ else mon_w / 2
        LS.set_margin(self.win, LS.Edge.LEFT, int(max(8, min(mon_w - width - 8, cx - width / 2))))
        self.win.set_default_size(1, 1)
        self.win.present()
        GLib.timeout_add(16, lambda: self.box.add_css_class("shown") and False)
        if not self.live_id:
            self.refresh()
            self.live_id = GLib.timeout_add(1200, self.refresh)
        if not self.watch_id:
            self.watch_id = GLib.timeout_add(250, self.pointer_gone)
        return False

    def pointer_gone(self):
        """The pointer can leave without GTK hearing of it (it jumped – to the
        other screen, a window opened under it): while the preview is open,
        check that it's still over the taskbar or the preview."""
        if not self.win.get_visible() or not self.mon:
            self.watch_id = 0
            return False
        try:
            x, y = (float(v) for v in hypr("cursorpos").split(","))
        except (OSError, ValueError):
            return True
        g = self.mon.get_geometry()
        top = g.y + g.height - 48 - 8 - self.win.get_height() - 4
        if not (g.x <= x < g.x + g.width and top <= y < g.y + g.height):
            self.hide_soon()
        return True

    def card(self, c, sw, is_active):
        addr = c["address"]
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.add_css_class("tbp-card")
        if is_active:
            card.add_css_class("active")
        head = Gtk.Box(spacing=8)
        img = Gtk.Image.new_from_gicon(sw.icons.get(c["class"]))
        img.set_pixel_size(16)
        head.append(img)
        t = label(c.get("title") or c["class"], "tbp-title")
        t.set_hexpand(True)
        t.set_width_chars(1)
        t.set_max_width_chars(1)
        head.append(t)
        x = Gtk.Button(child=icon("win11-close", 12))
        x.add_css_class("tbp-close")
        x.set_tooltip_text("Close window")
        x.connect("clicked", lambda *_: self.close_window(addr))
        head.append(x)
        head.set_size_request(self.W, -1)
        card.append(head)
        stack = Gtk.Stack()
        stack.add_css_class("thumb")
        big = Gtk.Image.new_from_gicon(sw.icons.get(c["class"]))
        big.set_pixel_size(40)
        stack.add_named(big, "icon")
        pic = FixedPicture(self.W, self.H, content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
        stack.add_named(pic, "pic")
        card.append(stack)
        card.client, card.pic, card.stack, card.tex = c, pic, stack, None
        self.set_pic(card, sw.thumbs.get(addr))
        g = Gtk.GestureClick()
        g.connect("released", lambda *_: self.pick(addr))
        card.add_controller(g)
        self.cards[addr] = card
        return card

    @staticmethod
    def set_pic(card, tex):
        if tex is not None and tex is not card.tex:
            card.tex = tex
            card.pic.set_paintable(tex)
            card.stack.set_visible_child_name("pic")

    def refresh(self):
        """New pictures of the windows that are on screen (minimized ones
        keep their last picture), and show the ones that came in."""
        if not self.win.get_visible():
            self.live_id = 0
            return False
        sw = self.shell.switcher
        for addr, card in self.cards.items():
            self.set_pic(card, sw.thumbs.get(addr))
            if not (card.client.get("workspace") or {}).get("name", "").startswith("special:"):
                sw.capture(card.client, update=False)
        GLib.timeout_add(450, self.update_pics)
        return True

    def update_pics(self):
        sw = self.shell.switcher
        for addr, card in self.cards.items():
            self.set_pic(card, sw.thumbs.get(addr))
        return False

    def pick(self, addr):
        self.hide()
        hypr_eval(f'ShowWindow("{addr}")')

    def close_window(self, addr):
        dispatch(f'hl.dsp.window.close({{ window = "address:{addr}" }})')
        card = self.cards.pop(addr, None)
        if card:
            self.box.remove(card)
        if not self.cards:
            self.hide()


class Taskbar:
    """Windows 11 taskbar: start + pinned/running apps in the middle (drag to
    reorder, right click to pin/unpin), tray with overflow flyout, status
    icons, clock and the "show desktop" sliver on the right."""

    # own icons: the terminal like Windows Terminal, winshell's own windows
    # (Display settings) with Windows' settings gear
    ICON_OVERRIDE = {"winterm.desktop": "win11-terminal",
                     "org.kde.dolphin.desktop": "win11-explorer",
                     "class:io.github.noe1.winshell": "win11-settings-app"}
    DEFAULT_PINNED = ["brave-browser.desktop", "winterm.desktop"]

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
        self.start_logo = StartLogo(26)
        self.start_btn = button(self.start_logo, "tb-start", tooltip="Start",
                                on_click=lambda: (self.shell.flyouts_on(None),
                                                  setattr(self.shell, "start_click_at", time.monotonic()),
                                                  self.shell.start.toggle()))
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
        cur = current_desktop()
        groups = {}
        for c in sorted(clients, key=lambda c: c.get("focusHistoryID", 99)):
            ws = (c.get("workspace") or {}).get("name", "")
            if not c.get("mapped") or (ws.startswith("special:") and ws != "special:minimized"):
                continue
            # like Windows: only the windows of the desktop you're on (minimized
            # ones count for the desktop they were on; unknown: shown everywhere)
            if desktop_of(c) not in (cur, None):
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
            # several windows of the app: the button looks stacked, like Windows
            (b.add_css_class if len(wins) > 1 else b.remove_css_class)("multi")
            (b.add_css_class if k == self.active_key else b.remove_css_class)("active")
            app = self.app_of(k)
            # an app with windows shows its preview instead (TaskPreview) –
            # a tooltip on top of it covered the pictures
            b.set_tooltip_text(None if wins else
                               (app.get_display_name() if app else k.split(":", 1)[-1]))
        for other in getattr(self.shell, "secondaries", {}).values():
            other.refresh()

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
        # resting on it: its windows as pictures above it (TaskPreview)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: self.preview_hover(key, b))
        motion.connect("leave", lambda *_: SHELL and SHELL.preview.unhover())
        b.add_controller(motion)
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

    def preview_hover(self, key, b):
        if not SHELL or getattr(self, "idrag", None):
            return
        root = b.get_root()
        bar = next((x for x in [self] + list(SHELL.secondaries.values()) if x.win is root), self)
        if self.groups.get(key):
            SHELL.preview.hover(key, b, bar)
        else:
            SHELL.preview.unhover()             # a pinned app that isn't running

    def on_app_click(self, key):
        if SHELL:
            SHELL.preview.hide()                # like Windows: the preview goes on a click
        wins = self.groups.get(key, [])
        if not wins:
            self.launch(key)
            return
        if key == self.active_key:
            # the app in front: minimize the window in front – also with
            # several windows (it went to the next one before; picking one
            # is what the preview on hover is for)
            hypr_eval(f'MinimizeWindow("{wins[0]["address"]}")')
            return
        hypr_eval(f'ShowWindow("{wins[0]["address"]}")')

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
            # like Windows 11 ("End task"): ends the app's process even when
            # it hangs – every window of that app, on every desktop
            items.append(("End task", lambda: self.end_task(key)))
            items.append(("Close window" if len(wins) == 1 else "Close all windows",
                          lambda: [spawn(f"hyprctl dispatch 'hl.dsp.window.close({{ window = \"address:{w['address']}\" }})'")
                                   for w in wins]))
        popup_menu(anchor, items)

    def end_task(self, key):
        """Ask the app's processes to quit (SIGTERM), and force it (SIGKILL)
        if one is still there 2 s later – it hangs."""
        pids = {c.get("pid") for c in hypr_json("clients") or []
                if c.get("pid", 0) > 1 and self.key_for(c) == key}
        pids.discard(os.getpid())
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

        def force():
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass                    # gone already
            return False
        if pids:
            GLib.timeout_add(2000, force)

    def set_pinned(self, key, on):
        pinned = [k for k in self.cfg["pinned"] if k != key]
        if on:
            pinned.append(key)
        # keep the order the user sees
        self.cfg["pinned"] = [k for k in self.order if k in pinned] + [k for k in pinned if k not in self.order]
        self.save_config()
        self.refresh()

    def set_start_open(self, on):
        """Grey while the menu is open. Opened by a click the logo dips (it
        was pressed in), by the Windows key its squares flicker (StartLogo) – like Windows 11."""
        b = self.start_btn
        for cls in ("clicked", "kbd"):
            b.remove_css_class(cls)
        if on:
            clicked = time.monotonic() - getattr(self.shell, "start_click_at", 0) < 0.4
            b.add_css_class("clicked" if clicked else "kbd")
            b.add_css_class("open")
            if not clicked:
                self.start_logo.flicker()
        else:
            b.remove_css_class("open")

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
        self.quick_btn = button(quick, "tb-quick", on_click=lambda: (self.shell.flyouts_on(None),
                                                                     self.shell.qs.toggle()))
        g = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        g.connect("released", lambda *_: self.shell.qs.show("wifi"))
        self.quick_btn.add_controller(g)
        sc = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL |
                                       Gtk.EventControllerScrollFlags.DISCRETE)
        sc.connect("scroll", self.on_volume_scroll)
        self.quick_btn.add_controller(sc)
        self.net_speed, self.speed_timer, self.speed_last = "", 0, None
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", self.speed_start)
        hover.connect("leave", self.speed_stop)
        self.quick_btn.add_controller(hover)
        right.append(self.quick_btn)
        self.status = {}
        for what in ("net", "vol", "bat"):
            threading.Thread(target=self.watch_status, args=(what,), daemon=True).start()

        self.clock_lbl = Gtk.Label(justify=Gtk.Justification.RIGHT, xalign=1.0)
        self.clock_lbl.add_css_class("tb-clock-text")
        right.append(button(clock_with_dnd(self), "tb-clock", on_click=lambda: (self.shell.flyouts_on(None),
                                                                                 self.shell.calendar.toggle())))
        self.tick()

        right.append(button(Gtk.Box(), "tb-peek", on_click=lambda: spawn(f"{SCRIPTS}/wallpaper.sh next")))
        return right

    def tick(self):
        now = GLib.DateTime.new_now_local()
        self.clock_lbl.set_text(now.format("%H:%M:%S\n%d.%m.%Y"))
        for other in getattr(self.shell, "secondaries", {}).values():
            other.clock_lbl.set_text(self.clock_lbl.get_text())
        GLib.timeout_add(1000 - now.get_microsecond() // 1000 + 5, self.tick)
        return False

    def on_volume_scroll(self, _c, _dx, dy):
        step = "2%+" if dy < 0 else "2%-"
        spawn(f"wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ {step}")
        return True

    STATUS_ICON = {"ethernet": "win11-ethernet", "wifi-off": "win11-wifi-off", "mute": "win11-volume-mute",
                   "wifi-noinet": "win11-wifi-noinet", "no-internet": "win11-no-internet"}

    def watch_status(self, what):
        """Follow status.sh (it prints a line whenever Wi-Fi, volume or the
        battery changes) and show the matching icon."""
        while True:
            try:
                # a process group of its own, ended with winshell: status.sh
                # then ends everything it started (no monitors left behind)
                proc = subprocess.Popen([STATUS, what], stdout=subprocess.PIPE, text=True,
                                        stderr=subprocess.DEVNULL, env=child_env(),
                                        start_new_session=True, preexec_fn=die_with_parent)
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
            m = re.match(r"(wifi|vol|bat)-(\d+)(-charging|-plugged|-low|-crit)?", cls)
            if m:
                kind = {"wifi": "wifi", "vol": "volume", "bat": "battery"}[m.group(1)]
                name = f"win11-{kind}-{m.group(2)}{m.group(3) or ''}"
        img = {"net": self.st_net, "vol": self.st_vol, "bat": self.st_bat}[what]
        img.set_visible(bool(name))
        if name:
            img.set_from_icon_name(name)
        self.status[what] = (data.get("tooltip") or "").replace("\\n", "\n")
        self.update_quick_tooltip()
        return False

    def update_quick_tooltip(self):
        lines = [self.status.get("net"), self.net_speed, self.status.get("vol"), self.status.get("bat")]
        self.quick_btn.set_tooltip_text("\n".join(t for t in lines if t))

    # the link speed in the tooltip ("Link speed: 866 Mbit/s", "1 Gbit/s"):
    # looked up while the pointer is on the Wi-Fi / volume / battery button
    def speed_start(self, *_):
        if not self.speed_timer:
            self.speed_sample()
            self.speed_timer = GLib.timeout_add(3000, self.speed_sample)

    def speed_stop(self, *_):
        if self.speed_timer:
            GLib.source_remove(self.speed_timer)
            self.speed_timer = 0

    @staticmethod
    def fmt_link(mbit):
        if mbit >= 1000:
            g = mbit / 1000
            return f"{g:.0f} Gbit/s" if abs(g - round(g)) < 0.05 else f"{g:.1f} Gbit/s"
        return f"{mbit:.0f} Mbit/s"

    @classmethod
    def link_speed(cls):
        """How fast the connection to the network is ("Link speed" in
        Windows): Ethernet as negotiated (100 Mbit/s, 1 Gbit/s), Wi-Fi the
        rate the adapter sends at right now. None when not connected.
        Ethernet wins when both are up."""
        wifi = None
        for dev in sorted(os.listdir("/sys/class/net")):
            base = f"/sys/class/net/{dev}"
            if not os.path.exists(f"{base}/device"):
                continue                        # lo, VPN tunnels
            try:
                if open(f"{base}/operstate").read().strip() != "up":
                    continue
            except OSError:
                continue
            if os.path.isdir(f"{base}/wireless"):
                wifi = dev
                continue
            try:
                mbit = int(open(f"{base}/speed").read())
            except (OSError, ValueError):
                mbit = -1
            if mbit > 0:
                return cls.fmt_link(mbit)
        if wifi:
            m = re.search(r"tx bitrate:\s*([\d.]+)\s*MBit/s", sh(["iw", "dev", wifi, "link"], timeout=2))
            if m:
                return cls.fmt_link(float(m.group(1)))
        return None

    def speed_sample(self):
        def done(speed):
            self.net_speed = f"Link speed: {speed}" if speed else ""
            self.update_quick_tooltip()
        bg(self.link_speed, done)
        return True

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
        # Holding still near the "^" or the flyout: a new place can move the
        # icons so that the pointer is over the old place again – it went
        # back and forth between flyout and taskbar many times a second.
        # Changing places needs the pointer to have moved since the last
        # change (a few px); until then the icon stays where it is.
        if where != d["target"]:
            sx, sy = d.get("switched_at", (None, None))
            if sx is not None and abs(tx - sx) + abs(ty - sy) < 8:
                where, lx = d["target"], None
            else:
                d["switched_at"] = (tx, ty)
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
        self.notify_guard = NotifyGuard()        # no notification as high as the screen
        self.snap = SnapPreview(self)
        self.freeze = Freeze(self)
        self.fingerprint = FingerprintPopup(self)
        self.taskview = TaskView(self, self)
        self.deskslide = DesktopSlide(self, self)
        self.desk = DesktopAnim(self, self)
        self.clip = ClipboardPanel(self, self)
        self.settings = SettingsMenu(self, self)
        self.shutdown = ShutdownDialog(self, self)
        self.start = StartMenu(self, self)
        self.calendar = CalendarFlyout(self, self)
        self.preview = TaskPreview(self, self)  # hover over a taskbar app: its windows
        self.taskbar = Taskbar(self, self)
        self.secondaries = {}               # connector -> SecondaryTaskbar
        self.primary = ""
        self.flyout_screen = None
        self.arrange_tries = 0
        self.arrange_bars()
        Gdk.Display.get_default().get_monitors().connect("items-changed", self.screens_changed)
        import tray as tray_mod
        self.tray = tray_mod.Tray(lambda: GLib.idle_add(self.taskbar.tray_changed))
        import thispc                       # "This PC" for Dolphin: drive tiles with free space
        self.thispc = thispc.ThisPC()
        self.display_settings = DisplaySettings(self, self)
        self.mouse_settings = MouseSettings(self, self)
        self.project = ProjectMenu(self, self)

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

    # ── several screens ──
    def screens_changed(self, *_):
        """A screen was plugged in or out: taskbars again (debounced – the
        list changes several times while a screen comes up)."""
        if self.arrange_id:
            GLib.source_remove(self.arrange_id)
        self.arrange_id = GLib.timeout_add(800, self.arrange_bars)

    def show_dnd(self, on):
        """Do not disturb on / off: the bell next to the clock on every taskbar."""
        self.dnd_on = on
        for bar in [getattr(self, "taskbar", None)] + list(getattr(self, "secondaries", {}).values()):
            img = getattr(bar, "dnd_img", None) if bar else None
            if img:
                img.set_visible(on)
        return False

    def drop_bar(self, connector):
        """Close a screen's (slim) taskbar now; arrange_bars() makes a new
        one when the screen is back."""
        bar = self.secondaries.pop(connector, None)
        if bar:
            bar.win.set_visible(False)
            bar.destroy()
            # look again shortly: if the screen is (back) on, it gets a new
            # taskbar – otherwise it stayed without one (e.g. after Duplicate → Extend)
            GLib.timeout_add(1500, lambda: self.screens_changed() and False)

    def arrange_bars(self):
        """The full taskbar on the main display, a slim one on every other."""
        self.arrange_id = 0
        mons = displays.monitors()
        if not mons:
            # no screen on any more (e.g. "Second screen only" and that screen
            # was unplugged): the laptop's own screen comes back on
            for m in displays.all_monitors():
                hypr_eval(f"hl.monitor({displays.on_spec(m)})")
            self.arrange_id = GLib.timeout_add(1200, self.arrange_bars)
            return False
        names = [m["name"] for m in mons]
        self.primary = displays.primary_name(mons)
        main = gdk_monitor(self.primary)
        if main:
            for w in (self.taskbar.win, self.taskbar.ov_win, self.osd.win, self.fingerprint.win):
                LS.set_monitor(w, main)
        for n in list(self.secondaries):
            # also when the screen was off and on again meanwhile: it is a new
            # screen for GTK then, and the old taskbar had slipped onto
            # another screen (a second taskbar above the main one)
            if n not in names or n == self.primary or gdk_monitor(n) is not self.secondaries[n].monitor:
                self.secondaries.pop(n).destroy()
        for n in names:
            if n != self.primary and n not in self.secondaries and gdk_monitor(n):
                self.secondaries[n] = SecondaryTaskbar(self, self, n)
        # a screen that just came (back) on may not be known to GTK yet – then
        # it got no taskbar at all: look again in a moment
        if any(not gdk_monitor(n) for n in names) and self.arrange_tries < 8:
            self.arrange_tries += 1
            self.arrange_id = GLib.timeout_add(700, self.arrange_bars)
        else:
            self.arrange_tries = 0
        self.flyouts_on(None)
        self.taskbar.refresh()
        return False

    def screens_snapshot(self):
        """Which desktop is on which screen, which one each screen shows and
        where the focus is – Hyprland shuffles that when a screen changes."""
        mons = hypr_json("monitors") or []
        self.screens_busy = time.monotonic() + 4       # no "Desktop 2" slide meanwhile
        hypr_eval("DesktopsHold(3500)")                 # Hyprland's shuffling isn't a desktop switch
        return {"ws": {w["id"]: w.get("monitor") for w in hypr_json("workspaces") or []
                       if (w.get("id") or 0) > 0},
                "shown": {m["name"]: (m.get("activeWorkspace") or {}).get("id") for m in mons},
                "focused": next((m["name"] for m in mons if m.get("focused")), None)}

    def restore_screens(self, snap):
        """After screens were switched: hyprland.lua puts every window and
        desktop back on its screen; every screen shows the desktop you were
        on (a desktop spans all screens, switching screens never changes it)."""
        self.screens_busy = time.monotonic() + 2.5
        hypr_eval("DesktopsArrange()")

    def flyouts_on(self, connector):
        """Start menu, calendar and quick settings open on the screen whose
        taskbar was clicked (None: the main display); the notification
        center goes along with the calendar."""
        connector = connector or self.primary
        mon = gdk_monitor(connector)
        if not mon or connector == self.flyout_screen:
            return
        self.flyout_screen = connector
        for w in (self.start.win, self.calendar.win, self.qs.win, self.qs.backdrop):
            LS.set_monitor(w, mon)
        if ncplace.load().get("output") != connector:
            ncplace.save(output=connector)
            ncplace.sync()                  # swaync re-reads its screen

    def close_flyouts(self):
        """Close every open menu (start menu, quick settings with its Wi-Fi /
        Bluetooth / sound pages, calendar, clipboard, tray flyout), like
        Windows does when Alt+Tab starts."""
        for f in (self.start, self.qs, self.calendar, self.clip, self.shutdown, self.taskview,
                  getattr(self, "project", None)):
            if f is None:
                continue
            if f.visible:
                f.hide()
        self.taskbar.close_overflow()

    def command(self, args):
        if not args:
            return
        what, arg = args[0], (args[1] if len(args) > 1 else "")
        if what == "switch":
            if arg not in ("release", "commit", "cancel"):
                self.close_flyouts()
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
                self.flyouts_on(None)
                self.qs.show(arg)
            else:
                self.flyouts_on(None)
                self.qs.toggle()
        elif what == "shutdown-dialog":         # Alt + F4 on the desktop
            self.shutdown.hide() if arg == "hide" else self.shutdown.toggle()
        elif what == "clipboard":               # Win + V
            self.clip.hide() if arg == "hide" else self.clip.toggle()
        elif what == "settings":                # Win + I
            self.settings.hide() if arg == "hide" else self.settings.toggle()
        elif what == "desk":                    # hyprland.lua: minimize / show desktop animation
            if arg in ("out", "in") and len(args) > 3 and args[2].isidentifier() and args[3].isdigit():
                self.desk.run(arg, args[2], int(args[3]), args[4:])
            else:
                self.desk.stop()
        elif what == "taskview":                # Win + Tab
            self.taskview.hide() if arg == "hide" else self.taskview.toggle()
        elif what == "mouse":                   # Win + I → Mouse
            self.mouse_settings.show()
        elif what == "displays":                # Win + I → Displays
            ds = self.display_settings
            ds.show()
            if arg == "preview-confirm":            # displays preview-confirm: only the question
                ds.preview_confirm()
            if arg == "test" and len(args) > 3:     # displays test <screen> <scale> [Hz]: set + Apply
                for i, m in enumerate(ds.items):
                    if m["name"] == args[2]:
                        ds.sel = i
                        m["scale"] = min(m["scales"], key=lambda v: abs(v - float(args[3])))
                        if len(args) > 4:
                            m["rate"] = min(m["rates"], key=lambda v: abs(v - float(args[4])))
                        ds.update_info()
                        ds.apply()
            elif arg == "ui" and len(args) > 3:     # displays ui <screen> <rate index>: as if clicked
                for i, m in enumerate(ds.items):
                    if m["name"] == args[2]:
                        ds.sel = i
                        ds.update_info()
                        ds.area.queue_draw()
                        ds.rate_dd.set_selected(int(args[3]))
                        print(f"winshell: displays ui: rate now {m['rate']}, rates {ds.rates}, "
                              f"apply sensitive {ds.apply_btn.get_sensitive()}, sig changed "
                              f"{ds.signature() != ds.orig}", file=sys.stderr, flush=True)
                        ds.apply_btn.emit("clicked")
                        print(f"winshell: displays ui: confirming {bool(ds.confirming)}", file=sys.stderr, flush=True)
            elif arg in ("keep", "revert"):
                getattr(ds, arg)()
        elif what == "project":                 # Win + P
            if arg == "hide":
                self.project.hide()
            elif arg in [m[0] for m in ProjectMenu.MODES]:     # project pc|duplicate|extend|second
                self.project.choose(arg)
            elif arg == "click" and len(args) > 2:  # project click <n>: open, click tile n
                if not self.project.visible:
                    self.project.toggle()
                self.project.tiles[int(args[2])].emit("clicked")
            else:
                self.project.toggle()
        elif what == "freeze":                  # screenshot.sh: still picture while picking a region
            self.freeze.hide() if arg == "off" else self.freeze.show(args[1:])
        elif what == "thumbs":                  # hyprland.lua: a window is about to change
            self.switcher.pause_thumbs()
        elif what == "snap":                    # hyprland.lua, while a window is dragged
            if arg == "show" and len(args) >= 8:
                self.snap.show(*(float(v) for v in args[2:8]))
            else:
                self.snap.hide()
        elif what == "osd":
            self.osd.show(arg if arg in ("brightness", "mic") else "volume")
            if self.qs.visible:
                self.qs.refresh()
        elif what == "calendar":
            if arg == "hide":
                self.calendar.hide()
            elif arg == "fold":                 # the arrow: fold the month away / back
                if self.calendar.visible:
                    self.calendar.toggle_fold()
            else:
                self.flyouts_on(None)
                self.calendar.toggle()
        elif what == "menu":
            if arg == "hide":
                self.start.hide()
            else:
                self.flyouts_on(None)
                self.start.toggle()
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
        elif what == "status" and len(args) > 2 and args[1] in ("net", "vol", "bat"):
            # status <net|vol|bat> <class> [tooltip]: show that icon for a moment
            # (to look at the icons); the real state comes back with the next change
            self.taskbar.show_status(args[1], {"class": args[2], "tooltip": " ".join(args[3:])})
        elif what == "status-refresh":          # back to the real state right away
            def first_line(w):
                # status.sh keeps watching: only its first line, then it ends
                p = subprocess.Popen([STATUS, w], stdout=subprocess.PIPE, text=True,
                                     stderr=subprocess.DEVNULL, env=child_env(), start_new_session=True)
                try:
                    line = p.stdout.readline()
                finally:
                    try:
                        os.killpg(p.pid, signal.SIGTERM)
                    except OSError:
                        pass
                try:
                    GLib.idle_add(self.taskbar.show_status, w, json.loads(line))
                except ValueError:
                    pass
            for w in ("net", "vol", "bat"):
                threading.Thread(target=first_line, args=(w,), daemon=True).start()
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
