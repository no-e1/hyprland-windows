#!/usr/bin/env bash
# Lock the screen like Windows 11 (Win + L, idle, power menu, quick settings):
# winshell/lockscreen.py – the lock screen with time and date, any key shows
# the sign-in with the profile picture. Should it ever fail, hyprlock takes
# over the lock (misc.allow_session_lock_restore), so the screen never ends
# up unlocked or stuck.
# Profile picture: ~/.face (falls back to the system account picture).
pidof hyprlock >/dev/null && exit 0
pgrep -f "winshell/lockscreen.py" >/dev/null && exit 0
HERE="$(dirname "$(realpath "$0")")"
dir="$HOME/.cache/hyprlock"
mkdir -p "$dir"

# background: the current wallpaper (a still frame for videos / GIFs)
f=$(cat "$HOME/.config/hypr/.current_wallpaper" 2>/dev/null)
img="$f"
if [[ "${f,,}" =~ \.(mp4|webm|mkv|gif)$ ]]; then
    img="${f%.*}.poster.jpg"
    [ -f "$img" ] || ffmpeg -loglevel error -y -i "$f" -frames:v 1 "$img" 2>/dev/null
fi
[ -f "$img" ] && ln -sfn "$img" "$dir/wallpaper"

# profile picture
for p in "$HOME/.face" "$HOME/.face.icon" "/var/lib/AccountsService/icons/$USER"; do
    [ -f "$p" ] && { ln -sfn "$p" "$dir/face"; break; }
done

LD_PRELOAD=/usr/lib64/libgtk4-layer-shell.so.0 python3 "$HERE/../winshell/lockscreen.py" \
    2>>"${XDG_RUNTIME_DIR:-/tmp}/lockscreen.log" && exit 0
# not unlocked properly: keep the screen locked with hyprlock
exec hyprlock
