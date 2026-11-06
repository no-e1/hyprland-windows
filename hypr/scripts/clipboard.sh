#!/usr/bin/env bash
# Zwischenablage-Verlauf (Win + V)
pkill -x rofi && exit 0
if ! command -v cliphist >/dev/null; then
    notify-send "Zwischenablage" "cliphist ist nicht installiert – Verlauf nicht verfügbar."
    exit 0
fi
sel=$(cliphist list | rofi -dmenu -i -p "Zwischenablage" -display-columns 2 -theme "$HOME/.config/rofi/list.rasi")
[ -n "$sel" ] && cliphist decode <<<"$sel" | wl-copy
