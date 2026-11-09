#!/usr/bin/env bash
# Screenshots like the Windows Snipping Tool: copied to clipboard AND saved as a file.
dir="$(xdg-user-dir PICTURES 2>/dev/null || echo "$HOME/Pictures")/Screenshots"
mkdir -p "$dir"
file="$dir/Screenshot_$(date +%Y-%m-%d_%H-%M-%S).png"

# only one screenshot at a time: pressing Print again while the region
# selection is open does nothing (otherwise every press stacked another
# darkened overlay and the picture came out black)
exec 9>"${XDG_RUNTIME_DIR:-/tmp}/screenshot.lock"
flock -n 9 || exit 0

case "${1:-area}" in
    area) geo=$(slurp -d -b 00000088 -c 4cc2ffff -w 2) || exit 0
          grim -g "$geo" "$file" ;;
    full) grim "$file" ;;
esac
# picture taken: free the lock before wl-copy, which stays running in the
# background to serve the clipboard and would otherwise keep holding it
exec 9>&-

[ -f "$file" ] || exit 1
wl-copy < "$file"
# like Windows: app icon next to the text, the picture itself big below it
notify-send -a Screenshot -i win11-snip "Screenshot copied" \
    "Saved to $(basename "$dir")<img src=\"$file\" alt=\"Screenshot\"/>"
