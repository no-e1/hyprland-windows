#!/usr/bin/env bash
# Window actions used by the title bar buttons and drag-to-edge snapping.
#   winctl.sh close | maximize | unmaximize | minimize
#   winctl.sh snap top|left|right
S="$HOME/.config/hypr/scripts"
echo "$(date +%T) $*" >> "${XDG_RUNTIME_DIR:-/tmp}/winctl.log"
d() { hyprctl dispatch "$1" >/dev/null; }
max() { hyprctl eval "WinMax(\"$1\")" >/dev/null; }

case "$1" in
    close)      d 'hl.dsp.window.close()' ;;
    maximize)   max toggle ;;
    unmaximize) max restore ;;
    drag-start) hyprctl eval "WinDragStart()" >/dev/null ;;
    minimize)   d 'hl.dsp.window.move({ workspace = "special:minimized", follow = false })' ;;
    snap)
        case "$2" in
            top)        max max ;;
            left|right) "$S/snap.sh" "$2" ;;
        esac ;;
esac
