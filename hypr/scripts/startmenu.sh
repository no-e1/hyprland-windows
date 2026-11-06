#!/usr/bin/env bash
# Open/close the start menu (Windows key). Clicking outside closes it.
S="$HOME/.config/hypr/scripts"
CTL="$HOME/.config/hypr/winshell/winshell-ctl"
if pkill -x rofi; then exit 0; fi
"$CTL" backdrop rofi
rofi -show drun \
    -modi "drun,power:$S/powermenu.sh" \
    -theme "$HOME/.config/rofi/startmenu.rasi"
"$CTL" backdrop off
