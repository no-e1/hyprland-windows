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
import os
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
from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402
try:                                    # GLib >= 2.86 moved it to GioUnix
    gi.require_version("GioUnix", "2.0")
    from gi.repository import GioUnix  # noqa: E402
    DesktopAppInfo = GioUnix.DesktopAppInfo
except (ValueError, ImportError):
    DesktopAppInfo = Gio.DesktopAppInfo

HERE = os.path.dirname(os.path.realpath(__file__))
ICONS = os.path.join(os.path.dirname(os.path.dirname(HERE)), "icons")
RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
FIFO = os.path.join(RUNTIME, "winshell.fifo")
READY = os.path.join(RUNTIME, "winshell.ready")
LOCK = os.path.join(RUNTIME, "winshell.lock")
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


# environment only winshell itself needs (see winshell-ctl); apps and tools
# started from here must not inherit it
OWN_ENV = ("LD_PRELOAD", "LC_TIME", "GSK_DEBUG")


def child_env():
    return {k: v for k, v in os.environ.items() if k not in OWN_ENV}


def spawn(cmd):
    subprocess.Popen(cmd, shell=True, start_new_session=True, env=child_env(),
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
    subprocess.run(["hyprctl", "dispatch", lua], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
        self.items, self.cards = [], []
        self.sel, self.visible = 0, False
        self.task_view = False
        self.paused_until = 0.0
        self.win = layer_window(app, "winshell-switcher", LS.Layer.OVERLAY)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                max_children_per_line=6, min_children_per_line=1,
                                column_spacing=6, row_spacing=6)
        panel = Gtk.Box()
        panel.add_css_class("switcher")
        panel.append(self.flow)
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
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_Down):
            self.step(1)
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_Up):
            self.step(-1)
        elif keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            back = keyval == Gdk.KEY_ISO_Left_Tab or state & Gdk.ModifierType.SHIFT_MASK
            self.step(-1 if back else 1)
        return True

    def on_key_released(self, _c, keyval, _code, _state):
        if not self.task_view and keyval in (Gdk.KEY_Alt_L, Gdk.KEY_Alt_R):
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
            if not self.poll and not self.task_view:
                self.poll = GLib.timeout_add(100, self.start_poll)
            for c in self.items:
                self.capture(c)
        else:
            self.sel = (self.sel + delta) % len(self.items)
            self.update_selection()

    def build(self):
        self.cards = []
        clear(self.flow)
        per_line = max(1, min(6, len(self.items)))
        self.flow.set_min_children_per_line(per_line)
        self.flow.set_max_children_per_line(per_line)
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

    def update_selection(self):
        for i, card in enumerate(self.cards):
            (card.add_css_class if i == self.sel else card.remove_css_class)("selected")

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
        GLib.timeout_add(50, finish)

    def focus(self, addr, cls, tries):
        """Focus a window and make sure it really ended up active and on top.
        ShowWindow (hyprland.lua) restores a minimized window before focusing it."""
        subprocess.run(["hyprctl", "eval", f'ShowWindow("{addr}")'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
        self.win.set_visible(False)
        LS.set_keyboard_mode(self.win, LS.KeyboardMode.NONE)

    # previews ------------------------------------------------------------
    def capture(self, c, update=True):
        if time.monotonic() < self.paused_until:
            return
        if c["workspace"].get("name", "").startswith("special:"):
            return                      # hidden windows are not rendered
        addr, sid = c["address"], c.get("stableId")
        if not sid:
            return
        factor = min(1.0, 2 * self.W / max(1, c["size"][0]))

        def grab():
            try:
                out = subprocess.run(["grim", "-T", sid, "-s", f"{factor:.3f}", "-t", "jpeg", "-q", "85", "-"],
                                     capture_output=True, timeout=3).stdout
                return Gdk.Texture.new_from_bytes(GLib.Bytes.new(out)) if out else None
            except Exception:
                return None

        def done(tex):
            if tex:
                self.thumbs[addr] = tex
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
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        page.add_css_class("qs-main")
        grid = Gtk.Grid(column_spacing=12, row_spacing=14, column_homogeneous=True)
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
                                             on_more=lambda: (self.hide(), spawn("pavucontrol")))
        sliders = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        sliders.append(self.bright_row)
        sliders.append(self.vol_row)
        page.append(sliders)
        return page

    def tile(self, ic, text, on_toggle, on_more):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        row = Gtk.Box()
        row.add_css_class("tile")
        img = icon(ic, 16)
        main = button(img, "tile-main", on_click=on_toggle)
        main.set_hexpand(True)
        row.append(main)
        if on_more:
            row.append(button(icon("win11-chevron-right", 12), "tile-more", on_click=on_more))
        lbl = label(text, "tile-label", xalign=0.5)
        outer.append(row)
        outer.append(lbl)
        return outer, row, lbl, img

    def slider(self, ic, on_change, on_icon=None, on_more=None):
        row = Gtk.Box(spacing=10)
        row.add_css_class("slider-row")
        img = icon(ic, 18)
        row.append(button(img, "flat-btn", on_click=on_icon) if on_icon else img)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        scale.set_hexpand(True)
        scale.set_draw_value(False)
        scale.connect("value-changed", lambda s: None if self.updating else on_change(int(s.get_value())))
        row.append(scale)
        if on_more:
            row.append(button(icon("win11-chevron-right", 12), "flat-btn", on_click=on_more))
        row.img = img
        return row, scale

    def build_footer(self):
        foot = Gtk.Box(spacing=8)
        foot.add_css_class("qs-footer")
        self.bat_img = icon("win11-battery-100", 18)
        self.bat_lbl = label("", "footer-text")
        foot.append(self.bat_img)
        foot.append(self.bat_lbl)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        foot.append(spacer)
        foot.append(button(icon("win11-settings", 16), "flat-btn", tooltip="All settings",
                           on_click=lambda: (self.hide(), spawn(f"{SCRIPTS}/settings.sh"))))
        foot.append(button(icon("win11-power", 16), "flat-btn", tooltip="Power",
                           on_click=lambda: (self.hide(), spawn(f"{SCRIPTS}/powermenu.sh --menu"))))
        return foot

    # ── sub pages ──
    def page(self, title, on_switch, more_text, more_cmd):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        page.add_css_class("qs-page")
        head = Gtk.Box(spacing=8)
        head.append(button(icon("win11-chevron-left", 14), "flat-btn", on_click=lambda: self.open_page("main")))
        head.append(label(title, "page-title"))
        sp = Gtk.Box()
        sp.set_hexpand(True)
        head.append(sp)
        sw = Gtk.Switch(valign=Gtk.Align.CENTER)
        sw.connect("state-set", lambda s, v: None if self.updating else on_switch(v))
        head.append(sw)
        page.append(head)

        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    min_content_height=300, max_content_height=300)
        lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        scroll.set_child(lst)
        page.append(scroll)

        foot = Gtk.Box()
        foot.append(button(more_text, "link-btn", on_click=lambda: (self.hide(), spawn(more_cmd))))
        page.append(foot)
        return page, sw, lst

    def build_wifi(self):
        page, self.wifi_switch, self.wifi_list = self.page(
            "Wi-Fi", self.set_wifi, "More Wi-Fi settings", "nm-connection-editor")
        return page

    def build_bt(self):
        page, self.bt_switch, self.bt_list = self.page(
            "Bluetooth", self.set_bt, "More Bluetooth settings", "blueman-manager")
        return page

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

    def open_page(self, name):
        self.stack.set_visible_child_name(name)
        if name == "wifi":
            self.load_wifi(rescan=True)
        elif name == "bluetooth":
            self.load_bt()

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
            self.wifi_switch.set_active(st["wifi"])
            self.bt_switch.set_active(st["bt"])
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

    def want(self, key, on):
        """Show a clicked tile's new state right away (before it is applied)."""
        self.wanted[key] = (on, time.monotonic() + 4)
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
        self.want("wifi", on)
        bg(lambda: ok(f"nmcli radio wifi {'on' if on else 'off'}"),
           lambda _r: (self.refresh(), self.later(lambda: self.load_wifi(True), 1500)
                       if self.stack.get_visible_child_name() == "wifi" else None))

    def toggle_bt(self):
        self.set_bt(not self.state.get("bt"))

    def set_bt(self, on):
        self.want("bt", on)

        def run():
            if on:
                ok("rfkill unblock bluetooth")
            return ok(["bluetoothctl", "power", "on" if on else "off"], timeout=5)
        bg(run, lambda _r: (self.refresh(), self.load_bt()
                            if self.stack.get_visible_child_name() == "bluetooth" else None))

    def toggle_airplane(self):
        on = not self.state.get("airplane")
        self.want("airplane", on)
        bg(lambda: ok(f"rfkill {'block' if on else 'unblock'} all"), lambda _r: self.later(self.refresh))

    def toggle_dnd(self):
        on = not self.state.get("dnd")
        self.want("dnd", on)
        bg(lambda: ok(f"swaync-client {'-dn' if on else '-df'}"), lambda _r: self.refresh())

    def snip(self):
        self.hide()
        spawn(f"sleep 0.3; {SCRIPTS}/screenshot.sh area")

    def lock(self):
        self.hide()
        spawn("pidof hyprlock || hyprlock")

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
        if rescan:
            bg(lambda: self.scan_wifi(True), self.show_wifi)

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
        for n in nets:
            self.wifi_list.append(self.wifi_row(n))

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
            btn.get_child().set_text("Connecting…")
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
                if n["known"]:
                    return ok(["nmcli", "connection", "up", "id", n["ssid"]], timeout=45)
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
                return ok(cmd, timeout=45)

            def done(success):
                btn.set_sensitive(True)
                btn.get_child().set_text("Connect")
                if success:
                    self.refresh()
                    self.load_wifi()
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

    @staticmethod
    def scan_bt():
        def devs(kind):
            res = []
            for l in sh(["bluetoothctl", "devices", kind], timeout=4).splitlines():
                p = l.split(" ", 2)
                if len(p) == 3 and p[0] == "Device":
                    res.append((p[1], p[2]))
            return res
        connected = {m for m, _ in devs("Connected")}
        out = []
        for mac, name in devs("Paired"):
            info = sh(["bluetoothctl", "info", mac], timeout=3)
            ic = re.search(r"Icon: (\S+)", info)
            out.append({"mac": mac, "name": name, "connected": mac in connected,
                        "audio": bool(ic and "audio" in ic.group(1))})
        return sorted(out, key=lambda d: (not d["connected"], d["name"].lower()))

    def show_bt(self, devices):
        clear(self.bt_list)
        if not self.state.get("bt", True):
            self.bt_list.append(label("Bluetooth is turned off", "empty-text", xalign=0.5))
            return
        if not devices:
            self.bt_list.append(label("No paired devices", "empty-text", xalign=0.5))
        for d in devices:
            self.bt_list.append(self.bt_row(d))

    def bt_row(self, d):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.add_css_class("net")
        top = Gtk.Box(spacing=12)
        top.append(icon("win11-headphones" if d["audio"] else "win11-devices", 20))
        txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        txt.set_hexpand(True)
        txt.append(label(d["name"], "net-name"))
        txt.append(label("Connected" if d["connected"] else "Paired", "net-status"))
        top.append(txt)
        box.append(top)

        rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=120)
        actions = Gtk.Box(halign=Gtk.Align.END)
        verb = "disconnect" if d["connected"] else "connect"

        def run_action(*_):
            btn.set_sensitive(False)
            btn.get_child().set_text("Disconnecting…" if d["connected"] else "Connecting…")
            bg(lambda: ok(["bluetoothctl", verb, d["mac"]], timeout=20),
               lambda _r: (self.refresh(), self.load_bt()))
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
        click.connect("released", toggle_row)
        top.add_controller(click)
        box.rev = rev
        return box


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
        self.head = label("", "cal-head")
        panel.append(self.head)

        nav = Gtk.Box()
        nav.add_css_class("cal-nav")
        self.month_lbl = label("", "cal-month", ellipsize=False)
        self.month_lbl.set_hexpand(True)
        nav.append(self.month_lbl)
        nav.append(button(icon("win11-chevron-up", 12), "flat-btn", on_click=lambda: self.shift(-1)))
        nav.append(button(icon("win11-chevron-down", 12), "flat-btn", on_click=lambda: self.shift(1)))
        panel.append(nav)

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
        panel.append(self.grid)
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
        threading.Thread(target=self.watch_notification_center, daemon=True).start()

    def watch_notification_center(self):
        while True:
            try:
                proc = subprocess.Popen(["swaync-client", "-s"], stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, text=True)
                for line in proc.stdout:
                    try:
                        visible = bool(json.loads(line).get("visible"))
                    except ValueError:
                        continue
                    if self.nc_open and not visible:
                        GLib.idle_add(lambda: self.hide() and False)
                    self.nc_open = visible
            except OSError:
                pass
            time.sleep(2)            # swaync restarted or not running yet

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
        avatar = icon("win11-user", 18)
        avatar.add_css_class("avatar")
        user.append(avatar)
        user.append(label(os.environ.get("USER", "User"), "start-user"))
        ub = Gtk.MenuButton(child=user, direction=Gtk.ArrowType.UP)
        ub.add_css_class("start-btn")
        ub.set_popover(self.menu([("win11-lock", "Lock", "Lock"),
                                  ("win11-signout", "Sign out", "Sign out")]))
        foot.append(ub)
        sp = Gtk.Box()
        sp.set_hexpand(True)
        foot.append(sp)
        items = [("win11-sleep", "Sleep", "Sleep"),
                 ("win11-power", "Shut down", "Shut down"),
                 ("win11-restart", "Restart", "Restart")]
        cache = os.path.expanduser("~/.cache/hypr-has-windows")
        if os.path.exists(cache) and open(cache).read().strip() == "1":
            items.append(("win11-restart", "Restart to Windows", "Restart to Windows"))
        pb = Gtk.MenuButton(child=icon("win11-power", 16), direction=Gtk.ArrowType.UP)
        pb.add_css_class("start-btn")
        pb.set_tooltip_text("Power")
        pb.set_popover(self.menu(items))
        foot.append(pb)
        return foot

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
        self.lbl.set_width_chars(3)
        box.append(self.img)
        box.append(self.bar)
        box.append(self.lbl)
        self.win.set_child(box)

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

    def update(self, res):
        name, value, muted = res
        self.img.set_from_icon_name(name)
        self.bar.set_fraction(max(0, min(100, value)) / 100)
        (self.bar.add_css_class if muted else self.bar.remove_css_class)("muted")
        self.lbl.set_text(str(value))
        self.win.present()
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer = GLib.timeout_add(1600, self.hide)

    def hide(self):
        self.timer = None
        self.win.set_visible(False)
        return False



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
        self.app_box = Gtk.Box()
        center.append(self.app_box)
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("drop", self.on_app_drop)
        self.app_box.add_controller(drop)
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
        if keys != self.shown:
            clear(self.app_box)
            for k in keys:
                if k not in self.buttons:
                    self.buttons[k] = self.make_app_button(k)
                self.app_box.append(self.buttons[k])
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
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        src.connect("prepare", lambda *_: Gdk.ContentProvider.new_for_value(
            GObject.Value(GObject.TYPE_STRING, "app:" + key)))
        src.connect("drag-begin", lambda s, _d: s.set_icon(Gtk.WidgetPaintable.new(img), 12, 12))
        b.add_controller(src)
        return b

    def on_app_click(self, key):
        wins = self.groups.get(key, [])
        if not wins:
            self.launch(key)
            return
        if key == self.active_key:
            if len(wins) == 1:          # the app in front: minimize it, like Windows
                spawn(f"hyprctl eval 'HideWindow(\"{wins[0]['address']}\")'")
                return
            target = wins[1]            # several windows: go to the next one
        else:
            target = wins[0]
        spawn(f"hyprctl eval 'ShowWindow(\"{target['address']}\")'")

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

    def on_app_drop(self, _target, value, x, _y):
        if not isinstance(value, str) or not value.startswith("app:"):
            return False
        key = value[4:]
        keys = [k for k in self.shown if k != key]
        idx = len(keys)
        for i, k in enumerate(keys):
            ok_, r = self.buttons[k].compute_bounds(self.app_box)
            if ok_ and x < r.get_x() + r.get_width() / 2:
                idx = i
                break
        keys.insert(idx, key)
        self.order = keys + [k for k in self.order if k not in keys]
        self.cfg["pinned"] = [k for k in self.order if k in self.cfg["pinned"]]
        self.save_config()
        self.shown = []                 # force re-adding in the new order
        self.refresh()
        return True

    def set_start_open(self, on):
        (self.start_btn.add_css_class if on else self.start_btn.remove_css_class)("open")

    # ── right side: tray, status icons, clock, show-desktop sliver ──
    def build_right(self):
        right = Gtk.Box()
        right.add_css_class("tb-right")

        self.right = right
        # "^": only there while icons are hidden (or while an icon is dragged)
        self.tray_arrow = button(icon("win11-chevron-up", 12), "tb-tray-arrow",
                                 tooltip="Show hidden icons", on_click=self.toggle_overflow)
        self.tray_arrow.set_visible(False)
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("drop", lambda _t, v, _x, _y: self.move_tray(v, hidden=True))
        drop.connect("enter", lambda *_: (self.open_overflow(), Gdk.DragAction.MOVE)[1])
        self.tray_arrow.add_controller(drop)
        right.append(self.tray_arrow)

        self.tray_box = Gtk.Box()
        self.tray_box.add_css_class("tb-tray")
        right.append(self.tray_box)
        # dropping an icon anywhere on the right side (not on "^") shows it in
        # the taskbar – also when no icon is shown there yet
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("drop", self.on_right_drop)
        right.add_controller(drop)
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
        clear(self.tray_box)
        clear(self.overflow_grid)
        for it in items:
            btn = self.tray_button(it)
            if it.ident in hidden:
                self.overflow_grid.append(btn)
            else:
                self.tray_box.append(btn)
        any_hidden = any(it.ident in hidden for it in items)
        n = sum(it.ident in hidden for it in items)
        self.overflow_grid.set_min_children_per_line(max(1, min(5, n)))
        self.overflow_grid.set_max_children_per_line(max(1, min(5, n)))
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
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        src.connect("prepare", lambda *_: Gdk.ContentProvider.new_for_value(
            GObject.Value(GObject.TYPE_STRING, "tray:" + item.ident)))
        src.connect("drag-begin", lambda s, _d: (s.set_icon(Gtk.WidgetPaintable.new(img), 8, 8),
                                                 self.tray_drag(True)))
        src.connect("drag-end", lambda *_: self.tray_drag(False))
        b.add_controller(src)
        return b

    def tray_drag(self, on):
        """While a tray icon is dragged: "^" is there to drop it on, and the
        click catcher is out of the way so the taskbar takes drops."""
        self.dragging_tray = on
        if on:
            self.tray_arrow.set_visible(True)
            self.shell.qs.backdrop.set_visible(False)
        else:
            if self.ov_win.get_visible():
                self.shell.qs.backdrop.present()
            GLib.idle_add(self.tray_changed)

    def on_right_drop(self, _target, value, x, _y):
        ok_, r = self.tray_box.compute_bounds(self.right)
        return self.move_tray(value, hidden=False, x=(x - r.get_x()) if ok_ else None)

    def move_tray(self, value, hidden, x=None):
        if not isinstance(value, str) or not value.startswith("tray:"):
            return False
        ident = value[5:]
        hid = [i for i in self.cfg["tray_hidden"] if i != ident]
        if hidden:
            hid.append(ident)
        self.cfg["tray_hidden"] = hid
        if x is not None:                       # dropped in the bar: put it where it was dropped
            order = [i for i in self.cfg["tray_order"] if i != ident]
            pos = len(order)
            child, i = self.tray_box.get_first_child(), 0
            while child:
                ok_, r = child.compute_bounds(self.tray_box)
                if ok_ and x < r.get_x() + r.get_width() / 2:
                    tip = child.get_tooltip_text()
                    match = next((it.ident for it in self.shell.tray.items.values() if it.title == tip), None)
                    if match in order:
                        pos = order.index(match)
                    break
                child = child.get_next_sibling()
            order.insert(pos, ident)
            self.cfg["tray_order"] = order
        self.save_config()
        GLib.idle_add(self.tray_changed)
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
        self.overflow_grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                         min_children_per_line=1, max_children_per_line=5)
        self.overflow_empty = label("Drop here to hide", "overflow-empty", xalign=0.5)
        panel.append(self.overflow_grid)
        panel.append(self.overflow_empty)
        drop = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop.connect("drop", lambda _t, v, _x, _y: self.move_tray(v, hidden=True))
        panel.add_controller(drop)
        self.ov_win.set_child(panel)

    def place_overflow(self):
        """Center the flyout above the "^" button."""
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
             "view": self.switcher.view,
             "commit": self.switcher.commit,
             "cancel": lambda: self.switcher.visible and self.switcher.hide()}.get(arg, lambda: None)()
        elif what == "qs":
            if arg == "hide":
                self.qs.hide()
            elif arg in ("wifi", "bluetooth"):
                self.qs.show(arg)
            else:
                self.qs.toggle()
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
                child = self.taskbar.tray_box.get_first_child()
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
