#!/usr/bin/env bash
# Angeheftete Taskleisten-Apps:
#   pinned.sh status <klasse> <tooltip> <symbol>  -> JSON für Waybar (zeigt "läuft"-Strich)
#   pinned.sh open   <klasse> <befehl>    -> Fenster fokussieren oder App starten
mode="$1"; cls="$2"; arg="$3"

addr() {
    hyprctl clients -j 2>/dev/null \
      | jq -r --arg c "$cls" '[.[] | select(.class | test($c; "i"))] | sort_by(.focusHistoryID) | .[0].address // empty'
}

case "$mode" in
    status)
        a=$(addr)
        active=$(hyprctl activewindow -j 2>/dev/null | jq -r '.address // empty')
        class=""
        [ -n "$a" ] && class="running"
        [ -n "$a" ] && [ "$a" = "$active" ] && class="active"
        printf '{"text":"%s","class":"%s","tooltip":"%s"}\n' "$4" "$class" "$arg" ;;
    open)
        a=$(addr)
        if [ -n "$a" ]; then
            hyprctl dispatch "hl.dsp.focus({ window = \"address:$a\" })" >/dev/null
            hyprctl dispatch 'hl.dsp.window.alter_zorder({ mode = "top" })' >/dev/null
        else
            setsid -f sh -c "$arg" >/dev/null 2>&1
        fi ;;
esac
