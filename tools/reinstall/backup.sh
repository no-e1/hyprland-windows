#!/usr/bin/env bash
# Before reinstalling: copies everything needed to set this laptop up again
# to a Windows share on the network (the desktop PC).
#   tools/reinstall/backup.sh [//192.168.0.162/fedora-laptop-backup] [windows-user]
#
# Writes into <share>/backup-<date>/:
#   home.tar.zst     your home folder – without what is downloaded or built
#                    again anyway (caches, Steam games, Podman images, …)
#   system.tar.zst   Wi-Fi passwords, Bluetooth pairings, fingerprints,
#                    Mullvad settings, the extra package sources, GRUB settings
#   packages.txt     the packages you installed yourself (for reference)
#   SHA256SUMS       to check the files after copying them back
# Asks for your sudo password (system files, mounting) and the Windows password.
set -euo pipefail
SHARE="${1:-//192.168.0.162/fedora-laptop-backup}"
WINUSER="${2:-noko2}"
RUN="${XDG_RUNTIME_DIR:-/tmp}"
MNT="$RUN/backup-share"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "Repository"
if [ -n "$(git -C "$REPO" status --porcelain)" ] || [ -n "$(git -C "$REPO" log '@{u}..' --oneline 2>/dev/null)" ]; then
    echo "The repo has changes that are not on GitHub yet – commit and push them first:"
    git -C "$REPO" status --short
    exit 1
fi
echo "everything is on GitHub"

say "Mounting $SHARE (Windows user $WINUSER)"
read -rsp "Windows password of $WINUSER: " WINPASS; echo
CRED="$(mktemp "$RUN/cred.XXXXXX")"; chmod 600 "$CRED"
printf 'username=%s\npassword=%s\n' "$WINUSER" "$WINPASS" > "$CRED"
unset WINPASS
cleanup() { sudo umount "$MNT" 2>/dev/null || true; rm -f "$CRED"; }
trap cleanup EXIT
mkdir -p "$MNT"
sudo mount -t cifs "$SHARE" "$MNT" -o "credentials=$CRED,uid=$(id -u),gid=$(id -g),vers=3.0"
DEST="$MNT/backup-$(date +%Y-%m-%d)"
mkdir -p "$DEST"
echo "writing to $DEST"

say "Home folder (a few minutes)"
# Left out: downloaded or built again anyway. Steam: the games download
# again, saves are in the Steam cloud. Claude Code's program files: installed
# again (its settings in ~/.claude stay in the backup).
tar --zstd -cpf "$DEST/home.tar.zst" -C /home \
    --exclude='noel/.cache' \
    --exclude='noel/.local/share/Steam' \
    --exclude='noel/.local/share/containers' \
    --exclude='noel/.local/share/pnpm' \
    --exclude='noel/.local/share/claude' \
    --exclude='noel/.local/share/Trash' \
    --exclude='*/Cache' --exclude='*/Code Cache' --exclude='*/GPUCache' \
    --exclude='*/GrShaderCache' --exclude='*/ShaderCache' --exclude='*/DawnGraphiteCache' \
    --exclude='*/DawnWebGPUCache' --exclude='*/Service Worker/CacheStorage' \
    --exclude='node_modules' \
    --warning=no-file-changed --warning=no-file-ignored \
    "$(id -un)" || [ $? -eq 1 ]          # 1: a file changed while reading (browser running) – fine

say "System settings"
sys=()
for p in /etc/NetworkManager/system-connections /var/lib/bluetooth /var/lib/fprint /etc/mullvad-vpn \
         /etc/default/grub /etc/yum.repos.d/brave-browser.repo /etc/yum.repos.d/mullvad.repo \
         /etc/yum.repos.d/vscodium.repo "/etc/yum.repos.d/_copr:copr.fedorainfracloud.org:ashbuk:Hyprland-Fedora.repo"; do
    sudo test -e "$p" && sys+=("${p#/}")
done
sudo tar --zstd -cpf "$DEST/system.tar.zst" -C / "${sys[@]}"
sudo chown "$(id -u):$(id -g)" "$DEST/system.tar.zst" 2>/dev/null || true

say "Package list"
dnf repoquery --userinstalled --qf '%{name}\n' 2>/dev/null | sort > "$DEST/packages.txt"
rpm -qa --qf '%{NAME}\n' | sort > "$DEST/all-packages.txt"
dnf copr list > "$DEST/copr.txt" 2>/dev/null || true

say "Checking"
( cd "$DEST" && sha256sum home.tar.zst system.tar.zst > SHA256SUMS )
tar --zstd -tf "$DEST/home.tar.zst" > /dev/null && echo "home.tar.zst reads back fine"
sudo tar --zstd -tf "$DEST/system.tar.zst" > /dev/null && echo "system.tar.zst reads back fine"
du -sh "$DEST"/*
echo
echo "Done. Keep this folder on the PC until the new system is set up."
