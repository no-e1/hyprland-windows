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

local terminal    = "kitty"
local fileManager = "nemo"
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

---------------------------------------------------------------------
-- Environment
---------------------------------------------------------------------
hl.env("XCURSOR_SIZE", "24")
hl.env("HYPRCURSOR_SIZE", "24")
hl.env("XCURSOR_THEME", "Fluent-dark-cursors")   -- white pointer with black outline, like Windows 11
hl.env("GDK_BACKEND", "wayland,x11,*")
hl.env("QT_QPA_PLATFORM", "wayland;xcb")
hl.env("QT_WAYLAND_DISABLE_WINDOWDECORATION", "1")
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
    hl.exec_cmd(winshell .. " supervise")
    hl.exec_cmd("hypridle")                    -- lock / screen off when idle
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
            enabled  = true,
            size     = 12,
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
hl.animation({ leaf = "layersIn",      enabled = true, speed = 3,   bezier = "winOut", style = "slide bottom" })
hl.animation({ leaf = "layersOut",     enabled = true, speed = 1.8, bezier = "winIn",  style = "slide bottom" })
hl.animation({ leaf = "fadeLayersIn",  enabled = true, speed = 2.5, bezier = "smooth" })
hl.animation({ leaf = "fadeLayersOut", enabled = true, speed = 1.8, bezier = "linear" })
-- virtual desktops slide sideways like on Windows
hl.animation({ leaf = "workspaces",    enabled = true, speed = 4,   bezier = "winOut", style = "slide" })

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

-- Touchpad: 4-finger swipe switches virtual desktops (like Windows)
hl.gesture({ fingers = 4, direction = "horizontal", action = "workspace" })

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
    match = { class = "^(kitty)$" },
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

-- Mica/Acrylic blur for taskbar, start menu, flyouts and Alt+Tab
hl.layer_rule({ name = "blur-startmenu", match = { namespace = "^rofi$" },                      blur = true, ignore_alpha = 0.2 })
hl.layer_rule({ name = "blur-swaync",    match = { namespace = "^swaync-.*$" },                 blur = true, ignore_alpha = 0.2 })
hl.layer_rule({ name = "blur-winshell",  match = { namespace = "^winshell-(qs|switcher|start|calendar|clipboard)$" }, blur = true, ignore_alpha = 0.2 })
hl.layer_rule({ name = "blur-osd",        match = { namespace = "^winshell-osd$" },             blur = true, ignore_alpha = 0.2 })
-- winshell animates its flyouts itself (they glide out of the taskbar)
hl.layer_rule({ name = "no-anim-winshell", match = { namespace = "^winshell-.*$" }, no_anim = true })
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
    square_corners(addr, on)
    if AppMaximized and not quiet then AppMaximized(addr, on) end
end
-- windows that are maximized already (config reload, login)
for _, w in ipairs(hl.get_windows() or {}) do
    if w.fullscreen_client == 1 then square_corners(w.address, true) end
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
end

-- Bring a hidden window back to a desktop.
-- Maximized windows were told "not maximized" while hidden (see HideWindow),
-- so tell them again. Other Chromium-based windows need a reconfigure or
-- their input ends up shifted, so nudge the size by one pixel and back –
-- the second resize goes out a moment later, otherwise Hyprland merges both
-- and the window grows by a pixel on every restore.
function RestoreWindow(w, ws_id)
    if ThumbsPause then ThumbsPause() end
    local a, sel = w.address, "address:" .. w.address
    local x, y = w.at.x, w.at.y     -- numbers: the window object reads live values
    hl.dispatch(hl.dsp.window.move({ workspace = ws_id, follow = false, window = sel }))
    set_alpha(a, 1)                 -- faded out by "show desktop": fades back in
    hl.dispatch(hl.dsp.window.move({ x = x, y = y, window = sel }))
    if hidden_maximized[a] then
        hidden_maximized[a] = nil
        fs_hook_paused[a] = true
        client_maximized(a, true, true)
        later(300, function() fs_hook_paused[a] = nil end)
    else
        local sx, sy = w.size.x, w.size.y
        hl.dispatch(hl.dsp.window.resize({ x = sx + 1, y = sy, window = sel }))
        later(60, function()
            hl.dispatch(hl.dsp.window.resize({ x = sx, y = sy, window = sel }))
            -- shrinking back moves the window 1 px to the right: put it back
            hl.dispatch(hl.dsp.window.move({ x = x, y = y, window = sel }))
        end)
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
OWN_TITLEBAR = { "brave-browser", "discord", "vesktop", "webcord", "steam",
                 "spotify", "obsidian", "slack", "firefox", "org.mozilla.firefox",
                 "code", "code-oss" }
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

function WinMax(action, w)
    w = w or hl.get_active_window()
    if not w or not w.mapped then return end
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
    elseif action == "restore" and (saved_geometry[a] or w.fullscreen_client == 1) then
        -- no normal size known: a centered window, like Windows
        local g = saved_geometry[a] or { x + math.floor(ww * 0.15), y + math.floor(hh * 0.12),
                                         math.floor(ww * 0.7), math.floor(hh * 0.7) }
        saved_geometry[a] = nil
        client_maximized(a, false)
        set_geometry(a, g[1], g[2], g[3], g[4])
        if EnsureOnScreenSoon then EnsureOnScreenSoon(a, g) end
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

-- Apps asking to be maximized (their own maximize button, double click on
-- their title bar) get the Windows-style maximize instead.
hl.on("window.fullscreen", function(ev)
    local w = event_window(ev) or hl.get_active_window()
    if not w or fs_hook_paused[w.address] then return end
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
hl.bind(win .. " + N", hl.dsp.exec_cmd("swaync-client -t -sw"))              -- notifications
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

function WinSnap(zone, w)
    w = w or hl.get_active_window()
    if not w or not w.mapped then return end
    local a = w.address
    local mon = w.monitor or hl.get_active_monitor()
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
end

-- An app's own maximize / restore button (Brave's □; via the hyprbars
-- plugin, which can't tell which of the two it is): toggle by the real state.
-- Once per half second – some apps ask twice.
local max_toggled = {}
function WinMaxWindow(addr)
    if drag and drag.moved then return end
    local w = hl.get_window("address:" .. addr)
    if not w or max_toggled[w.address] then return end
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
    set_geometry(a, g[1], g[2], g[3], g[4])
    if EnsureOnScreenSoon then EnsureOnScreenSoon(a) end
    return true
end

-- Win + Left/Right like Windows: towards the other side a snapped window
-- first comes loose, otherwise it snaps to that half
function WinSnapKey(side)
    local w = hl.get_active_window()
    if not w then return end
    local z = snapped_to[w.address]
    local other = side == "left" and "right" or "left"
    if z and z:find(other) then
        WinUnsnap(w)
    else
        WinSnap(side, w)
    end
end

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
    if not w then return end
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
local desktop_hidden = nil      -- { windows = {addresses, bottom to top}, active = address }
local function windows_here()
    local ws = hl.get_active_workspace()
    local list = {}
    for _, w in ipairs(hl.get_windows() or {}) do
        if w.mapped and w.workspace and ws and w.workspace.id == ws.id then
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
    for _, a in ipairs(desktop_hidden.windows) do f:write("win ", a, "\n") end
    f:close()
end
local function load_desktop()
    local f = io.open(DESK_STATE, "r")
    if not f then return end
    local saved = { windows = {} }
    for line in f:lines() do
        local kind, a = line:match("^(%S+) (%S+)")
        if kind == "active" and a ~= "-" then
            saved.active = a
        elseif kind == "win" then
            table.insert(saved.windows, a)
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
local function desk_tokens(list)
    local t = {}
    for _, w in ipairs(list) do
        table.insert(t, string.format("%s|%d|%d|%d|%d|%d|%s", w.address, w.at.x, w.at.y,
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
    local saved = { windows = {}, active = active and active.address }
    local list = windows_here()             -- back to front
    for _, w in ipairs(list) do table.insert(saved.windows, w.address) end
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
local function cancel_hide(a)
    local w = hl.get_window("address:" .. a)
    if not w or not w.workspace or w.workspace.name == "special:minimized" then return false end
    set_alpha(a, 1)
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
-- the real windows right under the pictures at the end.
local function restore_hidden()
    local saved = desktop_hidden
    desktop_hidden = nil
    desk_settling = false
    save_desktop()
    desk_gen = desk_gen + 1                 -- whatever is still on its way out stays
    local gen = desk_gen
    desk_out = nil
    if not saved then return end
    local todo, still_here = {}, false
    for _, a in ipairs(saved.windows) do
        if cancel_hide(a) then
            still_here = true
        else
            local w = hl.get_window("address:" .. a)
            if w and w.workspace and w.workspace.name == "special:minimized" then
                table.insert(todo, w)
            end
        end
    end
    if still_here then winshell_send("desk stop") end
    desk_in = { gen = gen, windows = todo, active = saved.active }
    if #todo == 0 then
        DesktopInNow(gen)
        return
    end
    winshell_send("desk in DesktopInNow " .. gen .. " " .. desk_tokens(todo))
    later(700, function() DesktopInNow(gen) end)        -- winshell didn't answer
end
function DesktopInNow(gen)
    local d = desk_in
    if not d or d.gen ~= gen then return end
    desk_in = nil
    local ws = hl.get_active_workspace()
    for _, w in ipairs(d.windows) do        -- back ones first: the stacking order comes back
        local cur = hl.get_window("address:" .. w.address)
        if cur and cur.workspace and cur.workspace.name == "special:minimized" then
            RestoreWindow(cur, ws.id)
            hl.dispatch(hl.dsp.window.alter_zorder({ mode = "top", window = "address:" .. w.address }))
        end
    end
    if d.active and hl.get_window("address:" .. d.active) then
        hl.dispatch(hl.dsp.focus({ window = "address:" .. d.active }))
    end
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
hl.bind(win .. " + 1", hl.dsp.exec_cmd(scripts .. "/pinned.sh open '^nemo$' nemo"))
hl.bind(win .. " + 2", hl.dsp.exec_cmd(scripts .. "/pinned.sh open '^(brave-browser)$' brave-browser"))
hl.bind(win .. " + 3", hl.dsp.exec_cmd(scripts .. "/pinned.sh open '^kitty$' kitty"))

-- Win + Tab: task view (the switcher stays open until you pick a window)
hl.bind(win .. " + Tab", function() winshell_send("switch view") end)

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

-- Virtual desktops like Windows: Ctrl + Win + arrows / Ctrl + Win + D
hl.bind("CTRL + " .. win .. " + Right", hl.dsp.focus({ workspace = "r+1" }))
hl.bind("CTRL + " .. win .. " + Left",  hl.dsp.focus({ workspace = "r-1" }))
hl.bind("CTRL + " .. win .. " + D",     hl.dsp.focus({ workspace = "empty" }))
hl.bind(win .. " + SHIFT + Right", hl.dsp.window.move({ workspace = "r+1" }))
hl.bind(win .. " + SHIFT + Left",  hl.dsp.window.move({ workspace = "r-1" }))

-- Screenshots: Print / Win + Shift + S select a region with the mouse,
-- Shift + Print captures the whole screen
hl.bind("Print",               hl.dsp.exec_cmd(scripts .. "/screenshot.sh area"))
hl.bind(win .. " + SHIFT + S", hl.dsp.exec_cmd(scripts .. "/screenshot.sh area"))
hl.bind("SHIFT + Print",       hl.dsp.exec_cmd(scripts .. "/screenshot.sh full"))

-- Laptop keys: volume, brightness, media
hl.bind("XF86AudioRaiseVolume", hl.dsp.exec_cmd("wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ 5%+; " .. winshell .. " osd volume"), { locked = true, repeating = true })
hl.bind("XF86AudioLowerVolume", hl.dsp.exec_cmd("wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-; " .. winshell .. " osd volume"),      { locked = true, repeating = true })
hl.bind("XF86AudioMute",        hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle; " .. winshell .. " osd volume"),     { locked = true })
hl.bind("XF86AudioMicMute",     hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle"),   { locked = true })
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
