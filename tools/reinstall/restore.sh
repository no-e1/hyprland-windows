#!/usr/bin/env bash
# After reinstalling (Fedora Everything, "Minimal Install", encrypted disk):
# sets the laptop up again from the backup on the desktop PC.
#   tools/reinstall/restore.sh [//192.168.0.162/fedora-laptop-backup] [windows-user] [backup-folder]
#
# Run it on the new system as your user (noel), in the text console, with
# the network up (LAN cable, or Wi-Fi with `nmtui`). It asks for your sudo
# password and the Windows password, then:
#   1. package sources (RPM Fusion, Brave, Mullvad, VSCodium, Hyprland COPR)
#   2. all packages of packages.txt
#   3. your home folder and the system settings (Wi-Fi, Bluetooth,
#      fingerprints, Mullvad) from the backup
#   4. this repo's install.sh, the mouse filter, the title bar plugin
#   5. greetd: right after the disk is unlocked Hyprland starts – no login
#      screen in between
# Then reboot.
set -euo pipefail
SHARE="${1:-//192.168.0.162/fedora-laptop-backup}"
WINUSER="${2:-noko2}"
PICK="${3:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
ME="$(id -un)"
MNT="/mnt/backup-share"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
[ "$ME" != root ] || { echo "Run it as your user, not as root (it uses sudo itself)."; exit 1; }
sudo -v

say "Tools for the restore"
sudo dnf -y install cifs-utils zstd tar git-core dnf-plugins-core

say "Mounting $SHARE (Windows user $WINUSER)"
read -rsp "Windows password of $WINUSER: " WINPASS; echo
CRED="$(mktemp)"; chmod 600 "$CRED"
printf 'username=%s\npassword=%s\n' "$WINUSER" "$WINPASS" > "$CRED"
unset WINPASS
cleanup() { sudo umount "$MNT" 2>/dev/null || true; rm -f "$CRED"; }
trap cleanup EXIT
sudo mkdir -p "$MNT"
sudo mount -t cifs "$SHARE" "$MNT" -o "credentials=$CRED,uid=$(id -u),gid=$(id -g),vers=3.0,ro"
if [ -n "$PICK" ]; then
    SRC="$MNT/$PICK"
else
    SRC="$(ls -d "$MNT"/backup-* | sort | tail -n1)"     # the newest
fi
[ -f "$SRC/home.tar.zst" ] || { echo "No backup found in $SRC"; exit 1; }
echo "backup: $SRC"
( cd "$SRC" && sha256sum -c SHA256SUMS ) || { echo "The backup files are damaged – stopping."; exit 1; }

say "1/5 Package sources"
v="$(rpm -E %fedora)"
sudo dnf -y install \
    "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$v.noarch.rpm" \
    "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-$v.noarch.rpm"
# Brave, Mullvad, VSCodium, Hyprland COPR: their .repo files from the backup
sudo tar --zstd -xpf "$SRC/system.tar.zst" -C / --wildcards 'etc/yum.repos.d/*'
sudo dnf -y makecache

say "2/5 Packages (this takes a while)"
mapfile -t pkgs < <(sed 's/#.*//' "$HERE/packages.txt" | awk 'NF{print $1}')
sudo dnf -y install --skip-unavailable "${pkgs[@]}"

say "3/5 Home folder and system settings"
# everything into place, owners and rights as they were
sudo tar --zstd -xpf "$SRC/home.tar.zst" -C /home
sudo chown -R "$ME:$ME" "/home/$ME"
sudo tar --zstd -xpf "$SRC/system.tar.zst" -C / --exclude='etc/yum.repos.d/*'
# SELinux labels for the restored files (else NetworkManager, bluez, fprintd can't read them)
sudo restorecon -R "/home/$ME" /etc/NetworkManager /var/lib/bluetooth /var/lib/fprint /etc/mullvad-vpn 2>/dev/null || true
sudo systemctl enable --now NetworkManager bluetooth tuned mullvad-daemon 2>/dev/null || true
# fingerprint for sudo and admin prompts, like before
sudo authselect enable-feature with-fingerprint 2>/dev/null || true

say "4/5 This setup"
"$REPO/install.sh"
sudo "$REPO/tools/mouse-debounce/install.sh"
if command -v podman >/dev/null; then
    "$REPO/tools/build-hyprbars.sh" || echo "!! title bar plugin: build it later with tools/build-hyprbars.sh"
    "$REPO/tools/build-swaync.sh" || echo "!! swaync animations: build later with tools/build-swaync.sh"
fi

say "5/5 Start Hyprland right after unlocking the disk"
# The disk passphrase at boot is the sign-in: greetd logs you in once,
# straight into Hyprland (no SDDM / login screen). Logging out shows a
# simple text sign-in.
sudo tee /etc/greetd/config.toml > /dev/null <<EOF
[terminal]
vt = 1

[default_session]
command = "agreety --cmd start-hyprland"
user = "greetd"

[initial_session]
command = "start-hyprland"
user = "$ME"
EOF
sudo systemctl enable greetd
sudo systemctl set-default graphical.target

say "Done"
cat <<EOF
Reboot now (sudo reboot). After the disk passphrase Hyprland starts by itself.
Still to do by hand:
  * fingerprint: works if the backup had it; else enroll again with fprintd-enroll
  * Brave asks once for the KDE wallet password (your old login password)
  * Steam: sign in, the games download again
  * Claude Code: install again (its settings in ~/.claude are back)
EOF
