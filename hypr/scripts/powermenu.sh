#!/usr/bin/env bash
# Power menu. Works as a rofi script mode (inside the start menu)
# and standalone:  powermenu.sh --menu   (Win + X)

S="$HOME/.config/hypr/scripts"

if [ "${1:-}" = "--menu" ]; then
    pkill -x rofi && exit 0
    exec rofi -show power -modi "power:$0" -theme "$HOME/.config/rofi/power.rasi"
fi

entries=(
    "Lock|system-lock-screen"
    "Sign out|system-log-out"
    "Sleep|system-suspend"
    "Restart|system-reboot"
    "Shut down|system-shutdown"
)
# Dual boot: restart straight into Windows (only if a Windows boot entry exists)
# efibootmgr is slow (~0.6 s) -> cache the result once
cache="${XDG_CACHE_HOME:-$HOME/.cache}/hypr-has-windows"
if [ ! -f "$cache" ]; then
    if command -v efibootmgr >/dev/null && efibootmgr 2>/dev/null | grep -qi "Windows Boot Manager"
    then echo 1 > "$cache"; else echo 0 > "$cache"; fi
fi
if [ "$(cat "$cache")" = 1 ]; then
    entries+=("Restart to Windows|computer")
fi

run() { setsid -f "$@" >/dev/null 2>&1; }

if [ -z "${1:-}" ]; then
    for e in "${entries[@]}"; do
        printf '%s\0icon\x1f%s\n' "${e%%|*}" "${e##*|}"
    done
    exit 0
fi

case "$1" in
    "Lock")             run sh -c 'sleep 0.2; ~/.config/hypr/scripts/lock.sh' ;;
    "Sign out")         run hyprctl dispatch 'hl.dsp.exit()' ;;
    "Sleep")            run sh -c 'sleep 0.2; systemctl suspend' ;;
    "Restart")          run systemctl reboot ;;
    "Shut down")        run systemctl poweroff ;;
    "Restart to Windows") run "$S/reboot-windows.sh" ;;
esac
exit 0
