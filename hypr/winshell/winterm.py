#!/usr/bin/env python3
"""winterm – a terminal that looks and works like Windows Terminal.

The tabs sit in the title bar: each with its icon, title and ×, rounded at
(new tab), and on the right minimize / maximize / close.
(new tab) and ⌄ (menu), and on the right minimize / maximize / close.
The terminal itself is VTE (the terminal of GNOME Terminal), with Windows
Terminal's Campbell colours and Cascadia Mono.

One process for all windows (starting it again opens a new window in the
running one). Shortcuts like Windows Terminal:
  Ctrl+Shift+T / D  new tab (in the same folder) / duplicate tab
  Ctrl+Shift+W      close tab          Ctrl+Shift+N  new window
  Ctrl+Tab / Ctrl+Shift+Tab  next / previous tab, Ctrl+Alt+1…9 tab 1…9
  Ctrl+C            copy (with a selection) – else interrupt, as usual
  Ctrl+V / Ctrl+Shift+C / Ctrl+Shift+V   paste / copy / paste
  Ctrl+= / Ctrl+- / Ctrl+0   bigger / smaller / normal text
Mouse: middle click on a tab closes it, double click on the free part of the
title bar maximizes the window. "winterm --new-tab" opens a tab in the last
used window.
"""
import os
import pwd
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
try:
    gi.require_version("Vte", "3.91")
except ValueError:
    # VTE for GTK 4 isn't installed (sudo dnf install vte291-gtk4): kitty instead
    os.execvp("kitty", ["kitty"] + sys.argv[1:])
from gi.repository import Gdk, Gio, GLib, Graphene, Gtk, Pango, Vte  # noqa: E402

APP_ID = "io.github.noe1.winterm"
FONT = "Cascadia Mono 12"
SCROLLBACK = 10000
TITLE_CHARS = 18            # a tab's title at most this wide: tabs of about 175 px

# Campbell, Windows Terminal's default colours
BG, FG, CURSOR = "#0c0c0c", "#cccccc", "#ffffff"
PALETTE = ["#0c0c0c", "#c50f1f", "#13a10e", "#c19c00", "#0037da", "#881798", "#3a96dd", "#cccccc",
           "#767676", "#e74856", "#16c60c", "#f9f1a5", "#3b78ff", "#b4009e", "#61d6d6", "#f2f2f2"]

# caption buttons: the same glyphs as the other windows' title bars (codicons)
GLYPH_MIN, GLYPH_MAX, GLYPH_RESTORE, GLYPH_CLOSE = "", "", "", ""

CSS = """
window.wt-window, window.wt-window.csd {
    background: #0c0c0c; border-radius: 0; box-shadow: none; margin: 0; border: none;
}
window.wt-window decoration { box-shadow: none; border-radius: 0; margin: 0; }

/* no theme look on any button up here – only what's set below */
.wt-strip button {
    background: none; background-image: none; border: none; border-image: none;
    box-shadow: none; outline: none; text-shadow: none; -gtk-icon-shadow: none;
}

/* the title bar with the tabs */
.wt-strip { background: #202020; min-height: 40px; }
.wt-tabs { margin-left: 8px; }
.wt-tab {
    min-height: 36px; margin-top: 4px; padding: 0 4px 0 12px;
    border-radius: 8px 8px 0 0; background: transparent; color: #c8c8c8;
    font-family: "Segoe UI", "Selawik", "Inter", sans-serif; font-size: 12px;
    transition: background-color 90ms ease-out;
}
.wt-tab:hover { background: rgba(255, 255, 255, 0.06); }
.wt-tab.active, .wt-tab.active:hover { background: #0c0c0c; color: #ffffff; }
.wt-tab label { margin: 0 10px 0 8px; }
.wt-sep { background: #3c3c3c; min-width: 1px; margin: 14px 0 8px 0; }
.wt-sep.hidden { background: transparent; }

button.wt-close {
    min-width: 28px; min-height: 26px; padding: 0; margin: 0;
    border-radius: 4px; border: none; box-shadow: none; background: transparent;
    color: #c8c8c8; font-family: "Symbols Nerd Font"; font-size: 9pt;
}
button.wt-close:hover { background: rgba(255, 255, 255, 0.09); color: #ffffff; }

/* + exactly like the tabs' ×: same size, same height (the middle of a tab, 22 px) */
button.wt-new {
    min-width: 28px; min-height: 26px; margin: 9px 0 0 6px; padding: 0;
    border-radius: 4px; border: none; box-shadow: none; background: transparent;
}
button.wt-new:hover { background: rgba(255, 255, 255, 0.07); }

/* minimize / maximize / close, like the other title bars */
button.wt-cap {
    min-width: 46px; min-height: 40px; padding: 0; margin: 0;
    border-radius: 0; border: none; box-shadow: none; outline: none;
    background: transparent; color: #ffffff;
    font-family: "Symbols Nerd Font"; font-size: 10pt;
}
button.wt-cap:hover { background: rgba(255, 255, 255, 0.08); }
button.wt-cap:active { background: rgba(255, 255, 255, 0.05); }
button.wt-cap.wt-x:hover { background: #c42b1c; }
button.wt-cap.wt-x:active { background: #b22a1b; }

vte-terminal { padding: 8px 10px; }
scrollbar { background: transparent; }
"""


def rgba(hexcolor):
    c = Gdk.RGBA()
    c.parse(hexcolor)
    return c


def user_shell():
    try:
        return pwd.getpwuid(os.getuid()).pw_shell or "/bin/bash"
    except KeyError:
        return os.environ.get("SHELL", "/bin/bash")


class Tab:
    """One terminal: its widget in the stack and its tab in the title bar."""

    def __init__(self, win, cwd=None):
        self.win = win
        self.title = "Terminal"
        t = Vte.Terminal()
        self.term = t
        t.set_font(Pango.FontDescription.from_string(FONT))
        t.set_colors(rgba(FG), rgba(BG), [rgba(c) for c in PALETTE])
        t.set_color_cursor(rgba(CURSOR))
        t.set_color_cursor_foreground(rgba(BG))
        t.set_cursor_shape(Vte.CursorShape.IBEAM)
        t.set_cursor_blink_mode(Vte.CursorBlinkMode.ON)
        t.set_scrollback_lines(SCROLLBACK)
        t.set_audible_bell(False)
        t.set_mouse_autohide(True)
        t.set_bold_is_bright(False)
        t.set_scroll_on_keystroke(True)
        t.set_scroll_on_output(False)
        t.set_hexpand(True)
        t.set_vexpand(True)
        t.connect("child-exited", lambda *_: self.win.close_tab(self))
        for sig in ("window-title-changed", "termprop-changed"):
            try:
                t.connect(sig, lambda *_: self.update_title())
            except TypeError:
                pass
        self.page = Gtk.ScrolledWindow(child=t, hexpand=True, vexpand=True)
        self.page.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.page.set_overlay_scrolling(True)
        self.build_tab()
        self.spawn(cwd or os.path.expanduser("~"))

    # ── the tab in the title bar ──
    def build_tab(self):
        box = Gtk.Box(spacing=0, valign=Gtk.Align.END, hexpand=False)
        box.add_css_class("wt-tab")
        img = Gtk.Image.new_from_icon_name("win11-terminal")
        img.set_pixel_size(16)
        box.append(img)
        # the tab is at most about 120 px wide (TITLE_CHARS of title, cut off
        # with …); it gets narrower only when the tabs don't all fit any more
        # (the window never gets wider, a strip to drag it stays free)
        self.label = Gtk.Label(label=self.title, xalign=0.0, hexpand=True,
                               ellipsize=Pango.EllipsizeMode.END)
        self.label.set_width_chars(1)
        self.label.set_max_width_chars(TITLE_CHARS)
        box.append(self.label)
        # the × as a picture: a font glyph sat off the middle of its hover box
        xi = Gtk.Image.new_from_icon_name("win11-close")
        xi.set_pixel_size(12)
        x = Gtk.Button(child=xi, valign=Gtk.Align.CENTER)
        x.add_css_class("wt-close")
        x.set_focusable(False)
        x.set_tooltip_text("Close tab")
        x.connect("clicked", lambda *_: self.win.close_tab(self))
        box.append(x)
        self.close_btn = x
        # press: select it (and no window drag from a tab); drag sideways:
        # move it between the others, like Windows Terminal
        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self.on_drag_begin)
        drag.connect("drag-update", self.on_drag_update)
        drag.connect("drag-end", lambda *_: self.win.drag_end())
        box.add_controller(drag)
        # middle click closes it
        mid = Gtk.GestureClick(button=Gdk.BUTTON_MIDDLE)
        mid.connect("released", lambda *_: self.win.close_tab(self))
        box.add_controller(mid)
        self.tab = box

    def on_close_button(self, x, y):
        w = self.tab.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and w is not self.tab:
            if w is self.close_btn:
                return True
            w = w.get_parent()
        return False

    def on_drag_begin(self, g, x, y):
        if self.on_close_button(x, y):
            g.set_state(Gtk.EventSequenceState.DENIED)  # the × gets its click
            return
        g.set_state(Gtk.EventSequenceState.CLAIMED)
        self.drag_x0 = x
        self.win.select(self)
        self.win.drag_begin(self)

    def on_drag_update(self, g, dx, _dy):
        ok_, p = self.tab.compute_point(self.win.tabbox, Graphene.Point().init(self.drag_x0 + dx, 0))
        if ok_:
            self.win.drag_to(self, dx, p.x)

    def update_title(self):
        title = None
        for getter in ("get_window_title",):
            try:
                title = getattr(self.term, getter)()
            except Exception:
                title = None
        if not title:
            try:
                ok, title = self.term.get_termprop_string(Vte.TERMPROP_XTERM_TITLE)
                title = title if ok else None
            except Exception:
                title = None
        if title and title != self.title:
            self.title = title
            self.label.set_text(title)
            self.tab.set_tooltip_text(title)
            if self.win.current is self:
                self.win.set_title(title)

    # ── the shell ──
    def spawn(self, cwd):
        shell = user_shell()
        args = (Vte.PtyFlags.DEFAULT, cwd, [shell], None, GLib.SpawnFlags.DEFAULT,
                None, None, -1, None, None, None)
        try:
            self.term.spawn_async(*args)
        except TypeError:                   # older bindings: no child_setup_data
            self.term.spawn_async(*(args[:6] + args[7:]))

    def cwd(self):
        uri = None
        try:
            uri = self.term.get_current_directory_uri()
        except Exception:
            pass
        if not uri:
            try:
                ok, uri = self.term.get_termprop_uri(Vte.TERMPROP_CURRENT_DIRECTORY_URI)
                uri = uri.to_string() if ok and uri else None
            except Exception:
                uri = None
        if uri:
            path = Gio.File.new_for_uri(uri).get_path()
            if path and os.path.isdir(path):
                return path
        return None

    # ── clipboard, text size ──
    def copy(self):
        if self.term.get_has_selection():
            self.term.copy_clipboard_format(Vte.Format.TEXT)
            return True
        return False

    def paste(self):
        self.term.paste_clipboard()

    def zoom(self, step):
        scale = 1.0 if step == 0 else max(0.5, min(3.0, self.term.get_font_scale() + step))
        self.term.set_font_scale(scale)


class Window(Gtk.ApplicationWindow):
    def __init__(self, app, cwd=None):
        super().__init__(application=app, title="Terminal")
        self.add_css_class("wt-window")
        self.set_default_size(1000, 640)
        self.tabs = []
        self.current = None

        strip = Gtk.Box(spacing=0)
        strip.add_css_class("wt-strip")
        self.tabbox = Gtk.Box(spacing=0, valign=Gtk.Align.END)
        self.tabbox.add_css_class("wt-tabs")
        # the tabs never make the window wider: they get narrower, and with
        # very many the row scrolls (mouse wheel over the tabs)
        self.tabscroll = Gtk.ScrolledWindow(child=self.tabbox, valign=Gtk.Align.END,
                                            propagate_natural_width=True, propagate_natural_height=True)
        self.tabscroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.NEVER)
        # the title inside each tab may grow (hexpand) – that must not make
        # the whole row take the free space of the title bar
        self.tabscroll.set_hexpand(False)
        self.tabbox.set_hexpand(False)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        wheel.connect("scroll", self.on_tab_wheel)
        self.tabscroll.add_controller(wheel)
        strip.append(self.tabscroll)
        pi = Gtk.Image.new_from_icon_name("win11-add")
        pi.set_pixel_size(12)
        plus = Gtk.Button(child=pi, valign=Gtk.Align.START)
        plus.add_css_class("wt-new")
        plus.set_focusable(False)
        plus.set_tooltip_text("New tab (Ctrl+Shift+T)")
        plus.connect("clicked", lambda *_: self.new_tab(self.current.cwd() if self.current else None))
        strip.append(plus)
        space = Gtk.Box(hexpand=True)               # the free part: drag the window here
        space.set_size_request(40, -1)              # … always some of it, even with many tabs
        strip.append(space)
        for glyph, cls, act in ((GLYPH_MIN, "wt-min", self.minimize),
                                (GLYPH_MAX, "wt-max", self.toggle_max),
                                (GLYPH_CLOSE, "wt-x", self.close)):
            b = Gtk.Button(label=glyph, valign=Gtk.Align.START)
            b.add_css_class("wt-cap")
            b.add_css_class(cls)
            b.set_focusable(False)
            b.connect("clicked", lambda *_, f=act: f())
            strip.append(b)
            if cls == "wt-max":
                self.max_btn = b
        handle = Gtk.WindowHandle(child=strip)
        self.set_titlebar(handle)
        self.max_shown = None

        self.stack = Gtk.Stack(hexpand=True, vexpand=True)
        self.set_child(self.stack)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.add_controller(keys)
        self.new_tab(cwd)

    # ── tabs ──
    def new_tab(self, cwd=None):
        tab = Tab(self, cwd)
        self.tabs.append(tab)
        sep = Gtk.Box()
        sep.add_css_class("wt-sep")
        tab.sep = sep
        self.tabbox.append(tab.tab)
        self.tabbox.append(sep)
        self.stack.add_child(tab.page)
        self.select(tab)
        return tab

    def on_tab_wheel(self, _c, dx, dy):
        adj = self.tabscroll.get_hadjustment()
        adj.set_value(adj.get_value() + (dx + dy) * 40)
        return True

    def scroll_into_view(self, tab):
        ok_, b = tab.tab.compute_bounds(self.tabbox)
        if not ok_:
            return False
        adj = self.tabscroll.get_hadjustment()
        if b.get_x() < adj.get_value():
            adj.set_value(b.get_x())
        elif b.get_x() + b.get_width() > adj.get_value() + adj.get_page_size():
            adj.set_value(b.get_x() + b.get_width() - adj.get_page_size())
        return False

    def select(self, tab):
        GLib.idle_add(self.scroll_into_view, tab)
        self.current = tab
        self.stack.set_visible_child(tab.page)
        for i, t in enumerate(self.tabs):
            (t.tab.add_css_class if t is tab else t.tab.remove_css_class)("active")
        self.update_seps()
        self.set_title(tab.title)
        tab.term.grab_focus()

    def update_seps(self):
        """A line between two tabs – not next to the active one (as in
        Windows Terminal), and none after the last."""
        for i, t in enumerate(self.tabs):
            nxt = self.tabs[i + 1] if i + 1 < len(self.tabs) else None
            show = nxt is not None and t is not self.current and nxt is not self.current
            (t.sep.remove_css_class if show else t.sep.add_css_class)("hidden")

    def close_tab(self, tab):
        if tab not in self.tabs:
            return
        i = self.tabs.index(tab)
        self.tabs.remove(tab)
        self.tabbox.remove(tab.tab)
        self.tabbox.remove(tab.sep)
        self.stack.remove(tab.page)
        if not self.tabs:
            self.destroy()
            return
        if self.current is tab:
            self.select(self.tabs[min(i, len(self.tabs) - 1)])
        else:
            self.update_seps()

    # ── moving a tab by dragging it ──
    DRAG_START = 8                  # px before a press becomes a drag

    def drag_begin(self, tab):
        self.dragging = tab

    def drag_to(self, tab, dx, x):
        """The pointer is at x (in the tab strip): the dragged tab takes the
        place whose middle it passed – the others make room."""
        if getattr(self, "dragging", None) is not tab or abs(dx) < self.DRAG_START:
            return
        i = self.tabs.index(tab)
        target = i
        for j, t in enumerate(self.tabs):
            if t is tab:
                continue
            ok_, b = t.tab.compute_bounds(self.tabbox)
            if not ok_:
                continue
            mid = b.get_x() + b.get_width() / 2
            if j < i and x < mid:
                target = min(target, j)
            elif j > i and x > mid:
                target = max(target, j)
        if target != i:
            self.tabs.insert(target, self.tabs.pop(i))
            prev = None
            for t in self.tabs:
                self.tabbox.reorder_child_after(t.tab, prev)
                self.tabbox.reorder_child_after(t.sep, t.tab)
                prev = t.sep
            self.update_seps()

    def drag_end(self):
        self.dragging = None

    def step(self, delta):
        if self.current in self.tabs and len(self.tabs) > 1:
            i = (self.tabs.index(self.current) + delta) % len(self.tabs)
            self.select(self.tabs[i])

    # Hyprland tells every window "maximized" (so apps draw no shadows of
    # their own) – whether it really is: as wide as its screen
    def do_size_allocate(self, width, height, baseline):
        Gtk.ApplicationWindow.do_size_allocate(self, width, height, baseline)
        GLib.idle_add(self.update_max)

    def really_maximized(self):
        surf = self.get_surface()
        mon = surf and Gdk.Display.get_default().get_monitor_at_surface(surf)
        return bool(mon) and self.get_width() >= mon.get_geometry().width - 4

    def update_max(self):
        on = self.really_maximized()
        if on != self.max_shown:
            self.max_shown = on
            self.max_btn.set_label(GLYPH_RESTORE if on else GLYPH_MAX)
            self.max_btn.set_tooltip_text("Restore Down" if on else "Maximize")
        return False

    def toggle_max(self):
        # the same Windows-style maximize as the other title bars' □
        try:
            Gio.Subprocess.new([os.path.expanduser("~/.config/hypr/scripts/winctl.sh"), "maximize"],
                               Gio.SubprocessFlags.NONE)
        except GLib.Error:
            pass

    # ── keys ──
    def on_key(self, _c, keyval, _code, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(state & Gdk.ModifierType.ALT_MASK)
        if not ctrl:
            return False
        k = Gdk.keyval_to_lower(keyval)
        tab = self.current
        here = tab.cwd() if tab else None
        if shift and not alt:
            if k in (Gdk.KEY_t, Gdk.KEY_d):
                self.new_tab(here)
            elif k == Gdk.KEY_w:
                tab and self.close_tab(tab)
            elif k == Gdk.KEY_n:
                self.get_application().new_window(here)
            elif k == Gdk.KEY_c:
                tab and tab.copy()
            elif k == Gdk.KEY_v:
                tab and tab.paste()
            elif k == Gdk.KEY_ISO_Left_Tab:
                self.step(-1)
            else:
                return False
            return True
        if alt and not shift and Gdk.KEY_1 <= k <= Gdk.KEY_9:
            n = k - Gdk.KEY_1
            if n < len(self.tabs):
                self.select(self.tabs[n])
            return True
        if not shift and not alt:
            if k == Gdk.KEY_Tab:
                self.step(1)
                return True
            if k == Gdk.KEY_c:
                return bool(tab and tab.copy())     # nothing selected: Ctrl+C goes to the program
            if k == Gdk.KEY_v:
                tab and tab.paste()
                return True
            if k in (Gdk.KEY_equal, Gdk.KEY_plus, Gdk.KEY_KP_Add):
                tab and tab.zoom(0.1)
                return True
            if k in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                tab and tab.zoom(-0.1)
                return True
            if k in (Gdk.KEY_0, Gdk.KEY_KP_0):
                tab and tab.zoom(0)
                return True
        return False


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_string(CSS) if hasattr(provider, "load_from_string") else provider.load_from_data(CSS.encode())
        # above the user's own GTK theme (~/.config/gtk-4.0), which styles
        # every button with a frame and a background
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)

    def new_window(self, cwd=None):
        w = Window(self, cwd)
        w.present()
        return w

    def do_command_line(self, cmdline):
        # every start opens a window – in the running process, in the folder
        # it was started from (or --working-directory DIR)
        args = cmdline.get_arguments()[1:]
        cwd = cmdline.get_cwd()
        if "--new-tab" in args:                 # winterm --new-tab: a tab in the last used window
            wins = [w for w in self.get_windows() if isinstance(w, Window)]
            if wins:
                wins[0].new_tab(cwd if cwd and os.path.isdir(cwd) else None)
                wins[0].present()
                return 0
        if "--working-directory" in args:
            i = args.index("--working-directory")
            if i + 1 < len(args):
                cwd = args[i + 1]
        self.new_window(cwd if cwd and os.path.isdir(cwd) else None)
        return 0


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
