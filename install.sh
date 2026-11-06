#!/usr/bin/env bash
# Links this repo into ~/.config and installs the icons.
# Existing config folders are moved to <name>.bak-<timestamp>.
set -euo pipefail
REPO="$(cd "$(dirname "$0")" && pwd)"
ts=$(date +%Y%m%d-%H%M%S)

for d in hypr waybar rofi swaync kitty; do
    target="$HOME/.config/$d"
    if [ -L "$target" ]; then
        rm "$target"
    elif [ -e "$target" ]; then
        mv "$target" "$target.bak-$ts"
    fi
    ln -s "$REPO/$d" "$target"
done

icons="$HOME/.local/share/icons/hicolor/scalable/status"
mkdir -p "$icons"
for f in "$REPO"/icons/win11-*.svg; do
    ln -sf "$f" "$icons/$(basename "$f")"
done
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
"$REPO/tools/make-icon-theme.sh" >/dev/null || echo "Win11-dark icon theme missing – skipped Win11-modern"

echo "Linked configs and icons."
echo "Title bars: run tools/build-hyprbars.sh once (needs podman)."
