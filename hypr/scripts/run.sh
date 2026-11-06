#!/usr/bin/env bash
# "Ausführen"-Dialog (Win + R)
pkill -x rofi && exit 0
exec rofi -show run -p "Öffnen" -theme "$HOME/.config/rofi/list.rasi"
