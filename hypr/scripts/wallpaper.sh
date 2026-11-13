#!/usr/bin/env bash
# Set the desktop background – still pictures (jpg, png, webp), shown by swaybg.
#   wallpaper.sh <file>     set a file
#   wallpaper.sh pick       choose from a menu (Win + W): your wallpapers,
#                           pictures you downloaded lately, and a link
#                           to find more online (wallhaven.cc)
#   wallpaper.sh next       next picture in the folder
#   wallpaper.sh restore    restore the last one (autostart)
#
# Folder: ~/Pictures/Wallpapers  (just drop your own pictures in there)

WALLDIR="$(xdg-user-dir PICTURES 2>/dev/null || echo "$HOME/Pictures")/Wallpapers"
DLDIR="$(xdg-user-dir DOWNLOAD 2>/dev/null || echo "$HOME/Downloads")"
ONLINE="https://wallhaven.cc/search?categories=110&purity=100&atleast=2560x1600&ratios=16x10&sorting=toplist&topRange=1M"
WEB_ENTRY="🌐  Find more online…"
STATE="$HOME/.config/hypr/.current_wallpaper"
mkdir -p "$WALLDIR"

pictures() {   # pictures <dir> [extra find args]
    find "$1" -maxdepth 1 -type f \
        \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \) \
        ! -iname '*.poster.*' "${@:2}"
}
list_walls() { pictures "$WALLDIR" | sort; }
# pictures downloaded in the last 30 days, newest first
list_downloads() {
    [ -d "$DLDIR" ] || return 0
    pictures "$DLDIR" -mtime -30 -printf '%T@ %p\n' | sort -rn | cut -d' ' -f2-
}

apply() {
    local f="$1"
    [ -f "$f" ] || { notify-send "Background" "File not found: $f"; return 1; }
    pkill -x swaybg 2>/dev/null
    sleep 0.2
    setsid -f swaybg -m fill -i "$f" >/dev/null 2>&1
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
        entry() {   # entry <file> <prefix>: the picture is its own icon
            printf '%s\0icon\x1f%s\n' "$2$(basename "$1")" "$1"
        }
        choice=$( {
                    printf '%s\0icon\x1f%s\n' "$WEB_ENTRY" "web-browser"
                    list_walls | while read -r f; do entry "$f" ""; done
                    list_downloads | while read -r f; do entry "$f" "⬇  "; done
                  } | rofi -dmenu -i -p "Background" -show-icons -theme "$HOME/.config/rofi/wallpaper.rasi" -theme-str 'entry { placeholder: "Search backgrounds"; }')
        [ -z "$choice" ] && exit 0
        if [ "$choice" = "$WEB_ENTRY" ]; then
            xdg-open "$ONLINE" >/dev/null 2>&1 &
            notify-send -i preferences-desktop-wallpaper "Background" \
                "Download a picture you like – it shows up under Win + W with ⬇. Picking it there copies it to Pictures/Wallpapers."
            exit 0
        fi
        name="${choice#⬇  }"
        if [[ "$choice" == "⬇  "* ]]; then
            # a download: keep a copy with the other wallpapers, then use it
            cp -n "$DLDIR/$name" "$WALLDIR/$name"
        fi
        apply "$WALLDIR/$name" ;;
    *)
        apply "$(realpath "$1")" ;;
esac
