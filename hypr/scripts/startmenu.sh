#!/usr/bin/env bash
# Start menu (Windows key / start button).
#   startmenu.sh          open/close it
#   startmenu.sh status   JSON for Waybar's start button (class "open" while
#                         the menu is open; winshell sends SIGRTMIN+9 on change)
if [ "$1" = status ]; then
    if [ -e "${XDG_RUNTIME_DIR:-/tmp}/winshell.start-open" ]; then
        echo '{"text":" ","class":"open"}'
    else
        echo '{"text":" ","class":""}'
    fi
    exit 0
fi
exec "$HOME/.config/hypr/winshell/winshell-ctl" menu
