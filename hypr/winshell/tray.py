"""System tray for winshell: StatusNotifierItem watcher + host, and the
app menus (com.canonical.dbusmenu) shown on right click.

Apps such as Discord, Steam or Mullvad put their tray icon on D-Bus as a
StatusNotifierItem and announce it to org.kde.StatusNotifierWatcher. That
registry is normally sni-watcher.py (its own service, started first, never
restarts); winshell then reads the items from it and draws the icons. Only if
no watcher is there, winshell is the watcher itself.
"""
import sys
import traceback

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

WATCHER = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_IFACES = ("org.kde.StatusNotifierItem", "org.freedesktop.StatusNotifierItem")

WATCHER_XML = """
<node>
  <interface name="org.kde.StatusNotifierWatcher">
    <method name="RegisterStatusNotifierItem"><arg name="service" type="s" direction="in"/></method>
    <method name="RegisterStatusNotifierHost"><arg name="service" type="s" direction="in"/></method>
    <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
    <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
    <property name="ProtocolVersion" type="i" access="read"/>
    <signal name="StatusNotifierItemRegistered"><arg type="s"/></signal>
    <signal name="StatusNotifierItemUnregistered"><arg type="s"/></signal>
    <signal name="StatusNotifierHostRegistered"/>
  </interface>
</node>"""


class TrayItem:
    """One tray icon (one StatusNotifierItem)."""

    def __init__(self, bus, path):
        self.bus, self.path = bus, path
        self.iface = ITEM_IFACES[0]
        self.props = {}
        self.menu_path = None

    @property
    def key(self):
        return f"{self.bus}{self.path}"

    @property
    def ident(self):
        """Stable name for remembering where the user put the icon."""
        return self.props.get("Id") or self.props.get("Title") or self.key

    @property
    def title(self):
        tip = self.props.get("ToolTip")
        if tip and len(tip) >= 3 and tip[2]:
            return tip[2]
        return self.props.get("Title") or self.ident

    @property
    def passive(self):
        return self.props.get("Status") == "Passive"

    def paintable(self, size=16):
        """Gdk.Paintable / icon name for the current state of the item."""
        attention = self.props.get("Status") == "NeedsAttention"
        name = (attention and self.props.get("AttentionIconName")) or self.props.get("IconName")
        pixmaps = (attention and self.props.get("AttentionIconPixmap")) or self.props.get("IconPixmap")
        theme_path = self.props.get("IconThemePath")
        display = Gdk.Display.get_default()
        theme = Gtk.IconTheme.get_for_display(display)
        if theme_path and theme_path not in theme.get_search_path():
            theme.add_search_path(theme_path)
        if name and (name.startswith("/") and GLib.file_test(name, GLib.FileTest.EXISTS)):
            try:
                return Gdk.Texture.new_from_filename(name)
            except GLib.Error:
                pass
        if name and theme.has_icon(name):
            return name
        if pixmaps:
            return pixmap_texture(pixmaps, size * 2)
        return name or "application-x-executable"


def pixmap_texture(pixmaps, want):
    """IconPixmap a(iiay), ARGB32 in network byte order -> Gdk.Texture."""
    best = None
    for w, h, data in pixmaps:
        if w <= 0 or h <= 0 or len(data) < w * h * 4:
            continue
        if best is None or abs(w - want) < abs(best[0] - want):
            best = (w, h, data)
    if not best:
        return None
    w, h, data = best
    argb = bytes(data[:w * h * 4])
    rgba = bytearray(len(argb))
    rgba[0::4], rgba[1::4], rgba[2::4], rgba[3::4] = argb[1::4], argb[2::4], argb[3::4], argb[0::4]
    pb = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(bytes(rgba)), GdkPixbuf.Colorspace.RGB,
                                         True, 8, w, h, w * 4)
    return Gdk.Texture.new_for_pixbuf(pb)


class Tray:
    """Keeps the list of tray items and tells `on_change` when it changes."""

    def __init__(self, on_change):
        self.on_change = on_change
        self.items = {}             # key -> TrayItem
        self.watched = {}           # bus name -> watch id
        self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.own_watcher = False
        self.node = Gio.DBusNodeInfo.new_for_xml(WATCHER_XML)
        # export the watcher object *before* taking the name: apps re-register
        # the moment the name appears, and those calls must not fail
        self.reg_id = self.conn.register_object(WATCHER_PATH, self.node.interfaces[0],
                                                self.on_method, self.on_get_property, None)
        Gio.bus_own_name_on_connection(self.conn, WATCHER, Gio.BusNameOwnerFlags.NONE,
                                       self.on_name_acquired, self.on_name_lost)

    # ── watcher (we own the registry) ──
    def on_name_acquired(self, conn, _name):
        self.own_watcher = True
        if not self.reg_id:         # dropped when the name was lost first (kded6 had it)
            self.reg_id = self.conn.register_object(WATCHER_PATH, self.node.interfaces[0],
                                                    self.on_method, self.on_get_property, None)
        conn.emit_signal(None, WATCHER_PATH, WATCHER, "StatusNotifierHostRegistered", None)

    def on_name_lost(self, _conn, _name):
        if not self.own_watcher and self.reg_id:
            self.conn.unregister_object(self.reg_id)
            self.reg_id = 0
        if not self.own_watcher:
            # someone else (e.g. Waybar) is the watcher: be a host of theirs
            GLib.timeout_add(300, self.use_foreign_watcher)

    def on_method(self, conn, sender, _path, _iface, method, params, invocation):
        if method == "RegisterStatusNotifierItem":
            service = params.unpack()[0]
            if service.startswith("/"):
                bus, path = sender, service
            else:
                bus, path = service, "/StatusNotifierItem"
            self.add(bus, path)
            conn.emit_signal(None, WATCHER_PATH, WATCHER, "StatusNotifierItemRegistered",
                             GLib.Variant("(s)", (f"{bus}{path}",)))
        invocation.return_value(None)

    def on_get_property(self, _conn, _sender, _path, _iface, prop):
        if prop == "RegisteredStatusNotifierItems":
            return GLib.Variant("as", list(self.items))
        if prop == "IsStatusNotifierHostRegistered":
            return GLib.Variant("b", True)
        if prop == "ProtocolVersion":
            return GLib.Variant("i", 0)
        return None

    # ── host of another watcher ──
    def use_foreign_watcher(self):
        if self.own_watcher:
            return False

        def got(conn, res):
            try:
                items = conn.call_finish(res).unpack()[0]
            except GLib.Error:
                return
            for entry in items:
                self.add_service(entry)
        self.conn.call(WATCHER, WATCHER_PATH, "org.freedesktop.DBus.Properties", "Get",
                       GLib.Variant("(ss)", (WATCHER, "RegisteredStatusNotifierItems")),
                       GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 2000, None, got)
        self.conn.signal_subscribe(None, WATCHER, "StatusNotifierItemRegistered", WATCHER_PATH, None,
                                   Gio.DBusSignalFlags.NONE,
                                   lambda *a: self.add_service(a[5].unpack()[0]))
        self.conn.call(WATCHER, WATCHER_PATH, WATCHER, "RegisterStatusNotifierHost",
                       GLib.Variant("(s)", (self.conn.get_unique_name(),)), None,
                       Gio.DBusCallFlags.NONE, 2000, None, None)
        return False

    def add_service(self, entry):
        """"bus/path" or "bus" as listed by a watcher."""
        if "/" in entry:
            i = entry.index("/")
            self.add(entry[:i], entry[i:])
        else:
            self.add(entry, "/StatusNotifierItem")

    # ── items ──
    def add(self, bus, path):
        item = TrayItem(bus, path)
        if item.key in self.items:
            return
        self.items[item.key] = item
        if bus not in self.watched:
            self.watched[bus] = Gio.bus_watch_name_on_connection(
                self.conn, bus, Gio.BusNameWatcherFlags.NONE, None, self.on_vanished)
        item.sub = self.conn.signal_subscribe(bus, None, None, path, None, Gio.DBusSignalFlags.NONE,
                                              lambda *_a, it=item: self.fetch(it))
        self.fetch(item)

    def on_vanished(self, _conn, bus):
        Gio.bus_unwatch_name(self.watched.pop(bus))
        for key in [k for k, it in self.items.items() if it.bus == bus]:
            it = self.items.pop(key)
            self.conn.signal_unsubscribe(it.sub)
            if self.own_watcher:
                self.conn.emit_signal(None, WATCHER_PATH, WATCHER, "StatusNotifierItemUnregistered",
                                      GLib.Variant("(s)", (key,)))
        self.on_change()

    def fetch(self, item, iface_index=0):
        """(Re)read all properties – items signal NewIcon/NewStatus/… without
        PropertiesChanged, so every signal means "read again"."""
        iface = ITEM_IFACES[iface_index]

        def got(conn, res):
            try:
                props = conn.call_finish(res).unpack()[0]
            except GLib.Error:
                if iface_index + 1 < len(ITEM_IFACES):
                    self.fetch(item, iface_index + 1)
                return
            item.iface = iface
            item.props = props
            item.menu_path = props.get("Menu")
            if item.key in self.items:
                self.on_change()
        self.conn.call(item.bus, item.path, "org.freedesktop.DBus.Properties", "GetAll",
                       GLib.Variant("(s)", (iface,)), GLib.VariantType("(a{sv})"),
                       Gio.DBusCallFlags.NONE, 3000, None, got)

    def call(self, item, method, args=None, on_error=None):
        def done(conn, res):
            try:
                conn.call_finish(res)
            except GLib.Error as e:
                if on_error:
                    on_error(e)
        self.conn.call(item.bus, item.path, item.iface, method, args, None,
                       Gio.DBusCallFlags.NONE, 3000, None, done)

    # ── clicks ──
    def activate(self, item, widget):
        if item.props.get("ItemIsMenu") and item.menu_path:
            self.menu(item, widget)
            return
        self.call(item, "Activate", GLib.Variant("(ii)", (0, 0)),
                  on_error=lambda _e: self.menu(item, widget))

    def secondary(self, item):
        self.call(item, "SecondaryActivate", GLib.Variant("(ii)", (0, 0)))

    def scroll(self, item, delta):
        self.call(item, "Scroll", GLib.Variant("(is)", (int(delta), "vertical")))

    def menu(self, item, widget):
        if item.menu_path:
            DBusMenu(self.conn, item.bus, item.menu_path, widget).show()
        else:
            self.call(item, "ContextMenu", GLib.Variant("(ii)", (0, 0)))


class DBusMenu:
    """Shows an app's com.canonical.dbusmenu menu as a popover."""
    IFACE = "com.canonical.dbusmenu"

    def __init__(self, conn, bus, path, anchor):
        self.conn, self.bus, self.path, self.anchor = conn, bus, path, anchor

    def show(self):
        self.conn.call(self.bus, self.path, self.IFACE, "AboutToShow", GLib.Variant("(i)", (0,)),
                       None, Gio.DBusCallFlags.NONE, 1000, None, lambda c, r: self.load())

    def load(self):
        def got(conn, res):
            try:
                _rev, layout = conn.call_finish(res).unpack()
            except GLib.Error as e:
                print("winshell: tray menu:", e.message, file=sys.stderr, flush=True)
                return
            try:
                self.popup(layout)
            except Exception:
                traceback.print_exc()
        self.conn.call(self.bus, self.path, self.IFACE, "GetLayout",
                       GLib.Variant("(iias)", (0, -1, [])), None,
                       Gio.DBusCallFlags.NONE, 3000, None, got)

    def popup(self, layout):
        pop = Gtk.Popover(has_arrow=False, position=Gtk.PositionType.TOP)
        pop.add_css_class("tray-menu")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        self.fill(box, layout[2], pop)
        pop.set_child(box)
        pop.set_parent(self.anchor)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: p.unparent() and False))
        pop.popup()

    def fill(self, box, children, pop, depth=0):
        for child in children:
            node = child.unpack() if isinstance(child, GLib.Variant) else child
            mid, props, sub = node
            if not props.get("visible", True):
                continue
            if props.get("type") == "separator":
                sep = Gtk.Separator()
                sep.add_css_class("tray-menu-sep")
                box.append(sep)
                continue
            text = props.get("label", "").replace("__", "\0").replace("_", "").replace("\0", "_")
            row = Gtk.Box(spacing=10)
            mark = ""
            if props.get("toggle-type") in ("checkmark", "radio"):
                mark = "✓" if props.get("toggle-state") == 1 else ""
            check = Gtk.Label(label=mark)
            check.set_size_request(14, -1)
            row.append(check)
            lbl = Gtk.Label(label=text, xalign=0.0)
            lbl.set_hexpand(True)
            row.append(lbl)
            btn = Gtk.Button(child=row)
            btn.add_css_class("tray-menu-item")
            btn.set_sensitive(props.get("enabled", True))
            if depth:
                btn.set_margin_start(14 * depth)
            if props.get("children-display") == "submenu" and sub:
                row.append(Gtk.Image.new_from_icon_name("win11-chevron-down"))
                inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
                rev = Gtk.Revealer(child=inner, transition_duration=120)
                self.fill(inner, sub, pop, depth + 1)
                btn.connect("clicked", lambda _b, r=rev: r.set_reveal_child(not r.get_reveal_child()))
                box.append(btn)
                box.append(rev)
                continue
            btn.connect("clicked", lambda _b, i=mid: (self.event(i), pop.popdown()))
            box.append(btn)

    def event(self, mid):
        self.conn.call(self.bus, self.path, self.IFACE, "Event",
                       GLib.Variant("(isvu)", (mid, "clicked", GLib.Variant("s", ""), 0)),
                       None, Gio.DBusCallFlags.NONE, 2000, None, None)
