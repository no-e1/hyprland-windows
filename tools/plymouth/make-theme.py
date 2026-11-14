#!/usr/bin/env python3
"""Builds the "win11-signin" Plymouth theme: the disk passphrase prompt at
boot looks like the sign-in of winshell's lock screen (lockscreen.py) –
the blurred, dimmed wallpaper, the round profile picture, the name, the
password field with "Password" in it and dots while typing.

Plymouth can't draw rounded boxes or nice text by itself (fonts in the boot
image are limited), so everything is drawn here into pictures, sized for
this screen; win11-signin.script only places them.

    make-theme.py <output folder>      (tools/plymouth/install.sh calls it)

Reads the current wallpaper (~/.config/hypr/.current_wallpaper) and ~/.face.
Needs python3-pillow (also used by the lock screen).
"""
import json
import os
import pwd
import shutil
import subprocess
import sys

import cairo
import gi
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402
from PIL import Image, ImageDraw, ImageFilter  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1]
HOME = os.path.expanduser("~")

# the lock screen's sizes (lockscreen.py, logical px) – drawn at the screen's scale
AVATAR, NAME_PT, NAME_GAP, BOX_W, BOX_H, BOX_GAP = 150, 27, 18, 290, 34, 22


def screen():
    """Size and scale of the main screen (Hyprland), else 2880x1800 at 2x."""
    try:
        mons = json.loads(subprocess.run(["hyprctl", "-j", "monitors"], capture_output=True,
                                         text=True, timeout=3).stdout)
        m = next((m for m in mons if m.get("focused")), mons[0])
        return m["width"], m["height"], float(m.get("scale") or 1)
    except Exception:
        return 2880, 1800, 2.0


def display_name():
    """As winshell shows it: the full name, else the login name, capitalized."""
    pw = pwd.getpwuid(os.getuid())
    name = pw.pw_gecos.split(",")[0].strip() or pw.pw_name
    return name[:1].upper() + name[1:]


def text_png(path, text, size_px, weight, color=(1, 1, 1, 1)):
    """White text in Inter, cropped to its size."""
    layout_surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
    lay = PangoCairo.create_layout(cairo.Context(layout_surf))
    desc = Pango.FontDescription.from_string("Inter")
    desc.set_absolute_size(size_px * Pango.SCALE)
    desc.set_weight(weight)
    lay.set_font_description(desc)
    lay.set_text(text, -1)
    _ink, log = lay.get_pixel_extents()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, log.width), max(1, log.height))
    cr = cairo.Context(surf)
    cr.set_source_rgba(*color)
    PangoCairo.show_layout(cr, lay)
    surf.write_to_png(path)


def main():
    os.makedirs(OUT, exist_ok=True)
    W, H, s = screen()
    px = lambda v: round(v * s)          # logical → screen pixels

    # background: the wallpaper, blurred like the lock screen (640 px wide,
    # Gaussian 14), then scaled to the screen and dimmed (its .signin .tint)
    try:
        wall = open(os.path.join(HOME, ".config/hypr/.current_wallpaper")).read().strip()
        im = Image.open(wall).convert("RGB")
    except (OSError, ValueError):
        im = Image.new("RGB", (W, H), (24, 24, 28))
    r = max(W / im.width, H / im.height)                    # cover the screen
    im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
    im = im.crop(((im.width - W) // 2, (im.height - H) // 2, (im.width - W) // 2 + W, (im.height - H) // 2 + H))
    small = im.copy()
    small.thumbnail((640, 640))
    small = small.filter(ImageFilter.GaussianBlur(14))
    bg = small.resize((W, H), Image.BICUBIC)
    bg = Image.blend(bg, Image.new("RGB", (W, H), (0, 0, 0)), 0.28)
    bg.save(os.path.join(OUT, "background.png"))

    # profile picture: round, on a faint circle like the lock screen's
    a = px(AVATAR)
    av = Image.new("RGBA", (a, a), (255, 255, 255, 31))
    face = os.path.join(HOME, ".face")
    if os.path.exists(face):
        f = Image.open(face).convert("RGBA")
        side = min(f.size)
        f = f.crop(((f.width - side) // 2, (f.height - side) // 2,
                    (f.width - side) // 2 + side, (f.height - side) // 2 + side)).resize((a, a), Image.LANCZOS)
        av = f
    mask = Image.new("L", (a * 4, a * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, a * 4 - 1, a * 4 - 1), fill=255)
    out = Image.new("RGBA", (a, a), (0, 0, 0, 0))
    out.paste(av, (0, 0), mask.resize((a, a), Image.LANCZOS))
    out.save(os.path.join(OUT, "avatar.png"))

    # name, "Password" placeholder
    text_png(os.path.join(OUT, "name.png"), display_name(), px(NAME_PT), Pango.Weight.SEMIBOLD)
    text_png(os.path.join(OUT, "placeholder.png"), "Password", px(14), Pango.Weight.NORMAL,
             (1, 1, 1, 0.72))

    # the password field: dark glass, light 1 px edge, 4 px corners
    bw, bh, rr, lw = px(BOX_W), px(BOX_H), px(4), max(1, px(1))
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, bw, bh)
    cr = cairo.Context(surf)

    def rounded(x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -1.5708, 0)
        cr.arc(x + w - r, y + h - r, r, 0, 1.5708)
        cr.arc(x + r, y + h - r, r, 1.5708, 3.1416)
        cr.arc(x + r, y + r, r, 3.1416, 4.7124)
        cr.close_path()
    rounded(lw / 2, lw / 2, bw - lw, bh - lw, rr)
    cr.set_source_rgba(0, 0, 0, 0.32)
    cr.fill_preserve()
    cr.set_source_rgba(1, 1, 1, 0.7)          # focused, like the lock screen's field
    cr.set_line_width(lw)
    cr.stroke()
    surf.write_to_png(os.path.join(OUT, "box.png"))

    # one dot of the typed password
    d = px(8)
    dot = cairo.ImageSurface(cairo.FORMAT_ARGB32, d, d)
    cr = cairo.Context(dot)
    cr.arc(d / 2, d / 2, d / 2, 0, 6.2832)
    cr.set_source_rgba(1, 1, 1, 1)
    cr.fill()
    dot.write_to_png(os.path.join(OUT, "dot.png"))

    # the layout numbers for the script (screen pixels)
    with open(os.path.join(HERE, "win11-signin.script")) as f:
        script = f.read()
    for k, v in {"@NAME_GAP@": px(NAME_GAP), "@BOX_GAP@": px(BOX_GAP), "@PAD@": px(12),
                 "@DOT_STEP@": px(14), "@REF_H@": H}.items():
        script = script.replace(k, str(v))
    with open(os.path.join(OUT, "win11-signin.script"), "w") as f:
        f.write(script)
    shutil.copy(os.path.join(HERE, "win11-signin.plymouth"), OUT)
    print(f"theme for {W}x{H} at {s:g}x written to {OUT}")


if __name__ == "__main__":
    main()
