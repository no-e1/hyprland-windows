#!/bin/bash -l
# The Hyprland session, started by greetd right after the disk is unlocked
# (/etc/greetd/config.toml, set up by tools/reinstall/restore.sh).
# A login shell (-l) first: /etc/profile and ~/.bashrc give the usual PATH
# (~/.local/bin: winterm, …) – greetd itself starts with a bare one, and
# Win + T found no terminal. Then the variables a display manager would set.
export XDG_SESSION_TYPE=wayland
export XDG_CURRENT_DESKTOP=Hyprland
export XDG_SESSION_DESKTOP=Hyprland
exec start-hyprland
