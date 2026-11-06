#!/usr/bin/env bash
# Clipboard history (Win + V)
pkill -x rofi && exit 0
if ! command -v cliphist >/dev/null; then
    notify-send "Clipboard" "cliphist is not installed – history unavailable."
    exit 0
fi
sel=$(cliphist list | rofi -dmenu -i -p "Clipboard" -display-columns 2 -theme "$HOME/.config/rofi/list.rasi")
[ -n "$sel" ] && cliphist decode <<<"$sel" | wl-copy
