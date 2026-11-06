#!/usr/bin/env bash
# Titelleisten-Knöpfe: winctl.sh close|maximize|minimize
case "$1" in
    close)    hyprctl dispatch 'hl.dsp.window.close()' ;;
    maximize) hyprctl dispatch 'hl.dsp.window.fullscreen({ mode = "maximized", action = "toggle" })' ;;
    minimize) hyprctl dispatch 'hl.dsp.window.move({ workspace = "special:minimized", follow = false })' ;;
esac >/dev/null
