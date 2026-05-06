#!/bin/bash
# ADA Kitty terminal launcher — spawns visible terminal with tmux status bar.
SEAL_HOME="/home/dadito/IA/proyecto-seal"

exec kitty \
    --listen-on unix:/tmp/seal-ada-kitty.sock \
    -o allow_remote_control=yes \
    -o initial_window_width=130c \
    -o initial_window_height=40c \
    --title "ADA — Team SEAL" \
    bash "$SEAL_HOME/ada.sh"
