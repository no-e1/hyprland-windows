#!/usr/bin/env bash
# Window actions used by the title bar buttons and drag-to-edge snapping.
#   winctl.sh close | maximize | unmaximize | minimize | minimize-window <address>
#   winctl.sh snap top|left|right
S="$HOME/.config/hypr/scripts"
echo "$(date +%T) $*" >> "${XDG_RUNTIME_DIR:-/tmp}/winctl.log"
d() { hyprctl dispatch "$1" >/dev/null; }
max() { hyprctl eval "WinMax(\"$1\")" >/dev/null; }

# Title bar buttons (hyprbars) by position, right to left – same order as
# the add_button calls in hyprland.lua.
if [ "$1" = button ]; then
    case "$2" in
        0) set -- close ;;
        1) set -- maximize ;;
        2) set -- minimize ;;
        *) exit 0 ;;
    esac
fi

case "$1" in
    close)      d 'hl.dsp.window.close()' ;;
    maximize)   max toggle ;;
    unmaximize) max restore ;;
    drag-start) hyprctl eval "WinDragStart()" >/dev/null ;;
    # a window was dropped (any window, sent by the hyprbars plugin):
    # top edge = maximize, left/right edge = half, else keep it on screen
    drag-end)
        case "$2" in
            top)        max max ;;
            left|right) hyprctl eval "WinForget()" >/dev/null; "$S/snap.sh" "$2" ;;
            *)          hyprctl eval "WinClamp()" >/dev/null ;;
        esac ;;
    minimize)   hyprctl eval "HideWindow()" >/dev/null ;;
    # an app's own "–" button (sent by the hyprbars plugin with the window)
    minimize-window) hyprctl eval "HideWindow(\"$2\")" >/dev/null ;;
    snap)
        case "$2" in
            top)        max max ;;
            left|right) hyprctl eval "WinForget()" >/dev/null; "$S/snap.sh" "$2" ;;
        esac ;;
esac
