#!/usr/bin/env bash
# "Settings" (Win + I): one menu for all settings tools
S="$HOME/.config/hypr/scripts"
pkill -x rofi && exit 0

has() { command -v "$1" >/dev/null; }

items=()
items+=("󰖩  Wi-Fi")
items+=("󰂯  Bluetooth")
items+=("󰕾  Sound")
items+=("󰍹  Displays")
items+=("󰍽  Mouse")
items+=("󰸉  Background")
has nwg-look && items+=("󰏘  Appearance (colors, fonts)")
items+=("󰛳  Network connections")
items+=("󰍛  Task Manager")
items+=("󰒓  Edit Hyprland config")

choice=$(printf '%s\n' "${items[@]}" | rofi -dmenu -i -p "Settings" -theme "$HOME/.config/rofi/list.rasi")

case "$choice" in
    *Wi-Fi)               exec "$HOME/.config/hypr/winshell/winshell-ctl" qs wifi ;;
    *Bluetooth)           exec blueman-manager ;;
    *Sound)               exec pavucontrol ;;
    *Displays)         exec "$HOME/.config/hypr/winshell/winshell-ctl" displays ;;
    *Mouse)            exec "$HOME/.config/hypr/winshell/winshell-ctl" mouse ;;
    *Background)     exec "$S/wallpaper.sh" pick ;;
    *Appearance*)             exec nwg-look ;;
    *connections) exec nm-connection-editor ;;
    *Manager)        exec gnome-system-monitor ;;
    *config)      exec xdg-open "$HOME/.config/hypr/hyprland.lua" ;;
esac
