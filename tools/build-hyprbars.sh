#!/usr/bin/env bash
# Builds the "hyprbars" title bar plugin for the installed Hyprland version
# inside a Podman container (no sudo needed), with the Windows-like
# drag-to-edge snapping patch applied.  Output:
#   ~/.local/share/hypr-plugins/hyprbars.so
#
# Versions = the libraries bundled in the ashbuk/Hyprland-Fedora COPR package:
# read from its vendor folder, so the plugin always matches the installed
# Hyprland (a plugin built against other library versions doesn't load).
# The container is the same Fedora release as this system.
set -euo pipefail

HL_VER="v$(hyprctl version -j | jq -r .tag | sed 's/^v//')"
FEDORA="$(rpm -E %fedora)"
VENDOR=/usr/libexec/hyprland/vendor/lib64
ver() {   # ver <lib> <fallback>: the version of a vendored library (libfoo.so.X.Y.Z)
    local f; f=$(ls "$VENDOR/lib$1.so."*.*.* 2>/dev/null | head -n1)
    [ -n "$f" ] && echo "v${f##*.so.}" || echo "$2"
}
V_UTILS=$(ver hyprutils v0.14.3); V_LANG=$(ver hyprlang v0.6.8); V_WIRE=$(ver hyprwire v0.3.1)
V_AQ=$(ver aquamarine v0.15.1); V_GFX=$(ver hyprgraphics v0.5.1); V_CURSOR=$(ver hyprcursor v0.1.13)
# hyprland-plugins has a tag per minor release only (v0.57.0 for 0.57.1 too)
PL_VER="$HL_VER"
git ls-remote --exit-code --tags https://github.com/hyprwm/hyprland-plugins "$PL_VER" >/dev/null 2>&1 \
    || PL_VER="$(echo "$HL_VER" | sed -E 's/^(v[0-9]+\.[0-9]+)\..*/\1.0/')"
echo "Hyprland $HL_VER (plugins $PL_VER) on Fedora $FEDORA: hyprutils $V_UTILS, hyprlang $V_LANG," \
     "hyprwire $V_WIRE, aquamarine $V_AQ, hyprgraphics $V_GFX, hyprcursor $V_CURSOR"
OUT="$HOME/.local/share/hypr-plugins"
mkdir -p "$OUT"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/hyprbars-build"; mkdir -p "$CACHE"
[ -s "$CACHE/lua-5.5.1.tar.gz" ] || curl -fsSL --retry 3 -o "$CACHE/lua-5.5.1.tar.gz" https://www.lua.org/ftp/lua-5.5.1.tar.gz

TOOLS="$(cd "$(dirname "$0")" && pwd)"

podman run --rm -v "$OUT:/out:Z" -v "$CACHE:/cache:Z" -v "$TOOLS:/tools:ro,Z" -e HL_VER="$HL_VER" -e PL_VER="$PL_VER" \
    -e V_UTILS="$V_UTILS" -e V_LANG="$V_LANG" -e V_WIRE="$V_WIRE" -e V_AQ="$V_AQ" -e V_GFX="$V_GFX" -e V_CURSOR="$V_CURSOR" \
    "registry.fedoraproject.org/fedora:$FEDORA" bash -euxc '
dnf -y -q install gcc-c++ cmake ninja-build meson git pkgconf-pkg-config \
    wayland-devel wayland-protocols-devel pixman-devel libdrm-devel libinput-devel \
    systemd-devel libxkbcommon-devel mesa-libEGL-devel mesa-libgbm-devel libglvnd-devel \
    cairo-devel pango-devel libzip-devel librsvg2-devel tomlplusplus-devel \
    libseat-devel libdisplay-info-devel hwdata-devel pugixml-devel libffi-devel \
    libjpeg-turbo-devel libwebp-devel libpng-devel libjxl-devel file-devel \
    re2-devel lua-devel libuuid-devel muParser-devel xcb-util-wm-devel \
    xcb-util-errors-devel libXcursor-devel libxcb-devel glslang-devel libeis-devel \
    lcms2-devel libcanberra-devel glib2-devel sdbus-cpp-devel mesa-libGLES-devel \
    readline-devel cpio make curl python3 >/dev/null

# Lua 5.5 (Fedora ships 5.4)
tar xzf /cache/lua-5.5.1.tar.gz -C /tmp
make -s -C /tmp/lua-5.5.1 linux MYCFLAGS=-fPIC >/dev/null
make -s -C /tmp/lua-5.5.1 install INSTALL_TOP=/usr/local >/dev/null
mkdir -p /usr/local/lib/pkgconfig
printf "prefix=/usr/local\nName: lua55\nDescription: Lua\nVersion: 5.5.1\nCflags: -I/usr/local/include\nLibs: -L/usr/local/lib -llua -lm\n" > /usr/local/lib/pkgconfig/lua55.pc
export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:/usr/lib64/pkgconfig:/usr/share/pkgconfig

b() { # repo tag [cmake-args]
    git clone -q --depth 1 -b "$2" "https://github.com/hyprwm/$1" "/src/$1"
    cmake -S "/src/$1" -B "/src/$1/build" -G Ninja -DCMAKE_BUILD_TYPE=Release \
          -DCMAKE_INSTALL_PREFIX=/usr -DBUILD_TESTING=OFF "${@:3}" >/dev/null
    cmake --build "/src/$1/build" >/dev/null
    cmake --install "/src/$1/build" >/dev/null
}
b hyprwayland-scanner v0.4.5
b hyprutils          "$V_UTILS"
b hyprlang           "$V_LANG"
b hyprwire           "$V_WIRE"
b hyprland-protocols v0.8.0
b aquamarine         "$V_AQ"
b hyprgraphics       "$V_GFX"
b hyprcursor         "$V_CURSOR"

# glaze (header-only)
git clone -q --depth 1 -b v7.2.0 https://github.com/stephenberry/glaze /src/glaze
cmake -S /src/glaze -B /src/glaze/build -DCMAKE_INSTALL_PREFIX=/usr -Dglaze_DEVELOPER_MODE=OFF -Dglaze_BUILD_EXAMPLES=OFF >/dev/null
cmake --install /src/glaze/build >/dev/null

# Hyprland: configure only, then generate protocol headers and install the headers
git clone -q --depth 1 --recursive -b "$HL_VER" https://github.com/hyprwm/Hyprland /src/Hyprland
cd /src/Hyprland
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr -DNO_HYPRPM=ON -DBUILD_TESTING=OFF
make installheaders PREFIX=/usr
pkg-config --modversion hyprland

git clone -q --depth 1 -b "$PL_VER" https://github.com/hyprwm/hyprland-plugins /src/hp
cd /src/hp && git apply /tools/hyprbars-snap.patch
cd /src/hp/hyprbars && make CXX=g++ && cp hyprbars.so /out/hyprbars.so.new
'
# Swap in atomically: never write into a file a running Hyprland has loaded.
mv -f "$OUT/hyprbars.so.new" "$OUT/hyprbars.so"
echo "Done: $OUT/hyprbars.so (log out and back in to load the new version)"
