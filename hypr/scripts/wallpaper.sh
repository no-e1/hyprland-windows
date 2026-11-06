#!/usr/bin/env bash
# Set the desktop background – images, GIFs AND videos (animated).
#   wallpaper.sh <file>     set a file
#   wallpaper.sh pick       choose from a menu (Win + W): your wallpapers,
#                           pictures/videos you downloaded lately, and a link
#                           to find more online (wallhaven.cc)
#   wallpaper.sh next       next image in the folder
#   wallpaper.sh restore    restore the last one (autostart)
#
# Folder: ~/Pictures/Wallpapers  (just drop your own images/videos in there)

WALLDIR="$(xdg-user-dir PICTURES 2>/dev/null || echo "$HOME/Pictures")/Wallpapers"
DLDIR="$(xdg-user-dir DOWNLOAD 2>/dev/null || echo "$HOME/Downloads")"
ONLINE="https://wallhaven.cc/search?categories=110&purity=100&atleast=2560x1600&ratios=16x10&sorting=toplist&topRange=1M"
WEB_ENTRY="🌐  Find more online…"
STATE="$HOME/.config/hypr/.current_wallpaper"
mkdir -p "$WALLDIR"

media() {   # media <dir> [extra find args]
    find "$1" -maxdepth 1 -type f \
        \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' \
           -o -iname '*.gif' -o -iname '*.mp4' -o -iname '*.webm' -o -iname '*.mkv' \) \
        ! -iname '*.poster.*' "${@:2}"
}
list_walls() { media "$WALLDIR" | sort; }
# pictures/videos downloaded in the last 30 days, newest first
list_downloads() {
    [ -d "$DLDIR" ] || return 0
    media "$DLDIR" -mtime -30 -printf '%T@ %p\n' | sort -rn | cut -d' ' -f2-
}

is_video() { [[ "${1,,}" =~ \.(mp4|webm|mkv|gif)$ ]]; }

stop_all() {
    pkill -x mpvpaper 2>/dev/null
    pkill -x swaybg   2>/dev/null
    sleep 0.2
}

apply() {
    local f="$1"
    [ -f "$f" ] || { notify-send "Background" "File not found: $f"; return 1; }
    stop_all
    if command -v mpvpaper >/dev/null; then
        # video/GIF animated, image static – all through mpv (hardware decoding)
        local opts="no-audio --loop-file=inf --panscan=1.0 --hwdec=auto --vo=gpu --profile=fast --really-quiet"
        is_video "$f" || opts="$opts --image-display-duration=inf"
        setsid -f mpvpaper -p -o "$opts" ALL "$f" >/dev/null 2>&1
    else
        # fallback without mpvpaper: static image (poster frame for videos)
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
        entry() {   # entry <file> <prefix>
            local f="$1" name icon p
            name="$2$(basename "$f")"; icon="$f"
            if is_video "$f"; then
                p="${f%.*}.poster.jpg"; [ -f "$p" ] && icon="$p"
                name="$2▶  $(basename "$f")"
            fi
            printf '%s\0icon\x1f%s\n' "$name" "$icon"
        }
        choice=$( {
                    printf '%s\0icon\x1f%s\n' "$WEB_ENTRY" "web-browser"
                    list_walls | while read -r f; do entry "$f" ""; done
                    list_downloads | while read -r f; do entry "$f" "⬇  "; done
                  } | rofi -dmenu -i -p "Background" -show-icons -theme "$HOME/.config/rofi/wallpaper.rasi")
        [ -z "$choice" ] && exit 0
        if [ "$choice" = "$WEB_ENTRY" ]; then
            xdg-open "$ONLINE" >/dev/null 2>&1 &
            notify-send -i preferences-desktop-wallpaper "Background" \
                "Download a picture you like – it shows up under Win + W with ⬇. Picking it there copies it to Pictures/Wallpapers."
            exit 0
        fi
        name="${choice#⬇  }"; name="${name#▶  }"
        if [[ "$choice" == "⬇  "* ]]; then
            # a download: keep a copy with the other wallpapers, then use it
            cp -n "$DLDIR/$name" "$WALLDIR/$name"
        fi
        apply "$WALLDIR/$name" ;;
    *)
        apply "$(realpath "$1")" ;;
esac
