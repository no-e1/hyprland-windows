#!/usr/bin/env bash
# Screenshots like the Windows Snipping Tool: copied to clipboard AND saved as a file.
dir="$(xdg-user-dir PICTURES 2>/dev/null || echo "$HOME/Pictures")/Screenshots"
mkdir -p "$dir"
file="$dir/Screenshot_$(date +%Y-%m-%d_%H-%M-%S).png"

case "${1:-area}" in
    area) geo=$(slurp -d -b 00000088 -c 4cc2ffff -w 2) || exit 0
          grim -g "$geo" "$file" ;;
    full) grim "$file" ;;
esac

[ -f "$file" ] || exit 1
wl-copy < "$file"
notify-send -i "$file" "Screenshot copied" "Saved to $(basename "$dir")"
