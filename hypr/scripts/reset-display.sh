#!/usr/bin/env bash
# Ctrl + Win + Shift + B, like Windows' "reset the graphics driver": when a
# screen hangs (a still picture, black, no taskbar) this brings everything
# back with one key press – it works even if winshell itself hangs:
#   1. Hyprland reloads its config (screens as saved in Display settings)
#   2. every external screen is switched off and on again (that is what
#      unfreezes a stuck screen), the laptop's own screen is switched on
#   3. winshell (taskbar, menus) restarts
exec 9>"${XDG_RUNTIME_DIR:-/tmp}/reset-display.lock"
flock -n 9 || exit 0                    # pressed twice: once is enough
W="$HOME/.config/hypr/winshell"

hyprctl reload >/dev/null 2>&1
sleep 0.5

# "name<TAB>hl.monitor() table" for every connected screen (displays.py
# knows the saved layout; "disabled = false" switches a screen back on)
specs=$(python3 -I - "$W" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import displays
for m in displays.all_monitors():
    print(m["name"], displays.on_spec(dict(m, disabled=True)), sep="\t")
PY
)
while IFS=$'\t' read -r name spec; do
    [ -n "$name" ] || continue
    case "$name" in
        eDP*) ;;                        # the laptop's own screen stays on meanwhile
        *) hyprctl eval "hl.monitor({ output = \"$name\", disabled = true })" >/dev/null 2>&1 ;;
    esac
done <<<"$specs"
sleep 1
while IFS=$'\t' read -r name spec; do
    [ -n "$name" ] && hyprctl eval "hl.monitor($spec)" >/dev/null 2>&1
done <<<"$specs"
hyprctl dispatch 'hl.dsp.dpms({ action = "enable" })' >/dev/null 2>&1
sleep 1.5

"$W/winshell-ctl" restart
notify-send -a "Display" -i video-display "Display reset" "Screens and taskbar were restarted." 2>/dev/null
