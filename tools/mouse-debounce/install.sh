#!/usr/bin/env bash
# Installs the mouse double-click filter as a system service (run with sudo):
#   sudo tools/mouse-debounce/install.sh            install / update and start it
#   sudo tools/mouse-debounce/install.sh remove     stop and remove it
# The script is copied to /usr/local (a root service must not run a file
# your user can change).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo." >&2; exit 1; }
DIR="$(cd "$(dirname "$0")" && pwd)"

if [ "${1:-}" = remove ]; then
    systemctl disable --now mouse-debounce.service 2>/dev/null || true
    rm -f /etc/systemd/system/mouse-debounce.service /usr/local/libexec/mouse-debounce.py
    systemctl daemon-reload
    echo "Removed."
    exit 0
fi

rpm -q python3-evdev >/dev/null 2>&1 || dnf -y install python3-evdev
install -D -m 755 -o root -g root "$DIR/mouse-debounce.py" /usr/local/libexec/mouse-debounce.py
install -D -m 644 -o root -g root "$DIR/mouse-debounce.service" /etc/systemd/system/mouse-debounce.service
systemctl daemon-reload
systemctl enable mouse-debounce.service
systemctl restart mouse-debounce.service
sleep 1
systemctl --no-pager --lines=3 status mouse-debounce.service || true
