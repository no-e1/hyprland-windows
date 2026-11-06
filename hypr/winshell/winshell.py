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
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402
from gi.repository import Gtk4LayerShell as LS  # noqa: E402

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


def spawn(cmd):
    subprocess.Popen(cmd, shell=True, start_new_session=True,
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

    # windows of the current desktop (plus minimized ones), most recent first
    def collect(self):
        clients = hypr_json("clients") or []
        ws = (hypr_json("activeworkspace") or {}).get("id")
        wins = [c for c in clients if c.get("mapped") and not c.get("hidden") and
                (c["workspace"]["id"] == ws or c["workspace"]["name"] == "special:minimized")]
        wins.sort(key=lambda c: c.get("focusHistoryID", 99))
        return wins

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
            self.sel = (1 if delta > 0 else -1) % len(self.items)
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
        """Focus a window and make sure it really ended up active and on top."""
        dispatch(f'hl.dsp.focus({{ window = "address:{addr}" }})')
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
        if c["workspace"]["name"].startswith("special:"):
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
                chg = st["bat_status"] in ("Charging", "Full", "Not charging")
                self.bat_img.set_from_icon_name(
                    f"win11-battery-{round(st['bat'], -1)}{'-charging' if chg else ''}")
                self.bat_lbl.set_text(f"{st['bat']}%")
        finally:
            self.updating = False

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
        self.set_tile("wifi", on)
        bg(lambda: ok(f"nmcli radio wifi {'on' if on else 'off'}"),
           lambda _r: (self.refresh(), self.later(lambda: self.load_wifi(True), 1500)
                       if self.stack.get_visible_child_name() == "wifi" else None))

    def toggle_bt(self):
        self.set_bt(not self.state.get("bt"))

    def set_bt(self, on):
        self.set_tile("bt", on)

        def run():
            if on:
                ok("rfkill unblock bluetooth")
            return ok(["bluetoothctl", "power", "on" if on else "off"], timeout=5)
        bg(run, lambda _r: (self.refresh(), self.load_bt()
                            if self.stack.get_visible_child_name() == "bluetooth" else None))

    def toggle_airplane(self):
        on = not self.state.get("airplane")
        self.set_tile("airplane", on)
        bg(lambda: ok(f"rfkill {'block' if on else 'unblock'} all"), lambda _r: self.later(self.refresh))

    def toggle_dnd(self):
        bg(lambda: ok("swaync-client -d"), lambda _r: self.refresh())

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
        return sorted(nets.values(), key=lambda n: (not n["active"], -n["signal"]))

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
        if n["enterprise"] and not n["known"] and not n["active"]:
            # school / company Wi-Fi: user name + password (PEAP / MSCHAPv2)
            user = Gtk.Entry(placeholder_text="User name")
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
                if n["known"]:
                    return ok(["nmcli", "connection", "up", "id", n["ssid"]], timeout=45)
                if user:
                    dev = wifi_device()
                    added = ok(["nmcli", "connection", "add", "type", "wifi", "con-name", n["ssid"],
                                "ifname", dev, "ssid", n["ssid"],
                                "wifi-sec.key-mgmt", "wpa-eap", "802-1x.eap", "peap",
                                "802-1x.phase2-auth", "mschapv2", "802-1x.identity", name,
                                "802-1x.password", pw], timeout=15)
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
                                keyboard=LS.KeyboardMode.ON_DEMAND)
        keys = Gtk.EventControllerKey()
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
        self.month_lbl = label("", "cal-month")
        self.month_lbl.set_hexpand(True)
        nav.append(self.month_lbl)
        nav.append(button(icon("win11-chevron-up", 12), "flat-btn", on_click=lambda: self.shift(-1)))
        nav.append(button(icon("win11-chevron-down", 12), "flat-btn", on_click=lambda: self.shift(1)))
        panel.append(nav)

        self.grid = Gtk.Grid(column_homogeneous=True, row_homogeneous=True, column_spacing=2, row_spacing=2)
        self.grid.add_css_class("cal-grid")
        for i, d in enumerate(("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")):
            self.grid.attach(label(d, "cal-dayname", xalign=0.5, ellipsize=False), i, 0, 1, 1)
        self.cells = []
        for r in range(6):
            for c in range(7):
                cell = label("", "cal-day", xalign=0.5, ellipsize=False)
                cell.set_size_request(40, 40)
                cell.set_valign(Gtk.Align.CENTER)
                cell.set_halign(Gtk.Align.CENTER)
                self.grid.attach(cell, c, r + 1, 1, 1)
                self.cells.append(cell)
        panel.append(self.grid)
        self.win.set_child(panel)

    def shift(self, months):
        m = self.month - 1 + months
        self.year, self.month = self.year + m // 12, m % 12 + 1
        self.render()

    def render(self):
        today = GLib.DateTime.new_now_local()
        first = GLib.DateTime.new_local(self.year, self.month, 1, 0, 0, 0)
        self.month_lbl.set_text(first.format("%B %Y"))
        start = first.add_days(-(first.get_day_of_week() - 1))     # back to Monday
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
        self.win.present()
        spawn("swaync-client -op")     # notification center sits right above

    def hide(self):
        if not self.visible:
            return
        self.visible = False
        self.win.set_visible(False)
        self.shell.qs.catcher_for(None)
        spawn("swaync-client -cp")


# ── Start menu (Windows 11 layout) ─────────────────────────────────────────
class StartMenu:
    COLS = 6

    def __init__(self, app, shell):
        self.shell = shell
        self.visible = False
        self.apps = []
        self.win = layer_window(app, "winshell-start", LS.Layer.OVERLAY,
                                anchors=(LS.Edge.BOTTOM,), margins={LS.Edge.BOTTOM: 12})
        keys = Gtk.EventControllerKey()
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
        self.search.connect("search-changed", lambda *_: self.filter())
        self.search.connect("activate", lambda *_: self.launch_first())
        body.append(self.search)

        head = Gtk.Box()
        head.add_css_class("start-head")
        self.title = label("All apps", "start-title")
        self.title.set_hexpand(True)
        head.append(self.title)
        body.append(head)

        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                                min_children_per_line=self.COLS, max_children_per_line=self.COLS,
                                row_spacing=4, column_spacing=4, valign=Gtk.Align.START)
        self.flow.set_filter_func(self.match)
        self.flow.connect("child-activated", lambda _f, child: self.launch(child.app))
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    min_content_height=420, max_content_height=420)
        scroll.set_child(self.flow)
        body.append(scroll)
        panel.append(body)
        panel.append(self.build_footer())
        self.win.set_child(panel)

        self.load_apps()
        self.monitor = Gio.AppInfoMonitor.get()
        self.monitor.connect("changed", lambda *_: self.load_apps())

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
    def load_apps(self):
        apps = [a for a in Gio.AppInfo.get_all() if a.should_show()]
        apps.sort(key=lambda a: a.get_display_name().lower())
        self.apps = apps
        clear(self.flow)
        for a in apps:
            tile = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            tile.add_css_class("app")
            img = Gtk.Image.new_from_gicon(a.get_icon() or Gio.ThemedIcon.new("application-x-executable"))
            img.set_pixel_size(32)
            tile.append(img)
            name = label(a.get_display_name(), "app-name", xalign=0.5)
            name.set_max_width_chars(10)
            name.set_width_chars(10)
            tile.append(name)
            child = Gtk.FlowBoxChild(child=tile)
            child.app = a
            child.key = " ".join(filter(None, [a.get_display_name(), a.get_name(),
                                               a.get_executable() or "", a.get_description() or "",
                                               " ".join(a.get_keywords() if hasattr(a, "get_keywords") else [])])).lower()
            click = Gtk.GestureClick()
            click.connect("released", lambda *_x, app=a: self.launch(app))
            child.add_controller(click)
            self.flow.append(child)

    def match(self, child):
        q = self.search.get_text().strip().lower()
        return not q or all(part in child.key for part in q.split())

    def filter(self):
        self.flow.invalidate_filter()
        q = self.search.get_text().strip()
        self.title.set_text("Best matches" if q else "All apps")

    def first_visible(self):
        c = self.flow.get_first_child()
        while c:
            if c.get_child_visible() and self.match(c):
                return c
            c = c.get_next_sibling()
        return None

    def launch_first(self):
        c = self.first_visible()
        if c:
            self.launch(c.app)

    def launch(self, app):
        self.hide()
        try:
            ctx = Gdk.Display.get_default().get_app_launch_context()
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
        self.search.set_text("")
        self.filter()
        self.visible = True
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

    def on_key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
            else:
                self.hide()
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


# ── application ──────────────────────────────────────────────────────────
class WinShell(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.github.noe1.winshell",
                         flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.switcher = self.qs = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        display = Gdk.Display.get_default()
        Gtk.IconTheme.get_for_display(display).add_search_path(ICONS)
        css = Gtk.CssProvider()
        css.load_from_path(os.path.join(HERE, "style.css"))
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_USER)

        icons = AppIcons()
        self.switcher = Switcher(self, icons)
        self.qs = QuickSettings(self)
        self.osd = OSD(self)
        self.start = StartMenu(self, self)
        self.calendar = CalendarFlyout(self, self)

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
        elif what == "backdrop":
            self.qs.backdrop_rofi(arg == "rofi")
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
