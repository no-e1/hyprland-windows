#!/usr/bin/env python3
"""Lock screen like Windows 11 (started by scripts/lock.sh).

  1. the lock screen: the desktop background with the time and date
  2. any key or a click: the time slides away, the background blurs and the
     sign-in appears – profile picture, name, password field
Password (PAM) and fingerprint (fprintd) both unlock; the fingerprint works
in both steps, like Windows Hello. Wi-Fi and battery are shown bottom right.

It uses the Wayland session lock (ext-session-lock), so the compositor keeps
the desktop hidden until it unlocks. If this program ever fails, lock.sh
starts hyprlock instead – the screen never ends up unlocked or stuck.

  lockscreen.py              lock the screen
  lockscreen.py --preview    show it in a normal window, nothing is locked
                             (add "signin" to start at the sign-in step)
"""
import ctypes
import ctypes.util
import os
import pwd
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

PREVIEW = "--preview" in sys.argv
if PREVIEW:
    gi.require_version("Gtk4LayerShell", "1.0")
    from gi.repository import Gtk4LayerShell as LS  # noqa: E402
else:
    gi.require_version("Gtk4SessionLock", "1.0")
    from gi.repository import Gtk4SessionLock as SL  # noqa: E402

USER = pwd.getpwuid(os.getuid()).pw_name
CACHE = os.path.expanduser("~/.cache/hyprlock")
PAM_SERVICE = "password-auth"       # Fedora's stack without the fingerprint step
SIGNIN_IDLE = 45                    # s without input: back to the lock screen


def display_name():
    gecos = pwd.getpwuid(os.getuid()).pw_gecos.split(",")[0].strip()
    name = gecos or USER
    return name[:1].upper() + name[1:]


# ── password check (PAM, through ctypes) ──────────────────────────────────
class PamMessage(ctypes.Structure):
    _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]


class PamResponse(ctypes.Structure):
    _fields_ = [("resp", ctypes.c_void_p), ("resp_retcode", ctypes.c_int)]


CONV_FUNC = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int,
                             ctypes.POINTER(ctypes.POINTER(PamMessage)),
                             ctypes.POINTER(ctypes.POINTER(PamResponse)), ctypes.c_void_p)


class PamConv(ctypes.Structure):
    _fields_ = [("conv", CONV_FUNC), ("appdata_ptr", ctypes.c_void_p)]


_pam = ctypes.CDLL(ctypes.util.find_library("pam"))
_libc = ctypes.CDLL(ctypes.util.find_library("c"))
_libc.calloc.restype = ctypes.c_void_p
_libc.calloc.argtypes = [ctypes.c_size_t, ctypes.c_size_t]
_libc.strdup.restype = ctypes.c_void_p
_libc.strdup.argtypes = [ctypes.c_char_p]
_pam.pam_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(PamConv),
                           ctypes.POINTER(ctypes.c_void_p)]
_pam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
_pam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]


def pam_check(password):
    secret = password.encode()

    @CONV_FUNC
    def conv(n, msgs, resp, _data):
        arr = _libc.calloc(n, ctypes.sizeof(PamResponse))   # PAM frees it
        if not arr:
            return 5                                        # PAM_BUF_ERR
        r = ctypes.cast(arr, ctypes.POINTER(PamResponse))
        for i in range(n):
            if msgs[i].contents.msg_style in (1, 2):        # PROMPT_ECHO_OFF / ON
                r[i].resp = _libc.strdup(secret)
        resp[0] = r
        return 0

    handle = ctypes.c_void_p()
    c = PamConv(conv, None)
    if _pam.pam_start(PAM_SERVICE.encode(), USER.encode(), ctypes.byref(c), ctypes.byref(handle)) != 0:
        return False
    rc = _pam.pam_authenticate(handle, 0)
    _pam.pam_end(handle, rc)
    return rc == 0


# ── fingerprint (fprintd over D-Bus) ──────────────────────────────────────
class Fingerprint:
    MESSAGES = {
        "verify-no-match": "Couldn't recognize your fingerprint. Try again.",
        "verify-retry-scan": "Try again",
        "verify-swipe-too-short": "Try again",
        "verify-finger-not-centered": "Center your finger and try again",
        "verify-remove-and-retry": "Lift your finger and try again",
    }

    def __init__(self, on_match, on_message):
        self.on_match, self.on_message = on_match, on_message
        self.dev = None
        self.closed = False
        threading.Thread(target=self.open, daemon=True).start()

    def open(self):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            mgr = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None, "net.reactivated.Fprint",
                                         "/net/reactivated/Fprint/Manager", "net.reactivated.Fprint.Manager", None)
            path = mgr.call_sync("GetDefaultDevice", None, Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
            dev = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None, "net.reactivated.Fprint",
                                         path, "net.reactivated.Fprint.Device", None)
            fingers = dev.call_sync("ListEnrolledFingers", GLib.Variant("(s)", (USER,)),
                                    Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
            if not fingers:
                return
            dev.call_sync("Claim", GLib.Variant("(s)", (USER,)), Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error as e:
            print("lockscreen: no fingerprint:", e.message, file=sys.stderr)
            return
        GLib.idle_add(self.ready, dev)

    def ready(self, dev):
        if self.closed:
            self.release(dev)
            return False
        self.dev = dev
        dev.connect("g-signal", self.on_signal)
        self.verify()
        return False

    def verify(self):
        if self.dev and not self.closed:
            self.dev.call("VerifyStart", GLib.Variant("(s)", ("any",)), Gio.DBusCallFlags.NONE, -1, None,
                          lambda d, res: self.finish(d, res))
        return False

    @staticmethod
    def finish(dev, res):
        try:
            dev.call_finish(res)
        except GLib.Error as e:
            print("lockscreen: fingerprint:", e.message, file=sys.stderr)

    def on_signal(self, _proxy, _sender, name, params):
        if name != "VerifyStatus" or self.closed:
            return
        result, done = params.unpack()
        if result == "verify-match":
            self.on_match()
            return
        if result in self.MESSAGES:
            self.on_message(self.MESSAGES[result])
        if done:                                    # start over for the next try
            self.dev.call("VerifyStop", None, Gio.DBusCallFlags.NONE, -1, None,
                          lambda d, res: (self.finish(d, res), GLib.timeout_add(300, self.verify)))

    def close(self):
        self.closed = True
        if self.dev:
            self.release(self.dev)
            self.dev = None

    @staticmethod
    def release(dev):
        for method in ("VerifyStop", "Release"):
            try:
                dev.call_sync(method, None, Gio.DBusCallFlags.NONE, 2000, None)
            except GLib.Error:
                pass


# ── status icons (Wi-Fi, battery) ─────────────────────────────────────────
def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=4).stdout
    except Exception:
        return ""


def status_icons():
    net = "win11-wifi-off"
    for line in sh(["nmcli", "-t", "-f", "TYPE,STATE", "dev"]).splitlines():
        if line.startswith("ethernet:connected"):
            net = "win11-ethernet"
    if net == "win11-wifi-off":
        for line in sh(["nmcli", "-t", "-f", "ACTIVE,SIGNAL", "dev", "wifi", "list", "--rescan", "no"]).splitlines():
            if line.startswith("yes:"):
                sig = int(line.split(":")[1] or 0)
                net = f"win11-wifi-{min(4, 1 + sig // 25)}"
    bat = None
    try:
        cap = int(open("/sys/class/power_supply/BAT0/capacity").read())
        st = open("/sys/class/power_supply/BAT0/status").read().strip()
        kind = {"Charging": "-charging", "Full": "-plugged", "Not charging": "-plugged"}.get(st, "")
        bat = f"win11-battery-{round(cap, -1)}{kind}"
    except (OSError, ValueError):
        pass
    return net, bat


# ── the screen ────────────────────────────────────────────────────────────
CSS = """
window.lock { background: #18181c; }
.tint { background: rgba(0, 0, 0, 0.12); transition: background-color 300ms ease-out; }
.signin .tint { background: rgba(0, 0, 0, 0.28); }
.bg-blur { opacity: 0; transition: opacity 320ms ease-out; }
.signin .bg-blur { opacity: 1; }

.stage1 { transition: opacity 260ms ease-out, transform 320ms cubic-bezier(0.2, 0.8, 0.2, 1); }
.signin .stage1 { opacity: 0; transform: translateY(-90px); }
.clock { color: white; font-family: "Inter Display", Inter; font-weight: 600; font-size: 132px; }
.date { color: white; font-family: Inter; font-weight: 500; font-size: 23px; margin-top: -6px; }

.stage2 { opacity: 0; transform: scale(0.96); transition: opacity 260ms ease-out 60ms, transform 300ms cubic-bezier(0.2, 0.8, 0.2, 1) 60ms; }
.signin .stage2 { opacity: 1; transform: none; }
.avatar { border-radius: 9999px; background: rgba(255, 255, 255, 0.12); }
.name { color: white; font-family: Inter; font-weight: 600; font-size: 27px; margin-top: 18px; }
entry.password, passwordentry.password {
    min-width: 290px; min-height: 34px; margin-top: 22px;
    color: white; caret-color: white;
    background: rgba(0, 0, 0, 0.32);
    border: 1px solid rgba(255, 255, 255, 0.38); border-radius: 4px;
    box-shadow: none; outline: none;
}
passwordentry.password:focus-within { border-color: rgba(255, 255, 255, 0.7); }
passwordentry.password text placeholder { color: rgba(255, 255, 255, 0.72); }
passwordentry.password image { color: white; }
.message { color: rgba(255, 255, 255, 0.92); font-family: Inter; font-size: 13px; margin-top: 16px; }
.message.error { color: white; font-weight: 600; }

.status { margin: 0 26px 22px 0; }
.status image { color: white; margin-left: 14px; -gtk-icon-shadow: 0 1px 3px rgba(0, 0, 0, 0.5); }
"""


class LockScreen:
    AVATAR = 150                # px, the round profile picture
    def __init__(self, app, start_signin=False):
        self.app = app
        self.screens = []
        self.signin = False
        self.busy = False
        self.idle_id = 0
        self.wall = os.path.join(CACHE, "wallpaper")
        self.face = os.path.join(CACHE, "face")
        self.blur_tex = None
        threading.Thread(target=self.make_blur, daemon=True).start()
        self.fp = None if PREVIEW else Fingerprint(self.unlock, self.say)
        self.start_signin = start_signin

    # blurred copy of the background for the sign-in step (Pillow, small)
    def make_blur(self):
        try:
            from PIL import Image, ImageFilter
            src = os.path.realpath(self.wall)
            out = os.path.join(CACHE, "wallpaper-blur.jpg")
            if not (os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(src)
                    and open(out + ".src").read() == src):
                im = Image.open(src).convert("RGB")
                im.thumbnail((640, 640))
                im = im.filter(ImageFilter.GaussianBlur(14))
                im.save(out, quality=88)
                with open(out + ".src", "w") as f:
                    f.write(src)
            tex = Gdk.Texture.new_from_filename(out)
            GLib.idle_add(self.set_blur, tex)
        except Exception as e:
            print("lockscreen: no blurred background:", e, file=sys.stderr)

    def set_blur(self, tex):
        self.blur_tex = tex
        for s in self.screens:
            s["blur"].set_paintable(tex)
        return False

    def build(self, primary, height=900):
        root = Gtk.Overlay()
        root.add_css_class("lock-root")
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, hexpand=True, vexpand=True)
        if os.path.exists(self.wall):
            pic.set_filename(self.wall)
        root.set_child(pic)
        blur = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
        blur.add_css_class("bg-blur")
        if self.blur_tex:
            blur.set_paintable(self.blur_tex)
        root.add_overlay(blur)
        tint = Gtk.Box(hexpand=True, vexpand=True)
        tint.add_css_class("tint")
        root.add_overlay(tint)

        # 1. time and date
        s1 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        s1.add_css_class("stage1")
        s1.set_can_target(False)
        clock = Gtk.Label()
        clock.add_css_class("clock")
        date = Gtk.Label()
        date.add_css_class("date")
        s1.append(clock)
        s1.append(date)
        root.add_overlay(s1)

        # 2. sign-in
        s2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        s2.add_css_class("stage2")
        av_box = Gtk.Box(halign=Gtk.Align.CENTER)
        av_box.add_css_class("avatar")
        av_box.set_overflow(Gtk.Overflow.HIDDEN)
        av = Gtk.Image(pixel_size=self.AVATAR)          # fixed size, sharp on HiDPI
        if os.path.exists(self.face):
            try:
                av.set_from_paintable(Gdk.Texture.new_from_filename(self.face))
            except GLib.Error:
                pass
        av_box.append(av)
        s2.append(av_box)
        name = Gtk.Label(label=display_name())
        name.add_css_class("name")
        s2.append(name)
        entry = None
        if primary:
            entry = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="Password", halign=Gtk.Align.CENTER)
            entry.add_css_class("password")
            entry.connect("activate", lambda e: self.try_password(e.get_text()))
            entry.connect("changed", lambda e: self.touch())
            s2.append(entry)
        msg = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=48)
        msg.add_css_class("message")
        s2.append(msg)
        s2.set_can_target(False)
        root.add_overlay(s2)

        # Wi-Fi and battery, bottom right (just shown, not clickable)
        st = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.END)
        st.add_css_class("status")
        st.set_can_target(False)
        net = Gtk.Image(pixel_size=20)
        bat = Gtk.Image(pixel_size=24)
        st.append(net)
        st.append(bat)
        root.add_overlay(st)

        s1.set_margin_top(int(height * 0.11))

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda *_: self.show_signin())
        root.add_controller(click)
        screen = {"root": root, "clock": clock, "date": date, "entry": entry, "msg": msg,
                  "blur": blur, "s1": s1, "s2": s2, "net": net, "bat": bat, "keys": keys}
        self.screens.append(screen)
        return root, keys

    def tick(self):
        now = GLib.DateTime.new_now_local()
        for s in self.screens:
            s["clock"].set_text(now.format("%H:%M"))
            s["date"].set_text(now.format("%A, %B %-d"))
        GLib.timeout_add(1000 - now.get_microsecond() // 1000 + 5, self.tick)
        return False

    def update_status(self):
        def done(res):
            net, bat = res
            for s in self.screens:
                s["net"].set_from_icon_name(net)
                s["bat"].set_visible(bool(bat))
                if bat:
                    s["bat"].set_from_icon_name(bat)
        threading.Thread(target=lambda: GLib.idle_add(done, status_icons()), daemon=True).start()
        return True

    # ── steps ──
    def show_signin(self):
        if not self.signin:
            self.signin = True
            for s in self.screens:
                s["root"].add_css_class("signin")
                s["s2"].set_can_target(True)
        for s in self.screens:
            if s["entry"]:
                s["entry"].grab_focus()
        self.touch()

    def show_lock(self):
        self.signin = False
        for s in self.screens:
            s["root"].remove_css_class("signin")
            s["s2"].set_can_target(False)
            if s["entry"]:
                s["entry"].set_text("")
        if self.idle_id:
            GLib.source_remove(self.idle_id)
            self.idle_id = 0

    def touch(self):
        if self.idle_id:
            GLib.source_remove(self.idle_id)
        self.idle_id = GLib.timeout_add_seconds(SIGNIN_IDLE, self.idle_back)

    def idle_back(self):
        self.idle_id = 0
        if not self.busy:
            self.show_lock()
        return False

    def on_key(self, _c, keyval, _code, state):
        if keyval in (Gdk.KEY_Shift_L, Gdk.KEY_Shift_R, Gdk.KEY_Control_L, Gdk.KEY_Control_R,
                      Gdk.KEY_Alt_L, Gdk.KEY_Alt_R, Gdk.KEY_Super_L, Gdk.KEY_Super_R, Gdk.KEY_Caps_Lock):
            return False
        if not self.signin:
            if keyval == Gdk.KEY_Escape:
                return True
            self.show_signin()
            ch = Gdk.keyval_to_unicode(keyval)
            entry = next((s["entry"] for s in self.screens if s["entry"]), None)
            if entry and ch >= 32 and not state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
                entry.set_text(entry.get_text() + chr(ch))     # the first key counts already
                entry.set_position(-1)
            return True
        if keyval == Gdk.KEY_Escape:
            entry = next((s["entry"] for s in self.screens if s["entry"]), None)
            if entry and entry.get_text():
                entry.set_text("")
            else:
                self.show_lock()
            return True
        self.touch()
        return False

    def say(self, text, error=False):
        for s in self.screens:
            s["msg"].set_text(text)
            (s["msg"].add_css_class if error else s["msg"].remove_css_class)("error")

    def try_password(self, pw):
        if self.busy or not pw:
            return
        if PREVIEW:
            self.say("Preview – nothing to unlock")
            return
        self.busy = True
        entry = next((s["entry"] for s in self.screens if s["entry"]), None)
        if entry:
            entry.set_sensitive(False)
        self.say("Checking…")

        def check():
            ok = pam_check(pw)
            GLib.idle_add(self.checked, ok)
        threading.Thread(target=check, daemon=True).start()

    def checked(self, ok):
        self.busy = False
        entry = next((s["entry"] for s in self.screens if s["entry"]), None)
        if ok:
            self.say("Welcome")
            self.unlock()
            return False
        if entry:
            entry.set_sensitive(True)
            entry.set_text("")
            entry.grab_focus()
        self.say("The password is incorrect. Try again.", error=True)
        return False

    def unlock(self):
        if self.fp:
            self.fp.close()
        self.app.finish()


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=None, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.unlocked = False
        self.lock = None

    def do_activate(self):
        st = Gtk.Settings.get_default()   # unhinted text: round tops not cut off (see winshell.crisp_text)
        try:
            st.set_property("gtk-font-rendering", Gtk.FontRendering.MANUAL)
            st.set_property("gtk-xft-hinting", 0)
            st.set_property("gtk-xft-hintstyle", "hintnone")
        except (TypeError, AttributeError):
            pass
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.hold()
        self.screen = LockScreen(self, start_signin=PREVIEW and "signin" in sys.argv)
        display = Gdk.Display.get_default()
        monitors = [display.get_monitors().get_item(i) for i in range(display.get_monitors().get_n_items())]
        if PREVIEW:
            self.preview_window(monitors[0])
        else:
            self.lock = SL.Instance.new()
            if not self.lock.lock():
                print("lockscreen: could not lock", file=sys.stderr)
                self.quit_with(2)
                return
            for i, mon in enumerate(monitors):
                win = Gtk.Window(application=self)
                win.add_css_class("lock")
                child, keys = self.screen.build(primary=(i == 0), height=mon.get_geometry().height)
                win.set_child(child)
                win.add_controller(keys)
                self.lock.assign_window_to_monitor(win, mon)
        self.screen.tick()
        self.screen.update_status()
        GLib.timeout_add_seconds(10, self.screen.update_status)
        if self.screen.start_signin:
            GLib.timeout_add(300, lambda: self.screen.show_signin() and False)

    def preview_window(self, mon):
        win = Gtk.Window(application=self)
        LS.init_for_window(win)
        LS.set_namespace(win, "lockscreen-preview")
        LS.set_layer(win, LS.Layer.OVERLAY)
        for e in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
            LS.set_anchor(win, e, True)
        LS.set_exclusive_zone(win, -1)
        LS.set_keyboard_mode(win, LS.KeyboardMode.NONE)    # a preview must not take the keyboard
        win.add_css_class("lock")
        child, keys = self.screen.build(primary=True, height=mon.get_geometry().height)
        win.set_child(child)
        win.add_controller(keys)
        win.present()

    def finish(self):
        if self.lock:
            self.lock.unlock()
        self.unlocked = True
        GLib.timeout_add(150, lambda: self.quit_with(0))

    def quit_with(self, code):
        self.exit_code = code
        self.release()
        self.quit()
        return False


def main():
    app = App()
    app.exit_code = 1
    app.run([])
    # only a real unlock ends with 0 – anything else makes lock.sh start hyprlock
    return app.exit_code if (PREVIEW or app.unlocked) else 1


if __name__ == "__main__":
    sys.exit(main())
