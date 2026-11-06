#!/usr/bin/env bash
# Pinned taskbar apps:
#   pinned.sh status <class> <tooltip> [text]  -> JSON for Waybar (running/active indicator)
#   pinned.sh open   <class> <command>         -> focus / minimize the window or launch the app
#
# Waybar refreshes the status right away when winshell sends SIGRTMIN+8
# (on every window event), so the highlight follows clicks immediately.
mode="$1"; cls="$2"; arg="$3"

clients=$(hyprctl clients -j 2>/dev/null)
active=$(hyprctl activewindow -j 2>/dev/null)

# most recently used window of this app (minimized ones included)
addr=$(jq -r --arg c "$cls" '[.[] | select(.class | test($c; "i"))] | sort_by(.focusHistoryID) | .[0].address // empty' <<<"$clients")
# the active window, if it belongs to this app and is not minimized
front=$(jq -r --arg c "$cls" 'select(.class? and (.class | test($c; "i")) and (.workspace.name | startswith("special:") | not)) | .address' <<<"$active")

case "$mode" in
    status)
        class=""
        [ -n "$addr" ] && class="running"
        [ -n "$front" ] && class="active"
        printf '{"text":"%s","class":"%s","tooltip":"%s"}\n' "${4:- }" "$class" "$arg" ;;
    open)
        if [ -n "$front" ]; then
            # like Windows: clicking the app that is in front minimizes it
            hyprctl eval "HideWindow(\"$front\")" >/dev/null
        elif [ -n "$addr" ]; then
            # brings it back if it was minimized (see the window.active hook)
            hyprctl eval "ShowWindow(\"$addr\")" >/dev/null
        else
            setsid -f sh -c "$arg" >/dev/null 2>&1
        fi
        pkill -RTMIN+8 -x waybar ;;
esac
