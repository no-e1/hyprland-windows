#!/usr/bin/env bash
# Startmenü öffnen bzw. schliessen (Windows-Taste)
S="$HOME/.config/hypr/scripts"
if pkill -x rofi; then exit 0; fi
exec rofi -show drun \
    -modi "drun,power:$S/powermenu.sh" \
    -theme "$HOME/.config/rofi/startmenu.rasi"
