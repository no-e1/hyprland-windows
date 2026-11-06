#!/usr/bin/env bash
# Hintergrundbild setzen – Bilder, GIFs UND Videos (animiert).
#   wallpaper.sh <datei>    setzt eine Datei
#   wallpaper.sh pick       Auswahl per Menü (Win + W)
#   wallpaper.sh next       nächstes Bild im Ordner
#   wallpaper.sh restore    letztes Bild wiederherstellen (Autostart)
#
# Ordner: ~/Bilder/Wallpapers  (einfach eigene Bilder/Videos hineinlegen)

WALLDIR="$(xdg-user-dir PICTURES 2>/dev/null || echo "$HOME/Bilder")/Wallpapers"
STATE="$HOME/.config/hypr/.current_wallpaper"
mkdir -p "$WALLDIR"

list_walls() {
    find "$WALLDIR" -maxdepth 1 -type f \
        \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \
           -o -iname '*.gif' -o -iname '*.mp4' -o -iname '*.webm' -o -iname '*.mkv' \) \
        ! -iname '*.poster.*' | sort
}

is_video() { [[ "${1,,}" =~ \.(mp4|webm|mkv|gif)$ ]]; }

stop_all() {
    pkill -x mpvpaper 2>/dev/null
    pkill -x swaybg   2>/dev/null
    sleep 0.2
}

apply() {
    local f="$1"
    [ -f "$f" ] || { notify-send "Hintergrund" "Datei nicht gefunden: $f"; return 1; }
    stop_all
    if command -v mpvpaper >/dev/null; then
        # Video/GIF animiert, Bild statisch – alles über mpv (Hardware-Decoding)
        local opts="no-audio --loop-file=inf --panscan=1.0 --hwdec=auto --vo=gpu --profile=fast --really-quiet"
        is_video "$f" || opts="$opts --image-display-duration=inf"
        setsid -f mpvpaper -p -o "$opts" ALL "$f" >/dev/null 2>&1
    else
        # Fallback ohne mpvpaper: statisches Bild (bei Videos das Vorschaubild)
        local img="$f"
        if is_video "$f"; then
            img="${f%.*}.poster.jpg"
            [ -f "$img" ] || ffmpeg -loglevel error -y -i "$f" -frames:v 1 "$img" 2>/dev/null
        fi
        setsid -f swaybg -m fill -i "$img" >/dev/null 2>&1
    fi
    printf '%s\n' "$f" > "$STATE"
}

case "${1:-restore}" in
    restore)
        f=$(cat "$STATE" 2>/dev/null)
        [ -f "$f" ] || f=$(list_walls | head -n1)
        [ -n "$f" ] && apply "$f" ;;
    next)
        cur=$(cat "$STATE" 2>/dev/null)
        mapfile -t all < <(list_walls)
        [ ${#all[@]} -eq 0 ] && exit 0
        idx=0
        for i in "${!all[@]}"; do [ "${all[$i]}" = "$cur" ] && idx=$(( (i + 1) % ${#all[@]} )); done
        apply "${all[$idx]}" ;;
    pick)
        pkill -x rofi && exit 0
        choice=$(list_walls | while read -r f; do
                    name=$(basename "$f")
                    icon="$f"
                    if is_video "$f"; then
                        p="${f%.*}.poster.jpg"; [ -f "$p" ] && icon="$p"
                        name="▶  $name"
                    fi
                    printf '%s\0icon\x1f%s\n' "$name" "$icon"
                 done | rofi -dmenu -i -p "Hintergrund" -show-icons -theme "$HOME/.config/rofi/wallpaper.rasi")
        [ -z "$choice" ] && exit 0
        apply "$WALLDIR/${choice#▶  }" ;;
    *)
        apply "$(realpath "$1")" ;;
esac
