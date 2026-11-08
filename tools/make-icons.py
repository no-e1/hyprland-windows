#!/usr/bin/env python3
"""Generates thin icons in the style of Windows 11 (Segoe Fluent).
Output: icons/win11-*.svg (linked into the hicolor icon theme by install.sh)."""
import math, os
OUT = os.path.join(os.path.dirname(__file__), "..", "icons")
W, DIM = "#ffffff", "#ffffff"
SW = 1.5  # stroke width

def svg(body):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
            'fill="none" stroke-linecap="round" stroke-linejoin="round">' + body + '</svg>\n')

def save(name, body):
    with open(os.path.join(OUT, f"win11-{name}.svg"), "w") as f:
        f.write(svg(body))

def arc(cx, cy, r, a0, a1, color, op=1.0, sw=SW):
    p0 = (cx + r * math.sin(math.radians(a0)), cy - r * math.cos(math.radians(a0)))
    p1 = (cx + r * math.sin(math.radians(a1)), cy - r * math.cos(math.radians(a1)))
    return (f'<path d="M{p0[0]:.2f} {p0[1]:.2f} A{r} {r} 0 0 1 {p1[0]:.2f} {p1[1]:.2f}" '
            f'stroke="{color}" stroke-opacity="{op}" stroke-width="{sw}"/>')

# ── Wi-Fi: dot + 3 arcs, unreached levels dimmed ─────────────────────
for lvl in range(5):            # 0 = dot only ... 4 = full
    b = f'<circle cx="12" cy="18.6" r="1.35" fill="{W}"/>'
    for i, r in enumerate((5, 9.5, 14)):
        b += arc(12, 19.2, r, -48, 48, W, 1.0 if lvl > i + 1 or lvl == 4 else 0.3)
    save(f"wifi-{lvl}", b)
b = f'<circle cx="12" cy="18.6" r="1.35" fill="{W}" fill-opacity="0.3"/>'
for r in (5, 9.5, 14):
    b += arc(12, 19.2, r, -48, 48, W, 0.3)
b += f'<path d="M16.5 15.5l4 4M20.5 15.5l-4 4" stroke="{W}" stroke-width="{SW}"/>'
save("wifi-off", b)
# Ethernet: monitor with cable
save("ethernet", f'<rect x="3" y="4.5" width="18" height="12" rx="1.5" stroke="{W}" stroke-width="{SW}"/>'
                 f'<path d="M12 16.5v3M8 19.5h8" stroke="{W}" stroke-width="{SW}"/>')

# ── Speaker ──────────────────────────────────────────────────────────
spk = (f'<path d="M3.5 9.25h3L10.75 5.5v13L6.5 14.75h-3a.75.75 0 0 1-.75-.75v-4a.75.75 0 0 1 .75-.75z" '
       f'stroke="{W}" stroke-width="{SW}"/>')
for lvl in range(4):            # 0 = no wave ... 3 = three waves
    b = spk
    for i, r in enumerate((3.6, 6.8, 10)):
        if lvl > i:
            b += arc(10.75, 12, r, 40, 140, W)
    save(f"volume-{lvl}", b)
save("volume-mute", spk + f'<path d="M15 9.5l5 5M20 9.5l-5 5" stroke="{W}" stroke-width="{SW}"/>')

# ── Battery (Windows 11 taskbar style) ───────────────────────────────
# Wide, flat body with a thin outline and a small terminal on the right;
# the level fills almost the whole inside (thin gap to the outline).
# Charging: the level turns green with a bolt cut into the middle.
# Plugged in but not charging (full): a small plug instead of the bolt.
BODY = '<rect x="2.6" y="7.6" width="16.8" height="8.8" rx="2.4" stroke="{c}" stroke-width="1.2"/>'
NUB = '<path d="M20.6 10.3h.6a1 1 0 0 1 1 1v1.4a1 1 0 0 1-1 1h-.6z" fill="{c}"/>'
FILL_X, FILL_W = 4.4, 13.9
BOLT = "M12.2 8.3L9.4 12.5h2.3l-.6 3.3 2.8-4.5h-2.3z"
PLUG = "M9.5 8.8V6.3M12.5 8.8V6.3M8.1 8.8h5.8v2.2a2.9 2.9 0 0 1-5.8 0zM11 14v3.6"
GREEN, RED = "#6ccb5f", "#ff6363"
for pct in range(0, 101, 10):
    for kind in ("", "-charging", "-plugged"):
        col = GREEN if kind == "-charging" else RED if pct <= 10 and not kind else W
        w = FILL_W * pct / 100
        fill = (f'<rect x="{FILL_X}" y="9.4" width="{w:.2f}" height="5.2" rx="{min(1, w / 2):.2f}" fill="{col}"/>'
                if w > 0.3 else "")
        shape = BODY.format(c=W) + NUB.format(c=W) + fill
        if kind == "-charging":
            gap = f'<path d="{BOLT}" fill="black" stroke="black" stroke-width="1.8" stroke-linejoin="round"/>'
            glyph = f'<path d="{BOLT}" fill="{W}"/>'
        elif kind == "-plugged":
            gap = f'<path d="{PLUG}" fill="black" stroke="black" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>'
            glyph = f'<path d="{PLUG}" fill="{W}" stroke="{W}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        else:
            gap = glyph = ""
        if gap:
            mid = f"m{pct}{kind}"
            shape = (f'<mask id="{mid}"><rect width="24" height="24" fill="white"/>{gap}</mask>'
                     f'<g mask="url(#{mid})">{shape}</g>{glyph}')
        save(f"battery-{pct}{kind}", shape)

# ── Quick settings and switcher glyphs ───────────────────────────────
st = f'stroke="{W}" stroke-width="{SW}"'
save("close", f'<path d="M6.5 6.5l11 11M17.5 6.5l-11 11" {st}/>')
# keyboard light: a keyboard with light rays above it (off: no rays)
kbd = (f'<rect x="3" y="11" width="18" height="9.5" rx="1.5" {st}/>'
       f'<path d="M6.5 14.2h.01M9.5 14.2h.01M12.5 14.2h.01M15.5 14.2h.01M17.5 14.2h.01M8 17.3h8" {st}/>')
save("keyboard-light", kbd + f'<path d="M12 3.5v2.5M6.3 5.3l1.4 2M17.7 5.3l-1.4 2" {st}/>')
save("keyboard-light-off", kbd)
# "select a sound output": two slider lines, the lower one ends in a speaker
save("sound-output", f'<path d="M3 7.5h9.5M18.5 7.5H21M3 14.5h2.5" {st}/>'
                     f'<circle cx="15.5" cy="7.5" r="2.25" {st}/><circle cx="8.5" cy="14.5" r="2.25" {st}/>'
                     f'<path d="M13 13.5h1.6l2.6-2.2v8.4l-2.6-2.2H13z" {st}/>'
                     + arc(17.2, 15.5, 2.6, 45, 135, W) + arc(17.2, 15.5, 5, 45, 135, W))
save("bluetooth", f'<path d="M7 7.5l10 9-5 4.5V3l5 4.5-10 9" {st}/>')
save("airplane", f'<path d="M21 15.5v-1.8l-7.5-4.7V4.2a1.5 1.5 0 0 0-3 0V9L3 13.7v1.8l7.5-2.3v4.9l-2 1.5V21l3.5-1 3.5 1v-1.4l-2-1.5v-4.9z" {st}/>')
save("moon", f'<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z" {st}/>')
save("snip", f'<circle cx="6" cy="17.5" r="2.5" {st}/><circle cx="6" cy="6.5" r="2.5" {st}/><path d="M8.2 7.8L20 18M8.2 16.2L20 6" {st}/>')
# start menu search: "Open" (arrow out of a box) and app actions (window)
save("open", f'<path d="M10 5H6.5A1.5 1.5 0 0 0 5 6.5v11A1.5 1.5 0 0 0 6.5 19h11a1.5 1.5 0 0 0 1.5-1.5V14" {st}/>'
             f'<path d="M13.5 5H19v5.5M19 5l-8 8" {st}/>')
save("window", f'<rect x="3.75" y="5" width="16.5" height="14" rx="2" {st}/><path d="M3.75 9h16.5" {st}/>')
save("lock", f'<rect x="5" y="10.5" width="14" height="10" rx="2" {st}/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" {st}/>')
# power: open ring (gap at the top) + short stem, like Segoe Fluent "PowerButton"
save("power", arc(12, 12.75, 7.75, 38, 322, W).replace('0 0 1', '0 1 1') + f'<path d="M12 3.75v7.5" {st}/>')
save("user", f'<circle cx="12" cy="8.5" r="4" {st}/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0" {st}/>')
save("sleep", f'<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z" {st}/>')
save("restart", arc(12, 12, 7.75, 30, 330, W).replace('0 0 1', '0 1 1') + f'<path d="M8 4.2l3.3-.2-.3 3.4" {st}/>')
save("signout", f'<path d="M14 4.5H6.5a1.5 1.5 0 0 0-1.5 1.5v12a1.5 1.5 0 0 0 1.5 1.5H14M10 12h10.5M17.5 9l3 3-3 3" {st}/>')
save("sun", f'<circle cx="12" cy="12" r="4" {st}/>' + "".join(
    f'<path d="M{12+7*math.sin(a):.2f} {12-7*math.cos(a):.2f}L{12+9.5*math.sin(a):.2f} {12-9.5*math.cos(a):.2f}" {st}/>'
    for a in [i * math.pi / 4 for i in range(8)]))
pts = []
for i in range(48):
    a = i * 2 * math.pi / 48
    r = 9.5 if (i % 6) in (1, 2, 3) else 7.2
    pts.append(f"{12 + r * math.sin(a):.2f} {12 - r * math.cos(a):.2f}")
save("settings", f'<path d="M{"L".join(pts)}z" {st}/><circle cx="12" cy="12" r="3" {st}/>')
save("chevron-right", f'<path d="M9.5 6l6 6-6 6" {st}/>')
save("chevron-left", f'<path d="M14.5 6l-6 6 6 6" {st}/>')
save("chevron-up", f'<path d="M6 14.5l6-6 6 6" {st}/>')
save("chevron-down", f'<path d="M6 9.5l6 6 6-6" {st}/>')
save("refresh", arc(12, 12, 7.5, 40, 330, W).replace('0 0 1', '0 1 1') + f'<path d="M15.5 4.5l3.5.9-1 3.4" {st}/>')
save("headphones", f'<path d="M4 15v-3a8 8 0 0 1 16 0v3" {st}/><rect x="3.5" y="14" width="4" height="6.5" rx="1.5" {st}/><rect x="16.5" y="14" width="4" height="6.5" rx="1.5" {st}/>')
# sound output page: built-in speaker and a monitor (HDMI/DisplayPort)
save("speaker", f'<rect x="6" y="3" width="12" height="18" rx="2" {st}/><circle cx="12" cy="14.5" r="3" {st}/><path d="M12 7.2h.01" {st}/>')
save("monitor", f'<rect x="3" y="4.5" width="18" height="12" rx="1.5" {st}/><path d="M9 20h6M12 16.5V20" {st}/>')
# notification center: "do not disturb" = a bell with a small zZ (Segoe "QuietHours")
bell = f'<path d="M3.5 17.5h13M5.5 17.5v-6a4.5 4.5 0 0 1 9 0v6M8.5 20.3a1.6 1.6 0 0 0 3 0" {st}/>'
save("bell-snooze", bell + f'<path d="M16 3.5h3.5L16 8h3.5" stroke="{W}" stroke-width="1.3"/>')
save("bell-snooze-dark", (bell + f'<path d="M16 3.5h3.5L16 8h3.5" stroke="{W}" stroke-width="1.3"/>')
     .replace("#ffffff", "#000000"))         # on the blue "on" button
save("devices", f'<rect x="3" y="5" width="13" height="10" rx="1.5" {st}/><path d="M6 19h7" {st}/><rect x="17.5" y="8" width="4" height="11" rx="1" {st}/>')
# secured Wi-Fi: small lock in the corner
for lvl in range(5):
    b = open(os.path.join(OUT, f"win11-wifi-{lvl}.svg")).read().split(">", 1)[1].rsplit("</svg>", 1)[0]
    b += (f'<rect x="15.5" y="15.5" width="7" height="6" rx="1" fill="{W}" stroke="#202020" stroke-width="1"/>'
          f'<path d="M17 15.5v-1.2a2 2 0 0 1 4 0v1.2" stroke="{W}" stroke-width="1.2"/>')
    save(f"wifi-{lvl}-lock", b)
# start button: 2x2 grid of rounded squares in the accent color
save("start", '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
              '<stop offset="0" stop-color="#6ccfff"/><stop offset="1" stop-color="#1f8fe0"/></linearGradient></defs>'
              + "".join(f'<rect x="{x}" y="{y}" width="9.6" height="9.6" rx="1" fill="url(#g)"/>'
                        for x in (2, 12.4) for y in (2, 12.4)))

# Waybar CSS: one rule per state class -> icon
css = ["/* Generated by tools/make-icons.py – do not edit */"]
def rule(sel, name, size=20):
    css.append(f'{sel} {{ background-image: -gtk-icontheme("win11-{name}"); background-size: {size}px {size}px; }}')
for lvl in range(5):
    rule(f"#custom-net.wifi-{lvl}", f"wifi-{lvl}")
rule("#custom-net.wifi-off", "wifi-off")
rule("#custom-net.ethernet", "ethernet")
for lvl in range(4):
    rule(f"#custom-vol.vol-{lvl}", f"volume-{lvl}")
rule("#custom-vol.mute", "volume-mute")
for pct in range(0, 101, 10):
    rule(f"#custom-bat.bat-{pct}", f"battery-{pct}", 24)
    rule(f"#custom-bat.bat-{pct}-charging", f"battery-{pct}-charging", 24)
rule("#custom-start", "start", 26)
with open(os.path.join(OUT, "..", "waybar", "icons.css"), "w") as f:
    f.write("\n".join(css) + "\n")
print("ok")
