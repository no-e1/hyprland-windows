#!/usr/bin/env bash
# Lock the screen like Windows 11 (Win + L, idle, power menu, quick settings):
# winshell/lockscreen.py – the lock screen with time and date, any key shows
# the sign-in with the profile picture. Should it ever fail, hyprlock takes
# over the lock (misc.allow_session_lock_restore), so the screen never ends
# up unlocked or stuck.
# Profile picture: ~/.face (falls back to the system account picture).
#
# Instant: a lock screen waits ready in the background (lockscreen.py
# --standby, everything loaded) and only gets a signal – starting Python and
# GTK first took a noticeable moment. After each unlock the next one is
# started.
#   lock.sh            lock now
#   lock.sh --standby  only start the waiting lock screen (Hyprland autostart)
HERE="$(dirname "$(realpath "$0")")"
RUN="${XDG_RUNTIME_DIR:-/tmp}"
PIDFILE="$RUN/lockscreen-standby.pid"
RESULT="$RUN/lockscreen.result"
LOG="$RUN/lockscreen.log"

standby_pid() {
    local p; p=$(cat "$PIDFILE" 2>/dev/null)
    [ -n "$p" ] && grep -q "lockscreen.py" "/proc/$p/cmdline" 2>/dev/null && echo "$p"
}
start_standby() {
    [ -n "$(standby_pid)" ] && return
    # 9>&- : the waiting lock screen must not hold the "locking" lock below
    LD_PRELOAD=/usr/lib64/libgtk4-layer-shell.so.0 setsid -f \
        python3 "$HERE/../winshell/lockscreen.py" --standby 9>&- 2>>"$LOG"
}

if [ "${1:-}" = "--standby" ]; then
    start_standby
    exit 0
fi

# locked already (Win + L and the idle timer, or two presses): nothing to do
exec 9>"$RUN/lock.sh.lock"
flock -n 9 || exit 0
pidof hyprlock >/dev/null && exit 0

dir="$HOME/.cache/hyprlock"
mkdir -p "$dir"
# background: the current wallpaper – set before the lock screen is woken,
# it reads it right then
img=$(cat "$HOME/.config/hypr/.current_wallpaper" 2>/dev/null)
[ -f "$img" ] && ln -sfn "$img" "$dir/wallpaper"
# profile picture
for p in "$HOME/.face" "$HOME/.face.icon" "/var/lib/AccountsService/icons/$USER"; do
    [ -f "$p" ] && { ln -sfn "$p" "$dir/face"; break; }
done

pid=$(standby_pid)
rm -f "$RESULT"
if [ -n "$pid" ]; then
    kill -USR1 "$pid"                      # locks right now
    tail --pid="$pid" -f /dev/null        # until it unlocks (or dies)
    [ "$(cat "$RESULT" 2>/dev/null)" = unlocked ] && ok=1
else
    # no waiting lock screen (first lock, or it crashed): start one directly
    LD_PRELOAD=/usr/lib64/libgtk4-layer-shell.so.0 python3 "$HERE/../winshell/lockscreen.py" \
        2>>"$LOG" && ok=1
fi
rm -f "$RESULT"

start_standby                              # ready for the next Win + L
[ -n "${ok:-}" ] && exit 0
# not unlocked properly: keep the screen locked with hyprlock
exec hyprlock
