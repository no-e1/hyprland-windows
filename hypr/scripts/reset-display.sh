#!/usr/bin/env bash
# Ctrl + Win + Shift + B, like Windows' "reset the graphics driver": when a
# screen hangs (a still picture, black, no taskbar) this brings everything
# back with one key press – it works even if winshell itself hangs:
#   1. Hyprland reloads its config (screens as saved in Display settings)
#   2. every external screen is switched off and on again (that is what
#      unfreezes a stuck screen), the laptop's own screen is switched on
#   3. winshell (taskbar, menus) restarts
#   4. Wi-Fi that is on but not connected gets its card reset, a missing
#      Bluetooth adapter is reloaded (wifi-recover.service, runs alongside;
#      installed by tools/wifi-resume/install.sh, no password needed)
exec 9>"${XDG_RUNTIME_DIR:-/tmp}/reset-display.lock"
flock -n 9 || exit 0                    # pressed twice: once is enough
W="$HOME/.config/hypr/winshell"

repair_radios() {
    wifi_bad=0
    nmcli radio wifi 2>/dev/null | grep -q enabled &&
        ! nmcli -t -f TYPE,STATE dev 2>/dev/null | grep -q '^wifi:connected$' && wifi_bad=1
    [ -n "$(ls /sys/class/bluetooth 2>/dev/null)" ] && [ "$wifi_bad" = 0 ] && return
    if ! systemctl cat wifi-recover.service >/dev/null 2>&1; then
        notify-send -a "Wi-Fi" -i network-wireless-offline "Wi-Fi repair not installed" \
            "Run once: sudo tools/wifi-resume/install.sh" 2>/dev/null
        return
    fi
    notify-send -a "Wi-Fi" -i network-wireless "Repairing Wi-Fi…" "The Wi-Fi card is being reset." 2>/dev/null
    if systemctl start wifi-recover.service; then
        # connected again within ~30 s?
        for _ in $(seq 1 30); do
            nmcli -t -f TYPE,STATE dev 2>/dev/null | grep -q '^wifi:connected$' && break
            sleep 1
        done
        if nmcli -t -f TYPE,STATE dev 2>/dev/null | grep -q '^wifi:connected$'; then
            notify-send -a "Wi-Fi" -i network-wireless "Wi-Fi repaired" "Connected again." 2>/dev/null
        else
            notify-send -a "Wi-Fi" -i network-wireless "Wi-Fi card was reset" \
                "Networks should show up again – pick one in Quick Settings." 2>/dev/null
        fi
    else
        notify-send -a "Wi-Fi" -i network-wireless-offline "Wi-Fi repair failed" \
            "See: journalctl -u wifi-recover" 2>/dev/null
    fi
}
repair_radios 9>&- &

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
