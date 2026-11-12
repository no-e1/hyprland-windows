#!/usr/bin/env bash
# Tray status for Waybar (JSON with a CSS class that selects the icon).
#   status.sh net   – Wi-Fi / Ethernet, updates on network changes
#   status.sh vol   – speaker volume, updates on audio changes
#   status.sh bat   – battery level, updates when the charger is plugged in/out

json() { printf '{"text":" ","class":"%s","tooltip":"%s"}\n' "$1" "$2"; }

net() {
    local eth wifi sig ssid lvl
    eth=$(nmcli -t -f TYPE,STATE dev 2>/dev/null | grep -c '^ethernet:connected$')
    wifi=$(nmcli -t -f ACTIVE,SIGNAL,SSID dev wifi list --rescan no 2>/dev/null | grep '^yes:' | head -n1)
    if [ "$eth" -gt 0 ]; then
        json ethernet "Ethernet\\nInternet access"
    elif [ -n "$wifi" ]; then
        sig=$(cut -d: -f2 <<<"$wifi"); ssid=$(cut -d: -f3- <<<"$wifi" | sed 's/\\:/:/g; s/"/\\"/g')
        lvl=$(( sig >= 75 ? 4 : sig >= 50 ? 3 : sig >= 25 ? 2 : 1 ))
        json "wifi-$lvl" "$ssid\\nInternet access"
    else
        json wifi-off "Not connected"
    fi
}

vol() {
    local out v
    out=$(wpctl get-volume @DEFAULT_AUDIO_SINK@ 2>/dev/null)
    v=$(awk '{printf "%d", $2 * 100 + 0.5}' <<<"$out")
    if [[ "$out" == *MUTED* ]] || [ "${v:-0}" -eq 0 ]; then
        json mute "Speakers: muted"
    else
        json "vol-$(( v < 34 ? 1 : v < 67 ? 2 : 3 ))" "Speakers: $v%"
    fi
}

bat() {
    local b=/sys/class/power_supply/BAT0 cap st cls tip
    [ -r "$b/capacity" ] || exit 0
    cap=$(cat "$b/capacity"); st=$(cat "$b/status")
    cls="bat-$(( (cap + 5) / 10 * 10 ))"
    case "$st" in
        Charging)      cls="$cls-charging"; tip="Battery: $cap% (charging)" ;;
        Full|"Not charging") cls="$cls-plugged"; tip="Battery: $cap% (plugged in)" ;;
        *)             tip="Battery: $cap% remaining" ;;
    esac
    [ "$cap" -le 10 ] && [ "$st" = Discharging ] && cls="$cls critical"
    json "$cls" "$tip"
}

# Ending this script ends everything it started (nmcli monitor, pactl
# subscribe, udevadm monitor …) – before, those kept running forever when the
# program reading this script went away: every winshell restart left a set
# behind (1.4 GB after a day). winshell starts this script in a process group
# of its own; the pipelines run in the background and `wait` lets the trap
# run right away (bash runs traps only once a foreground command is done).
if [ "$(ps -o pgid= $$ | tr -d ' ')" = "$$" ]; then
    trap 'trap - TERM INT HUP EXIT; kill 0 2>/dev/null' TERM INT HUP EXIT
fi

case "$1" in
    net)
        net
        { nmcli monitor 2>/dev/null & while sleep 30; do echo tick; done; } |
            while read -r _; do net; done &
        wait ;;
    vol)
        vol
        pactl subscribe 2>/dev/null | grep --line-buffered -E "'change' on (sink|server)" |
            while read -r _; do vol; done &
        wait ;;
    bat)
        # redraw as soon as the charger is plugged in or out (udev event),
        # plus every 30 s for the charge level
        bat
        { udevadm monitor --udev --subsystem-match=power_supply 2>/dev/null &
          while sleep 30; do echo tick; done; } |
            while read -r line; do
                case "$line" in UDEV*|tick) bat ;; esac
            done &
        wait ;;
esac
