-- ════════════════════════════════════════════════════════════════
--  Hyprland in the style of Windows 11
--  For Hyprland 0.57+ (Lua config)
--
--  Personal tweaks (keyboard, monitors) belong in user.lua,
--  which is loaded at the end of this file.
-- ════════════════════════════════════════════════════════════════

local home     = os.getenv("HOME")
local scripts  = home .. "/.config/hypr/scripts"
local winshell = home .. "/.config/hypr/winshell/winshell-ctl"

local terminal    = "winterm"          -- like Windows Terminal (hypr/winshell/winterm.py); kitty stays installed
local fileManager = "dolphin"
local browser     = "brave-browser"

---------------------------------------------------------------------
-- Monitors
---------------------------------------------------------------------
hl.monitor({
    output   = "",
    mode     = "preferred",
    position = "auto",
    scale    = "auto",
})
-- where the screens stand and which resolution: set in Display settings
-- (Win + I → Displays), kept in monitors.lua (not in the repo – per machine)
pcall(dofile, home .. "/.config/hypr/monitors.lua")

---------------------------------------------------------------------
-- Environment
---------------------------------------------------------------------
hl.env("XCURSOR_SIZE", "24")
hl.env("HYPRCURSOR_SIZE", "24")
hl.env("XCURSOR_THEME", "Fluent-dark-cursors")   -- white pointer with black outline, like Windows 11
hl.env("GDK_BACKEND", "wayland,x11,*")
hl.env("QT_QPA_PLATFORM", "wayland;xcb")
hl.env("QT_WAYLAND_DISABLE_WINDOWDECORATION", "1")
-- Qt/KDE apps (Dolphin, Okular, Ark …) follow the KDE settings: dark Breeze
-- colors (~/.config/kdeglobals, ColorScheme=BreezeDark) – without it they
-- came up light and plain
hl.env("QT_QPA_PLATFORMTHEME", "kde")
hl.env("MOZ_ENABLE_WAYLAND", "1")
hl.env("ELECTRON_OZONE_PLATFORM_HINT", "auto")

---------------------------------------------------------------------
-- Autostart
---------------------------------------------------------------------
hl.on("hyprland.start", function()
    hl.exec_cmd("dbus-update-activation-environment --systemd WAYLAND_DISPLAY XDG_CURRENT_DESKTOP HYPRLAND_INSTANCE_SIGNATURE")
    hl.exec_cmd(scripts .. "/theme.sh")        -- GTK theme, icons, cursor, font
    hl.exec_cmd(scripts .. "/polkit.sh")       -- password prompts for admin actions
    hl.exec_cmd(scripts .. "/wallpaper.sh restore")
    hl.exec_cmd(scripts .. "/radios.sh")       -- Wi-Fi on, Bluetooth off
    hl.exec_cmd(scripts .. "/swaync.sh")       -- notifications (placed above the calendar)
    -- winshell: taskbar, start menu, Alt+Tab, quick settings, calendar;
    -- restarts itself if it ever crashes (like explorer.exe)
    -- the tray's registry first, as a service of its own: apps announce their
    -- tray icon to it once, it must outlive winshell restarts
    hl.exec_cmd("python3 -I " .. home .. "/.config/hypr/winshell/sni-watcher.py")
    hl.exec_cmd(winshell .. " supervise")
    hl.exec_cmd("hypridle")                    -- lock / screen off when idle
    hl.exec_cmd(scripts .. "/lock.sh --standby")   -- lock screen waits ready: Win + L locks at once
    -- clipboard history for Win + V (winshell/clipstore.py keeps the last 25)
    hl.exec_cmd("wl-paste --type text --watch python3 -I " .. home .. "/.config/hypr/winshell/clipstore.py text")
    hl.exec_cmd("wl-paste --type image/png --watch python3 -I " .. home .. "/.config/hypr/winshell/clipstore.py image")
end)

---------------------------------------------------------------------
-- Look: Windows 11 (dark, rounded corners, shadows, Mica-like blur)
---------------------------------------------------------------------
hl.config({
    general = {
        gaps_in  = 0,
        gaps_out = 0,
        border_size = 1,
        col = {
            active_border   = "rgba(ffffff38)",
            inactive_border = "rgba(ffffff14)",
        },
        -- resize windows by grabbing their edges, like on Windows
        resize_on_border = true,
        extend_border_grab_area = 12,
        allow_tearing = false,
        layout = "dwindle",
    },

    decoration = {
        rounding       = 8,
        rounding_power = 2,
        active_opacity   = 1.0,
        inactive_opacity = 1.0,

        shadow = {
            enabled      = true,
            range        = 22,
            render_power = 3,
            color        = 0x66000000,
        },

        blur = {
            -- only for the task view (Win + Tab, layer rule below): the panels
            -- are plain, solid gray (style.css), windows get none (window rule)
            enabled  = true,
            size     = 9,
            passes   = 3,
            noise    = 0.02,
            vibrancy = 0.2,
        },
    },

    animations = {
        enabled = true,
    },

    cursor = {
        no_warps = true,             -- the mouse never jumps on its own (like Windows)
    },

    misc = {
        -- hyprland-guiutils (ANR/permission dialogs) is not packaged for
        -- Fedora; don't warn about it on every login
        disable_hyprland_guiutils_check = true,
        force_default_wallpaper = 0,
        disable_hyprland_logo   = true,
        focus_on_activate       = true,
        -- if the lock screen ever crashes, another one (hyprlock, see
        -- scripts/lock.sh) may take over the lock instead of a red screen
        allow_session_lock_restore = true,
        -- the lock screen fades in over the desktop and out again on unlock,
        -- like Windows (lockscreen.py is fully opaque once it has faded in)
        session_lock_xray = true,
    },
})

---------------------------------------------------------------------
-- Animations: windows pop in softly like on Windows
---------------------------------------------------------------------
hl.curve("winOut",  { type = "bezier", points = { {0.1, 0.9},  {0.1, 1}  } })
hl.curve("winIn",   { type = "bezier", points = { {0.4, 0},    {1, 0.6}  } })
hl.curve("smooth",  { type = "bezier", points = { {0.25, 1},   {0.5, 1}  } })
hl.curve("linear",  { type = "bezier", points = { {0, 0},      {1, 1}    } })
hl.curve("soft",    { type = "spring", mass = 1, stiffness = 260, damping = 26 })
-- panels sliding in (calendar + notification center): decelerates too, but
-- gently – with winOut they covered 45 % of the way in the first frame and
-- 68 % in the second (a jump of ~180 px, it looked like a stutter)
hl.curve("panelIn", { type = "bezier", points = { {0.2, 0.7},  {0.2, 1}  } })
-- … and out: speeds up, but moves from the first frame (winIn barely moved
-- for the first ~70 ms – closing felt sluggish)
hl.curve("panelOut", { type = "bezier", points = { {0.4, 0.1}, {0.7, 0.6} } })

hl.animation({ leaf = "global",        enabled = true, speed = 8,   bezier = "smooth" })
hl.animation({ leaf = "windows",       enabled = true, speed = 4,   spring = "soft" })
hl.animation({ leaf = "windowsIn",     enabled = true, speed = 2.2, bezier = "winOut", style = "popin 92%" })
hl.animation({ leaf = "windowsOut",    enabled = true, speed = 2,   bezier = "winIn",  style = "popin 94%" })
hl.animation({ leaf = "windowsMove",   enabled = true, speed = 3.5, spring = "soft" })
hl.animation({ leaf = "border",        enabled = true, speed = 4,   bezier = "smooth" })
hl.animation({ leaf = "fadeIn",        enabled = true, speed = 1.8, bezier = "smooth" })
hl.animation({ leaf = "fadeOut",       enabled = true, speed = 2,   bezier = "linear" })
hl.animation({ leaf = "fade",          enabled = true, speed = 3,   bezier = "smooth" })
-- window opacity changes: only "show desktop" uses them – instant, winshell
-- animates pictures of the windows instead
hl.animation({ leaf = "fadeSwitch",    enabled = false })
-- start menu and quick settings slide in from the bottom
hl.animation({ leaf = "layersIn",      enabled = true, speed = 3.5, bezier = "panelIn", style = "slide bottom" })
hl.animation({ leaf = "layersOut",     enabled = true, speed = 1.5, bezier = "panelOut", style = "slide bottom" })
hl.animation({ leaf = "fadeLayersIn",  enabled = true, speed = 2.5, bezier = "smooth" })
hl.animation({ leaf = "fadeLayersOut", enabled = true, speed = 1.8, bezier = "linear" })
-- virtual desktops slide sideways like on Windows
WORKSPACE_ANIM = { leaf = "workspaces", enabled = true, speed = 4, bezier = "winOut", style = "slide" }
hl.animation(WORKSPACE_ANIM)

---------------------------------------------------------------------
-- Colours: no automatic HDR. A full-screen video that asks for HDR / wide
-- colours (YouTube in Brave) switched the laptop screen to that mode: a few
-- seconds black (the screen is set up anew), and as the panel only runs at
-- 8 bit the colours came out wrong ("wide color gamut is enabled but the
-- display is not in 10-bit mode"). Videos simply play in normal colours.
---------------------------------------------------------------------
hl.config({ render = { cm_auto_hdr = 0 } })

---------------------------------------------------------------------
-- Input
---------------------------------------------------------------------
hl.config({
    input = {
        kb_layout  = "ch",   -- overridden in user.lua
        kb_variant = "",
        numlock_by_default = true,
        -- Like Windows: focus changes on click, not on hover.
        -- Scrolling still goes to the window under the cursor.
        follow_mouse = 2,
        float_switch_override_focus = 0,
        -- Windows default pointer speed (10/20, "enhance pointer precision"):
        -- libinput's adaptive profile at 0 is noticeably faster, so slow it down
        accel_profile = "adaptive",
        sensitivity  = -0.3,
        touchpad = {
            natural_scroll = true,
            -- tap and touch again = "hold the button and drag": with many
            -- quick 3-finger swipes (lift, touch again) that turned into
            -- stray clicks and drags. Tapping still clicks; to drag, press
            -- the touchpad down.
            tap_and_drag   = false,
            -- two-finger scrolling: a bit slower than Linux' default; browsers
            -- scroll much further per step, they get their own factor below
            scroll_factor  = 0.7,
        },
    },
})

-- The touchpad keeps libinput's default speed (close to Windows' default)
hl.device({ name = "syna8018:00-06cb:ce67-touchpad", sensitivity = 0 })
-- Mouse settings (Win + I → Mouse): primary button, pointer / touchpad
-- speed, scroll speed – kept in mouse.lua (not in the repo – per machine)
pcall(dofile, home .. "/.config/hypr/mouse.lua")

-- Touchpad: 4-finger swipe switches virtual desktops (like Windows) – on
-- all screens at once (a desktop spans all of them; Hyprland's own
-- "workspace" swipe only moved the screen you're on). Fingers to the left:
-- the desktop on the right. One desktop per swipe; a short flick counts
-- when lifted.
local DESK4_SWIPE, DESK4_FLICK = 60, 20
local desk4_acc, desk4_done = 0, false
local function desk4_go(acc)
    desk4_done = true
    DesktopStep(acc < 0 and 1 or -1)
end
hl.gesture({ fingers = 4, direction = "horizontal", action = {
    start = function() desk4_acc, desk4_done = 0, false end,
    update = function(e)
        if desk4_done then return end
        desk4_acc = desk4_acc + ((e.delta and e.delta.x) or 0)
        if math.abs(desk4_acc) >= DESK4_SWIPE then desk4_go(desk4_acc) end
    end,
    finish = function(e)
        if not desk4_done and not (e and e.cancelled) and math.abs(desk4_acc) >= DESK4_FLICK then
            desk4_go(desk4_acc)
        end
    end,
} })

---------------------------------------------------------------------
-- Window rules: every window floats freely (no tiling), like Windows
---------------------------------------------------------------------
hl.window_rule({
    name   = "float-everything",
    match  = { class = ".*" },
    float  = true,
    center = true,
})

hl.window_rule({
    name  = "terminal-size",
    match = { class = "^(kitty|io\\.github\\.noe1\\.winterm)$" },
    size  = "1000 640",
})

hl.window_rule({
    name  = "small-tools",
    match = { class = "^(org.pulseaudio.pavucontrol|pavucontrol|blueman-manager|.*blueman.*|nm-connection-editor)$" },
    size  = "760 560",
})

-- Browsers scroll much further per touchpad step than other apps
hl.window_rule({
    name  = "browser-touchpad-scroll",
    match = { class = "^(brave-browser|chromium|google-chrome|firefox)$" },
    scroll_touchpad = 0.25,
})

-- XWayland drag fix from the default config
hl.window_rule({
    name  = "fix-xwayland-drags",
    match = { class = "^$", title = "^$", xwayland = true, float = true, fullscreen = false, pin = false },
    no_focus = true,
})

-- Blur only in the task view (Win + Tab): like Windows, the whole screen
-- behind it is blurred and dimmed (winshell's style.css .taskview).
-- Everything else is solid (no blur rules), and windows get no blur at all.
hl.window_rule({ name = "no-blur-windows", match = { class = ".*" }, no_blur = true })
hl.layer_rule({ name = "blur-taskview", match = { namespace = "^winshell-taskview$" }, blur = true })
-- the notification center slides in from the right screen edge and back out,
-- like Windows 11 – in step with winshell's calendar below it (same rule)
hl.layer_rule({ name = "slide-swaync-cc", match = { namespace = "^swaync-control-center$" }, animation = "slide right" })
-- winshell animates its flyouts itself (they glide out of the taskbar)
hl.layer_rule({ name = "no-anim-winshell", match = { namespace = "^winshell-.*$" }, no_anim = true })
-- …except the task view (Win + Tab): it fades in and out as a whole, like
-- Windows (its contents move by themselves, see winshell's style.css)
hl.layer_rule({ name = "fade-taskview",    match = { namespace = "^winshell-taskview$" }, no_anim = false, animation = "fade" })
-- …and the calendar: slides in from the right edge and back out, together
-- with the notification center above it (swaync-control-center)
hl.layer_rule({ name = "slide-calendar",   match = { namespace = "^winshell-calendar$" }, no_anim = false, animation = "slide right" })
-- slurp's selection overlay must vanish at once: otherwise it is still sliding
-- out (dim + blue frame) while grim takes the screenshot
hl.layer_rule({ name = "no-anim-slurp",    match = { namespace = "^selection$" },   no_anim = true })

---------------------------------------------------------------------
-- Active window always comes to the front (taskbar, click, Alt+Tab).
-- Minimized windows live in the hidden "special:minimized" workspace;
-- activating one (e.g. from the taskbar) brings it back.
---------------------------------------------------------------------
-- State shared with the maximize code further down (defined here because
-- hiding and restoring need it too).
local saved_geometry = {}       -- address -> geometry before maximizing
local fs_hook_paused = {}       -- address -> true while we change its state ourselves
local hidden_maximized = {}     -- address -> true: was maximized when it got minimized
-- address -> { ws, x, y }: where a minimized window was. The hidden workspace
-- sits on one screen and shifts a window from another screen onto that one;
-- it comes back on its own screen, right where it was.
local hidden_from = {}
-- the workspace the screen of that slot shows now (nil: that screen is off)
local function shown_ws_of_slot(id)
    if not id or id <= 0 or not DESK_STRIDE then return nil end
    local s = id // DESK_STRIDE
    for _, m in ipairs(hl.get_monitors() or {}) do
        local a = m.active_workspace and m.active_workspace.id
        if a and a > 0 and a // DESK_STRIDE == s then return a end
    end
end
-- where a hidden window is to come back: x, y (nil: as it is)
local function hidden_place(w, pos)
    local p = (pos and pos[w.address]) or hidden_from[w.address]
    if p and w.workspace and w.workspace.name == "special:minimized" then
        local ws = p.ws or (pos and pos.ws and pos.ws[w.address])
        if not ws or shown_ws_of_slot(ws) then return p.x, p.y end
    end
end

-- Tell the app it is maximized (no shadow margins, "restore" button) while
-- Hyprland keeps treating it as a normal window (normal stacking order).
-- Like Windows 11, a maximized window has square corners and no border;
-- otherwise the normal rounding / border apply again.
local function square_corners(addr, on)
    for _, p in ipairs({ "rounding", "border_size" }) do
        hl.dispatch(hl.dsp.window.set_prop({ prop = p, value = on and "0" or "unset", window = "address:" .. addr }))
    end
end

-- quiet: don't tell the app itself (hiding / bringing back a window: an app
-- like Brave would rebuild itself right while it goes away and could hang)
local function client_maximized(addr, on, quiet)
    hl.dispatch(hl.dsp.window.fullscreen_state({ internal = 0, client = on and 1 or 0,
                                                 window = "address:" .. addr }))
    -- "maximized" for the app only (internal 0, client 1) unhooks Hyprland
    -- from the app's own full-screen requests: a YouTube video asked for full
    -- screen, the app was told yes, but the window stayed as big as before
    -- (the taskbar stayed). Keep them hooked up (window.fullscreen handles it).
    hl.dispatch(hl.dsp.window.set_prop({ prop = "sync_fullscreen", value = "1", window = "address:" .. addr }))
    square_corners(addr, on)
    if AppMaximized and not quiet then AppMaximized(addr, on) end
end
-- windows that are maximized already (config reload, login)
for _, w in ipairs(hl.get_windows() or {}) do
    if w.fullscreen_client == 1 then
        square_corners(w.address, true)
        hl.dispatch(hl.dsp.window.set_prop({ prop = "sync_fullscreen", value = "1", window = "address:" .. w.address }))
    end
end

-- the window an event is about (Hyprland passes the window object itself)
local function event_window(ev)
    local t = type(ev)
    if (t == "table" or t == "userdata") and ev.address then return ev end
    return nil
end

local function later(ms, fn)
    hl.timer(fn, { timeout = ms, type = "oneshot" })
end

-- Show desktop (Win + D, 3-finger swipe): the windows shrink into their
-- taskbar buttons and grow back out (drawn by winshell, see hide_all). The
-- real windows don't move, so Win + D works at any moment – pressed mid-way,
-- it turns around.
local function set_alpha(addr, v)
    for _, p in ipairs({ "opacity", "opacity_inactive", "opacity_fullscreen" }) do
        hl.dispatch(hl.dsp.window.set_prop({ prop = p, value = tostring(v), window = "address:" .. addr }))
    end
    -- No blur behind a window that is (being made) invisible: Hyprland blurs
    -- behind see-through windows, and when the invisible window is then moved
    -- to the hidden workspace it fades that blur out – the wallpaper where a
    -- maximized window had been flickered brighter (blurred) for a moment
    -- after "show desktop". Back to its own setting when it returns.
    hl.dispatch(hl.dsp.window.set_prop({ prop = "no_blur", value = v < 1 and "1" or "unset",
                                         window = "address:" .. addr }))
end

-- Bring a hidden window back to a desktop.
-- Maximized windows were told "not maximized" while hidden (see HideWindow),
-- so tell them again. Other Chromium-based windows need a reconfigure or
-- their input ends up shifted, so nudge the size by one pixel and back –
-- the second resize goes out a moment later, otherwise Hyprland merges both
-- and the window grows by a pixel on every restore.
function RestoreWindow(w, ws_id, pos)
    if ThumbsPause then ThumbsPause() end
    local a, sel = w.address, "address:" .. w.address
    -- numbers: the window object reads live values. pos: where it was before
    -- it was hidden – the hidden workspace sits on one screen and shifts a
    -- window from another screen onto that one
    local x, y = w.at.x, w.at.y
    local from = hidden_from[a]
    hidden_from[a] = nil
    if from then
        local back = shown_ws_of_slot(from.ws)
        if back then                -- its screen is on: there, where it was
            ws_id, pos = back, pos or { x = from.x, y = from.y }
        end
    end
    if pos then x, y = pos.x, pos.y end
    -- straight to its place: no sliding over from the other screen
    hl.dispatch(hl.dsp.window.set_prop({ prop = "no_anim", value = "1", window = sel }))
    hl.dispatch(hl.dsp.window.move({ workspace = tostring(ws_id), follow = false, window = sel }))
    hl.dispatch(hl.dsp.window.move({ x = x, y = y, window = sel }))
    set_alpha(a, 1)                 -- faded out by "show desktop": fades back in
    later(400, function()
        if hl.get_window(sel) then
            hl.dispatch(hl.dsp.window.set_prop({ prop = "no_anim", value = "unset", window = sel }))
        end
    end)
    if hidden_maximized[a] then
        hidden_maximized[a] = nil
        fs_hook_paused[a] = true
        client_maximized(a, true, true)
        later(300, function() fs_hook_paused[a] = nil end)
        later(400, function() KeepMaximized(a) end)     -- exactly its screen again
    else
        local sx, sy = w.size.x, w.size.y
        hl.dispatch(hl.dsp.window.resize({ x = sx + 1, y = sy, window = sel }))
        later(60, function()
            hl.dispatch(hl.dsp.window.resize({ x = sx, y = sy, window = sel }))
            -- shrinking back moves the window 1 px to the right: put it back
            hl.dispatch(hl.dsp.window.move({ x = x, y = y, window = sel }))
        end)
        later(400, function() KeepMaximized(a) end)     -- a maximized one: exactly its screen
    end
end

-- Hide (minimize) a window into the hidden "special:minimized" workspace.
--  * A maximized Chromium window (Brave) that is moved to a special
--    workspace while it believes it is maximized comes back with its
--    contents shifted ~50 px to the right (a blank strip on the left and
--    the mouse off target) until the browser is restarted. Dropping the
--    "maximized" state before hiding avoids that; RestoreWindow sets it again.
--  * Focus moves on to the next window of this desktop, so the taskbar no
--    longer shows the hidden window as the active one.
--  * Meanwhile Hyprland may hand focus to an already hidden window; the
--    restore hook below must ignore that for a moment.
local restore_blocked = false
local MAX_HIDE_DELAY = 250      -- ms
local hide_cancel = {}          -- address -> true: its pending (delayed) hide is off
local function focus_next_visible(except)
    local ws = hl.get_active_workspace()
    local best
    for _, w in ipairs(hl.get_windows() or {}) do
        if w.mapped and w.address ~= except and w.workspace and ws and w.workspace.id == ws.id
           and (not best or (w.focus_history_id or 99) < (best.focus_history_id or 99)) then
            best = w
        end
    end
    if best then
        hl.dispatch(hl.dsp.focus({ window = "address:" .. best.address }))
    end
end

function HideWindow(addr, keep_focus)
    if not addr then
        local w = hl.get_active_window()
        addr = w and w.address
    end
    if not addr then return end
    restore_blocked = true
    later(MAX_HIDE_DELAY + 400, function() restore_blocked = false end)
    local function hide()
        if hide_cancel[addr] then hide_cancel[addr] = nil return end
        if ThumbsPause then ThumbsPause() end
        local cur = hl.get_window("address:" .. addr)
        if cur and cur.workspace and (cur.workspace.id or 0) > 0 then
            hidden_from[addr] = { ws = cur.workspace.id, x = cur.at.x, y = cur.at.y }
        end
        hl.dispatch(hl.dsp.window.move({ workspace = "special:minimized", follow = false,
                                         window = "address:" .. addr }))
    end
    local w = hl.get_window("address:" .. addr)
    if saved_geometry[addr] or (w and w.fullscreen_client == 1) then
        -- the app has to redraw as "not maximized" before it disappears
        hidden_maximized[addr] = true
        fs_hook_paused[addr] = true
        client_maximized(addr, false, true)
        later(MAX_HIDE_DELAY, hide)
        later(MAX_HIDE_DELAY + 300, function() fs_hook_paused[addr] = nil end)
    else
        hide()
    end
    if not keep_focus then
        focus_next_visible(addr)
    end
end


-- Other ways of activating a minimized window (e.g. Waybar's app buttons)
-- focus it while it is still hidden: hide the minimized workspace again
-- first, then bring the window back.
hl.on("window.active", function()
    local w = hl.get_active_window()
    if w and w.workspace and w.workspace.name == "special:minimized" then
        local sp = hl.get_active_special_workspace()
        if sp and sp.name == "special:minimized" then
            hl.dispatch(hl.dsp.workspace.toggle_special("minimized"))
        end
        -- focus fell onto a hidden window as a side effect of hiding: ignore it
        if restore_blocked then return end
        local ws = hl.get_active_workspace()
        if ws and not ws.special then
            RestoreWindow(w, ws.id)
            hl.dispatch(hl.dsp.focus({ window = "address:" .. w.address }))
        end
    end
    hl.dispatch(hl.dsp.window.alter_zorder({ mode = "top" }))
end)

---------------------------------------------------------------------
-- Maximize like Windows: a maximized window is just a big floating window
-- that fills the work area, so other windows can still come in front of it.
-- (Hyprland's own "maximized" mode always draws the window on top.)
-- Shell access: hyprctl eval 'WinMax("toggle" | "max" | "restore")'
---------------------------------------------------------------------
local BAR_HEIGHT = 32
-- Apps that draw their own title bar with X / □ / – : they get no second
-- (hyprbars) title bar. Exact window classes, plus class prefixes.
-- (Spotify isn't here: on Wayland it draws no – □ X of its own)
OWN_TITLEBAR = { "brave-browser", "discord", "vesktop", "webcord", "steam",
                 "obsidian", "slack", "firefox", "org.mozilla.firefox",
                 "code", "code-oss", "codium", "vscodium", "VSCodium",
                 "com.visualstudio.CodeOSS",    -- VSCodium on Wayland
                 -- winshell's settings dialogs: their own bar with – and X
                 -- only (fixed size, no maximize, no snapping)
                 "io.github.noe1.winshell",
                 -- winterm: its tabs are in its own title bar (like Windows Terminal)
                 "io.github.noe1.winterm" }
OWN_TITLEBAR_PREFIX = { "org.gnome." }

local function has_bar(w)
    local cls = w.class or ""
    for _, c in ipairs(OWN_TITLEBAR) do
        if cls == c then return false end
    end
    for _, p in ipairs(OWN_TITLEBAR_PREFIX) do
        if cls:sub(1, #p) == p then return false end
    end
    return true
end

-- Hyprland tells every app "maximized" once and never again (otherwise apps
-- like Brave draw their own frame, and Hyprland shows it shifted). But then
-- an app with its own title bar waits forever for an answer to its first
-- □ click and ignores every further one. So answer it: "not maximized" for a
-- moment, then "maximized" again – it stays frame-less and takes the next
-- click (its □ then always toggles). Via the hyprbars plugin; GTK apps are
-- left alone.
function AppMaximized(addr, on)
    local hb = hl.plugin and hl.plugin.hyprbars
    if not hb or not hb.set_maximized then return end      -- older plugin
    local w = hl.get_window("address:" .. addr)
    if not w or has_bar(w) then return end
    local cls = w.class or ""
    for _, p in ipairs(OWN_TITLEBAR_PREFIX) do
        if cls:sub(1, #p) == p then return end
    end
    local sel = "address:" .. addr
    pcall(hb.set_maximized, sel, false)
    later(60, function() pcall(hb.set_maximized, sel, true) end)
end

local function work_area(mon)
    local r, s = mon.reserved, mon.scale
    return mon.x + r.left, mon.y + r.top,
           math.floor(mon.width / s) - r.left - r.right,
           math.floor(mon.height / s) - r.top - r.bottom
end

local function set_geometry(addr, x, y, w, h)
    if ThumbsPause then ThumbsPause() end
    local sel = "address:" .. addr
    hl.dispatch(hl.dsp.window.resize({ x = w, y = h, window = sel }))
    hl.dispatch(hl.dsp.window.move({ x = x, y = y, window = sel }))
end

-- Windows with a fixed size, like a Windows dialog – no maximize, no
-- snapping (also not by dragging to an edge), no full screen: winshell's own
-- dialogs (Display / Mouse settings) and apps that only come in one size.
-- (Hyprland doesn't tell whether an app can be resized, so they're listed.)
-- Window classes, lower case.
FIXED_SIZE = {
    ["io.github.noe1.winshell"] = true,
    ["mullvad vpn"] = true,
    ["mullvad-vpn"] = true,           -- Mullvad VPN (on Wayland)
}
local function fixed_size(w)
    return w and FIXED_SIZE[(w.class or ""):lower()] or false
end
hl.window_rule({ name = "winshell-dialogs", match = { class = "^io\\.github\\.noe1\\.winshell$" },
                 float = true, center = true })

-- A window coming out of maximized / snapped: its normal size (no bigger
-- than the screen allows), centered on the screen it is on now – its old
-- place may be on the other screen (or half on both) after it was moved.
local function centered(w, g)
    local x, y, ww, hh = work_area(w.monitor or hl.get_active_monitor())
    local bar = has_bar(w) and BAR_HEIGHT or 0
    local cw = math.min(g[3], ww)
    local ch = math.min(g[4], hh - bar)
    local top = y + math.floor((hh - (ch + bar)) / 2)
    return { x + math.floor((ww - cw) / 2), top + bar, cw, ch }
end

function WinMax(action, w)
    w = w or hl.get_active_window()
    if not w or not w.mapped or fixed_size(w) then return end
    local a = w.address
    if action == "toggle" then
        -- maximized also when its normal size got lost (e.g. a config reload)
        action = (saved_geometry[a] or w.fullscreen_client == 1) and "restore" or "max"
    end
    local mon = w.monitor or hl.get_active_monitor()
    local x, y, ww, hh = work_area(mon)
    local bar = has_bar(w) and BAR_HEIGHT or 0
    if not fs_hook_paused[a] then
        fs_hook_paused[a] = true
        later(400, function() fs_hook_paused[a] = nil end)
    end
    if action == "max" then
        if not saved_geometry[a] then
            local g = { w.at.x, w.at.y, w.size.x, w.size.y }
            -- (nearly) as big as the screen already – it started maximized, or
            -- the app chose a huge size itself: restore to a centered 70% window
            if g[3] >= ww - 4 or g[4] + bar >= hh - 4 then
                g = { x + math.floor(ww * 0.15), y + math.floor(hh * 0.12),
                      math.floor(ww * 0.7), math.floor(hh * 0.7) }
            end
            saved_geometry[a] = g
        end
        client_maximized(a, true)
        set_geometry(a, x, y + bar, ww, hh - bar)
        -- the app may answer with its own size a moment later: check again
        later(250, function() KeepMaximized(a) end)
        later(800, function() KeepMaximized(a) end)
    elseif action == "restore" and (saved_geometry[a] or w.fullscreen_client == 1) then
        -- no normal size known: a centered window, like Windows
        local g = saved_geometry[a] or { x + math.floor(ww * 0.15), y + math.floor(hh * 0.12),
                                         math.floor(ww * 0.7), math.floor(hh * 0.7) }
        g = centered(w, g)                  -- in the middle of this screen
        saved_geometry[a] = nil
        client_maximized(a, false)
        set_geometry(a, g[1], g[2], g[3], g[4])
        if EnsureOnScreenSoon then EnsureOnScreenSoon(a, g) end
    end
end

-- A maximized window fills exactly its screen's free area – not a pixel
-- more: one pixel too wide or shifted showed as a thin strip of it on the
-- screen next to it (Brave: its one-pixel resize nudge, or resizing it back
-- moving it a pixel to the right).
function KeepMaximized(a)
    local w = hl.get_window("address:" .. a)
    if not w or not w.mapped or not w.workspace or w.workspace.name:sub(1, 8) == "special:" then return end
    if not (saved_geometry[a] or w.fullscreen_client == 1) or w.fullscreen ~= 0 then return end
    local x, y, ww, hh = work_area(w.monitor or hl.get_active_monitor())
    local bar = has_bar(w) and BAR_HEIGHT or 0
    if w.at.x ~= x or w.at.y ~= y + bar or w.size.x ~= ww or w.size.y ~= hh - bar then
        set_geometry(a, x, y + bar, ww, hh - bar)
    end
end

-- Forget the "maximized" state without resizing (used before snapping)
function WinForget()
    local w = hl.get_active_window()
    if w and saved_geometry[w.address] then
        saved_geometry[w.address] = nil
        client_maximized(w.address, false)
    end
end

-- windows in full screen: address -> "max" / "normal" (what they were before)
app_fullscreen = app_fullscreen or {}
-- just left full screen: the app also sends a maximize / restore then, which
-- the title bar plugin passes on like a click on □ (WinMaxWindow) – it
-- toggled the window the wrong way. Ignored for a moment.
fs_just_left = fs_just_left or {}

-- Tabbing away from a full-screen window (Alt+Tab, the taskbar, a click on
-- another window): it leaves full screen and stays maximized behind –
-- not a full-screen window left lying under the others.
hl.on("window.active", function()
    local cur = hl.get_active_window()
    for a in pairs(app_fullscreen) do
        if not cur or cur.address ~= a then
            local w = hl.get_window("address:" .. a)
            if w and w.fullscreen == 2 then
                app_fullscreen[a] = "tab"
                hl.dispatch(hl.dsp.window.fullscreen_state({ internal = 0, client = 0, window = "address:" .. a }))
            elseif not w then
                app_fullscreen[a] = nil
            end
        end
    end
end)

-- Apps asking to be maximized (their own maximize button, double click on
-- their title bar) get the Windows-style maximize instead.
hl.on("window.fullscreen", function(ev)
    local w = event_window(ev) or hl.get_active_window()
    if not w or fs_hook_paused[w.address] then return end
    if fixed_size(w) then
        if w.fullscreen ~= 0 then           -- e.g. Win + Shift + Up: straight back
            local a = w.address
            fs_hook_paused[a] = true
            hl.dispatch(hl.dsp.window.fullscreen_state({ internal = 0, client = 0, window = "address:" .. a }))
            later(300, function() fs_hook_paused[a] = nil end)
        end
        return
    end
    -- Full screen (the app asked for it – a YouTube video, F11 in a game –
    -- or Win + Shift + Up): remember whether the window was maximized, and
    -- maximize it again when it leaves full screen.
    local a = w.address
    if w.fullscreen == 2 then
        if app_fullscreen[a] == nil then app_fullscreen[a] = saved_geometry[a] and "max" or "normal" end
        return
    end
    if app_fullscreen[a] and w.fullscreen == 0 then
        local was = app_fullscreen[a]
        app_fullscreen[a] = nil
        fs_just_left[a] = true
        later(800, function() fs_just_left[a] = nil end)
        if was == "max" and saved_geometry[a] then
            client_maximized(a, true, true)     -- maximized again (size as before)
        elseif was == "tab" then
            -- tabbed away: maximized behind the others (a moment later – done
            -- right inside this event, the full-screen change still running
            -- overwrote it)
            later(80, function()
                local cur = hl.get_window("address:" .. a)
                if not cur or cur.fullscreen ~= 0 then return end
                if saved_geometry[a] then client_maximized(a, true, true) else WinMax("max", cur) end
            end)
        end
        return
    end
    if w.fullscreen == 1 then
        -- Undo Hyprland's own maximize, then maximize Windows-style once it
        -- is gone. Events caused by these two steps arrive late; ignore them,
        -- or the late "not maximized" event restores the window right away.
        local a = w.address
        fs_hook_paused[a] = true
        hl.dispatch(hl.dsp.window.fullscreen({ mode = "maximized", action = "unset", window = "address:" .. a }))
        later(40, function()
            local cur = hl.get_window("address:" .. a)
            if cur then WinMax("toggle", cur) end
            later(400, function() fs_hook_paused[a] = nil end)
        end)
    elseif w.fullscreen == 0 and w.fullscreen_client == 0 and saved_geometry[w.address] then
        WinMax("restore", w)          -- the app's own "restore" button
    end
end)

hl.on("window.close", function(ev)
    local w = event_window(ev)
    if w then saved_geometry[w.address] = nil end
end)

-- Apps with their own title bar (Brave, Discord, …) ask to be minimized;
-- Hyprland ignores that by default, so hide them like the "–" button does.
hl.on("window.minimize", function(ev)
    local w = event_window(ev) or hl.get_active_window()
    if w and w.workspace and w.workspace.name ~= "special:minimized" then
        MinimizeWindow(w.address)
    end
end)

-- New windows that don't fit the work area (e.g. Brave restoring an old size)
-- open maximized, like on Windows; windows sticking out at the top are moved
-- down so their title bar can always be grabbed.
hl.on("window.open", function(ev)
    local w = event_window(ev) or hl.get_active_window()
    if not w or not w.floating then return end
    local x, y, ww, hh = work_area(w.monitor or hl.get_active_monitor())
    local bar = has_bar(w) and BAR_HEIGHT or 0
    if w.size.x > ww - 2 or w.size.y > hh - bar - 2 then
        WinMax("max", w)
    else
        if w.at.y < y + bar then
            hl.dispatch(hl.dsp.window.move({ x = w.at.x, y = y + bar + 1, window = "address:" .. w.address }))
        end
        if EnsureOnScreenSoon then EnsureOnScreenSoon(w.address) end
    end
end)

---------------------------------------------------------------------
-- Title bars like Windows 11 (hyprbars plugin, built with
-- tools/build-hyprbars.sh). Skipped when the plugin is missing.
---------------------------------------------------------------------
local hyprbars = home .. "/.local/share/hypr-plugins/hyprbars.so"
local f = io.open(hyprbars, "r")
if f then
    f:close()
    local ok, err = pcall(hl.plugin.load, hyprbars)
    if ok and hl.plugin.hyprbars then
        hl.config({
            plugin = {
                hyprbars = {
                    bar_height                 = 32,
                    bar_color                  = "rgb(202020)",
                    ["col.text"]               = "rgb(ffffff)",
                    bar_text_font              = "Inter",
                    bar_text_size              = 10,
                    bar_text_align             = "left",
                    bar_padding                = 12,
                    bar_button_padding         = 0,
                    bar_part_of_window         = true,
                    bar_precedence_over_border = true,
                    inactive_button_color      = "rgb(202020)",
                    on_double_click            = scripts .. "/winctl.sh maximize",
                },
            },
        })
        -- Windows 11 caption buttons, right to left: close, maximize, minimize.
        -- Patched hyprbars draws them as 46 px wide full-height rectangles with
        -- a hover background (close turns red), like on Windows.
        local function btn(icon, action)
            hl.plugin.hyprbars.add_button({
                bg_color = "rgba(00000000)",
                fg_color = "rgb(ffffff)",
                size     = 46,
                icon     = icon,
                action   = scripts .. "/winctl.sh " .. action,
            })
        end
        btn("", "close")
        btn("", "maximize")
        btn("", "minimize")

        -- apps that draw their own title bar (with X/□/–) don't get a second one
        local function re(str) return (str:gsub("[%.%-%+%*%?%^%$%(%)%[%]%{%}|\\]", "\\%0")) end
        local alts = {}
        for _, c in ipairs(OWN_TITLEBAR) do table.insert(alts, re(c)) end
        for _, p in ipairs(OWN_TITLEBAR_PREFIX) do table.insert(alts, re(p) .. ".*") end
        hl.window_rule({
            name  = "own-titlebar",
            match = { class = "^(" .. table.concat(alts, "|") .. ")$" },
            ["hyprbars:no_bar"] = true,
        })

        -- fixed-size apps (FIXED_SIZE, e.g. Mullvad): no maximize button –
        -- they can't be maximized. Needs the patched plugin (tools/build-hyprbars.sh),
        -- which Hyprland only loads at login: while an older build is still
        -- loaded (the file is newer than this Hyprland), the rule isn't known
        -- and would show as a config error – then it waits for the next login.
        local fixed = {}
        for c in pairs(FIXED_SIZE) do table.insert(fixed, re(c)) end
        local function mtime(path)
            local h = io.popen("stat -c %Y '" .. path .. "' 2>/dev/null")
            local t = h and tonumber(h:read("a"))
            if h then h:close() end
            return t
        end
        -- when this Hyprland started: its start (in 1/100 s since boot,
        -- field 22 of /proc/self/stat) plus the boot time from /proc/stat
        local function started_at()
            local f = io.open("/proc/self/stat", "r")
            local st = f and f:read("a")
            if f then f:close() end
            local rest = st and st:match("%) (.*)$")       -- after "(Hyprland)"
            if not rest then return nil end
            local fields = {}
            for x in rest:gmatch("%S+") do fields[#fields + 1] = x end
            local ticks = tonumber(fields[20])                -- field 22 overall
            f = io.open("/proc/stat", "r")
            local btime = f and tonumber(f:read("a"):match("\nbtime (%d+)"))
            if f then f:close() end
            return ticks and btime and btime + ticks / 100
        end
        local started, built = started_at(), mtime(hyprbars)
        local plugin_current = not (started and built and built > started)
        if plugin_current then pcall(hl.window_rule, {
            name  = "fixed-size-no-maximize",
            match = { class = "(?i)^(" .. table.concat(fixed, "|") .. ")$" },
            ["hyprbars:no_maximize"] = true,
        }) end
    elseif not ok then
        hl.notification.create({ text = "hyprbars: " .. tostring(err), timeout = 8000, icon = "error" })
    end
end

---------------------------------------------------------------------
-- Keyboard shortcuts like on Windows
---------------------------------------------------------------------
local win = "SUPER"

hl.bind(win .. " + E", hl.dsp.exec_cmd(fileManager))                         -- File Explorer
hl.bind(win .. " + R", hl.dsp.exec_cmd(scripts .. "/run.sh"))                -- Run
hl.bind(win .. " + I", hl.dsp.exec_cmd(scripts .. "/settings.sh"))           -- Settings
hl.bind(win .. " + A", hl.dsp.exec_cmd(winshell .. " qs"))                   -- quick settings
hl.bind(win .. " + N", hl.dsp.exec_cmd(winshell .. " calendar"))   -- notifications + calendar, like Windows
hl.bind(win .. " + X", hl.dsp.exec_cmd(scripts .. "/powermenu.sh --menu"))   -- power menu
hl.bind(win .. " + L", hl.dsp.exec_cmd(scripts .. "/lock.sh"))               -- lock (Windows-style)
hl.bind(win .. " + V", hl.dsp.exec_cmd(winshell .. " clipboard"))           -- clipboard history (like Windows 11)
hl.bind(win .. " + W", hl.dsp.exec_cmd(scripts .. "/wallpaper.sh pick"))     -- pick background
hl.bind(win .. " + T", hl.dsp.exec_cmd(terminal))                            -- terminal
hl.bind(win .. " + B", hl.dsp.exec_cmd(browser))                             -- browser
hl.bind("CTRL + SHIFT + Escape", hl.dsp.exec_cmd("gnome-system-monitor"))    -- Task Manager


-- Talk to winshell directly through its pipe, so commands arrive in the
-- order the keys were pressed (separate processes could overtake each other).
-- Falls back to winshell-ctl (which starts the daemon) when it isn't running.
local runtime = os.getenv("XDG_RUNTIME_DIR") or "/tmp"
local function winshell_send(cmd)
    local ready = io.open(runtime .. "/winshell.ready", "r")
    local pid = ready and ready:read("*l")
    if ready then ready:close() end
    local alive = pid and io.open("/proc/" .. pid .. "/comm", "r")
    if alive then
        alive:close()
        local fifo = io.open(runtime .. "/winshell.fifo", "a")
        if fifo then
            fifo:write(cmd .. "\n")
            fifo:close()
            return
        end
    end
    hl.exec_cmd(winshell .. " " .. cmd)
end

-- before a window changes size or place (or goes away): winshell stops
-- taking pictures of it for a moment – grim crashes on such windows
function ThumbsPause() winshell_send("thumbs pause") end

---------------------------------------------------------------------
-- Dragging windows like on Windows 11 (Snap)
--  * windows may be pushed partly off the screen and under the taskbar;
--    when dropped, only the title bar is kept reachable
--  * touching the top edge with the cursor shows a preview of the maximized
--    window, the left/right edge (after a moment, like Windows) one of the
--    half, the corners one of a quarter – leave the edge and it goes away;
--    letting go while it shows snaps the window there
--  * a snapped (or maximized) window dragged away gets its old size back
-- The hyprbars plugin reports when any window starts and stops moving
-- (winctl.sh drag-start / drag-end); the cursor is watched in between.
---------------------------------------------------------------------
local SNAP_POLL_MS    = 16
local SNAP_SIDE_DWELL = 140     -- ms at the left/right edge before it offers the half
local SNAP_CORNER     = 80      -- px from the top/bottom that count as a corner
local snap_saved = {}           -- address -> geometry before it was snapped
local snapped_to = {}           -- address -> the zone it is snapped to
local drag = nil                -- the move in progress

local function mon_box(mon)
    return mon.x, mon.y, math.floor(mon.width / mon.scale), math.floor(mon.height / mon.scale)
end

-- which snap area the cursor points at; true = only after a moment there
local function snap_zone(c, mon)
    local mx, my, mw, mh = mon_box(mon)
    local _, wy, _, wh = work_area(mon)
    local left, right = c.x <= mx + 1, c.x >= mx + mw - 2
    if left or right then
        local side = left and "left" or "right"
        if c.y <= wy + SNAP_CORNER then return "top" .. side, false end
        if c.y >= wy + wh - SNAP_CORNER then return "bottom" .. side, false end
        return side, true
    end
    if c.y <= my + 1 then return "max", false end
    return nil, false
end

-- the screen area of a snap zone (title bar included)
local function zone_rect(zone, mon)
    local x, y, ww, hh = work_area(mon)
    local hw, hh2 = math.floor(ww / 2), math.floor(hh / 2)
    local r = {
        max         = { x, y, ww, hh },
        left        = { x, y, hw, hh },
        right       = { x + hw, y, ww - hw, hh },
        topleft     = { x, y, hw, hh2 },
        bottomleft  = { x, y + hh2, hw, hh - hh2 },
        topright    = { x + hw, y, ww - hw, hh2 },
        bottomright = { x + hw, y + hh2, ww - hw, hh - hh2 },
    }
    return r[zone]
end

function WinSnap(zone, w, on_mon)
    w = w or hl.get_active_window()
    if not w or not w.mapped or fixed_size(w) then return end
    local a = w.address
    local mon = on_mon or w.monitor or hl.get_active_monitor()
    if zone == "max" then
        if snap_saved[a] and not saved_geometry[a] then
            saved_geometry[a] = snap_saved[a]          -- un-maximize: the size before snapping
        end
        snap_saved[a], snapped_to[a] = nil, nil
        WinMax("max", w)
        return
    end
    local r = zone_rect(zone, mon)
    if not r then return end
    if saved_geometry[a] then                         -- maximized: remember its normal size
        snap_saved[a] = saved_geometry[a]
        saved_geometry[a] = nil
        client_maximized(a, false)
    elseif not snap_saved[a] then
        snap_saved[a] = { w.at.x, w.at.y, w.size.x, w.size.y }
    end
    local bar = has_bar(w) and BAR_HEIGHT or 0
    set_geometry(a, r[1], r[2] + bar, r[3], r[4] - bar)
    snapped_to[a] = zone
    -- an app that can't get that small (Discord has a minimum width) stays
    -- bigger: then it's pushed back inside its half's screen edge, like
    -- Windows – it stuck out onto the screen next to it
    later(200, function()
        local cur = hl.get_window("address:" .. a)
        if not cur or snapped_to[a] ~= zone then return end
        local mx, my, mw, mh = work_area(mon)
        local nx, ny = cur.at.x, cur.at.y
        if nx + cur.size.x > mx + mw then nx = mx + mw - cur.size.x end
        if nx < mx then nx = mx end
        if ny + cur.size.y > my + mh then ny = math.max(my + bar, my + mh - cur.size.y) end
        if nx ~= cur.at.x or ny ~= cur.at.y then
            hl.dispatch(hl.dsp.window.move({ x = nx, y = ny, window = "address:" .. a }))
        end
    end)
end

-- An app's own maximize / restore button (Brave's □; via the hyprbars
-- plugin, which can't tell which of the two it is): toggle by the real state.
-- Once per half second – some apps ask twice.
local max_toggled = {}
function WinMaxWindow(addr)
    if drag and drag.moved then return end
    local w = hl.get_window("address:" .. addr)
    if not w or max_toggled[w.address] then return end
    if app_fullscreen[w.address] or fs_just_left[w.address] then return end    -- full screen in / out
    max_toggled[w.address] = true
    later(500, function() max_toggled[w.address] = nil end)
    if saved_geometry[w.address] or w.fullscreen_client == 1 then
        WinMax("restore", w)
    else
        WinSnap("max", w)
    end
end

-- a snapped window back to the size and place it had before (Win + Down)
function WinUnsnap(w)
    w = w or hl.get_active_window()
    if not w then return false end
    local a = w.address
    if not snapped_to[a] then return false end
    local g = snap_saved[a]
    snap_saved[a], snapped_to[a] = nil, nil
    if not g then
        local x, y, ww, hh = work_area(w.monitor or hl.get_active_monitor())
        g = { x + math.floor(ww * 0.2), y + math.floor(hh * 0.15), math.floor(ww * 0.6), math.floor(hh * 0.7) }
    end
    g = centered(w, g)                      -- in the middle of the screen it is on
    set_geometry(a, g[1], g[2], g[3], g[4])
    if EnsureOnScreenSoon then EnsureOnScreenSoon(a) end
    return true
end

-- Win + Left/Right like Windows: towards the other side a snapped window
-- first comes loose, otherwise it snaps to that half
-- the screen right next to mon on that side (sharing an edge), if any
local function neighbour_screen(mon, side)
    if not mon then return nil end
    local mx, my, mw, mh = mon_box(mon)
    for _, m in ipairs(hl.get_monitors() or {}) do
        if m.name ~= mon.name and m.enabled ~= false and not m.is_mirror then
            local x, y, w2, h2 = mon_box(m)
            local beside = (side == "right" and math.abs(x - (mx + mw)) <= 2)
                        or (side == "left" and math.abs((x + w2) - mx) <= 2)
            if beside and y < my + mh and my < y + h2 then return m end
        end
    end
end

-- Win + Left / Right like Windows: snap to that half; snapped to the other
-- half: back to its normal size; already snapped to that side and another
-- screen there: over to it, snapped to its near half (same desktop)
function WinSnapKey(side)
    local w = hl.get_active_window()
    if not w then return end
    local z = snapped_to[w.address]
    local other = side == "left" and "right" or "left"
    if z and z:find(other) then
        WinUnsnap(w)
    elseif z and z:find(side) then
        local n = neighbour_screen(w.monitor, side)
        if not n or fixed_size(w) then return end
        local a, sel = w.address, "address:" .. w.address
        local ws = n.active_workspace and n.active_workspace.id
        if ws then         -- the other screen's workspace of this desktop
            hl.dispatch(hl.dsp.window.move({ workspace = tostring(ws), follow = false, window = sel }))
        end
        WinSnap(other, hl.get_window(sel), n)
        hl.dispatch(hl.dsp.focus({ window = sel }))
    else
        WinSnap(side, w)
    end
end

-- Screens of different sizes: the mouse always gets over to the other one.
-- Where a screen's edge has no screen next to it (the bigger one sticks out
-- past the smaller one), the pointer used to stop at the edge; pushed there,
-- it now jumps to the neighbouring screen at the nearest height. Checked
-- every 25 ms – only the pointer position, the real work happens only at an
-- edge; and only when it just got there (not while it rests on the edge).
local EDGE_POLL_MS = 25
local edge_last = nil
local function edge_cross()
    local c = hl.get_cursor_pos()
    if not c then return end
    local last = edge_last
    edge_last = { x = c.x, y = c.y }
    if not last then return end
    local mon = hl.get_monitor_at_cursor()
    if not mon then return end
    local mx, my, mw, mh = mon_box(mon)
    local side
    if c.x <= mx + 0.5 and last.x > c.x + 0.25 then side = "left"
    elseif c.x >= mx + mw - 1.5 and last.x < c.x - 0.25 then side = "right"
    elseif c.y <= my + 0.5 and last.y > c.y + 0.25 then side = "top"
    elseif c.y >= my + mh - 1.5 and last.y < c.y - 0.25 then side = "bottom"
    else return end
    for _, m in ipairs(hl.get_monitors() or {}) do
        if m.name ~= mon.name and m.enabled ~= false and not m.is_mirror then
            local x, y, w, h = mon_box(m)
            local nx, ny
            if side == "left" and math.abs((x + w) - mx) <= 2 and (c.y < y or c.y >= y + h) then
                nx, ny = x + w - 2, math.max(y, math.min(c.y, y + h - 2))
            elseif side == "right" and math.abs(x - (mx + mw)) <= 2 and (c.y < y or c.y >= y + h) then
                nx, ny = x + 1, math.max(y, math.min(c.y, y + h - 2))
            elseif side == "top" and math.abs((y + h) - my) <= 2 and (c.x < x or c.x >= x + w) then
                nx, ny = math.max(x, math.min(c.x, x + w - 2)), y + h - 2
            elseif side == "bottom" and math.abs(y - (my + mh)) <= 2 and (c.x < x or c.x >= x + w) then
                nx, ny = math.max(x, math.min(c.x, x + w - 2)), y + 1
            end
            if nx then
                hl.dispatch(hl.dsp.cursor.move({ x = nx, y = ny }))
                edge_last = { x = nx, y = ny }
                return
            end
        end
    end
end
hl.timer(edge_cross, { timeout = EDGE_POLL_MS, type = "repeat" })

local function snap_preview(zone, mon, c)
    if not zone then
        winshell_send("snap hide")
        return
    end
    local mx, my = mon_box(mon)
    local r = zone_rect(zone, mon)
    winshell_send(string.format("snap show %d %d %d %d %d %d",
        r[1] - mx, r[2] - my, r[3], r[4], math.floor(c.x - mx), math.floor(c.y - my)))
end

-- the window really moves (not just a click on its title bar – apps like
-- Brave start a "move" on every click): a maximized or snapped one gets its
-- normal size back and stays under the cursor at the same relative spot
local DRAG_MOVE_PX = 8
local function drag_moved(c)
    drag.moved = true
    local w = hl.get_window("address:" .. drag.addr)
    if not w then return end
    local a = w.address
    local g = saved_geometry[a] or snap_saved[a]
    if not g then return end
    if saved_geometry[a] then client_maximized(a, false) end
    saved_geometry[a], snap_saved[a], snapped_to[a] = nil, nil, nil
    local rel = (drag.c0.x - w.at.x) / math.max(1, w.size.x)
    set_geometry(a, math.floor(c.x - g[3] * rel), w.at.y, g[3], g[4])
end

local function drag_poll()
    if not drag then return end
    drag.ticks = drag.ticks + 1
    if drag.ticks * SNAP_POLL_MS > 60000 then        -- never got a drag-end
        WinDragEnd()
        return
    end
    local c = hl.get_cursor_pos()
    if not c then return end
    if not drag.moved then
        if math.abs(c.x - drag.c0.x) + math.abs(c.y - drag.c0.y) < DRAG_MOVE_PX then return end
        drag_moved(c)
    end
    local mon = hl.get_monitor_at(c.x, c.y) or hl.get_active_monitor()
    local zone, wait = snap_zone(c, mon)
    if zone ~= drag.candidate then
        drag.candidate, drag.since = zone, drag.ticks
    end
    local shown = zone
    if zone and wait and (drag.ticks - drag.since) * SNAP_POLL_MS < SNAP_SIDE_DWELL then
        shown = nil
    end
    if shown ~= drag.zone then
        drag.zone = shown
        snap_preview(shown, mon, c)
    end
end

-- a window started moving: a maximized or snapped one gets its normal size
-- back and stays under the cursor at the same relative spot
function WinDragStart()
    local w = hl.get_active_window()
    if not w or fixed_size(w) then return end      -- a dialog: no snapping, no maximize
    if drag and drag.timer then drag.timer:set_enabled(false) end
    local c = hl.get_cursor_pos() or { x = 0, y = 0 }
    drag = { addr = w.address, ticks = 0, since = 0, c0 = { x = c.x, y = c.y }, moved = false }
    drag.timer = hl.timer(drag_poll, { timeout = SNAP_POLL_MS, type = "repeat" })
end

-- Every window stays reachable: its title bar never goes above the top or
-- under the taskbar, and sideways at least a third of it (200 px or more)
-- stays on screen. A window bigger than the free space is made to fit.
-- Used when a window is dropped and shortly after it comes back (restored,
-- unsnapped, opened) – apps like Brave set their own size a moment later.
local function keep_reachable(w)
    if not w or not w.floating or saved_geometry[w.address] or w.fullscreen_client == 1 then return end
    if not w.workspace or w.workspace.name:sub(1, 8) == "special:" then return end
    local x, y, ww, hh = work_area(w.monitor or hl.get_active_monitor())
    local bar = has_bar(w) and BAR_HEIGHT or 0
    local sel = "address:" .. w.address
    local sw, sh = w.size.x, w.size.y
    if sh + bar > hh or sw > ww then                -- too big for the screen: fit it
        sw, sh = math.min(sw, ww), math.min(sh, hh - bar)
        hl.dispatch(hl.dsp.window.resize({ x = sw, y = sh, window = sel }))
    end
    local fw, fh = sw, sh + bar                     -- the whole frame
    local keep_x = math.min(fw, math.max(200, math.floor(fw / 3)))
    local keep_y = math.min(fh, math.max(120, math.floor(fh / 3)))
    local top = w.at.y - bar                        -- top of the title bar
    local ntop = math.max(y, math.min(top, y + hh - keep_y))
    local nx = math.max(x - fw + keep_x, math.min(w.at.x, x + ww - keep_x))
    if nx ~= w.at.x or ntop ~= top or sw ~= w.size.x or sh ~= w.size.y then
        hl.dispatch(hl.dsp.window.move({ x = nx, y = ntop + bar, window = sel }))
    end
end
function EnsureOnScreen(addr)
    keep_reachable(addr and hl.get_window("address:" .. addr))
end
-- check now and again once the app had time to pick its own size. With
-- `want` (x, y, w, h): an app that sets its own size meanwhile (Brave puts
-- back its own remembered size on "restore") gets the intended one again.
function EnsureOnScreenSoon(addr, want)
    later(250, function()
        local w = hl.get_window("address:" .. addr)
        if want and w and w.fullscreen_client ~= 1 and not saved_geometry[addr]
           and (math.abs(w.size.x - want[3]) > 4 or math.abs(w.size.y - want[4]) > 4) then
            set_geometry(addr, want[1], want[2], want[3], want[4])
        end
        EnsureOnScreen(addr)
    end)
    later(700, function() EnsureOnScreen(addr) end)
end

function WinDragEnd()
    local d = drag
    drag = nil
    if d and d.timer then d.timer:set_enabled(false) end
    winshell_send("snap hide")
    if d and not d.moved then return end            -- just a click on the title bar
    local w = d and hl.get_window("address:" .. d.addr) or hl.get_active_window()
    if d and d.zone then
        WinSnap(d.zone, w)
    else
        keep_reachable(w)
    end
end
WinClamp = function() keep_reachable(hl.get_active_window()) end

hl.on("window.close", function(ev)
    local w = event_window(ev)
    if w then snap_saved[w.address], snapped_to[w.address] = nil, nil end
end)

-- Alt+Tab: hold Alt, press Tab to move through the windows, release Alt to
-- switch. The switcher grabs the keyboard while open and notices the Alt
-- release itself (Hyprland release binds don't fire reliably for this).
hl.bind("ALT + Tab",         function() winshell_send("switch next") end, { repeating = true })
-- letting go of Alt switches – told by Hyprland as well, the switcher can
-- miss it when it did not get the keyboard in time
hl.bind("ALT + Alt_L", function() winshell_send("switch release") end, { release = true, non_consuming = true })
hl.bind("ALT + Alt_R", function() winshell_send("switch release") end, { release = true, non_consuming = true })
hl.bind("ALT + SHIFT + Tab", function() winshell_send("switch prev") end, { repeating = true })

-- Windows key alone (on release) = start menu; Win + S = search.
-- Straight into winshell's pipe: no shell scripts to start first.
hl.bind("Super_L",     function() winshell_send("menu") end, { release = true })
hl.bind(win .. " + S", function() winshell_send("menu") end)

-- Show desktop (Win + D): hide every window of this desktop, press again to
-- bring them back exactly as they were. Win + M minimizes all,
-- Win + Shift + M restores them.
-- A desktop spans all screens: Win + D takes the windows of every screen.
local desktop_hidden = nil      -- { windows = {addresses, bottom to top}, ws = {address -> workspace id}, active = address }
local function windows_here()
    local shown = {}            -- the workspaces the screens show (this desktop)
    for _, m in ipairs(hl.get_monitors() or {}) do
        if m.active_workspace and m.active_workspace.id then shown[m.active_workspace.id] = true end
    end
    local list = {}
    for _, w in ipairs(hl.get_windows() or {}) do
        if w.mapped and w.workspace and shown[w.workspace.id] then
            table.insert(list, w)
        end
    end
    -- least recently focused first, so restoring rebuilds the stacking order
    table.sort(list, function(a, b) return (a.focus_history_id or 0) > (b.focus_history_id or 0) end)
    return list
end
-- The show-desktop state is kept in a file too: a config reload starts Lua
-- afresh, and Win + D must still bring the windows back afterwards.
local DESK_STATE = runtime .. "/hypr-show-desktop"
local function save_desktop()
    if not desktop_hidden then
        os.remove(DESK_STATE)
        return
    end
    local f = io.open(DESK_STATE, "w")
    if not f then return end
    f:write("active ", desktop_hidden.active or "-", "\n")
    for _, a in ipairs(desktop_hidden.windows) do
        local p = (desktop_hidden.pos or {})[a]
        f:write("win ", a, " ", (desktop_hidden.ws or {})[a] or "-",
                p and (" " .. p.x .. " " .. p.y) or "", "\n")
    end
    f:close()
end
local function load_desktop()
    local f = io.open(DESK_STATE, "r")
    if not f then return end
    local saved = { windows = {}, ws = {}, pos = {} }
    for line in f:lines() do
        local kind, a, ws, px, py = line:match("^(%S+) (%S+) ?(%S*) ?(%-?%d*) ?(%-?%d*)")
        if kind == "win" and tonumber(px) and tonumber(py) then
            saved.pos[a] = { x = tonumber(px), y = tonumber(py) }
        end
        if kind == "active" and a ~= "-" then
            saved.active = a
        elseif kind == "win" then
            table.insert(saved.windows, a)
            saved.ws[a] = tonumber(ws)
        end
    end
    f:close()
    if #saved.windows > 0 then desktop_hidden = saved end
end
load_desktop()

local desk_gen = 0              -- a newer Win + D cancels what an older one planned
local desk_settling = false     -- Win + D just hid the windows (some may still be on screen)
local desk_out = nil            -- windows waiting to be hidden: { gen, windows }
local desk_in = nil             -- windows waiting to come back: { gen, windows, active }

-- "address|x|y|w|h|title bar|class" for winshell's animation
local function desk_tokens(list, pos)
    local t = {}
    for _, w in ipairs(list) do
        local x, y = hidden_place(w, pos)   -- a hidden one: where it comes back
        table.insert(t, string.format("%s|%d|%d|%d|%d|%d|%s", w.address, x or w.at.x, y or w.at.y,
            w.size.x, w.size.y, has_bar(w) and BAR_HEIGHT or 0, (w.class or "?"):gsub("[|%s]", "_")))
    end
    return table.concat(t, " ")
end

-- Like Windows: every window shrinks into its taskbar button. winshell draws
-- that (pictures of the windows on a layer above everything) and calls
-- DesktopOutNow() as soon as they cover the windows – then they are hidden.
local function hide_all()
    desk_gen = desk_gen + 1
    local gen = desk_gen
    local active = hl.get_active_window()
    local saved = { windows = {}, ws = {}, pos = {}, active = active and active.address }
    local list = windows_here()             -- back to front
    for _, w in ipairs(list) do
        table.insert(saved.windows, w.address)
        saved.ws[w.address] = w.workspace.id    -- comes back on its own screen …
        saved.pos[w.address] = { x = w.at.x, y = w.at.y }    -- … right where it was
    end
    if #list > 0 then
        desk_out = { gen = gen, windows = saved.windows }
        winshell_send("desk out DesktopOutNow " .. gen .. " " .. desk_tokens(list))
        later(400, function() DesktopOutNow(gen) end)    -- winshell didn't answer
    end
    desktop_hidden = #list > 0 and saved or nil
    save_desktop()
    desk_settling = true
    later(MAX_HIDE_DELAY + 600, function() if desk_gen == gen then desk_settling = false end end)
end
function DesktopOutNow(gen)
    local d = desk_out
    if not d or d.gen ~= gen or desk_gen ~= gen then return end
    desk_out = nil
    for _, a in ipairs(d.windows) do
        set_alpha(a, 0)                     -- gone at once (the pictures fly on)
        HideWindow(a, true)
    end
end

-- Minimize like Windows: the window shrinks into its taskbar button (title
-- bar –, Win + Down, an app's own –, a click on its active taskbar button).
local mini_gen, mini_pending = 0, {}
function MinimizeWindow(addr)
    local w = addr and hl.get_window("address:" .. addr) or hl.get_active_window()
    if not w or not w.workspace or w.workspace.name == "special:minimized" then return end
    mini_gen = mini_gen + 1
    local gen = mini_gen
    mini_pending[gen] = w.address
    winshell_send("desk out MinimizeNow " .. gen .. " " .. desk_tokens({ w }))
    later(400, function() MinimizeNow(gen) end)        -- winshell didn't answer
end
function MinimizeNow(gen)
    local a = mini_pending[gen]
    if not a then return end
    mini_pending[gen] = nil
    set_alpha(a, 0)                         -- gone at once (the picture flies on)
    HideWindow(a)
end

-- Bring a window to the front, restoring it first if it is minimized – it
-- grows out of its taskbar button (taskbar, Alt+Tab, Win + 1/2/3).
local show_pending = {}                     -- gen -> { address, workspace }
local showing = {}                          -- address -> gen (asked again meanwhile)
function ShowWindow(addr)
    local w = hl.get_window("address:" .. addr)
    if not w then return end
    if showing[addr] then return end
    if w.workspace and w.workspace.name == "special:minimized" then
        local ws = hl.get_active_workspace()
        if ws and not ws.special then
            mini_gen = mini_gen + 1
            local gen = mini_gen
            show_pending[gen] = { addr = addr, ws = ws.id }
            showing[addr] = gen
            winshell_send("desk in ShowNow " .. gen .. " " .. desk_tokens({ w }))
            later(700, function() ShowNow(gen) end)    -- winshell didn't answer
            return
        end
    end
    hl.dispatch(hl.dsp.focus({ window = "address:" .. addr }))
end
function ShowNow(gen)
    local p = show_pending[gen]
    if not p then return end
    show_pending[gen] = nil
    showing[p.addr] = nil
    local w = hl.get_window("address:" .. p.addr)
    if not w then return end
    if w.workspace and w.workspace.name == "special:minimized" then
        RestoreWindow(w, p.ws)
        EnsureOnScreenSoon(p.addr)
    end
    hl.dispatch(hl.dsp.window.alter_zorder({ mode = "top", window = "address:" .. p.addr }))
    hl.dispatch(hl.dsp.focus({ window = "address:" .. p.addr }))
end

-- A window that is still on screen (being hidden right now): stop that.
-- It stays invisible under winshell's picture of it until the pictures have
-- flown back (DesktopInNow) – showing it at once made it pop in while the
-- others were still on their way.
local function cancel_hide(a)
    local w = hl.get_window("address:" .. a)
    if not w or not w.workspace or w.workspace.name == "special:minimized" then return false end
    set_alpha(a, 0)
    if hidden_maximized[a] then     -- told "not maximized" already, its hide still pending
        hide_cancel[a] = true
        hidden_maximized[a] = nil
        fs_hook_paused[a] = true
        client_maximized(a, true, true)
        later(300, function() fs_hook_paused[a] = nil end)
    end
    return true
end

-- ... and they grow back out of their taskbar buttons; DesktopInNow() puts
-- the real windows right under the pictures at the end. Pressed while they
-- are still flying out, winshell turns the pictures around where they are.
local function restore_hidden()
    local saved = desktop_hidden
    desktop_hidden = nil
    desk_settling = false
    save_desktop()
    desk_gen = desk_gen + 1                 -- whatever is still on its way out stays
    local gen = desk_gen
    desk_out = nil
    if not saved then return end
    local todo = {}                         -- back ones first, as they were stacked
    for _, a in ipairs(saved.windows) do
        cancel_hide(a)
        local w = hl.get_window("address:" .. a)
        if w and w.workspace then table.insert(todo, w) end
    end
    desk_in = { gen = gen, windows = todo, active = saved.active, saved = saved }
    if #todo == 0 then
        DesktopInNow(gen)
        return
    end
    winshell_send("desk in DesktopInNow " .. gen .. " " .. desk_tokens(todo, saved.pos))
    later(700, function() DesktopInNow(gen) end)        -- winshell didn't answer
end
function DesktopInNow(gen)
    local d = desk_in
    if not d or d.gen ~= gen then return end
    desk_in = nil
    local ws = hl.get_active_workspace()
    -- every window back on its own screen, on the desktop you're on now
    -- (its screen is off now: the one you're on)
    local shown = {}                        -- slot -> the workspace that screen shows
    for _, m in ipairs(hl.get_monitors() or {}) do
        local id = m.active_workspace and m.active_workspace.id
        if id and id > 0 then shown[id // DESK_STRIDE] = id end
    end
    for _, w in ipairs(d.windows) do        -- back ones first: the stacking order comes back
        local cur = hl.get_window("address:" .. w.address)
        if cur and cur.workspace then
            if cur.workspace.name == "special:minimized" then
                local was = d.saved and d.saved.ws and d.saved.ws[w.address]
                local back = was and was > 0 and shown[was // DESK_STRIDE]
                -- its own screen: where it was; another one: where the hidden one has it
                RestoreWindow(cur, back or ws.id, back and d.saved.pos and d.saved.pos[w.address] or nil)
            else
                set_alpha(w.address, 1)     -- never left (its hide was cancelled)
            end
            hl.dispatch(hl.dsp.window.alter_zorder({ mode = "top", window = "address:" .. w.address }))
        end
    end
    if d.active and hl.get_window("address:" .. d.active) then
        hl.dispatch(hl.dsp.focus({ window = "address:" .. d.active }))
    end
end

-- Win + D while the windows are still flying back: turn around again – they
-- are hidden still (or again), the pictures fly back down from where they are.
local function turn_back_down()
    local d = desk_in
    if not d then return false end
    desk_in = nil
    desk_gen = desk_gen + 1
    local gen = desk_gen
    desktop_hidden = d.saved
    save_desktop()
    desk_settling = true
    later(MAX_HIDE_DELAY + 600, function() if desk_gen == gen then desk_settling = false end end)
    desk_out = nil                          -- nothing left to do once the pictures cover them
    winshell_send("desk out DesktopOutNow " .. gen .. " " .. desk_tokens(d.windows))
    for _, w in ipairs(d.windows) do        -- the ones whose hide had been cancelled
        local cur = hl.get_window("address:" .. w.address)
        if cur and cur.workspace and cur.workspace.name ~= "special:minimized" then
            set_alpha(w.address, 0)
            HideWindow(w.address, true)
        end
    end
    return true
end

-- the desktop is shown: everything Win + D hid is hidden or on its way
-- Only while Win + D is still hiding may its windows be on screen; once it
-- is done, any window on screen means the desktop is no longer shown (a
-- window came back through the taskbar, Alt+Tab …). Before, such a stale
-- list made the 3-finger swipe down do nothing until Win + D was pressed.
local function desktop_shown()
    if not desktop_hidden then return false end
    local mine = {}
    for _, a in ipairs(desktop_hidden.windows) do mine[a] = true end
    for _, w in ipairs(windows_here()) do
        if not mine[w.address] or not desk_settling then return false end
    end
    return true
end
function ShowDesktop()
    if turn_back_down() then return end
    if desktop_shown() then
        restore_hidden()
    else
        hide_all()
    end
end
hl.bind(win .. " + D",         ShowDesktop)

-- Touchpad, 3 fingers like Windows: down shows the desktop (everything is
-- minimized), up brings back exactly those windows.
-- Reacts while the fingers move (no need to lift them): once they went
-- DESK_SWIPE far down / up it happens; a short flick counts when lifted.
local DESK_SWIPE, DESK_FLICK = 45, 18
local desk_acc, desk_done = 0, false
local function desk_swipe(dir)
    desk_done = true
    if dir > 0 then
        if turn_back_down() then return end
        if not desktop_shown() and #windows_here() > 0 then hide_all() end
    else
        restore_hidden()
    end
end
hl.gesture({ fingers = 3, direction = "vertical", action = {
    start = function() desk_acc, desk_done = 0, false end,
    update = function(e)
        if desk_done then return end
        desk_acc = desk_acc + ((e.delta and e.delta.y) or 0)
        if math.abs(desk_acc) >= DESK_SWIPE then desk_swipe(desk_acc) end
    end,
    finish = function(e)
        if not desk_done and not (e and e.cancelled) and math.abs(desk_acc) >= DESK_FLICK then
            desk_swipe(desk_acc)
        end
    end,
} })

-- 3 fingers left/right: the Alt+Tab switcher follows the fingers – every
-- SWIPE_STEP of movement selects the next/previous window (up/down: the
-- row above/below), it stops at the edges; lifting the fingers switches to
-- it (swipe back and forth to flip between apps).
-- The first step comes after a short way (it reacts right away), every
-- further one needs a longer way, and one movement moves at most one window
-- (a fast swipe must not race through several).
local SWIPE_FIRST, SWIPE_STEP, SWIPE_STEP_Y = 18, 42, 38
local swipe_acc, swipe_acc_y, swipe_steps = 0, 0, 0
local function swipe_axis(acc, step, plus, minus)
    local need = swipe_steps == 0 and SWIPE_FIRST or step
    if acc >= need then
        swipe_steps = swipe_steps + 1
        winshell_send("switch " .. plus)
        return 0
    elseif acc <= -need then
        swipe_steps = swipe_steps + 1
        winshell_send("switch " .. minus)
        return 0
    end
    return acc
end
hl.gesture({ fingers = 3, direction = "horizontal", action = {
    start = function()
        swipe_acc, swipe_acc_y, swipe_steps = 0, 0, 0
        winshell_send("switch swipe-begin")
    end,
    update = function(e)
        local d = e.delta or {}
        swipe_acc = swipe_axis(swipe_acc + (d.x or 0), SWIPE_STEP, "swipe-next", "swipe-prev")
        swipe_acc_y = swipe_axis(swipe_acc_y + (d.y or 0), SWIPE_STEP_Y, "swipe-down", "swipe-up")
    end,
    finish = function(e)
        winshell_send((e and e.cancelled) and "switch swipe-cancel" or "switch swipe-end")
    end,
} })
-- Alt + F4 closes the active window; on the desktop (no window focused)
-- it asks "shut down?" like Windows – Alt + F4 again closes that question.
hl.bind("ALT + F4", function()
    local w = hl.get_active_window()
    local ws = hl.get_active_workspace()
    -- a minimized / hidden window can still be "active": that's the desktop too
    if w and w.mapped and w.workspace and ws and w.workspace.id == ws.id then
        hl.dispatch(hl.dsp.window.close())
    else
        winshell_send("shutdown-dialog")
    end
end)
hl.bind(win .. " + M",         function() hide_all() end)
hl.bind(win .. " + SHIFT + M", restore_hidden)

-- Win + Home: minimize everything except the active window
hl.bind(win .. " + Home", function()
    local active = hl.get_active_window()
    for _, w in ipairs(windows_here()) do
        if not active or w.address ~= active.address then
            HideWindow(w.address, true)
        end
    end
end)

-- Win + 1/2/3: open or switch to the pinned taskbar apps
hl.bind(win .. " + 1", hl.dsp.exec_cmd(scripts .. "/pinned.sh open '^org.kde.dolphin$' dolphin"))
hl.bind(win .. " + 2", hl.dsp.exec_cmd(scripts .. "/pinned.sh open '^(brave-browser)$' brave-browser"))
hl.bind(win .. " + 3", hl.dsp.exec_cmd(scripts .. "/pinned.sh open '^io\\.github\\.noe1\\.winterm$' winterm"))

-- Win + Tab: task view like Windows – the windows of this desktop and all
-- desktops at the bottom (winshell's TaskView); Win + Tab again closes it
hl.bind(win .. " + Tab", function() winshell_send("taskview") end)
-- Win + P: project like Windows – PC screen only, duplicate, extend, second
-- screen only (Win + P again moves the choice); arranging is in Display settings
hl.bind(win .. " + P", function() winshell_send("project") end)
-- Ctrl + Win + Shift + B, like Windows' graphics reset: a screen hangs (still
-- picture, black, no taskbar)? This reloads the config, switches the screens
-- off and on and restarts winshell. Runs on its own, also while locked or
-- when winshell hangs. It also repairs Wi-Fi that didn't wake up from sleep
-- ("Looking for networks…" for ever) and a lost Bluetooth adapter. With Alt
-- held too it works the same (Hyprland needs the exact keys).
hl.bind("CTRL + " .. win .. " + SHIFT + B", hl.dsp.exec_cmd(scripts .. "/reset-display.sh"), { locked = true })
hl.bind("CTRL + " .. win .. " + SHIFT + ALT + B", hl.dsp.exec_cmd(scripts .. "/reset-display.sh"), { locked = true })

-- Win + Print: save a screenshot of the whole screen
hl.bind(win .. " + Print", hl.dsp.exec_cmd(scripts .. "/screenshot.sh full"))

-- Snap: Win + arrow keys
hl.bind(win .. " + Left",  function() WinSnapKey("left") end)
hl.bind(win .. " + Right", function() WinSnapKey("right") end)
hl.bind(win .. " + Up",    function() WinSnap("max") end)
-- like Windows: maximized -> normal size, snapped -> loose again (its
-- size and place from before), otherwise minimize
hl.bind(win .. " + Down",  function()
    local w = hl.get_active_window()
    if not w then return end
    if saved_geometry[w.address] then
        WinMax("restore", w)
    elseif not WinUnsnap(w) then
        MinimizeWindow(w.address)
    end
end)
hl.bind(win .. " + SHIFT + Up", hl.dsp.window.fullscreen({ mode = "fullscreen", action = "toggle" }))

-- Move / resize windows with Win + mouse (in addition to the title bar)
hl.bind(win .. " + mouse:272", hl.dsp.window.drag(),   { mouse = true })
hl.bind(win .. " + mouse:273", hl.dsp.window.resize(), { mouse = true })

---------------------------------------------------------------------
-- Virtual desktops like Windows: a desktop spans all screens. Switching
-- the desktop switches every screen at once; a desktop stays until you
-- close it, even when it's empty.
-- Hyprland has workspaces per screen, so desktop d is one workspace per
-- screen: slot * DESK_STRIDE + d (laptop screen: 1, 2, 3 …, the next
-- screen: 101, 102 …), each kept on its screen by a persistent rule.
-- A screen that goes off (Win + P, unplugged): its windows go to the main
-- screen, same desktop – and back when the screen comes back.
-- Screens changing never change the desktop you're on.
-- winshell's task view (Win + Tab) uses DesktopAdd / DesktopClose / DesktopShow.
---------------------------------------------------------------------
local desktops_file = runtime .. "/hypr-desktops"
local slots_file = runtime .. "/hypr-desktop-slots"
MAX_DESKTOPS = 6                    -- no more desktops than this (winshell knows it too)
DESK_STRIDE = 100                   -- winshell knows it too
local desktop_set = {}              -- desktop number -> true
local slot_of = {}                  -- screen (connector) -> slot
local desk_rules = {}               -- workspace id -> { mon, rule }
local exiled = {}                   -- window address -> { mon = its screen (off), ws = where it went }
local desk_quiet = false            -- we switch the screens ourselves
local desk_hold = 0                 -- screens are changing: Hyprland shuffles workspaces
local desk_now = nil                -- the desktop you are on

local function desk_of_ws(id)
    if not id or id <= 0 then return nil end
    local d = id % DESK_STRIDE
    return d > 0 and d or nil
end

function DesktopIds()
    local ids = {}
    for d in pairs(desktop_set) do ids[#ids + 1] = d end
    table.sort(ids)
    return ids
end

local function save_desktops()
    local f = io.open(desktops_file, "w")
    if not f then return end
    for _, d in ipairs(DesktopIds()) do f:write(d, "\n") end
    f:close()
end

-- the screens that show something of their own (on, not duplicating another)
local function screens()
    local list = {}
    for _, m in ipairs(hl.get_monitors() or {}) do
        if m.enabled ~= false and not m.is_mirror then list[#list + 1] = m end
    end
    return list
end

local function primary_screen(list)
    local f = io.open((os.getenv("XDG_CONFIG_HOME") or (os.getenv("HOME") .. "/.config"))
                      .. "/winshell/display.json", "r")
    local want
    if f then
        want = (f:read("a") or ""):match('"primary"%s*:%s*"([^"]+)"')
        f:close()
    end
    for _, m in ipairs(list) do if m.name == want then return m end end
    for _, m in ipairs(list) do if m.name:match("^eDP") then return m end end
    return list[1]
end

local function save_slots()
    local f = io.open(slots_file, "w")
    if not f then return end
    for n, s in pairs(slot_of) do f:write(n, " ", s, "\n") end
    f:close()
end

-- a screen keeps its slot for the whole session (its workspace numbers)
local function slot(name)
    if not slot_of[name] then
        local used = {}
        for _, s in pairs(slot_of) do used[s] = true end
        local s = name:match("^eDP") and 0 or 1
        if s == 1 and not used[0] then
            local laptop = false
            for _, m in ipairs(hl.get_monitors() or {}) do
                if m.name:match("^eDP") then laptop = true end
            end
            if not laptop then s = 0 end            -- no laptop screen: the first one is slot 0
        end
        while used[s] do s = s + 1 end
        slot_of[name] = s
        save_slots()
    end
    return slot_of[name]
end

local function ws_on(name, d) return slot(name) * DESK_STRIDE + d end

-- every desktop on every screen that is on: a persistent workspace there
local function sync_rules()
    local want = {}
    for _, m in ipairs(screens()) do
        for d in pairs(desktop_set) do want[ws_on(m.name, d)] = m.name end
    end
    for id, r in pairs(desk_rules) do
        if want[id] ~= r.mon then
            r.rule:set_enabled(false)
            desk_rules[id] = nil
        end
    end
    for id, mon in pairs(want) do
        if not desk_rules[id] then
            desk_rules[id] = { mon = mon, rule = hl.workspace_rule({ workspace = tostring(id),
                                                                    monitor = mon, persistent = true }) }
        end
    end
end

local function focused_screen()
    for _, m in ipairs(screens()) do if m.focused then return m end end
end

function CurrentDesktop()
    local ws = hl.get_active_workspace()
    local d = ws and desk_of_ws(ws.id)
    if d and desktop_set[d] then return d end
    return desk_now or DesktopIds()[1] or 1
end

-- A window belongs to the screen its middle is on: one dragged or snapped
-- over to the other screen still sat in the workspace of the screen it came
-- from (Hyprland draws it where it lies) – and stayed on screen when the
-- desktop switched. It moves to that screen's workspace of its desktop.
local function windows_to_their_screen()
    local list = screens()
    for _, w in ipairs(hl.get_windows() or {}) do
        local id = w.workspace and w.workspace.id
        local d = desk_of_ws(id)
        if d and w.mapped and type(w.at) == "table" and type(w.size) == "table" then
            local x, y = w.at.x, w.at.y
            local cx, cy = x + w.size.x / 2, y + w.size.y / 2
            for _, m in ipairs(list) do
                local sc = (m.scale and m.scale > 0) and m.scale or 1
                if cx >= m.x and cx < m.x + m.width / sc and cy >= m.y and cy < m.y + m.height / sc then
                    local want = ws_on(m.name, d)
                    if want ~= id then
                        local sel = "address:" .. w.address
                        hl.dispatch(hl.dsp.window.move({ workspace = tostring(want), follow = false, window = sel }))
                        if w.floating then          -- right where it was
                            hl.dispatch(hl.dsp.window.move({ x = x, y = y, window = sel }))
                        end
                        exiled[w.address] = nil
                    end
                    break
                end
            end
        end
    end
end

-- show desktop d on every screen (the screen you're on gets the focus)
function DesktopShow(d)
    if not desktop_set[d] then return end
    local was = desk_quiet
    desk_quiet = true
    windows_to_their_screen()
    desk_now = d
    local focus_id
    for _, m in ipairs(screens()) do
        local id = ws_on(m.name, d)
        if m.focused then
            focus_id = id
        elseif not (m.active_workspace and m.active_workspace.id == id) then
            m:set_workspace(tostring(id))
        end
    end
    if focus_id then
        local cur = hl.get_active_workspace()
        if not (cur and cur.id == focus_id) then
            hl.dispatch(hl.dsp.focus({ workspace = tostring(focus_id) }))
        end
    end
    desk_quiet = was
end

-- After screens went on / off / duplicating: every window back on its screen
-- (or, while that one is off, on the main screen), each workspace on its
-- screen, and every screen shows the desktop you were on.
function DesktopsArrange()
    local list = screens()
    if #list == 0 then return end
    local was = desk_quiet
    desk_quiet = true
    for _, w in ipairs(hl.get_windows() or {}) do          -- a desktop with windows stays
        local d = w.workspace and desk_of_ws(w.workspace.id)
        if d and not desktop_set[d] then desktop_set[d] = true end
    end
    if not next(desktop_set) then desktop_set[1] = true end
    if not desk_now or not desktop_set[desk_now] then desk_now = CurrentDesktop() end
    local on = {}
    for _, m in ipairs(list) do on[m.name] = true; slot(m.name) end
    local lead = primary_screen(list).name
    local by_slot = {}
    for n, s in pairs(slot_of) do by_slot[s] = n end
    sync_rules()
    for _, w in ipairs(hl.get_windows() or {}) do
        local id = w.workspace and w.workspace.id
        local d = desk_of_ws(id)
        if d then
            local e = exiled[w.address]
            if e and e.ws ~= id then exiled[w.address], e = nil, nil end   -- you moved it since
            local home = e and e.mon or by_slot[id // DESK_STRIDE]
            local mon = (home and on[home]) and home or lead
            local want = ws_on(mon, d)
            if want ~= id then
                local sel = "address:" .. w.address
                local leaving = home and not on[home]
                local rec
                if leaving then
                    -- remember its size and place on its screen: it gets them
                    -- back when that screen comes back (like Windows)
                    local m = w.monitor
                    rec = { mon = home, ws = want, size = { w.size.x, w.size.y },
                            rel = m and { w.at.x - m.x, w.at.y - m.y } or nil }
                end
                hl.dispatch(hl.dsp.window.move({ workspace = tostring(want), follow = false, window = sel }))
                exiled[w.address] = rec
                if e and not leaving and e.size and e.rel then          -- back on its own screen
                    for _, m in ipairs(list) do
                        if m.name == home then
                            set_geometry(w.address, m.x + e.rel[1], m.y + e.rel[2], e.size[1], e.size[2])
                        end
                    end
                end
            end
        end
    end
    for _, ws in ipairs(hl.get_workspaces() or {}) do       -- workspaces back on their screen
        local id = ws.id
        if id and id > 0 then
            local owner = by_slot[id // DESK_STRIDE]
            if owner and on[owner] and ws.monitor and ws.monitor.name ~= owner then
                hl.dispatch(hl.dsp.workspace.move({ workspace = tostring(id), monitor = owner }))
            end
        end
    end
    save_desktops()
    DesktopShow(desk_now)
    desk_quiet = was
    later(350, function() pcall(FitWindowsToScreens) end)
end

-- After screens changed (a screen unplugged: its windows came over to the
-- laptop's): every window fits the screen it is on now, like Windows –
-- maximized ones fill it, snapped ones snap to the same side of it, others
-- that are too big or not on it any more get its size at most, centered.
-- Before, a window from the big screen kept its size and only its top left
-- corner was to be seen on the small one.
function FitWindowsToScreens()
    for _, w in ipairs(hl.get_windows() or {}) do
        local id = w.workspace and w.workspace.id
        local a = w.address
        if w.mapped and id and id > 0 and w.monitor and w.fullscreen == 0 then
            if saved_geometry[a] or w.fullscreen_client == 1 then
                KeepMaximized(a)
            elseif snapped_to[a] then
                local mx, my, mw, mh = mon_box(w.monitor)
                local cx, cy = w.at.x + w.size.x / 2, w.at.y + w.size.y / 2
                if cx < mx or cx >= mx + mw or cy < my or cy >= my + mh then
                    WinSnap(snapped_to[a], w)
                end
            else
                local x, y, ww, hh = work_area(w.monitor)
                local bar = has_bar(w) and BAR_HEIGHT or 0
                local mx, my, mw, mh = mon_box(w.monitor)
                local cx, cy = w.at.x + w.size.x / 2, w.at.y + w.size.y / 2
                local inside = cx >= mx and cx < mx + mw and cy >= my and cy < my + mh
                if not inside or w.size.x > ww or w.size.y + bar > hh then
                    local g = centered(w, { w.at.x, w.at.y, w.size.x, w.size.y })
                    set_geometry(a, g[1], g[2], g[3], g[4])
                end
            end
        end
    end
end

-- screens are about to change / changing (winshell's Win + P, Display
-- settings): nothing Hyprland does meanwhile counts as switching desktops
function DesktopsHold(ms)
    desk_hold = desk_hold + 1
    later(ms or 1500, function()
        desk_hold = math.max(0, desk_hold - 1)
        pcall(DesktopsArrange)
    end)
end

local function screens_changed()
    later(250, function() pcall(DesktopsArrange) end)
    DesktopsHold(1500)
end
hl.on("monitor.added", screens_changed)
hl.on("monitor.removed", screens_changed)

-- a desktop switched on one screen (4-finger swipe, keys, task view):
-- the other screens follow. winshell calls it too on every workspace
-- event – the swipe doesn't always reach the hook here.
function DesktopSync()
    if desk_quiet or desk_hold > 0 then return end
    local m = focused_screen()
    local ws = m and m.active_workspace
    local d = ws and desk_of_ws(ws.id)
    if not d or not desktop_set[d] or ws.id ~= ws_on(m.name, d) then return end
    for _, o in ipairs(screens()) do
        if not (o.active_workspace and o.active_workspace.id == ws_on(o.name, d)) then
            DesktopShow(d)
            return
        end
    end
    desk_now = d
end
hl.on("workspace.active", function() DesktopSync() end)

-- the desktops and screens from before a config reload
do
    local f = io.open(desktops_file, "r")
    if f then
        for line in f:lines() do
            local d = tonumber(line)
            if d and d > 0 and d < DESK_STRIDE then desktop_set[d] = true end
        end
        f:close()
    end
    f = io.open(slots_file, "r")
    if f then
        for line in f:lines() do
            local n, s = line:match("^(%S+) (%d+)$")
            if n then slot_of[n] = tonumber(s) end
        end
        f:close()
    end
    if not next(desktop_set) then desktop_set[1] = true end
end
pcall(DesktopsArrange)
hl.on("hyprland.start", function() pcall(DesktopsArrange) end)

-- A new desktop after the last one. switch = false (task view): it only
-- appears, you stay where you are, like Windows. Its persistent workspaces
-- appear right away.
function DesktopAdd(switch)
    local ids = DesktopIds()
    if #ids >= MAX_DESKTOPS then return end
    local d = (ids[#ids] or 0) + 1
    if d >= DESK_STRIDE then return end
    desktop_set[d] = true
    save_desktops()
    sync_rules()
    if switch then DesktopShow(d) end
end

-- Close a desktop: its windows move to the desktop on the left (the first
-- one's to the right), each staying on its screen, like Windows.
function DesktopClose(d)
    local ids = DesktopIds()
    if #ids < 2 or not desktop_set[d] then return end
    local idx
    for i, v in ipairs(ids) do if v == d then idx = i end end
    local target = ids[idx - 1] or ids[idx + 1]
    for _, w in ipairs(hl.get_windows() or {}) do
        local id = w.workspace and w.workspace.id
        if desk_of_ws(id) == d then
            hl.dispatch(hl.dsp.window.move({ workspace = tostring(id - d + target), follow = false,
                                             window = "address:" .. w.address }))
        end
    end
    if CurrentDesktop() == d then DesktopShow(target) end
    desktop_set[d] = nil
    save_desktops()
    sync_rules()
end

-- Ctrl + Win + Left / Right: the neighbouring desktop (only existing ones,
-- like Windows – no new desktop past the last one)
function DesktopStep(delta)
    local ids, cur = DesktopIds(), CurrentDesktop()
    for i, v in ipairs(ids) do
        if v == cur and ids[i + delta] then
            DesktopShow(ids[i + delta])
            return
        end
    end
end

hl.bind("CTRL + " .. win .. " + Right", function() DesktopStep(1) end)
hl.bind("CTRL + " .. win .. " + Left",  function() DesktopStep(-1) end)
hl.bind("CTRL + " .. win .. " + D",     function() DesktopAdd(true) end)

-- Win + Shift + Left / Right: move the window to the neighbouring desktop
-- (same screen; existing desktops only), you go along
function DesktopMoveWindow(delta)
    local w = hl.get_active_window()
    local id = w and w.workspace and w.workspace.id
    local d = desk_of_ws(id)
    if not d then return end
    local ids = DesktopIds()
    for i, v in ipairs(ids) do
        if v == d and ids[i + delta] then
            hl.dispatch(hl.dsp.window.move({ workspace = tostring(id - d + ids[i + delta]), follow = false,
                                             window = "address:" .. w.address }))
            DesktopShow(ids[i + delta])
            hl.dispatch(hl.dsp.focus({ window = "address:" .. w.address }))
            return
        end
    end
end
hl.bind(win .. " + SHIFT + Right", function() DesktopMoveWindow(1) end)
hl.bind(win .. " + SHIFT + Left",  function() DesktopMoveWindow(-1) end)

-- Screenshots: Print / Win + Shift + S select a region with the mouse,
-- Shift + Print captures the whole screen
hl.bind("Print",               hl.dsp.exec_cmd(scripts .. "/screenshot.sh area"))
hl.bind(win .. " + SHIFT + S", hl.dsp.exec_cmd(scripts .. "/screenshot.sh area"))
hl.bind("SHIFT + Print",       hl.dsp.exec_cmd(scripts .. "/screenshot.sh full"))

-- Laptop keys: volume, brightness, media
hl.bind("XF86AudioRaiseVolume", hl.dsp.exec_cmd("wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ 5%+; " .. winshell .. " osd volume"), { locked = true, repeating = true })
hl.bind("XF86AudioLowerVolume", hl.dsp.exec_cmd("wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-; " .. winshell .. " osd volume"),      { locked = true, repeating = true })
hl.bind("XF86AudioMute",        hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle; " .. winshell .. " osd volume"),     { locked = true })
hl.bind("XF86AudioMicMute",     hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle; " .. winshell .. " osd mic"), { locked = true })
hl.bind("XF86MonBrightnessUp",  hl.dsp.exec_cmd("brightnessctl -e4 -n2 set 5%+; " .. winshell .. " osd brightness"),                  { locked = true, repeating = true })
hl.bind("XF86MonBrightnessDown",hl.dsp.exec_cmd("brightnessctl -e4 -n2 set 5%-; " .. winshell .. " osd brightness"),                  { locked = true, repeating = true })
hl.bind("XF86AudioNext",  hl.dsp.exec_cmd("playerctl next"),       { locked = true })
hl.bind("XF86AudioPause", hl.dsp.exec_cmd("playerctl play-pause"), { locked = true })
hl.bind("XF86AudioPlay",  hl.dsp.exec_cmd("playerctl play-pause"), { locked = true })
hl.bind("XF86AudioPrev",  hl.dsp.exec_cmd("playerctl previous"),   { locked = true })

---------------------------------------------------------------------
-- Personal settings (optional)
---------------------------------------------------------------------
local ok, err = pcall(require, "user")
if not ok and not tostring(err):find("not found") then
    hl.notification.create({ text = "user.lua: " .. tostring(err), timeout = 8000, icon = "error" })
end
