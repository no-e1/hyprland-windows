"""Which screen is the main one (Windows: "Make this my main display"),
shared by winshell and ncplace.py.

The main display gets the full taskbar (tray, Wi-Fi / volume / battery) and
the notifications; the others get start, apps and the clock. The choice is
kept in ~/.config/winshell/display.json; without one the screen at the top
left of the layout is the main one.
"""
import json
import os
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


def save(**kw):
    cfg = load()
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
    chosen = load().get("primary")
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
    saved = load().get("layout", {}) if saved is None else saved
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
