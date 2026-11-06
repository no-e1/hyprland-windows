#!/usr/bin/env bash
# "Run" dialog (Win + R)
pkill -x rofi && exit 0
exec rofi -show run -p "Open" -theme "$HOME/.config/rofi/list.rasi"
