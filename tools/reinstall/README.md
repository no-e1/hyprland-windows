# Reinstalling Fedora (encrypted disk, same setup)

Fedora gets installed again on its partition with disk encryption; Windows
(BitLocker), Secure Boot and GRUB stay. The data goes to a Windows share on
the desktop PC in between.

## Before

1. **BitLocker recovery key** of the laptop's Windows at hand
   (account.microsoft.com/devices/recoverykey) – Windows may ask for it
   after the reinstall.
2. Repo committed and pushed (backup.sh checks).
3. `tools/reinstall/backup.sh` – asks for the sudo and Windows password,
   writes `backup-<date>/` to `//192.168.0.162/fedora-laptop-backup`
   (about 1.5 GB). Mullvad must not block the local network
   (`mullvad lan set allow`, or disconnect it).
4. USB stick with **Fedora Everything netinstall** (Fedora Media Writer →
   "Other" → Everything).

## Install

1. Boot the stick (F12 on the ThinkPad); Secure Boot stays on.
2. Software selection: **Minimal Install**.
3. Installation destination → custom: **delete only the old Fedora
   partition** (nvme0n1p5, 371 GB, ext4). Keep everything else.
   - `/boot/efi`: the existing EFI partition nvme0n1p1 – mount it,
     **do not format it** (Windows boots from it).
   - the free space: automatic layout, **"Encrypt my data"** on, a good
     passphrase.
   - If the installer complains that the EFI partition (200 MB) is too
     small: stop and look again, don't let it make a new one.
4. User: `noel`, administrator, same password as before (it unlocks the
   KDE wallet with Brave's saved passwords).

## After

1. Boot: GRUB, then the disk passphrase, then a text login: log in as noel.
2. Network: LAN cable, or `nmtui` for Wi-Fi.
3. `sudo dnf -y install git-core gh && gh auth login` (the repo is private), then
   `gh repo clone no-e1/hyprland-windows ~/dev/hyprland-windows`
4. `~/dev/hyprland-windows/tools/reinstall/restore.sh`, then `sudo reboot`.

From then on: GRUB → disk passphrase → Hyprland starts by itself (greetd,
no login screen in between).
