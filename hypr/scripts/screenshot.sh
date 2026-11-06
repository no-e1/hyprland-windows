#!/usr/bin/env bash
# Screenshots wie das Windows-Snipping-Tool: in die Zwischenablage UND als Datei.
dir="$(xdg-user-dir PICTURES 2>/dev/null || echo "$HOME/Bilder")/Bildschirmfotos"
mkdir -p "$dir"
file="$dir/Screenshot_$(date +%Y-%m-%d_%H-%M-%S).png"

case "${1:-area}" in
    area) geo=$(slurp -d -b 00000088 -c 4cc2ffff -w 2) || exit 0
          grim -g "$geo" "$file" ;;
    full) grim "$file" ;;
esac

[ -f "$file" ] || exit 1
wl-copy < "$file"
notify-send -i "$file" "Bildschirmfoto kopiert" "Gespeichert in $(basename "$dir")"
