#!/usr/bin/env bash
# Ein/Aus-Menü. Funktioniert als Rofi-Skriptmodus (im Startmenü)
# und eigenständig mit:  powermenu.sh --menu   (Win + X)

S="$HOME/.config/hypr/scripts"

if [ "${1:-}" = "--menu" ]; then
    pkill -x rofi && exit 0
    exec rofi -show power -modi "power:$0" -theme "$HOME/.config/rofi/power.rasi"
fi

entries=(
    "Sperren|system-lock-screen"
    "Abmelden|system-log-out"
    "Energie sparen|system-suspend"
    "Neu starten|system-reboot"
    "Herunterfahren|system-shutdown"
)
# Dual-Boot: direkt in Windows neu starten (nur wenn ein Windows-Booteintrag existiert)
# efibootmgr ist langsam (~0.6 s) -> Ergebnis einmalig zwischenspeichern
cache="${XDG_CACHE_HOME:-$HOME/.cache}/hypr-has-windows"
if [ ! -f "$cache" ]; then
    if command -v efibootmgr >/dev/null && efibootmgr 2>/dev/null | grep -qi "Windows Boot Manager"
    then echo 1 > "$cache"; else echo 0 > "$cache"; fi
fi
if [ "$(cat "$cache")" = 1 ]; then
    entries+=("In Windows neu starten|computer")
fi

run() { setsid -f "$@" >/dev/null 2>&1; }

if [ -z "${1:-}" ]; then
    for e in "${entries[@]}"; do
        printf '%s\0icon\x1f%s\n' "${e%%|*}" "${e##*|}"
    done
    exit 0
fi

case "$1" in
    "Sperren")          run sh -c 'sleep 0.2; pidof hyprlock || hyprlock' ;;
    "Abmelden")         run hyprctl dispatch 'hl.dsp.exit()' ;;
    "Energie sparen")   run sh -c 'sleep 0.2; systemctl suspend' ;;
    "Neu starten")      run systemctl reboot ;;
    "Herunterfahren")   run systemctl poweroff ;;
    "In Windows neu starten") run "$S/reboot-windows.sh" ;;
esac
exit 0
