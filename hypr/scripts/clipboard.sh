#!/usr/bin/env bash
# Clipboard history (Win + V): winshell's panel, like Windows 11.
# The history itself is kept by winshell/clipstore.py (wl-paste --watch).
exec "$HOME/.config/hypr/winshell/winshell-ctl" clipboard
