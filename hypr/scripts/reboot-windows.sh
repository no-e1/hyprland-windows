#!/usr/bin/env bash
# Sets the Windows Boot Manager as the one-time next boot entry (UEFI)
# and reboots. Asks for your password via polkit.
id=$(efibootmgr | grep -i "Windows Boot Manager" | head -n1 | sed -E 's/^Boot([0-9A-Fa-f]{4}).*/\1/')
if [ -z "$id" ]; then
    notify-send "Windows not found" "No 'Windows Boot Manager' in the UEFI boot entries."
    exit 1
fi
if pkexec efibootmgr --bootnext "$id" >/dev/null; then
    systemctl reboot
else
    notify-send "Cancelled" "Restart to Windows was not confirmed."
fi
