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
hl.env("XCURSOR_THEME", "Fluent-cursors")   -- white pointer like Windows 11
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
    -- taskbar; restarts itself if it ever crashes (like explorer.exe)
    hl.exec_cmd("sh -c 'while :; do waybar; sleep 1; done'")
    hl.exec_cmd("swaync")                      -- notifications
    hl.exec_cmd(winshell .. " start")          -- Alt+Tab and quick settings
    hl.exec_cmd("hypridle")                    -- lock / screen off when idle
    hl.exec_cmd("sh -c 'command -v cliphist >/dev/null && wl-paste --watch cliphist store'")
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
        force_default_wallpaper = 0,
        disable_hyprland_logo   = true,
        focus_on_activate       = true,
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
hl.animation({ leaf = "windowsIn",     enabled = true, speed = 3.2, bezier = "winOut", style = "popin 92%" })
hl.animation({ leaf = "windowsOut",    enabled = true, speed = 2,   bezier = "winIn",  style = "popin 94%" })
hl.animation({ leaf = "windowsMove",   enabled = true, speed = 3.5, spring = "soft" })
hl.animation({ leaf = "border",        enabled = true, speed = 4,   bezier = "smooth" })
hl.animation({ leaf = "fadeIn",        enabled = true, speed = 2.5, bezier = "smooth" })
hl.animation({ leaf = "fadeOut",       enabled = true, speed = 2,   bezier = "linear" })
hl.animation({ leaf = "fade",          enabled = true, speed = 3,   bezier = "smooth" })
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

-- XWayland drag fix from the default config
hl.window_rule({
    name  = "fix-xwayland-drags",
    match = { class = "^$", title = "^$", xwayland = true, float = true, fullscreen = false, pin = false },
    no_focus = true,
})

-- Mica/Acrylic blur for taskbar, start menu, flyouts and Alt+Tab
hl.layer_rule({ name = "blur-startmenu", match = { namespace = "^rofi$" },                      blur = true, ignore_alpha = 0.2 })
hl.layer_rule({ name = "blur-swaync",    match = { namespace = "^swaync-.*$" },                 blur = true, ignore_alpha = 0.2 })
hl.layer_rule({ name = "blur-winshell",  match = { namespace = "^winshell-(qs|switcher|start|calendar)$" }, blur = true, ignore_alpha = 0.2 })
hl.layer_rule({ name = "blur-osd",        match = { namespace = "^winshell-osd$" },             blur = true, ignore_alpha = 0.2 })
-- winshell animates its flyouts itself (they glide out of the taskbar)
hl.layer_rule({ name = "no-anim-winshell", match = { namespace = "^winshell-.*$" }, no_anim = true })

---------------------------------------------------------------------
-- Active window always comes to the front (taskbar, click, Alt+Tab).
-- Minimized windows live in the hidden "special:minimized" workspace;
-- activating one (e.g. from the taskbar) brings it back.
---------------------------------------------------------------------
hl.on("window.active", function()
    local w = hl.get_active_window()
    if w and w.workspace and w.workspace.name == "special:minimized" then
        local ws = hl.get_active_workspace()
        if ws and not ws.special then
            hl.dispatch(hl.dsp.window.move({ workspace = ws.id, window = "address:" .. w.address }))
        end
        local sp = hl.get_active_special_workspace()
        if sp and sp.name == "special:minimized" then
            hl.dispatch(hl.dsp.workspace.toggle_special("minimized"))
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
local OWN_TITLEBAR = { "^brave%-browser$", "^discord$", "^firefox$", "^org%.mozilla%.firefox$",
                       "^code$", "^code%-oss$", "^org%.gnome%." }
local saved_geometry = {}

local function has_bar(w)
    for _, pat in ipairs(OWN_TITLEBAR) do
        if (w.class or ""):match(pat) then return false end
    end
    return true
end

local function work_area(mon)
    local r, s = mon.reserved, mon.scale
    return mon.x + r.left, mon.y + r.top,
           math.floor(mon.width / s) - r.left - r.right,
           math.floor(mon.height / s) - r.top - r.bottom
end

local function set_geometry(addr, x, y, w, h)
    local sel = "address:" .. addr
    hl.dispatch(hl.dsp.window.resize({ x = w, y = h, window = sel }))
    hl.dispatch(hl.dsp.window.move({ x = x, y = y, window = sel }))
end

-- Tell the app it is maximized (no shadow margins, "restore" button) while
-- Hyprland keeps treating it as a normal window (normal stacking order).
local function client_maximized(addr, on)
    hl.dispatch(hl.dsp.window.fullscreen_state({ internal = 0, client = on and 1 or 0,
                                                 window = "address:" .. addr }))
end

function WinMax(action, w)
    w = w or hl.get_active_window()
    if not w or not w.mapped then return end
    local a = w.address
    if action == "toggle" then
        action = saved_geometry[a] and "restore" or "max"
    end
    local mon = w.monitor or hl.get_active_monitor()
    local x, y, ww, hh = work_area(mon)
    local bar = has_bar(w) and BAR_HEIGHT or 0
    if action == "max" then
        if not saved_geometry[a] then
            local g = { w.at.x, w.at.y, w.size.x, w.size.y }
            -- window started maximized: restore to a centered 70% window later
            if g[3] >= ww - 4 then
                g = { x + math.floor(ww * 0.15), y + math.floor(hh * 0.12),
                      math.floor(ww * 0.7), math.floor(hh * 0.7) }
            end
            saved_geometry[a] = g
        end
        client_maximized(a, true)
        set_geometry(a, x, y + bar, ww, hh - bar)
    elseif action == "restore" and saved_geometry[a] then
        local g = saved_geometry[a]
        saved_geometry[a] = nil
        client_maximized(a, false)
        set_geometry(a, g[1], g[2], g[3], g[4])
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

-- Called by the title bar when a drag starts: a maximized window shrinks back
-- to its normal size and stays under the cursor at the same relative spot.
function WinDragStart()
    local w = hl.get_active_window()
    if not w then return end
    local g = saved_geometry[w.address]
    if not g then return end
    saved_geometry[w.address] = nil
    client_maximized(w.address, false)
    local c = hl.get_cursor_pos()
    local rel = (c.x - w.at.x) / math.max(1, w.size.x)
    set_geometry(w.address, math.floor(c.x - g[3] * rel), w.at.y, g[3], g[4])
end

-- Apps asking to be maximized (their own maximize button, double click on
-- their title bar) get the Windows-style maximize instead.
hl.on("window.fullscreen", function(ev)
    local w = (type(ev) == "table" and ev.address and ev) or hl.get_active_window()
    if not w then return end
    if w.fullscreen == 1 then
        hl.dispatch(hl.dsp.window.fullscreen({ mode = "maximized", action = "unset", window = "address:" .. w.address }))
        WinMax("toggle", w)
    elseif w.fullscreen == 0 and w.fullscreen_client == 0 and saved_geometry[w.address] then
        WinMax("restore", w)          -- the app's own "restore" button
    end
end)

hl.on("window.close", function(ev)
    if type(ev) == "table" and ev.address then saved_geometry[ev.address] = nil end
end)

-- Apps with their own title bar (Brave, Discord, …) ask to be minimized;
-- Hyprland ignores that by default, so hide them like the "–" button does.
hl.on("window.minimize", function(ev)
    local w = (type(ev) == "table" and ev.address and ev) or hl.get_active_window()
    if w and w.workspace and w.workspace.name ~= "special:minimized" then
        hl.dispatch(hl.dsp.window.move({ workspace = "special:minimized", follow = false,
                                         window = "address:" .. w.address }))
    end
end)

-- New windows that don't fit the work area (e.g. Brave restoring an old size)
-- open maximized, like on Windows; windows sticking out at the top are moved
-- down so their title bar can always be grabbed.
hl.on("window.open", function(ev)
    local w = (type(ev) == "table" and ev.address and ev) or hl.get_active_window()
    if not w or not w.floating then return end
    local x, y, ww, hh = work_area(w.monitor or hl.get_active_monitor())
    local bar = has_bar(w) and BAR_HEIGHT or 0
    if w.size.x > ww - 2 or w.size.y > hh - bar - 2 then
        WinMax("max", w)
    elseif w.at.y < y + bar then
        hl.dispatch(hl.dsp.window.move({ x = w.at.x, y = y + bar + 1, window = "address:" .. w.address }))
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
        hl.window_rule({
            name  = "own-titlebar",
            match = { class = "^(brave-browser|discord|firefox|org\\.mozilla\\.firefox|code|code-oss|org\\.gnome\\..*)$" },
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

-- Windows key alone (on release) = start menu
hl.bind("Super_L", hl.dsp.exec_cmd(scripts .. "/startmenu.sh"), { release = true })
hl.bind(win .. " + S", hl.dsp.exec_cmd(scripts .. "/startmenu.sh"))          -- search

hl.bind(win .. " + E", hl.dsp.exec_cmd(fileManager))                         -- File Explorer
hl.bind(win .. " + R", hl.dsp.exec_cmd(scripts .. "/run.sh"))                -- Run
hl.bind(win .. " + I", hl.dsp.exec_cmd(scripts .. "/settings.sh"))           -- Settings
hl.bind(win .. " + A", hl.dsp.exec_cmd(winshell .. " qs"))                   -- quick settings
hl.bind(win .. " + N", hl.dsp.exec_cmd("swaync-client -t -sw"))              -- notifications
hl.bind(win .. " + X", hl.dsp.exec_cmd(scripts .. "/powermenu.sh --menu"))   -- power menu
hl.bind(win .. " + L", hl.dsp.exec_cmd("pidof hyprlock || hyprlock"))        -- lock
hl.bind(win .. " + V", hl.dsp.exec_cmd(scripts .. "/clipboard.sh"))          -- clipboard history
hl.bind(win .. " + W", hl.dsp.exec_cmd(scripts .. "/wallpaper.sh pick"))     -- pick background
hl.bind(win .. " + T", hl.dsp.exec_cmd(terminal))                            -- terminal
hl.bind(win .. " + B", hl.dsp.exec_cmd(browser))                             -- browser
hl.bind("CTRL + SHIFT + Escape", hl.dsp.exec_cmd("gnome-system-monitor"))    -- Task Manager

-- Windows
hl.bind("ALT + F4", hl.dsp.window.close())

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

-- Alt+Tab: hold Alt, press Tab to move through the windows, release Alt to
-- switch. The switcher grabs the keyboard while open and notices the Alt
-- release itself (Hyprland release binds don't fire reliably for this).
hl.bind("ALT + Tab",         function() winshell_send("switch next") end, { repeating = true })
hl.bind("ALT + SHIFT + Tab", function() winshell_send("switch prev") end, { repeating = true })

-- Show desktop (Win + D): hide every window of this desktop, press again to
-- bring them back. Win + M minimizes all, Win + Shift + M restores them.
local desktop_hidden = nil
local function windows_here()
    local ws = hl.get_active_workspace()
    local list = {}
    for _, w in ipairs(hl.get_windows() or {}) do
        if w.mapped and w.workspace and ws and w.workspace.id == ws.id then
            table.insert(list, w.address)
        end
    end
    return list, ws
end
local function hide_all()
    local list = windows_here()
    for _, a in ipairs(list) do
        hl.dispatch(hl.dsp.window.move({ workspace = "special:minimized", follow = false, window = "address:" .. a }))
    end
    return list
end
local function restore_hidden()
    local ws = hl.get_active_workspace()
    for _, a in ipairs(desktop_hidden or {}) do
        if hl.get_window("address:" .. a) then
            hl.dispatch(hl.dsp.window.move({ workspace = ws.id, follow = false, window = "address:" .. a }))
        end
    end
    desktop_hidden = nil
end
hl.bind(win .. " + D", function()
    if desktop_hidden and #windows_here() == 0 then restore_hidden() else desktop_hidden = hide_all() end
end)
hl.bind(win .. " + M",         function() desktop_hidden = hide_all() end)
hl.bind(win .. " + SHIFT + M", function() restore_hidden() end)

-- Win + Home: minimize everything except the active window
hl.bind(win .. " + Home", function()
    local active = hl.get_active_window()
    for _, a in ipairs((windows_here())) do
        if not active or a ~= active.address then
            hl.dispatch(hl.dsp.window.move({ workspace = "special:minimized", follow = false, window = "address:" .. a }))
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
hl.bind(win .. " + Left",  hl.dsp.exec_cmd(scripts .. "/winctl.sh snap left"))
hl.bind(win .. " + Right", hl.dsp.exec_cmd(scripts .. "/winctl.sh snap right"))
hl.bind(win .. " + Up",    function() WinMax("max") end)
hl.bind(win .. " + Down",  function()
    local w = hl.get_active_window()
    if not w then return end
    if saved_geometry[w.address] then
        WinMax("restore", w)
    else
        hl.dispatch(hl.dsp.window.move({ workspace = "special:minimized", follow = false }))
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
