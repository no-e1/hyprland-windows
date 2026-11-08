#!/usr/bin/env bash
# Notification center: placed right above the calendar (winshell/ncplace.py).
conf=$(python3 -I "$HOME/.config/hypr/winshell/ncplace.py") || exec swaync
exec swaync -c "$conf"
