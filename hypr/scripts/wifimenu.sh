#!/usr/bin/env bash
# WLAN-Auswahl wie im Windows-Flyout: Netz anklicken, ggf. Passwort eingeben.
THEME="$HOME/.config/rofi/list.rasi"
pkill -x rofi

notify-send -t 1500 "WLAN" "Suche Netzwerke …"
list=$(nmcli -t -f IN-USE,SIGNAL,SECURITY,SSID device wifi list --rescan yes 2>/dev/null \
    | awk -F: '$4 != "" && !seen[$4]++ {
        s=$2; icon = (s>75)?"󰤨":(s>50)?"󰤥":(s>25)?"󰤢":"󰤟";
        lock = ($3=="" || $3=="--") ? "" : "  󰌾";
        cur  = ($1=="*") ? "  (verbunden)" : "";
        printf "%s  %s%s%s\n", icon, $4, lock, cur }')

state=$(nmcli radio wifi)
toggle=$([ "$state" = "enabled" ] && echo "󰖪  WLAN ausschalten" || echo "󰖩  WLAN einschalten")

choice=$(printf '%s\n%s\n' "$toggle" "$list" | rofi -dmenu -i -p "WLAN" -theme "$THEME")
[ -z "$choice" ] && exit 0

case "$choice" in
    *"WLAN ausschalten") nmcli radio wifi off; exit 0 ;;
    *"WLAN einschalten") nmcli radio wifi on;  exit 0 ;;
esac

ssid=$(sed -E 's/^[^ ]+  //; s/  󰌾//; s/  \(verbunden\)//' <<<"$choice")

if nmcli -t -f NAME connection show | grep -Fxq "$ssid"; then
    nmcli connection up id "$ssid" && notify-send "WLAN" "Verbunden mit $ssid" && exit 0
fi

if grep -q "󰌾" <<<"$choice"; then
    pw=$(rofi -dmenu -password -p "Passwort für $ssid" -theme "$THEME" -lines 0)
    [ -z "$pw" ] && exit 0
    if nmcli device wifi connect "$ssid" password "$pw"; then
        notify-send "WLAN" "Verbunden mit $ssid"
    else
        notify-send -u critical "WLAN" "Verbindung mit $ssid fehlgeschlagen"
    fi
else
    nmcli device wifi connect "$ssid" && notify-send "WLAN" "Verbunden mit $ssid"
fi
