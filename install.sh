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

# winterm, the terminal like Windows Terminal (needs vte291-gtk4): the
# "winterm" command and its menu entry
mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"
ln -sf "$REPO/hypr/winshell/winterm.py" "$HOME/.local/bin/winterm"
ln -sf "$REPO/applications/winterm.desktop" "$HOME/.local/share/applications/winterm.desktop"
ln -sf "$REPO/applications/spotify.desktop" "$HOME/.local/share/applications/spotify.desktop"   # sharp on Wayland

icons="$HOME/.local/share/icons/hicolor/scalable/status"
mkdir -p "$icons"
for f in "$REPO"/icons/win11-*.svg; do
    ln -sf "$f" "$icons/$(basename "$f")"
done
gtk-update-icon-cache -f -t -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true   # a stale cache hides new icons
"$REPO/tools/make-icon-theme.sh" >/dev/null || echo "Win11-dark icon theme missing – skipped Win11-modern"

# Windows' UI font for web pages: Selawik standing in for Segoe UI
mkdir -p "$HOME/.local/share/fonts" "$HOME/.config/fontconfig/conf.d"
ln -sfn "$REPO/fonts/selawik" "$HOME/.local/share/fonts/selawik"
ln -sf "$REPO/fontconfig/60-segoe-ui.conf" "$HOME/.config/fontconfig/conf.d/60-segoe-ui.conf"
fc-cache -f "$HOME/.local/share/fonts" >/dev/null 2>&1 || true

# Dolphin like Windows Explorer: toolbar with "Details" / "Large icons"
# (qt/dolphin/dolphinui.rc), every folder remembers its own view, the list
# shows Name, Date modified, Type, Size; "This PC" (desktop:/.thispc, kept
# up to date by winshell/thispc.py – Dolphin files all non-local places
# under view_properties/remote) shows tiles
mkdir -p "$HOME/.local/share/kxmlgui5/dolphin"
ln -sf "$REPO/qt/dolphin/dolphinui.rc" "$HOME/.local/share/kxmlgui5/dolphin/dolphinui.rc"
command -v plasma-apply-colorscheme >/dev/null && plasma-apply-colorscheme BreezeDark >/dev/null 2>&1 || true
if command -v kwriteconfig6 >/dev/null; then
    kwriteconfig6 --file dolphinrc --group General --key GlobalViewProps false
    kwriteconfig6 --file dolphinrc --group CompactMode --key IconSize 64
    kwriteconfig6 --file dolphinrc --group DetailsMode --key IconSize 22
    kwriteconfig6 --file dolphinrc --group DetailsMode --key PreviewSize 22
fi
vp="$HOME/.local/share/dolphin/view_properties"
roles="Icons_text,Compact_text,Details_text,Details_modificationtime,Details_type,Details_size"
mkdir -p "$vp/global" "$vp/remote"
ts=$(date +%Y,%-m,%-d,%-H,%-M,%-S.000)
printf '[Dolphin]\nTimestamp=%s\nVersion=4\nViewMode=0\nVisibleRoles=%s\n' "$ts" "$roles" > "$vp/global/.directory"
printf '[Dolphin]\nTimestamp=%s\nVersion=4\nViewMode=2\nVisibleRoles=%s\n' "$ts" "$roles" > "$vp/remote/.directory"

# no "connected to Wi-Fi" / "disconnected" pop-ups from KDE's network
# service (kded6, it runs along); errors and captive portals still show
if command -v kwriteconfig6 >/dev/null; then
    for e in ConnectionActivated ConnectionDeactivated NoLongerConnected; do
        kwriteconfig6 --file networkmanagement.notifyrc --group "Event/$e" --key Action None
    done
fi

# swaync started by D-Bus uses the same placement as the autostart
mkdir -p "$HOME/.config/systemd/user"
ln -sfn "$REPO/systemd/swaync.service.d" "$HOME/.config/systemd/user/swaync.service.d"
systemctl --user daemon-reload 2>/dev/null || true

echo "Linked configs and icons."
echo "Title bars: run tools/build-hyprbars.sh once (needs podman)."
