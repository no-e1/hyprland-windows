#!/usr/bin/env bash
# Applies the Windows 11 look to GTK apps on every Hyprland start
# (Plasma may overwrite these values on its own login).
conf="$HOME/.config/hypr/theme.env"
[ -f "$conf" ] && . "$conf"

g() { gsettings set org.gnome.desktop.interface "$1" "$2" 2>/dev/null; }

[ -n "${GTK_THEME_NAME:-}" ]  && g gtk-theme    "$GTK_THEME_NAME"
[ -n "${ICON_THEME_NAME:-}" ] && g icon-theme   "$ICON_THEME_NAME"
[ -n "${CURSOR_THEME:-}" ]    && g cursor-theme "$CURSOR_THEME"
[ -n "${UI_FONT:-}" ]         && g font-name    "$UI_FONT"
g color-scheme  "prefer-dark"
g cursor-size   24
gsettings set org.gnome.desktop.wm.preferences button-layout ':maximize,close' 2>/dev/null

if [ -n "${CURSOR_THEME:-}" ]; then
    hyprctl setcursor "$CURSOR_THEME" 24 >/dev/null 2>&1
fi
