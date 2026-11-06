#!/usr/bin/env bash
# Setzt den Windows-Bootmanager als einmaliges nächstes Bootziel (UEFI)
# und startet neu. Fragt per Polkit nach deinem Passwort.
id=$(efibootmgr | grep -i "Windows Boot Manager" | head -n1 | sed -E 's/^Boot([0-9A-Fa-f]{4}).*/\1/')
if [ -z "$id" ]; then
    notify-send "Windows nicht gefunden" "Kein 'Windows Boot Manager' in den UEFI-Einträgen."
    exit 1
fi
if pkexec efibootmgr --bootnext "$id" >/dev/null; then
    systemctl reboot
else
    notify-send "Abgebrochen" "Neustart in Windows wurde nicht bestätigt."
fi
