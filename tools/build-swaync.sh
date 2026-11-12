#!/usr/bin/env bash
# Builds swaync (the notification center) for the installed version with
# tools/swaync-anim.patch: a notification that is closed or cleared slides
# out to the right and the gap closes smoothly, like Windows (the stock
# control center only fades it and the list jumps).
# Inside a Podman container (no sudo needed). Output:
#   ~/.local/share/hypr-plugins/swaync   (scripts/swaync.sh prefers it)
set -euo pipefail

VER="v$(swaync --version | awk '{print $2}')"
OUT="$HOME/.local/share/hypr-plugins"
mkdir -p "$OUT"
TOOLS="$(cd "$(dirname "$0")" && pwd)"

podman run --rm -v "$OUT:/out:Z" -v "$TOOLS:/tools:ro,Z" -e VER="$VER" registry.fedoraproject.org/fedora:43 bash -euxc '
dnf -y -q install gcc vala meson ninja-build git pkgconf-pkg-config \
    gtk4-devel libadwaita-devel gtk4-layer-shell-devel json-glib-devel granite-7-devel \
    libgee-devel wayland-devel pulseaudio-libs-devel blueprint-compiler sassc \
    glib2-devel scdoc >/dev/null
git clone -q --depth 1 -b "$VER" https://github.com/ErikReider/SwayNotificationCenter /src
cd /src
git apply /tools/swaync-anim.patch
meson setup build --prefix=/usr -Dman-pages=false -Dsystemd-service=false \
    -Dzsh-completions=false -Dbash-completions=false -Dfish-completions=false >/dev/null
ninja -C build >/dev/null
install -m 755 build/src/swaync /out/swaync.new
mv /out/swaync.new /out/swaync
'
echo "built: $OUT/swaync ($VER)"
