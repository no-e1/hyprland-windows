"""Which screen is the main one (Windows: "Make this my main display"),
shared by winshell and ncplace.py.

The main display gets the full taskbar (tray, Wi-Fi / volume / battery) and
the notifications; the others get start, apps and the clock. The choice is
kept in ~/.config/winshell/display.json; without one the screen at the top
left of the layout is the main one.
"""
import json
import os
import re
import subprocess

CONFIG = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
                      "winshell", "display.json")


def monitors():
    """Hyprland's monitors (list of dicts, as `hyprctl monitors -j`)."""
    try:
        out = subprocess.run(["hyprctl", "monitors", "-j"], capture_output=True, text=True,
                             timeout=3).stdout
        return [m for m in json.loads(out) if not m.get("disabled")]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def load():
    try:
        with open(CONFIG) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def ident(m):
    """Which screen it is – make, model and serial ("Lenovo Group Limited
    P24h-30 V90DDT0H"), not the connector: behind a docking station the
    same screen comes up as DP-2 one day and DP-5 the next."""
    return (m.get("description") or m.get("name") or "").strip()


def combo(mons):
    """The set of screens plugged in – like Windows, where they stand and
    how they project is kept per set (laptop alone, laptop + the screen at
    home, laptop + the one at school, …)."""
    return " | ".join(sorted(ident(m) for m in mons))


def save(**kw):
    cfg = load()
    if "primary" in kw:                 # the main display: also by identity
        m = next((m for m in all_monitors() if m["name"] == kw["primary"]), None)
        if m:
            cfg["primary_screen"] = ident(m)
    cfg.update(kw)
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG)


def primary_name(mons=None):
    """Connector name of the main display (e.g. "eDP-1")."""
    mons = monitors() if mons is None else mons
    names = [m["name"] for m in mons]
    cfg = load()
    by_ident = next((m["name"] for m in mons if ident(m) == cfg.get("primary_screen")), None)
    if by_ident:
        return by_ident
    chosen = cfg.get("primary")
    if chosen in names:
        return chosen
    if not mons:
        return chosen or ""
    return min(mons, key=lambda m: (m.get("y", 0), m.get("x", 0)))["name"]


# ── layout: Hyprland places screens in logical px (pixels ÷ scale); the
#    Display settings draw them by their real resolution, like Windows ──
STEPS = (1, 1.25, 1.5, 1.75, 2, 2.25, 2.5, 3)        # Windows' choices


def valid_scales(pw, ph, current=None):
    """The scales a screen can really do. Hyprland only takes scales (in
    steps of 1/120) that divide the resolution into whole pixels – 2560 px
    at 150 % would be 1706.67, it refuses that ("Invalid scale"). For every
    Windows step the closest valid one (if close enough), e.g. 160 % instead
    of 150 %. The scale in use is always in the list."""
    ok = [k / 120 for k in range(120, 361)
          if (pw * 120) % k == 0 and (ph * 120) % k == 0]
    out = set()
    for step in STEPS:
        best = min(ok, key=lambda v: abs(v - step))
        if abs(best - step) <= 0.101:
            out.add(best)
    if current:
        near = min(out, key=lambda v: abs(v - current)) if out else None
        if near is None or abs(near - current) > 0.004:
            out.add(current)
    return sorted(out)
TOL = 2                         # px: edges this close count as touching


def nice_name(m):
    """"Lenovo P24h-30", or "Built-in display" for the laptop's own."""
    make = (m.get("make") or "").replace(" Group Limited", "").replace(" Inc.", "").strip()
    model = (m.get("model") or "").strip()
    if m.get("name", "").startswith("eDP") or not model or model.lower().startswith("0x"):
        return "Built-in display"
    return f"{make} {model}".strip()


def rates(m):
    """The refresh rates the screen offers at its current resolution (Hz,
    ascending). The one in use is listed with its exact value (59.951)."""
    cur = m.get("refreshRate") or 60
    found = set()
    for mode in m.get("availableModes") or []:
        try:
            res, hz = mode.split("@")
            if res == f'{m["width"]}x{m["height"]}':
                found.add(float(hz.rstrip("Hz")))
        except ValueError:
            pass
    out = sorted(r for r in found if abs(r - cur) > 0.05)
    return sorted(out + [cur])


def rate_label(hz):
    return f"{hz:.2f}".rstrip("0").rstrip(".") + " Hz"


def items_from(mons):
    """Hyprland monitors → layout items (logical and real sizes)."""
    out = []
    for m in mons:
        sc = m.get("scale") or 1
        out.append({"name": m["name"], "label": nice_name(m), "pw": m["width"], "ph": m["height"],
                    "rate": m.get("refreshRate") or 60, "rates": rates(m), "scale": sc,
                    "lx": m["x"], "ly": m["y"], "lw": round(m["width"] / sc), "lh": round(m["height"] / sc)})
    out.sort(key=lambda i: (i["lx"], i["ly"]))
    return out


def _touch(a, b, k):
    """How b sits next to a in coordinate system k ("l": logical, "p": real):
    "right" / "left" / "below" / "above", or None."""
    ax, ay, aw, ah = a[k + "x"], a[k + "y"], a[k + "w"], a[k + "h"]
    bx, by, bw, bh = b[k + "x"], b[k + "y"], b[k + "w"], b[k + "h"]
    over_y = by < ay + ah and ay < by + bh
    over_x = bx < ax + aw and ax < bx + bw
    if over_y and abs(bx - (ax + aw)) <= TOL:
        return "right"
    if over_y and abs((bx + bw) - ax) <= TOL:
        return "left"
    if over_x and abs(by - (ay + ah)) <= TOL:
        return "below"
    if over_x and abs((by + bh) - ay) <= TOL:
        return "above"
    return None


def _convert(items, src, dst):
    """Re-place all items in system dst so that whatever touched in src
    touches the same way in dst (the offset along the edge is scaled)."""
    for i in items:
        i[dst + "w"] = i["pw"] if dst == "p" else round(i["pw"] / i["scale"])
        i[dst + "h"] = i["ph"] if dst == "p" else round(i["ph"] / i["scale"])
    if not items:
        return items
    first = min(items, key=lambda i: (i[src + "y"], i[src + "x"]))
    first[dst + "x"], first[dst + "y"] = 0, 0
    done, todo = [first], [i for i in items if i is not first]
    while todo:
        hit = None
        for t in todo:
            for d in done:
                side = _touch(d, t, src)
                if side:
                    hit = (t, d, side)
                    break
            if hit:
                break
        if not hit:                         # not touching anything: to the right of all
            t = todo[0]
            t[dst + "x"] = max(i[dst + "x"] + i[dst + "w"] for i in done)
            t[dst + "y"] = 0
        else:
            t, d, side = hit
            f = (d[dst + "w"] / d[src + "w"]) if d[src + "w"] else 1
            if side in ("right", "left"):
                t[dst + "x"] = d[dst + "x"] + d[dst + "w"] if side == "right" else d[dst + "x"] - t[dst + "w"]
                t[dst + "y"] = d[dst + "y"] + round((t[src + "y"] - d[src + "y"]) * f)
            else:
                t[dst + "y"] = d[dst + "y"] + d[dst + "h"] if side == "below" else d[dst + "y"] - t[dst + "h"]
                t[dst + "x"] = d[dst + "x"] + round((t[src + "x"] - d[src + "x"]) * f)
        done.append(t)
        todo.remove(t)
    x0 = min(i[dst + "x"] for i in items)
    y0 = min(i[dst + "y"] for i in items)
    for i in items:
        i[dst + "x"] -= x0
        i[dst + "y"] -= y0
    return items


def to_real(items):
    """Fill px/py (layout by real resolution) from the logical layout."""
    return _convert(items, "l", "p")


def to_logical(items):
    """Fill lx/ly/lw/lh (for Hyprland) from the layout by real resolution."""
    return _convert(items, "p", "l")


def spec(i, **extra):
    """A Lua table for hl.monitor()."""
    # the exact refresh rate (59.951, not 59.95) and scale (1.33333333):
    # rounded ones made Hyprland pick another mode / refuse the scale
    # "disabled = false": without it a screen that was switched off (Win + P,
    # a reset) stays off – Hyprland keeps the old "disabled" otherwise; the
    # same for mirror = "": without it a duplicated screen stays duplicated
    s = (f'{{ output = "{i["name"]}", mode = "{i["pw"]}x{i["ph"]}@{i["rate"]:.3f}", '
         f'position = "{i["lx"]}x{i["ly"]}", scale = {i["scale"]:.8g}, disabled = false, mirror = ""')
    for k, v in extra.items():
        s += f', {k} = ' + (f'"{v}"' if isinstance(v, str) else ("true" if v is True else str(v)))
    return s + " }"


def on_spec(m, saved=None):
    """hl.monitor() table that switches a screen on: as saved in Display
    settings, else as it is / as Hyprland prefers."""
    rest = saved_rest(m)
    if rest:
        return '{ output = "%s", %s }' % (m["name"], rest)
    saved = load().get("layout", {}) if saved is None else saved      # old files: by connector
    if m["name"] in saved:
        rest = saved[m["name"]]
        if "disabled" not in rest:
            rest += ", disabled = false"
        if "mirror" not in rest:
            rest += ', mirror = ""'
        return '{ output = "%s", %s }' % (m["name"], rest)
    if m.get("disabled") or not m.get("width"):
        return (f'{{ output = "{m["name"]}", mode = "preferred", position = "auto", scale = "auto", '
                f'disabled = false, mirror = "" }}')
    return spec(items_from([m])[0])


# ── kept per screen (mode, scale) and per set of screens (positions, Win + P) ──
def remember(items, mons=None):
    """Display settings → Keep: every screen's resolution, refresh rate and
    scale under its identity, the positions under this set of screens."""
    mons = all_monitors() if mons is None else mons
    ids = {m["name"]: ident(m) for m in mons}
    cfg = load()
    screens = cfg.setdefault("screens", {})
    pos = cfg.setdefault("layouts", {}).setdefault(combo(mons), {})
    for i in items:
        sid = ids.get(i["name"])
        if not sid:
            continue
        screens[sid] = {"mode": f'{i["pw"]}x{i["ph"]}@{i["rate"]:.3f}', "scale": round(i["scale"], 8)}
        pos[sid] = f'{i["lx"]}x{i["ly"]}'
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG)


def saved_rest(m, mons=None, cfg=None):
    """The saved part of a hl.monitor() table for screen m (mode, position,
    scale), or None when this screen was never set up."""
    cfg = load() if cfg is None else cfg
    scr = cfg.get("screens", {}).get(ident(m))
    if not scr:
        return None
    mons = all_monitors() if mons is None else mons
    at = cfg.get("layouts", {}).get(combo(mons), {}).get(ident(m), "auto")
    return (f'mode = "{scr["mode"]}", position = "{at}", scale = {scr["scale"]:.8g}, '
            f'disabled = false, mirror = ""')


def recommended_scale(m, pw, ph):
    """Like Windows' "(Recommended)": about 96 dpi per 100 % on a monitor,
    120 dpi on a laptop's own screen (2880 px on 14" → 200 %, a 24" 1440p
    monitor → 125 %), to the nearest scale the screen can do."""
    mm = m.get("physicalWidth") or 0
    if mm < 100:                        # size unknown (projectors, some TVs)
        return 1
    want = pw / (mm / 25.4) / (120 if m["name"].startswith("eDP") else 96)
    return min(valid_scales(pw, ph, None), key=lambda v: abs(v - want))


def preferred_mode(m):
    """The screen's own preferred mode (Hyprland lists it first) – not the
    fastest one: "preferred" in Hyprland took 74.78 Hz on the P24h-30."""
    modes = m.get("availableModes") or []
    try:
        res, hz = modes[0].split("@")
        pw, ph = (int(v) for v in res.split("x"))
        rate = float(hz.rstrip("Hz"))
    except (IndexError, ValueError):
        return m["width"], m["height"], m.get("refreshRate") or 60
    if (pw, ph) == (m["width"], m["height"]) and abs((m.get("refreshRate") or 0) - rate) < 0.05:
        rate = m["refreshRate"]                 # the exact value (59.951)
    return pw, ph, rate


def plugged_specs(mons=None):
    """A screen was plugged in or out: how this set of screens is to be,
    like Windows – as it was the last time this set was plugged in (Win + P,
    positions), each screen with its own resolution and scale. A screen
    never seen before gets its preferred mode and the recommended scale and
    goes to the right of the others ("Extend", like Windows). Returns
    (wanted hl.monitor() tables by connector, Win + P mode)."""
    mons = all_monitors() if mons is None else mons
    if not mons:
        return {}, "extend"
    cfg = load()
    key = combo(mons)
    mode = cfg.get("projects", {}).get(key, "extend")
    if len(mons) > 1 and mode != "extend":
        return {re.search(r'output = "([^"]+)"', sp).group(1): sp
                for sp in project_specs(mode, mons)}, mode
    layout = cfg.get("layouts", {}).get(key, {})
    items, new = [], []
    for m in mons:
        scr = cfg.get("screens", {}).get(ident(m))
        if scr:
            res, hz = scr["mode"].split("@")
            pw, ph = (int(v) for v in res.split("x"))
            rate, sc = float(hz), scr["scale"]
        else:
            pw, ph, rate = preferred_mode(m)
            sc = recommended_scale(m, pw, ph)
        i = {"name": m["name"], "pw": pw, "ph": ph, "rate": rate, "scale": sc,
             "lw": round(pw / sc), "lh": round(ph / sc)}
        at = layout.get(ident(m))
        if at:
            i["lx"], i["ly"] = (int(v) for v in at.split("x"))
            items.append(i)
        else:
            new.append(i)
    if not items:                       # nothing known for this set: the laptop's screen first
        new.sort(key=lambda i: (not i["name"].startswith("eDP"), i["name"]))
        first = new.pop(0)
        first["lx"], first["ly"] = 0, 0
        items.append(first)
    for i in new:                       # new in this set: to the right, top edges in line
        i["lx"] = max(o["lx"] + o["lw"] for o in items)
        i["ly"] = min(o["ly"] for o in items)
        items.append(i)
    return {i["name"]: spec(i) for i in items}, "extend"


def current_spec(m):
    """The hl.monitor() table for how screen m is right now (to compare)."""
    if m.get("disabled"):
        return f'{{ output = "{m["name"]}", disabled = true }}'
    if (m.get("mirrorOf") or "none") != "none":
        return f'{{ output = "{m["name"]}", mirror = "{m["mirrorOf"]}" }}'
    return spec(items_from([m])[0])


def same(a, b):
    """Two hl.monitor() tables that set the same (refresh rates to 0.05 Hz)."""
    def norm(sp):
        sp = re.sub(r'@(\d+\.?\d*)', lambda x: "@%.1f" % float(x.group(1)), sp)
        return re.sub(r'scale = ([\d.]+)', lambda x: "scale = %.4f" % float(x.group(1)), sp)
    return norm(a) == norm(b)


# ── Win + P ──
def all_monitors():
    try:
        out = subprocess.run(["hyprctl", "monitors", "all", "-j"], capture_output=True, text=True,
                             timeout=3).stdout
        return json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def project_mode(mons=None):
    """"pc" | "duplicate" | "extend" | "second" – what the screens do now."""
    mons = all_monitors() if mons is None else mons
    inner = [m for m in mons if m["name"].startswith("eDP")]
    outer = [m for m in mons if not m["name"].startswith("eDP")]
    if any((m.get("mirrorOf") or "none") != "none" for m in mons):
        return "duplicate"
    if inner and all(m.get("disabled") for m in inner) and outer:
        return "second"
    if outer and all(m.get("disabled") for m in outer):
        return "pc"
    return "extend"


def project_specs(mode, mons=None):
    """The hl.monitor() tables for a Win + P choice. The laptop's own screen
    is "PC screen", every other screen a "second screen". Screens that are
    on use the layout saved in Display settings (or how they are now)."""
    mons = all_monitors() if mons is None else mons
    saved = load().get("layout", {})
    inner = [m for m in mons if m["name"].startswith("eDP")]
    outer = [m for m in mons if not m["name"].startswith("eDP")]
    if not inner or not outer:
        return []                           # one screen: nothing to choose

    def on(m):
        if not m.get("disabled") and (m.get("mirrorOf") or "none") == "none":
            return None                     # on already: don't touch it (it would flicker)
        return on_spec(m, saved)

    def off(m, really=True):
        return None if m.get("disabled") else f'{{ output = "{m["name"]}", disabled = true }}'
    main = inner[0]["name"]
    if mode == "pc":
        specs = [on(m) for m in inner] + [off(m) for m in outer]
    elif mode == "second":
        specs = [on(m) for m in outer] + [off(m) for m in inner]
    elif mode == "duplicate":
        specs = [on(m) for m in inner] + [
            f'{{ output = "{m["name"]}", mode = "preferred", position = "auto", scale = "auto", '
            f'disabled = false, mirror = "{main}" }}'
            for m in outer if m.get("disabled") or m.get("mirrorOf") in (None, "none")]
    else:
        specs = [on(m) for m in mons]       # extend
    return [sp for sp in specs if sp]


# ── applying several screens one after the other ──
def _geom(spec):
    """(x, y, w, h) in logical px that a hl.monitor() table takes up – None
    if it takes no room (off, duplicating) or isn't known ("auto")."""
    if re.search(r'disabled = true', spec) or re.search(r'mirror = "[^"]+"', spec):
        return None
    pos = re.search(r'position = "(-?\d+)x(-?\d+)"', spec)
    mode = re.search(r'mode = "(\d+)x(\d+)', spec)
    sc = re.search(r'scale = ([\d.]+)', spec)
    if not (pos and mode):
        return None
    k = float(sc.group(1)) if sc else 1.0
    return (int(pos.group(1)), int(pos.group(2)), int(mode.group(1)) / k, int(mode.group(2)) / k)


def _overlap(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


def safe_order(specs, mons=None):
    """The hl.monitor() tables in an order in which no two screens ever lie
    on top of each other – they are set one after the other, and e.g. moving
    the second screen to the left of the laptop's (0x0) while the laptop's
    still stood at 0x0 made Hyprland warn "monitor layout is set up
    incorrectly". When screens swap places, one is parked far to the right
    for a moment first."""
    mons = all_monitors() if mons is None else mons
    cur = {}
    for m in mons:
        if m.get("disabled") or (m.get("mirrorOf") or "none") != "none":
            continue
        k = m.get("scale") or 1
        cur[m["name"]] = (m["x"], m["y"], m["width"] / k, m["height"] / k)
    todo = []
    for sp in specs:
        n = re.search(r'output = "([^"]+)"', sp)
        if n:
            todo.append((n.group(1), sp))
    out = []
    while todo:
        for i, (n, sp) in enumerate(todo):
            g = _geom(sp)
            if g is None or not any(_overlap(g, cur[o]) for o in cur if o != n):
                out.append(sp)
                if g is None:
                    cur.pop(n, None)
                else:
                    cur[n] = g
                todo.pop(i)
                break
        else:
            n, sp = todo[0]             # stuck (swapping places): park this one first
            far = int(max((g[0] + g[2] for g in cur.values()), default=0)) + 1000
            parked = re.sub(r'position = "[^"]*"', f'position = "{far}x0"', sp)
            out.append(parked)
            g = _geom(parked)
            if g is None:
                cur.pop(n, None)
                todo.pop(0)
                out[-1] = sp
            else:
                cur[n] = g
    return out
