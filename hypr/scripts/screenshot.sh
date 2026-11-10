#!/usr/bin/env bash
# Screenshots like the Windows Snipping Tool: copied to clipboard AND saved
# as a file (no notification).
#   screenshot.sh area   pick a region; the screen holds still meanwhile
#   screenshot.sh full   the whole screen
dir="$(xdg-user-dir PICTURES 2>/dev/null || echo "$HOME/Pictures")/Screenshots"
mkdir -p "$dir"
file="$dir/Screenshot_$(date +%Y-%m-%d_%H-%M-%S).png"
RUN="${XDG_RUNTIME_DIR:-/tmp}"
winshell="$HOME/.config/hypr/winshell/winshell-ctl"

# only one screenshot at a time: pressing Print again while the region
# selection is open does nothing (otherwise every press stacked another
# darkened overlay and the picture came out black)
exec 9>"$RUN/screenshot.lock"
flock -n 9 || exit 0

case "${1:-area}" in
    area)
        # Freeze the screen like Windows: take a picture of every monitor right
        # away, show it on top (winshell) while slurp picks the region, then
        # take the region from that still picture – a playing video keeps
        # running underneath but holds still in the picture.
        tmp=$(mktemp -d "$RUN/screenshot.XXXXXX")
        trap '"$winshell" freeze off; rm -rf "$tmp"' EXIT
        pairs=()
        for m in $(hyprctl monitors | sed -n 's/^Monitor \([^ ]*\).*/\1/p'); do
            # -l 0: no compression – twice as fast, it's only shown, not kept
            grim -l 0 -o "$m" "$tmp/$m.png" && pairs+=("$m=$tmp/$m.png")
        done
        "$winshell" freeze "${pairs[@]}"
        # wait until the still picture is really on screen before dimming it
        for _ in $(seq 1 50); do
            hyprctl layers | grep -q "namespace: winshell-freeze" && break
            sleep 0.01
        done
        geo=$(slurp -d -b 00000088 -c 4cc2ffff -w 2) || exit 0
        # the frozen picture is what's on screen now: cut the region from it
        grim -g "$geo" "$file" ;;
    full) grim "$file" ;;
esac
# picture taken: free the lock before wl-copy, which stays running in the
# background to serve the clipboard and would otherwise keep holding it
exec 9>&-

[ -f "$file" ] || exit 1
wl-copy < "$file"
