#!/usr/bin/env bash
# At login: Wi-Fi on, Bluetooth off – only Wi-Fi is on by default.
# (bluez powers every adapter on by itself, AutoEnable=true; and an
# airplane mode left on at shutdown would otherwise survive the reboot.)
rfkill unblock wlan
nmcli radio wifi on

# wait for the Bluetooth adapter, then switch it off (and keep it off for
# a few seconds in case bluez powers it on a moment later)
for _ in $(seq 1 40); do
    bluetoothctl show 2>/dev/null | grep -q "Powered:" && break
    sleep 0.25
done
for _ in $(seq 1 10); do
    bluetoothctl show 2>/dev/null | grep -q "Powered: yes" && bluetoothctl power off >/dev/null
    sleep 0.5
done
