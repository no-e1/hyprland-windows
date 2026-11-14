#!/usr/bin/env bash
# The boot passphrase prompt as the Windows 11 sign-in (Plymouth theme
# "win11-signin"): run as your user, it asks for sudo.
#   tools/plymouth/install.sh
# Run it again after changing the wallpaper or the profile picture – the
# theme is a picture of them (the boot image can't read your home folder).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[ "$(id -u)" != 0 ] || { echo "Run it as your user (it uses sudo itself)."; exit 1; }

need=()
rpm -q python3-pillow >/dev/null 2>&1 || need+=(python3-pillow)
rpm -q plymouth-plugin-script >/dev/null 2>&1 || need+=(plymouth-plugin-script)
[ ${#need[@]} -eq 0 ] || sudo dnf -y install "${need[@]}"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
python3 "$HERE/make-theme.py" "$TMP/win11-signin"

sudo rm -rf /usr/share/plymouth/themes/win11-signin
sudo cp -r "$TMP/win11-signin" /usr/share/plymouth/themes/
sudo chown -R root:root /usr/share/plymouth/themes/win11-signin
sudo restorecon -R /usr/share/plymouth/themes/win11-signin 2>/dev/null || true
echo "Rebuilding the boot image (a minute)…"
sudo plymouth-set-default-theme win11-signin -R
echo "Done – it shows at the next start."
