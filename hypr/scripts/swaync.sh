#!/usr/bin/env bash
# Notification center: placed right above the calendar (winshell/ncplace.py).
# Prefers the build with Windows-like close animations (tools/build-swaync.sh).
bin=swaync
own="$HOME/.local/share/hypr-plugins/swaync"
[ -x "$own" ] && "$own" --version >/dev/null 2>&1 && bin="$own"
conf=$(python3 -I "$HOME/.config/hypr/winshell/ncplace.py") || exec "$bin"
exec "$bin" -c "$conf"
