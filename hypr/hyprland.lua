-- ════════════════════════════════════════════════════════════════
--  Hyprland im Windows-11-Stil
--  Für Hyprland 0.55+ (Lua-Konfiguration)
--
--  Persönliche Anpassungen (Tastatur, NVIDIA, Bildschirm) gehören
--  in ~/.config/hypr/user.lua – die wird vom Installer erzeugt und
--  am Ende dieser Datei geladen.
-- ════════════════════════════════════════════════════════════════

local home    = os.getenv("HOME")
local scripts = home .. "/.config/hypr/scripts"

local terminal    = "kitty"
local fileManager = "nemo"
local browser     = "brave-browser"

---------------------------------------------------------------------
-- Bildschirm
---------------------------------------------------------------------
hl.monitor({
    output   = "",
    mode     = "preferred",
    position = "auto",
    scale    = "auto",
})

---------------------------------------------------------------------
-- Umgebungsvariablen
---------------------------------------------------------------------
hl.env("XCURSOR_SIZE", "24")
hl.env("HYPRCURSOR_SIZE", "24")
hl.env("XCURSOR_THEME", "Fluent-dark-cursors")
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
    hl.exec_cmd(scripts .. "/theme.sh")        -- GTK-Theme, Icons, Cursor, Schrift
    hl.exec_cmd(scripts .. "/polkit.sh")       -- Passwortabfragen für Admin-Rechte
    hl.exec_cmd(scripts .. "/wallpaper.sh restore")
    hl.exec_cmd("waybar")                      -- Taskleiste
    hl.exec_cmd("swaync")                      -- Schnelleinstellungen + Benachrichtigungen
    hl.exec_cmd("hypridle")                    -- Sperren / Bildschirm aus bei Inaktivität
    hl.exec_cmd("sh -c 'command -v cliphist >/dev/null && wl-paste --watch cliphist store'")
end)

---------------------------------------------------------------------
-- Aussehen: Windows 11 (dunkel, abgerundet, Schatten, "Mica"-Blur)
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
        -- Fenster wie unter Windows am Rand mit der Maus vergrössern
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

    misc = {
        force_default_wallpaper = 0,
        disable_hyprland_logo   = true,
        focus_on_activate       = true,
    },
})

---------------------------------------------------------------------
-- Animationen: Fenster "ploppen" wie bei Windows weich auf
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
-- Startmenü / Schnelleinstellungen gleiten von unten herein
hl.animation({ leaf = "layersIn",      enabled = true, speed = 3,   bezier = "winOut", style = "slide bottom" })
hl.animation({ leaf = "layersOut",     enabled = true, speed = 1.8, bezier = "winIn",  style = "slide bottom" })
hl.animation({ leaf = "fadeLayersIn",  enabled = true, speed = 2.5, bezier = "smooth" })
hl.animation({ leaf = "fadeLayersOut", enabled = true, speed = 1.8, bezier = "linear" })
-- Virtuelle Desktops gleiten seitlich wie bei Windows
hl.animation({ leaf = "workspaces",    enabled = true, speed = 4,   bezier = "winOut", style = "slide" })

---------------------------------------------------------------------
-- Eingabe
---------------------------------------------------------------------
hl.config({
    input = {
        kb_layout  = "ch",   -- wird in user.lua mit deinem Layout überschrieben
        kb_variant = "",
        numlock_by_default = true,
        follow_mouse = 1,
        sensitivity  = 0,
        touchpad = {
            natural_scroll = true,   -- wie Windows-Standard bei Touchpads
        },
    },
})

-- Touchpad: 4 Finger seitlich = virtuellen Desktop wechseln (wie Windows)
hl.gesture({ fingers = 4, direction = "horizontal", action = "workspace" })

---------------------------------------------------------------------
-- Fensterregeln: ALLE Fenster schweben frei (kein Tiling) – wie Windows
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

-- XWayland-Drag-Fix aus der Standard-Config
hl.window_rule({
    name  = "fix-xwayland-drags",
    match = { class = "^$", title = "^$", xwayland = true, float = true, fullscreen = false, pin = false },
    no_focus = true,
})

-- Mica/Acrylic-Effekt für Taskleiste, Startmenü und Schnelleinstellungen
hl.layer_rule({ name = "blur-taskbar",   match = { namespace = "^waybar$" },        blur = true, ignore_alpha = 0.2 })
hl.layer_rule({ name = "blur-startmenu", match = { namespace = "^rofi$" },          blur = true, ignore_alpha = 0.2 })
hl.layer_rule({ name = "blur-swaync",    match = { namespace = "^swaync-.*$" },     blur = true, ignore_alpha = 0.2 })

---------------------------------------------------------------------
-- Tastenkürzel wie unter Windows
---------------------------------------------------------------------
local win = "SUPER"

-- Windows-Taste alleine (beim Loslassen) = Startmenü
hl.bind("Super_L", hl.dsp.exec_cmd(scripts .. "/startmenu.sh"), { release = true })
hl.bind(win .. " + S", hl.dsp.exec_cmd(scripts .. "/startmenu.sh"))          -- Suche

hl.bind(win .. " + E", hl.dsp.exec_cmd(fileManager))                         -- Explorer
hl.bind(win .. " + R", hl.dsp.exec_cmd(scripts .. "/run.sh"))                -- Ausführen
hl.bind(win .. " + I", hl.dsp.exec_cmd(scripts .. "/settings.sh"))           -- Einstellungen
hl.bind(win .. " + A", hl.dsp.exec_cmd("swaync-client -t -sw"))              -- Schnelleinstellungen
hl.bind(win .. " + N", hl.dsp.exec_cmd("swaync-client -t -sw"))              -- Benachrichtigungen
hl.bind(win .. " + X", hl.dsp.exec_cmd(scripts .. "/powermenu.sh --menu"))   -- Power-Menü
hl.bind(win .. " + L", hl.dsp.exec_cmd("pidof hyprlock || hyprlock"))        -- Sperren
hl.bind(win .. " + V", hl.dsp.exec_cmd(scripts .. "/clipboard.sh"))          -- Zwischenablage-Verlauf
hl.bind(win .. " + W", hl.dsp.exec_cmd(scripts .. "/wallpaper.sh pick"))     -- Hintergrund wählen
hl.bind(win .. " + T", hl.dsp.exec_cmd(terminal))                            -- Terminal
hl.bind(win .. " + B", hl.dsp.exec_cmd(browser))                             -- Browser
hl.bind("CTRL + SHIFT + Escape", hl.dsp.exec_cmd("gnome-system-monitor"))    -- Task-Manager

-- Fenster
hl.bind("ALT + F4", hl.dsp.window.close())
hl.bind("ALT + Tab", function()
    hl.dispatch(hl.dsp.window.cycle_next())
    hl.dispatch(hl.dsp.window.alter_zorder({ mode = "top" }))
end)

-- Snap: Win + Pfeiltasten
hl.bind(win .. " + Left",  hl.dsp.exec_cmd(scripts .. "/snap.sh left"))
hl.bind(win .. " + Right", hl.dsp.exec_cmd(scripts .. "/snap.sh right"))
hl.bind(win .. " + Up",    hl.dsp.window.fullscreen({ mode = "maximized", action = "toggle" }))
hl.bind(win .. " + Down",  hl.dsp.exec_cmd(scripts .. "/snap.sh restore"))
hl.bind(win .. " + SHIFT + Up", hl.dsp.window.fullscreen({ mode = "fullscreen", action = "toggle" }))

-- Fenster mit Win + Maus ziehen / vergrössern (zusätzlich zur Titelleiste)
hl.bind(win .. " + mouse:272", hl.dsp.window.drag(),   { mouse = true })
hl.bind(win .. " + mouse:273", hl.dsp.window.resize(), { mouse = true })

-- Virtuelle Desktops wie Windows: Strg + Win + Pfeil / Strg + Win + D
hl.bind("CTRL + " .. win .. " + Right", hl.dsp.focus({ workspace = "r+1" }))
hl.bind("CTRL + " .. win .. " + Left",  hl.dsp.focus({ workspace = "r-1" }))
hl.bind("CTRL + " .. win .. " + D",     hl.dsp.focus({ workspace = "empty" }))
hl.bind(win .. " + SHIFT + Right", hl.dsp.window.move({ workspace = "r+1" }))
hl.bind(win .. " + SHIFT + Left",  hl.dsp.window.move({ workspace = "r-1" }))

-- Screenshots: Win + Shift + S (Ausschnitt) / Druck (ganzer Bildschirm)
hl.bind(win .. " + SHIFT + S", hl.dsp.exec_cmd(scripts .. "/screenshot.sh area"))
hl.bind("Print",               hl.dsp.exec_cmd(scripts .. "/screenshot.sh full"))

-- Laptop-Tasten: Lautstärke, Helligkeit, Medien
hl.bind("XF86AudioRaiseVolume", hl.dsp.exec_cmd("wpctl set-volume -l 1 @DEFAULT_AUDIO_SINK@ 5%+"), { locked = true, repeating = true })
hl.bind("XF86AudioLowerVolume", hl.dsp.exec_cmd("wpctl set-volume @DEFAULT_AUDIO_SINK@ 5%-"),      { locked = true, repeating = true })
hl.bind("XF86AudioMute",        hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SINK@ toggle"),     { locked = true })
hl.bind("XF86AudioMicMute",     hl.dsp.exec_cmd("wpctl set-mute @DEFAULT_AUDIO_SOURCE@ toggle"),   { locked = true })
hl.bind("XF86MonBrightnessUp",  hl.dsp.exec_cmd("brightnessctl -e4 -n2 set 5%+"),                  { locked = true, repeating = true })
hl.bind("XF86MonBrightnessDown",hl.dsp.exec_cmd("brightnessctl -e4 -n2 set 5%-"),                  { locked = true, repeating = true })
hl.bind("XF86AudioNext",  hl.dsp.exec_cmd("playerctl next"),       { locked = true })
hl.bind("XF86AudioPause", hl.dsp.exec_cmd("playerctl play-pause"), { locked = true })
hl.bind("XF86AudioPlay",  hl.dsp.exec_cmd("playerctl play-pause"), { locked = true })
hl.bind("XF86AudioPrev",  hl.dsp.exec_cmd("playerctl previous"),   { locked = true })

---------------------------------------------------------------------
-- Persönliche Einstellungen (vom Installer erzeugt, optional)
---------------------------------------------------------------------
local ok, err = pcall(require, "user")
if not ok and not tostring(err):find("not found") then
    hl.notification.create({ text = "user.lua: " .. tostring(err), timeout = 8000, icon = "error" })
end
