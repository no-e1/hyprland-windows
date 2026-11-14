#!/usr/bin/env bash
# Wi-Fi after sleep (run with sudo):
#   sudo tools/wifi-resume/install.sh            install / update
#   sudo tools/wifi-resume/install.sh remove     remove it again
# - before sleep the Wi-Fi driver is unloaded, after it loaded again: the
#   ath11k card (WCN6855) otherwise often doesn't wake up ("Looking for
#   networks…" until a reboot)
# - wifi-recover.service resets a hung card; Ctrl + Win + Shift + B and the
#   "Repair" button in Quick Settings start it without a password
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo." >&2; exit 1; }
DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = remove ]; then
    systemctl disable wifi-sleep.service 2>/dev/null || true
    rm -f /etc/systemd/system/wifi-sleep.service /etc/systemd/system/wifi-recover.service \
          /etc/polkit-1/rules.d/50-wifi-recover.rules /usr/local/libexec/wifi-recover
    systemctl daemon-reload
    echo "Removed."
    exit 0
fi

install -D -m 755 -o root -g root "$DIR/wifi-recover" /usr/local/libexec/wifi-recover
install -D -m 644 -o root -g root "$DIR/wifi-sleep.service" /etc/systemd/system/wifi-sleep.service
install -D -m 644 -o root -g root "$DIR/wifi-recover.service" /etc/systemd/system/wifi-recover.service
install -D -m 644 -o root -g polkitd "$DIR/50-wifi-recover.rules" /etc/polkit-1/rules.d/50-wifi-recover.rules
command -v restorecon >/dev/null && restorecon -F /usr/local/libexec/wifi-recover \
    /etc/systemd/system/wifi-sleep.service /etc/systemd/system/wifi-recover.service \
    /etc/polkit-1/rules.d/50-wifi-recover.rules
systemctl daemon-reload
systemctl enable wifi-sleep.service
echo "Installed: Wi-Fi driver is unloaded during sleep; wifi-recover.service ready."
