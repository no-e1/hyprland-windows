"""'This PC' for Dolphin, like Windows: a folder with one tile per drive –
a drive icon with a bar for how full it is, and "120 GB free of 371 GB".

Dolphin has no such view, so winshell keeps a small folder up to date
(~/Desktop/.thispc, opened as desktop:/.thispc – only KDE's desktop:/
shows the names inside .desktop files; "This PC" in the sidebar points there):
  * a mounted drive is a link (.desktop, Type=Link) – double-click opens it
  * a drive that isn't mounted yet mounts on double-click (udisksctl), then opens
  * a BitLocker drive (Windows C:) can't be opened here: it says so
The icons are drawn here (SVG), the free space is refreshed every few
seconds and whenever a drive is mounted or unmounted.
"""
import hashlib
import json
import os
import shlex
import subprocess

from gi.repository import Gio, GLib

DIR = os.path.join(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DESKTOP)
                   or os.path.expanduser("~/Desktop"), ".thispc")
ICONS = os.path.join(DIR, ".icons")
REFRESH = 15                    # s
REAL_FS = {"ext4", "ext3", "btrfs", "xfs", "ntfs", "ntfs3", "fuseblk", "vfat", "exfat", "f2fs"}
SKIP_PARTS = {"EFI System", "Microsoft reserved", "Windows recovery environment", "BIOS boot"}
BLUE, RED = "#2b8fdd", "#e81123"      # Explorer's bar colors


def gb(n):
    """Like Windows: 1024³ bytes, shown as "GB"."""
    v = n / 1024 ** 3
    return f"{v:.0f} GB" if v >= 10 else f"{v:.1f} GB"


def os_name():
    try:
        for line in open("/etc/os-release"):
            if line.startswith("NAME="):
                return line.split("=", 1)[1].strip().strip('"').split()[0]
    except OSError:
        pass
    return "Linux"


def drives():
    """[(key, name, detail, used 0..1 or None, kind, target)] – kind: mounted |
    unmounted | locked; target: the folder, or the device to mount."""
    out, seen = [], set()
    try:
        mounts = [line.split()[:3] for line in open("/proc/self/mounts")]
    except OSError:
        mounts = []
    for src, target, fs in mounts:
        target = target.replace("\\040", " ")
        if not src.startswith("/dev/") or fs not in REAL_FS or src in seen:
            continue
        if target != "/" and not target.startswith(("/run/media/", "/media/", "/mnt/")):
            continue                    # /boot, /boot/efi … aren't drives you use
        seen.add(src)
        try:
            st = os.statvfs(target)
        except OSError:
            continue
        total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        if total <= 0:
            continue
        name = f"{os_name()} (/)" if target == "/" else os.path.basename(target) or target
        out.append((src, name, f"{gb(free)} free of {gb(total)}", 1 - free / total, "mounted", target))
    # partitions that aren't mounted (yet)
    try:
        tree = json.loads(subprocess.run(
            ["lsblk", "-J", "-b", "-o", "PATH,FSTYPE,LABEL,SIZE,TYPE,MOUNTPOINTS,PARTTYPENAME"],
            capture_output=True, text=True, timeout=3).stdout)
    except Exception:
        tree = {}

    def walk(devs):
        for d in devs or []:
            yield d
            yield from walk(d.get("children"))
    for d in walk(tree.get("blockdevices")):
        if d.get("type") != "part" or d.get("path") in seen:
            continue
        if any(d.get("mountpoints") or []) or d.get("parttypename") in SKIP_PARTS:
            continue
        fs, size, label = (d.get("fstype") or ""), int(d.get("size") or 0), d.get("label") or ""
        if fs == "BitLocker":
            name = "Windows (C:)" if "C:" in label else (label or "BitLocker drive")
            out.append((d["path"], name, f"{gb(size)} · locked (BitLocker)", None, "locked", d["path"]))
        elif fs in REAL_FS and size > 2 * 1024 ** 3:
            out.append((d["path"], label or f"{gb(size)} Volume", f"{gb(size)} · not opened yet",
                        None, "unmounted", d["path"]))
    return out


def icon_svg(used, kind, system):
    """Windows 11's drive: a flat grey drive seen from the front (lighter top,
    darker base, a small light), the Windows logo on the system drive; below
    it the capacity bar the way Explorer draws it in dark mode – a light
    1 px frame, dark inside, blue fill (red when over 90 % full)."""
    body = ('<defs><linearGradient id="t" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#e9e9e9"/><stop offset="1" stop-color="#c9c9c9"/></linearGradient>'
            '<linearGradient id="f" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#a9a9a9"/><stop offset="1" stop-color="#8b8b8b"/></linearGradient></defs>'
            # top face and front of the drive
            '<path d="M40 64h176l20 44H20z" fill="url(#t)"/>'
            '<rect x="20" y="108" width="216" height="52" rx="6" fill="url(#f)"/>'
            '<rect x="20" y="108" width="216" height="52" rx="6" fill="none" stroke="#6d6d6d" stroke-width="2"/>'
            '<path d="M40 64h176l20 44H20z" fill="none" stroke="#8d8d8d" stroke-width="2" stroke-linejoin="round"/>'
            f'<rect x="196" y="128" width="22" height="8" rx="2" fill="{"#3ddc84" if kind == "mounted" else "#6b6b6b"}"/>')
    if system:      # Windows' C: carries the Windows logo
        body += ('<g fill="#2b8fdd"><rect x="38" y="118" width="14" height="14"/><rect x="54" y="118" width="14" height="14"/>'
                 '<rect x="38" y="134" width="14" height="14"/><rect x="54" y="134" width="14" height="14"/></g>')
    if kind == "locked":
        body += ('<g transform="translate(100 112)"><rect x="0" y="16" width="56" height="40" rx="6" fill="#f2b300" stroke="#7a5a00" stroke-width="3"/>'
                 '<path d="M12 16v-6a16 16 0 0 1 32 0v6" fill="none" stroke="#7a5a00" stroke-width="7"/></g>')
    if used is not None:
        w = max(3, round(214 * used))
        body += ('<rect x="20" y="194" width="216" height="30" fill="#262626" stroke="#8a8a8a" stroke-width="2"/>'
                 f'<rect x="21" y="195" width="{w}" height="28" fill="{RED if used > 0.9 else BLUE}"/>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256">'
            + body + '</svg>\n')


class ThisPC:
    def __init__(self):
        self.last = None
        os.makedirs(ICONS, exist_ok=True)
        self.vm = Gio.VolumeMonitor.get()
        for sig in ("mount-added", "mount-removed", "volume-added", "volume-removed"):
            self.vm.connect(sig, lambda *_: GLib.timeout_add(400, self.refresh) and None)
        self.refresh()
        GLib.timeout_add_seconds(REFRESH, lambda: self.refresh() or True)

    def refresh(self):
        try:
            items = drives()
        except Exception as e:
            print("winshell: This PC:", e)
            return False
        if items == self.last:
            return False
        self.last = items
        keep = set()
        for i, (key, name, detail, used, kind, target) in enumerate(items):
            svg = icon_svg(used, kind, target == "/")
            ico = os.path.join(ICONS, hashlib.sha1(svg.encode()).hexdigest()[:12] + ".svg")
            if not os.path.exists(ico):
                with open(ico, "w") as f:
                    f.write(svg)
            keep.add(os.path.basename(ico))
            # two lines under the tile: the name, then the free space
            # (U+2028 is a line break for Qt's text layout)
            label = f"{name} {detail}"
            if kind == "mounted":
                entry = f"Type=Link\nURL=file://{target}\n"
            elif kind == "unmounted":
                cmd = (f"m=$(udisksctl mount -b {shlex.quote(target)} | sed -n 's/^Mounted .* at //p'); "
                       '[ -n "$m" ] && dolphin "$m"')
                entry = f"Type=Application\nExec=sh -c {shlex.quote(cmd)}\n"
            else:
                msg = ("This drive is encrypted with BitLocker. Windows can open it; "
                       "on Linux it needs the BitLocker recovery key.")
                entry = f"Type=Application\nExec=notify-send -i drive-harddisk {shlex.quote(name)} {shlex.quote(msg)}\n"
            path = os.path.join(DIR, f"{i:02d}.desktop")
            text = f"[Desktop Entry]\nName={label}\nIcon={ico}\n{entry}"
            try:
                old = open(path).read()
            except OSError:
                old = None
            if old != text:
                with open(path, "w") as f:
                    f.write(text)
                os.chmod(path, 0o755)       # Dolphin runs .desktop files only when executable
            keep.add(os.path.basename(path))
        for fname in os.listdir(DIR):
            if fname.endswith(".desktop") and fname not in keep:
                os.unlink(os.path.join(DIR, fname))
        for fname in os.listdir(ICONS):
            if fname not in keep:
                os.unlink(os.path.join(ICONS, fname))
        return False
