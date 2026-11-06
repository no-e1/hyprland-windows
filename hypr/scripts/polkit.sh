#!/usr/bin/env bash
# Starts the first available polkit agent (password prompts)
for a in \
    /usr/libexec/hyprpolkitagent \
    /usr/libexec/kf6/polkit-kde-authentication-agent-1 \
    /usr/libexec/polkit-kde-authentication-agent-1 \
    /usr/libexec/polkit-mate-authentication-agent-1 \
    /usr/libexec/polkit-gnome-authentication-agent-1 \
    /usr/bin/lxpolkit
do
    [ -x "$a" ] && exec "$a"
done
