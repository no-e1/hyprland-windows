#!/usr/bin/env bash
# Windows-Snap: Fenster an linke/rechte Bildschirmhälfte andocken.
# Benutzung: snap.sh left | right | restore
set -euo pipefail

dir="${1:-left}"
win=$(hyprctl activewindow -j)
[ "$(jq -r '.address // empty' <<<"$win")" ] || exit 0

# Maximiert? Dann erst normalisieren
if [ "$(jq -r '.fullscreen' <<<"$win")" != "0" ]; then
    hyprctl dispatch 'hl.dsp.window.fullscreen({ mode = "maximized", action = "unset" })' >/dev/null
    [ "$dir" = "restore" ] && exit 0
fi

mon=$(hyprctl monitors -j | jq '.[] | select(.focused == true)')
read -r mx my mw mh scale rl rt rr rb < <(jq -r '[.x, .y, .width, .height, .scale, .reserved[0], .reserved[1], .reserved[2], .reserved[3]] | @tsv' <<<"$mon")

# logische Grösse (berücksichtigt Skalierung) minus Taskleiste
read -r W H < <(awk -v w="$mw" -v h="$mh" -v s="$scale" 'BEGIN{printf "%d %d\n", w/s, h/s}')
ax=$((mx + rl)); ay=$((my + rt))
aw=$((W - rl - rr)); ah=$((H - rt - rb))

case "$dir" in
    left)    x=$ax;                y=$ay; w=$((aw / 2)); h=$ah ;;
    right)   x=$((ax + aw / 2));   y=$ay; w=$((aw / 2)); h=$ah ;;
    restore) w=$((aw * 6 / 10));   h=$((ah * 7 / 10))
             x=$((ax + (aw - w) / 2)); y=$((ay + (ah - h) / 2)) ;;
    *) exit 1 ;;
esac

hyprctl dispatch "hl.dsp.window.resize({ x = $w, y = $h })" >/dev/null
hyprctl dispatch "hl.dsp.window.move({ x = $x, y = $y })" >/dev/null
