#!/usr/bin/env python3
"""The tray's registry (org.kde.StatusNotifierWatcher) as a small service of
its own that runs the whole session (Hyprland starts it first).

Apps such as Mullvad or Discord announce their tray icon to the watcher once,
when they start. When the watcher lived inside winshell, every winshell
restart handed the name to KDE's kded6 (it runs along and queues for it), the
apps' icons were gone from the taskbar until the apps were restarted. This
one never restarts; winshell only shows the icons it lists (tray.py, as a
"host").
"""
import sys

import gi
from gi.repository import Gio, GLib

WATCHER = "org.kde.StatusNotifierWatcher"
PATH = "/StatusNotifierWatcher"
XML = """
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
    <signal name="StatusNotifierHostUnregistered"/>
  </interface>
</node>"""


class Watcher:
    def __init__(self):
        self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.items = []             # "bus/path", in the order they came
        self.hosts = {}             # bus name -> watch id
        self.watches = {}           # item bus name -> watch id
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        self.conn.register_object(PATH, node.interfaces[0], self.on_method, self.on_property, None)
        Gio.bus_own_name_on_connection(self.conn, WATCHER, Gio.BusNameOwnerFlags.NONE,
                                       self.on_acquired, None)

    def emit(self, signal, arg=None):
        self.conn.emit_signal(None, PATH, WATCHER, signal,
                              GLib.Variant("(s)", (arg,)) if arg is not None else None)

    def on_acquired(self, *_):
        print("sni-watcher: is the tray watcher", file=sys.stderr, flush=True)
        self.emit("StatusNotifierHostRegistered")

    def on_method(self, _conn, sender, _path, _iface, method, params, invocation):
        service = params.unpack()[0]
        if method == "RegisterStatusNotifierItem":
            if service.startswith("/"):             # just a path: on the caller's connection
                bus, path = sender, service
            else:
                bus, path = service, "/StatusNotifierItem"
            entry = f"{bus}{path}"
            if entry not in self.items:
                self.items.append(entry)
                if bus not in self.watches:
                    self.watches[bus] = Gio.bus_watch_name_on_connection(
                        self.conn, bus, Gio.BusNameWatcherFlags.NONE, None, self.item_gone)
                self.emit("StatusNotifierItemRegistered", entry)
        elif method == "RegisterStatusNotifierHost":
            host = sender
            if host not in self.hosts:
                self.hosts[host] = Gio.bus_watch_name_on_connection(
                    self.conn, host, Gio.BusNameWatcherFlags.NONE, None, self.host_gone)
                self.emit("StatusNotifierHostRegistered")
        invocation.return_value(None)

    def on_property(self, _conn, _sender, _path, _iface, prop):
        if prop == "RegisteredStatusNotifierItems":
            return GLib.Variant("as", self.items)
        if prop == "IsStatusNotifierHostRegistered":
            # always: apps only show an icon while a host is there, and
            # winshell (the host) may be restarting for a moment
            return GLib.Variant("b", True)
        if prop == "ProtocolVersion":
            return GLib.Variant("i", 0)
        return None

    def item_gone(self, _conn, bus):
        Gio.bus_unwatch_name(self.watches.pop(bus))
        for entry in [e for e in self.items if e.startswith(bus + "/")]:
            self.items.remove(entry)
            self.emit("StatusNotifierItemUnregistered", entry)

    def host_gone(self, _conn, host):
        Gio.bus_unwatch_name(self.hosts.pop(host))


if __name__ == "__main__":
    Watcher()
    GLib.MainLoop().run()
