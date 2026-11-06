#!/usr/bin/env bash
# Baut das Titelleisten-Plugin "hyprbars" passend zur installierten Hyprland-Version
# in einem Podman-Container (kein sudo nötig).  Ergebnis:
#   ~/.local/share/hypr-plugins/hyprbars.so
#
# Versionen = die im COPR-Paket ashbuk/Hyprland-Fedora gebündelten Bibliotheken.
set -euo pipefail

HL_VER="v$(hyprctl version -j | jq -r .tag | sed 's/^v//')"
OUT="$HOME/.local/share/hypr-plugins"
mkdir -p "$OUT"

podman run --rm -v "$OUT:/out:Z" -e HL_VER="$HL_VER" registry.fedoraproject.org/fedora:43 bash -euxc '
dnf -y -q install gcc-c++ cmake ninja-build meson git pkgconf-pkg-config \
    wayland-devel wayland-protocols-devel pixman-devel libdrm-devel libinput-devel \
    systemd-devel libxkbcommon-devel mesa-libEGL-devel mesa-libgbm-devel libglvnd-devel \
    cairo-devel pango-devel libzip-devel librsvg2-devel tomlplusplus-devel \
    libseat-devel libdisplay-info-devel hwdata-devel pugixml-devel libffi-devel \
    libjpeg-turbo-devel libwebp-devel libpng-devel libjxl-devel file-devel \
    re2-devel lua-devel libuuid-devel muParser-devel xcb-util-wm-devel \
    xcb-util-errors-devel libXcursor-devel libxcb-devel glslang-devel libeis-devel \
    lcms2-devel libcanberra-devel glib2-devel sdbus-cpp-devel mesa-libGLES-devel \
    cpio make curl python3 >/dev/null

# Lua 5.5 (Fedora 43 hat nur 5.4)
curl -sL https://www.lua.org/ftp/lua-5.5.1.tar.gz | tar xz -C /tmp
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
b hyprutils          v0.14.3
b hyprlang           v0.6.8
b hyprwire           v0.3.1
b hyprland-protocols v0.8.0
b aquamarine         v0.15.1
b hyprgraphics       v0.5.1
b hyprcursor         v0.1.13

# glaze (header-only)
git clone -q --depth 1 -b v7.2.0 https://github.com/stephenberry/glaze /src/glaze
cmake -S /src/glaze -B /src/glaze/build -DCMAKE_INSTALL_PREFIX=/usr -Dglaze_DEVELOPER_MODE=OFF -Dglaze_BUILD_EXAMPLES=OFF >/dev/null
cmake --install /src/glaze/build >/dev/null

# Hyprland: nur konfigurieren + Protokoll-Header erzeugen, dann Header installieren
git clone -q --depth 1 --recursive -b "$HL_VER" https://github.com/hyprwm/Hyprland /src/Hyprland
cd /src/Hyprland
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr -DNO_HYPRPM=ON -DBUILD_TESTING=OFF
make installheaders PREFIX=/usr
pkg-config --modversion hyprland

git clone -q --depth 1 -b "$HL_VER" https://github.com/hyprwm/hyprland-plugins /src/hp
cd /src/hp/hyprbars && make CXX=g++ && cp hyprbars.so /out/
'
echo "Fertig: $OUT/hyprbars.so"
