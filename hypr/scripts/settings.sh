#!/usr/bin/env bash
# "Einstellungen" (Win + I): Sammelmenü für alle Einstellungs-Tools
S="$HOME/.config/hypr/scripts"
pkill -x rofi && exit 0

has() { command -v "$1" >/dev/null; }

items=()
items+=("󰖩  WLAN")
items+=("󰂯  Bluetooth")
items+=("󰕾  Sound")
has wdisplays && items+=("󰍹  Bildschirme")
items+=("󰸉  Hintergrundbild")
has nwg-look && items+=("󰏘  Design (Farben, Schrift)")
items+=("󰛳  Netzwerkverbindungen")
items+=("󰍛  Task-Manager")
items+=("󰒓  Hyprland-Konfiguration bearbeiten")

choice=$(printf '%s\n' "${items[@]}" | rofi -dmenu -i -p "Einstellungen" -theme "$HOME/.config/rofi/list.rasi")

case "$choice" in
    *WLAN)                exec "$S/wifimenu.sh" ;;
    *Bluetooth)           exec blueman-manager ;;
    *Sound)               exec pavucontrol ;;
    *Bildschirme)         exec wdisplays ;;
    *Hintergrundbild)     exec "$S/wallpaper.sh" pick ;;
    *Design*)             exec nwg-look ;;
    *Netzwerkverbindungen) exec nm-connection-editor ;;
    *Task-Manager)        exec gnome-system-monitor ;;
    *Konfiguration*)      exec xdg-open "$HOME/.config/hypr/hyprland.lua" ;;
esac
