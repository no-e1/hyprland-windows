#!/usr/bin/env bash
# Set the desktop background – still pictures (jpg, png, webp), shown by swaybg.
#   wallpaper.sh <file>     set a file
#   wallpaper.sh pick       choose from a menu (Win + W): your wallpapers
#                           and a link to find more online (wallhaven.cc)
#   wallpaper.sh next       next picture in the folder
#   wallpaper.sh restore    restore the last one (autostart)
#
# Folder: ~/Pictures/Wallpapers  (just drop your own pictures in there) –
# only pictures from there can become the background (not from Downloads)

WALLDIR="$(xdg-user-dir PICTURES 2>/dev/null || echo "$HOME/Pictures")/Wallpapers"
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

apply() {
    local f="$1"
    [ -f "$f" ] || { notify-send "Background" "File not found: $f"; return 1; }
    # only pictures from the wallpaper folder (not Downloads or anywhere else)
    if [ "$(dirname "$(realpath "$f")")" != "$(realpath "$WALLDIR")" ]; then
        notify-send -i preferences-desktop-wallpaper "Background" \
            "Only pictures in Pictures/Wallpapers can be the background."
        return 1
    fi
    pkill -x swaybg 2>/dev/null
    sleep 0.2
    setsid -f swaybg -m fill -i "$f" >/dev/null 2>&1
    printf '%s\n' "$f" > "$STATE"
}

case "${1:-restore}" in
    restore)
        f=$(cat "$STATE" 2>/dev/null)
        # gone, or not from the wallpaper folder: the first one there
        { [ -f "$f" ] && [ "$(dirname "$(realpath "$f")")" = "$(realpath "$WALLDIR")" ]; } || f=$(list_walls | head -n1)
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
                    # own sharp icon (the theme's web-browser one was a blurry bitmap)
                    printf '%s\0icon\x1f%s\n' "$WEB_ENTRY" "$HOME/.local/share/icons/hicolor/scalable/status/win11-find-online.svg"
                    list_walls | while read -r f; do entry "$f" ""; done
                  } | rofi -dmenu -i -p "Background" -show-icons -theme "$HOME/.config/rofi/wallpaper.rasi" -theme-str 'entry { placeholder: "Search backgrounds"; }')
        [ -z "$choice" ] && exit 0
        if [ "$choice" = "$WEB_ENTRY" ]; then
            xdg-open "$ONLINE" >/dev/null 2>&1 &
            notify-send -i preferences-desktop-wallpaper "Background" \
                "Save a picture you like into Pictures/Wallpapers – then it shows up under Win + W."
            exit 0
        fi
        apply "$WALLDIR/$choice" ;;
    *)
        apply "$(realpath "$1")" ;;
esac
