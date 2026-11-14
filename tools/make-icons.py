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

# the status icons next to the clock a bit bigger (from the middle), so they
# match the network icons, which fill nearly the whole box
SCALE_UP = {"volume-": 1.12}       # (the battery is drawn that big already)


def save(name, body):
    for prefix, k in SCALE_UP.items():
        if name.startswith(prefix):
            body = f'<g transform="translate(12 12) scale({k}) translate(-12 -12)">{body}</g>'
            break
    with open(os.path.join(OUT, f"win11-{name}.svg"), "w") as f:
        f.write(svg(body))

def arc(cx, cy, r, a0, a1, color, op=1.0, sw=SW):
    p0 = (cx + r * math.sin(math.radians(a0)), cy - r * math.cos(math.radians(a0)))
    p1 = (cx + r * math.sin(math.radians(a1)), cy - r * math.cos(math.radians(a1)))
    return (f'<path d="M{p0[0]:.2f} {p0[1]:.2f} A{r} {r} 0 0 1 {p1[0]:.2f} {p1[1]:.2f}" '
            f'stroke="{color}" stroke-opacity="{op}" stroke-width="{sw}"/>')

# ── Wi-Fi: dot + 3 arcs, unreached levels dimmed ─────────────────────
# (centred a bit higher than the geometric middle: the heavy dot at the
# bottom made it look lower than the speaker and the battery next to it)
WIFI_Y = 18.0
WIFI_DIM = 0.45          # arcs above the signal: clearly grey like Windows, not almost gone
for lvl in range(5):            # 0 = dot only ... 4 = full
    b = f'<circle cx="12" cy="{WIFI_Y - 0.6}" r="1.35" fill="{W}"/>'
    for i, r in enumerate((5, 9.5, 14)):
        b += arc(12, WIFI_Y, r, -48, 48, W, 1.0 if lvl > i + 1 or lvl == 4 else WIFI_DIM)
    save(f"wifi-{lvl}", b)
b = f'<circle cx="12" cy="{WIFI_Y - 0.6}" r="1.35" fill="{W}" fill-opacity="{WIFI_DIM}"/>'
for r in (5, 9.5, 14):
    b += arc(12, WIFI_Y, r, -48, 48, W, WIFI_DIM)
b += f'<path d="M16.5 14.5l4 4M20.5 14.5l-4 4" stroke="{W}" stroke-width="{SW}"/>'
save("wifi-off", b)

# Wi-Fi connected, but no internet: full Wi-Fi with a "?" badge at the bottom
# right, cut out of the arcs around it (like Windows' badges)
_bx, _by, _br = 18.2, 17.6, 4.6
_q = ('<path d="M16.75 16.35a1.5 1.5 0 1 1 2.35 1.25c-.55.35-.9.7-.9 1.35v.25" '
      'stroke="black" stroke-width="1.35" fill="none"/><circle cx="18.2" cy="20.85" r=".8" fill="black"/>')
_wifi_full = f'<circle cx="12" cy="{WIFI_Y - 0.6}" r="1.35" fill="{W}"/>'
for _r in (5, 9.5, 14):
    _wifi_full += arc(12, WIFI_Y, _r, -48, 48, W, 1.0)
save("wifi-noinet",
     f'<mask id="wnm"><rect width="24" height="24" fill="white"/>'
     f'<circle cx="{_bx}" cy="{_by}" r="{_br + 1.3}" fill="black"/></mask>'
     f'<g mask="url(#wnm)">{_wifi_full}</g>'
     f'<mask id="wnq"><rect width="24" height="24" fill="white"/>{_q}</mask>'
     f'<circle cx="{_bx}" cy="{_by}" r="{_br}" fill="{W}" mask="url(#wnq)"/>')

# no connection at all (no Wi-Fi, no cable): Windows' globe with a "no" sign
save("no-internet",                     # as big as the other status icons
     '<mask id="nim"><rect width="24" height="24" fill="white"/>'
     '<circle cx="18.4" cy="18.4" r="6.1" fill="black"/></mask>'
     f'<g mask="url(#nim)" stroke="{W}" stroke-width="{SW}">'
     '<circle cx="10.6" cy="10.6" r="9.1"/>'
     '<ellipse cx="10.6" cy="10.6" rx="3.9" ry="9.1"/>'
     '<path d="M1.5 10.6h18.2M3 6h15.2M3 15.2h15.2"/></g>'
     f'<circle cx="18.4" cy="18.4" r="4.4" stroke="{W}" stroke-width="{SW}"/>'
     f'<path d="M15.3 21.5l6.2-6.2" stroke="{W}" stroke-width="{SW}"/>')
# Ethernet: monitor with cable
# ── App icons in colour (taskbar, Alt+Tab) ───────────────────────────
# Terminal, in the style of Windows Terminal: a dark window with a strip of
# three tabs in greys, a ">" prompt and a cursor block
save("terminal",
     '<defs><clipPath id="tw"><rect x="2" y="4.25" width="20" height="15.5" rx="1.7"/></clipPath>'
     '<linearGradient id="tb" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#454545"/>'
     '<stop offset="1" stop-color="#2f2f2f"/></linearGradient>'
     '<linearGradient id="tc" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f2f2f2"/>'
     '<stop offset="1" stop-color="#bdbdbd"/></linearGradient></defs>'
     '<g transform="translate(12 12) scale(1.17) translate(-12 -12)">'      # as big as the box allows
     '<g clip-path="url(#tw)"><rect x="2" y="4.25" width="20" height="15.5" fill="url(#tb)"/>'
     '<rect x="2" y="4.25" width="6.9" height="2.7" fill="#d0d0d0"/>'
     '<rect x="8.9" y="4.25" width="6.6" height="2.7" fill="#9c9c9c"/>'
     '<rect x="15.5" y="4.25" width="6.5" height="2.7" fill="#6e6e6e"/></g>'
     '<path d="M5.4 9.7l3.9 3.1-3.9 3.1" stroke="url(#tc)" stroke-width="1.9"/>'
     '<rect x="11.3" y="15.2" width="6.4" height="1.8" rx="0.4" fill="#e2e2e2"/></g>')
# Settings, in the style of Windows 11: a grey gear with six broad teeth, a
# light ring and a blue middle
_g = []
for _i in range(180):
    _a = _i * 2 * math.pi / 180
    _r = 9.35 + 1.55 * math.tanh(2.4 * (math.cos(6 * _a) + 0.3))     # broad teeth, shallow gaps
    _g.append(f"{12 + _r * math.sin(_a):.2f} {12 - _r * math.cos(_a):.2f}")
save("settings-app",
     '<defs><linearGradient id="sg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8b949b"/>'
     '<stop offset="1" stop-color="#69727a"/></linearGradient>'
     '<linearGradient id="sr" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#c9d0d4"/>'
     '<stop offset="1" stop-color="#f4f6f7"/></linearGradient>'
     '<linearGradient id="sb" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1273c9"/>'
     '<stop offset="1" stop-color="#0b4f96"/></linearGradient></defs>'
     '<g transform="translate(12 12) scale(1.09) translate(-12 -12)">'      # as big as the box allows
     f'<path d="M{"L".join(_g)}z" fill="url(#sg)"/>'
     '<circle cx="12" cy="12" r="5.4" fill="url(#sr)"/><circle cx="12" cy="12" r="3.65" fill="url(#sb)"/></g>')

# File Explorer like Windows 11 (measured from its taskbar icon): the tab
# a deep gold with a slanted edge; the front light yellow turning deeper
# towards the bottom right, a step up next to the tab; at the bottom the
# blue part – round at the top, flush with the folder's lower edge, light
# to dark diagonally, a thin dark line across its middle
save("explorer",
     '<defs><linearGradient id="et" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#dfa50c"/>'
     '<stop offset="1" stop-color="#c48902"/></linearGradient>'
     '<linearGradient id="ef" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffd962"/>'
     '<stop offset="1" stop-color="#f2ab0a"/></linearGradient>'
     '<linearGradient id="el" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#1c95e0"/>'
     '<stop offset="1" stop-color="#0457a0"/></linearGradient></defs>'
     '<path d="M1.5 4.5Q1.5 3 3 3H8.4Q8.95 3 9.2 3.5L10 5.1V6.6H1.5Z" fill="url(#et)"/>'
     '<path d="M1.5 6.25H9.25L10 5H21Q22.5 5 22.5 6.5V19.5Q22.5 21 21 21H3Q1.5 21 1.5 19.5Z" fill="url(#ef)"/>'
     '<path d="M5 21V18Q5 15.5 7.5 15.5H16.5Q19 15.5 19 18V21Z" fill="url(#el)"/>'
     '<path d="M7.6 18.05H16.4" stroke="#074f94" stroke-width="0.55" stroke-opacity="0.75"/>')

# search boxes (start menu, Win + I, …), like Windows 11's: a magnifier whose
# ring runs from blue into green at the top right, a soft grey glass inside,
# the blue handle to the bottom right
save("search",
     '<defs><linearGradient id="sr" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#0b72d4"/>'
     '<stop offset="0.6" stop-color="#1688e0"/><stop offset="1" stop-color="#48de78"/></linearGradient></defs>'
     '<circle cx="10.2" cy="10.2" r="6.1" fill="#ffffff" fill-opacity="0.16"/>'
     '<path d="M14.7 14.7L19.6 19.6" stroke="#0f78d8" stroke-width="2.5"/>'
     '<circle cx="10.2" cy="10.2" r="6.1" stroke="url(#sr)" stroke-width="2.1"/>')

# start menu search results: documents – a notepad (blue page with three
# lines, four binding rings on top, a white page and a brown back below)
save("document",
     '<defs><linearGradient id="dp" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#7cd7f2"/>'
     '<stop offset="1" stop-color="#43aed2"/></linearGradient>'
     '<linearGradient id="dw" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#cfcfcf"/>'
     '<stop offset="0.35" stop-color="#f3f3f3"/></linearGradient>'
     '<linearGradient id="db" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#6e3a14"/>'
     '<stop offset="0.45" stop-color="#ab5f28"/></linearGradient></defs>'
     '<rect x="4.5" y="15.5" width="15" height="6" rx="1" fill="url(#db)"/>'
     '<rect x="4.5" y="14" width="15" height="6" rx="1" fill="url(#dw)"/>'
     '<rect x="4.5" y="3" width="15" height="15.5" rx="1" fill="url(#dp)"/>'
     + "".join(f'<rect x="{x}" y="2" width="1.5" height="2.5" rx="0.75" fill="#066f94"/>'
               for x in (6, 9.5, 13, 16.5))
     + "".join(f'<rect x="6.5" y="{y}" width="11" height="1.5" rx="0.3" fill="#066f94"/>'
               for y in (8, 11, 14)))
# … photos (measured from Windows 11's Photos icon): a tile from bright blue
# at the top to violet at the bottom right, the white sun top right, a light
# cyan mountain with a rounded peak whose right slope folds over – the
# deeper blue part below it is the back of the fold, rounded on top too
save("photo",
     '<defs><linearGradient id="pb" x1="0" y1="0" x2="0.55" y2="1"><stop offset="0" stop-color="#1688d6"/>'
     '<stop offset="0.5" stop-color="#4560b4"/><stop offset="1" stop-color="#7462c0"/></linearGradient>'
     # the light band: brighter along its upper edge, deeper along the lower
     '<linearGradient id="pm" gradientUnits="userSpaceOnUse" x1="6" y1="14" x2="9.9" y2="17.9">'
     '<stop offset="0" stop-color="#4fd8fd"/><stop offset="1" stop-color="#2aaeea"/></linearGradient>'
     # the back of the fold: in the band's shadow next to it, lighter away from it
     '<linearGradient id="pf" gradientUnits="userSpaceOnUse" x1="10" y1="17.75" x2="14.5" y2="22.25">'
     '<stop offset="0" stop-color="#0567c9"/><stop offset="0.6" stop-color="#1590dc"/></linearGradient>'
     '<radialGradient id="ps" cx="0.4" cy="0.35" r="0.7"><stop offset="0" stop-color="#ffffff"/>'
     '<stop offset="1" stop-color="#dde1ea"/></radialGradient>'
     '<clipPath id="pc"><rect x="2" y="2" width="20" height="20" rx="1.6"/></clipPath></defs>'
     '<rect x="2" y="2" width="20" height="20" rx="1.6" fill="url(#pb)"/>'
     '<g clip-path="url(#pc)">'
     # traced from the original row by row (24 px box, the tile 2..22): the
     # light band – left edge x + y = 20, a round peak, right edge x - y = 3.8
     # straight down to the tile's edge (no dent at the fold) …
     '<path d="M-2 22L10.2 9.8Q11.9 8.1 13.6 9.8L24.4 20.6L12 24H-2Z" fill="url(#pm)"/>'
     # … and in front of it the deeper blue part: left edge x + y = 27.75,
     # a broad round top (the fold), then the same right edge x - y = 3.8
     '<path d="M3.75 24L13.1 14.65C14.3 13.45 15.5 12.4 16.4 12.6C16.6 12.7 16.8 13.0 17.1 13.3L24.4 20.6V24Z" fill="url(#pf)"/>'
     '</g>'
     '<circle cx="16.95" cy="6.3" r="1.95" fill="#1b2a70" opacity="0.22"/>'
     '<circle cx="16.8" cy="6.1" r="1.95" fill="url(#ps)"/>')

# Win + W, "Find more online…": the Photos tile, a little smaller, with a
# blue globe badge at the bottom right (a clear gap around it)
_photo = open(os.path.join(OUT, "win11-photo.svg")).read()
_photo = _photo[_photo.index(">") + 1:_photo.rindex("</svg>")]
save("find-online",
     '<defs><linearGradient id="gb" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3fa9f5"/>'
     '<stop offset="1" stop-color="#0b63c4"/></linearGradient>'
     '<mask id="gm"><rect width="24" height="24" fill="white"/><circle cx="17.6" cy="17.6" r="6.6" fill="black"/></mask></defs>'
     f'<g mask="url(#gm)"><g transform="translate(-1.2 -1.2) scale(0.92)">{_photo}</g></g>'
     '<circle cx="17.6" cy="17.6" r="5.4" fill="url(#gb)"/>'
     '<g stroke="#ffffff" stroke-width="0.8" fill="none">'
     '<circle cx="17.6" cy="17.6" r="3.5"/><ellipse cx="17.6" cy="17.6" rx="1.5" ry="3.5"/>'
     '<path d="M14.1 17.6h7M14.6 15.9h6M14.6 19.3h6"/></g>')

# Win + P (project): which screens show the picture – bright: on, faint: off
def screen(x, y, op):
    return (f'<rect x="{x}" y="{y}" width="9.5" height="7" rx="1" stroke="{W}" stroke-opacity="{op}" '
            f'stroke-width="{SW}"/><path d="M{x + 3.25} {y + 9.5}h3" stroke="{W}" stroke-opacity="{op}" '
            f'stroke-width="{SW}"/>')
save("project-pc", screen(1.5, 7, 1) + screen(13, 7, 0.3))
save("project-second", screen(1.5, 7, 0.3) + screen(13, 7, 1))
save("project-extend", screen(1.5, 7, 1) + screen(13, 7, 1)
     + f'<path d="M11 10.5h2" stroke="{W}" stroke-width="{SW}"/>')
save("project-duplicate", '<mask id="dupm"><rect width="24" height="24" fill="white"/>'
     '<rect x="8.5" y="8" width="11" height="8" rx="1" fill="black" stroke="black" stroke-width="3"/></mask>'
     f'<g mask="url(#dupm)"><rect x="3.5" y="5" width="11" height="8" rx="1" stroke="{W}" stroke-width="{SW}"/></g>'
     f'<rect x="8.5" y="8" width="11" height="8" rx="1" stroke="{W}" stroke-width="{SW}"/>'
     f'<path d="M12.25 19h3.5" stroke="{W}" stroke-width="{SW}"/>')

# Ethernet like Windows 11: a screen on a stand, open at the top right where
# a network plug sits; the plug's cable runs straight down to the floor
# measured from Windows 11's own icon (860 px picture, the strokes' middles),
# scaled into the 24 px box with its proportions – the first version was a
# little wider than Windows'
_K = 21.6 / 172                         # nearly the whole box: as big as the other icons look
def _ex(x): return f"{(24 - 179 * _K) / 2 + (x - 343) * _K:.2f}"
def _ey(y): return f"{(24 - 172 * _K) / 2 + (y - 176) * _K:.2f}"
_r = 12
_eth = (f'<path d="M{_ex(452)} {_ey(193.5)}H{_ex(349 + _r)}A{_r * _K:.2f} {_r * _K:.2f} 0 0 0 {_ex(349)} {_ey(193.5 + _r)}'
        f'V{_ey(310.5 - _r)}A{_r * _K:.2f} {_r * _K:.2f} 0 0 0 {_ex(349 + _r)} {_ey(310.5)}H{_ex(471)}" '
        f'stroke="{W}" stroke-width="1.5"/>'
        f'<path d="M{_ex(403)} {_ey(316)}V{_ey(342.5)}M{_ex(460)} {_ey(316)}V{_ey(342.5)}'
        f'M{_ex(381)} {_ey(342.5)}H{_ex(472)}" stroke="{W}" stroke-width="1.5"/>'
        f'<rect x="{_ex(469.5)}" y="{_ey(182)}" width="{46.5 * _K:.2f}" height="{62.5 * _K:.2f}" rx="{9 * _K:.2f}" '
        f'stroke="{W}" stroke-width="1.5"/>'
        f'<path d="M{_ex(469.5)} {_ey(202.5)}H{_ex(516)}M{_ex(492.5)} {_ey(244.5)}V{_ey(342.5)}" '
        f'stroke="{W}" stroke-width="1.5"/>')
save("ethernet", f'<g stroke-linecap="butt">{_eth}</g>')      # straight line ends, like Windows

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

# ── Microphone (on / muted with a slash) ─────────────────────────────
mic = (f'<rect x="9" y="3" width="6" height="11" rx="3" stroke="{W}" stroke-width="{SW}"/>'
       f'<path d="M6 11a6 6 0 0 0 12 0M12 17v3.5M9 20.5h6" stroke="{W}" stroke-width="{SW}"/>')
save("mic", mic)
save("mic-off", '<mask id="micoff"><rect width="24" height="24" fill="white"/>'
                '<path d="M4 3.5l16 17" stroke="black" stroke-width="4"/></mask>'
                f'<g mask="url(#micoff)">{mic}</g>'
                f'<path d="M4 3.5l16 17" stroke="{W}" stroke-width="{SW}"/>')

# ── Battery (Windows 11's new battery icons) ─────────────────────────
# Measured from Windows' own: a body about twice as wide as high with round
# corners, the fill well inside the outline, a narrow terminal on the right.
# The bolt (charging) and the plug (plugged in) stand in the middle and reach
# out over the outline – outline and fill are cut out around them.
#   battery-N           on battery: white
#   battery-N-low       below 30 %: amber       (status.sh picks by the real
#   battery-N-crit      15 % and less: red       level, not the rounded fill)
#   battery-N-charging  green, the bolt in the middle
#   battery-N-plugged   plugged in, not charging: white, the plug in the middle
BX, BY, BW, BH, BR = 1.3, 6.95, 19.5, 10.1, 2.6       # body (outline's middle)
BODY = f'<rect x="{BX}" y="{BY}" width="{BW}" height="{BH}" rx="{BR}" stroke="{{c}}" stroke-width="1.25"/>'
NUB = (f'<path d="M{BX + BW + 0.6:.2f} 10.4h.55a.75.75 0 0 1 .75.75v1.7a.75.75 0 0 1-.75.75h-.55z" '
       f'fill="{{c}}"/>')
FX, FY, FW, FH = BX + 2.0, BY + 2.0, BW - 4.0, BH - 4.0   # the fill, well inside
BOLT = "M12.2 3.1L7.8 11.2h3.1l-1.3 5.6 5-8.6h-3.15L13.2 3.1z"     # in the middle of the body
# … a bit smaller, from the middle of the body: it stood taller than Windows'
BOLT_SCALE = 0.85
BOLT_DY = -0.8            # … and a little up: it sits on the top edge, like Windows'
BOLT_T = f'transform="translate(11.5 {12 + BOLT_DY}) scale({BOLT_SCALE}) translate(-11.5 -12)"'
PLUG_PRONGS = "M9.9 5.0V7.7M12.2 5.0V7.7"
PLUG_HEAD = "M8.35 7.7h5.4v2.2a2.7 2.7 0 0 1-5.4 0z"
PLUG_CORD = "M11.05 12.6v5.6"              # just below the battery, like Windows'
GREEN, AMBER, RED = "#6ccb5f", "#f8b400", "#e74856"


def battery(pct, kind):
    col = {"-charging": GREEN, "-low": AMBER, "-crit": RED}.get(kind, W)
    w = max(FW * pct / 100, 1.1 if pct > 0 else 0)      # always a visible sliver
    fill = f'<rect x="{FX:.2f}" y="{FY:.2f}" width="{w:.2f}" height="{FH:.2f}" rx="0.9" fill="{col}"/>' if w else ""
    shape = BODY.format(c=W) + NUB.format(c=W) + fill
    if kind in ("-charging", "-plugged"):
        if kind == "-charging":
            cut = (f'<path d="{BOLT}" {BOLT_T} fill="black" stroke="black" '
                   f'stroke-width="{2.2 / BOLT_SCALE:.2f}" stroke-linejoin="round"/>')      # same gap
            glyph = f'<path d="{BOLT}" {BOLT_T} fill="{W}"/>'
        else:
            # slim like Windows': short prongs close together, a smaller
            # head, and a clear gap around it all
            cut = (f'<path d="{PLUG_PRONGS}{PLUG_CORD}" stroke="black" stroke-width="3.8"/>'
                   f'<path d="{PLUG_HEAD}" fill="black" stroke="black" stroke-width="3.0" stroke-linejoin="round"/>')
            glyph = (f'<path d="{PLUG_PRONGS}{PLUG_CORD}" stroke="{W}" stroke-width="1.3"/>'
                     f'<path d="{PLUG_HEAD}" fill="{W}"/>')
        mid = f"m{pct}{kind}"
        shape = (f'<mask id="{mid}"><rect width="24" height="24" fill="white"/>{cut}</mask>'
                 f'<g mask="url(#{mid})">{shape}</g>{glyph}')
    return shape


for pct in range(0, 101, 10):
    for kind in ("", "-low", "-crit", "-charging", "-plugged"):
        save(f"battery-{pct}{kind}", battery(pct, kind))

# ── Quick settings and switcher glyphs ───────────────────────────────
st = f'stroke="{W}" stroke-width="{SW}"'
save("close", f'<path d="M6.5 6.5l11 11M17.5 6.5l-11 11" {st}/>')
save("add", f'<path d="M12 4.5v15M4.5 12h15" {st}/>')
# fingerprint on the lock screen (Windows Hello): ready, not recognized, recognized
FP = ("M5.2 6.6A8.5 8.5 0 0 1 18.8 6.6M3.6 11a8.5 8.5 0 0 1 .5-2.3M20.4 11.5c0 1.7-.2 3.3-.6 4.8"
      "M7.4 19.6c-.9-1.6-1.4-3.6-1.4-5.8V12a6 6 0 0 1 12 0v1.3"
      "M17.7 16.8c-.3 1.3-.8 2.5-1.4 3.6M9 12a3 3 0 0 1 6 0v1.6c0 2.6-.7 4.9-2 6.9"
      "M12 12v1.6c0 2.3-.6 4.3-1.7 6.1")
save("fingerprint", f'<path d="{FP}" {st}/>')
save("fingerprint-fail", f'<path d="{FP}" stroke="#ff6b6b" stroke-width="{SW}"/>')
save("fingerprint-ok", f'<path d="{FP}" stroke="#6ccb5f" stroke-width="{SW}"/>')     # recognized: the print turns green
# do not disturb next to the taskbar clock: three Zs, a big one, a middle
# one to the right, a small one above
save("dnd", f'<path d="M3.6 12.2h7.4L3.6 20.799999999999997h7.4" stroke="{W}" stroke-width="2.1" stroke-linejoin="miter"/><path d="M14.6 9.6h5.8L14.6 15.8h5.8" stroke="{W}" stroke-width="1.9" stroke-linejoin="miter"/><path d="M6.6 2.0h3.9L6.6 5.9h3.9" stroke="{W}" stroke-width="1.6" stroke-linejoin="miter"/>')
# … and in black, for the blue "on" button in the notification center
save("dnd-dark", f'<path d="M3.6 12.2h7.4L3.6 20.8h7.4" stroke="{W}" stroke-width="2.1" stroke-linejoin="miter"/>'
                 f'<path d="M14.6 9.6h5.8L14.6 15.8h5.8" stroke="{W}" stroke-width="1.9" stroke-linejoin="miter"/>'
                 f'<path d="M6.6 2.0h3.9L6.6 5.9h3.9" stroke="{W}" stroke-width="1.6" stroke-linejoin="miter"/>'
     .replace("#ffffff", "#000000"))
# desktops: two screens, one behind the other (the "Desktop 2" pill)
save("desktops", f'<rect x="3" y="7" width="14" height="10" rx="1.5" {st}/>'
                 f'<path d="M7 4.5h12.5A1.5 1.5 0 0 1 21 6v8.5" {st}/><path d="M8 20h4" {st}/>')         # "New desktop" in the task view
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
# start menu search, a file's actions: its folder, copy the path
save("folder", f'<path d="M3.75 7.5v9.75A1.75 1.75 0 0 0 5.5 19h13a1.75 1.75 0 0 0 1.75-1.75V9.75A1.75 1.75 0 0 0 18.5 8h-6.25'
               f'l-2-2.25H5.5A1.75 1.75 0 0 0 3.75 7.5z" {st}/>')
save("copy", f'<rect x="8.25" y="8.25" width="11.5" height="11.5" rx="2" {st}/>'
             f'<path d="M15.75 8.25V6A1.75 1.75 0 0 0 14 4.25H6A1.75 1.75 0 0 0 4.25 6v8A1.75 1.75 0 0 0 6 15.75h2.25" {st}/>')
# the settings menu (Win + I)
save("mouse", f'<rect x="6.75" y="3.25" width="10.5" height="17.5" rx="5.25" {st}/><path d="M12 3.5v5.75" {st}/>')
save("background", f'<rect x="3.25" y="4.75" width="17.5" height="14.5" rx="2" {st}/>'
                   f'<path d="M3.5 16.5l5-4.75 4 3.75 2.75-2.5 5 4.25" {st}/><circle cx="15.5" cy="9" r="1.5" {st}/>')
save("palette", f'<path d="M12 3.5a8.5 8.5 0 1 0 0 17c1.4 0 2-1 1.5-2.1-.6-1.2.2-2.4 1.6-2.4H17a3.5 3.5 0 0 0 3.5-3.5'
                f'C20.5 7.3 16.7 3.5 12 3.5z" {st}/><circle cx="7.75" cy="11" r="1.1" fill="{W}"/>'
                f'<circle cx="10.5" cy="7.5" r="1.1" fill="{W}"/><circle cx="14.75" cy="7.75" r="1.1" fill="{W}"/>')
save("activity", f'<path d="M3.5 12.5h3.75l2.5-6.5 4.5 12 2.5-5.5h3.75" {st}/>')
save("code", f'<path d="M8.5 7.5L4 12l4.5 4.5M15.5 7.5L20 12l-4.5 4.5M13.25 5.5l-2.5 13" {st}/>')
save("window", f'<rect x="3.75" y="5" width="16.5" height="14" rx="2" {st}/><path d="M3.75 9h16.5" {st}/>')
save("lock", f'<rect x="5" y="10.5" width="14" height="10" rx="2" {st}/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3" {st}/>')
# power: open ring (gap at the top) + short stem, like Segoe Fluent "PowerButton"
save("power", arc(12, 12.75, 7.75, 38, 322, W).replace('0 0 1', '0 1 1') + f'<path d="M12 3.75v7.5" {st}/>')
save("user", f'<circle cx="12" cy="8.5" r="4" {st}/><path d="M4.5 20.5a7.5 7.5 0 0 1 15 0" {st}/>')
save("sleep", f'<path d="M19.5 14.5A8 8 0 0 1 9.5 4.5a8 8 0 1 0 10 10z" {st}/>')
save("restart", arc(12, 12, 7.75, 30, 330, W).replace('0 0 1', '0 1 1') + f'<path d="M8 4.2l3.3-.2-.3 3.4" {st}/>')
# "Restart to Windows": the Windows logo, four filled panes
save("windows", "".join(f'<rect x="{x}" y="{y}" width="8.1" height="8.1" fill="{W}"/>'
                        for x in (3.5, 12.4) for y in (3.5, 12.4)))
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
# refresh: a circle open at the top right, a filled arrowhead sitting exactly
# on the arc's end and pointing along it (clockwise); centred on 12/12 so it
# can spin around its middle (winshell turns it while searching)
def refresh_icon(a0=75, a1=340, r=7.5, length=3.9, width=4.5):
    p0 = (12 + r * math.sin(math.radians(a0)), 12 - r * math.cos(math.radians(a0)))
    p1 = (12 + r * math.sin(math.radians(a1)), 12 - r * math.cos(math.radians(a1)))
    t = (math.cos(math.radians(a1)), math.sin(math.radians(a1)))      # clockwise direction
    n = (-t[1], t[0])
    bx, by = p1[0] - t[0] * 0.6, p1[1] - t[1] * 0.6                    # base just behind the end
    tip = (bx + t[0] * length, by + t[1] * length)
    b1 = (bx + n[0] * width / 2, by + n[1] * width / 2)
    b2 = (bx - n[0] * width / 2, by - n[1] * width / 2)
    return (f'<path d="M{p0[0]:.2f} {p0[1]:.2f} A{r} {r} 0 1 1 {p1[0]:.2f} {p1[1]:.2f}" {st}/>'
            f'<path d="M{tip[0]:.2f} {tip[1]:.2f}L{b1[0]:.2f} {b1[1]:.2f}L{b2[0]:.2f} {b2[1]:.2f}Z" '
            f'fill="{W}" stroke="{W}" stroke-width="0.8"/>')
save("refresh", refresh_icon())
# media card above Quick Settings (Windows 11): filled, softly rounded
# triangles and bars (stroke + fill in the same colour rounds the corners)
fl = f'fill="{W}" stroke="{W}" stroke-width="1.6" stroke-linejoin="round"'
save("media-play", f'<path d="M8 5.5v13l10.5-6.5z" {fl}/>')
save("media-pause", f'<rect x="7" y="5.5" width="3" height="13" rx="0.6" {fl}/>'
                    f'<rect x="14" y="5.5" width="3" height="13" rx="0.6" {fl}/>')
save("media-prev", f'<path d="M18 6.5v11l-8.5-5.5z" {fl}/><rect x="6" y="6.5" width="1.4" height="11" rx="0.5" {fl}/>')
save("media-next", f'<path d="M6 6.5v11l8.5-5.5z" {fl}/><rect x="16.6" y="6.5" width="1.4" height="11" rx="0.5" {fl}/>')
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
