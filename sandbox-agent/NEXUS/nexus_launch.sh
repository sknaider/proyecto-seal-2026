#!/bin/bash
# NEXUS Kitty terminal launcher — spawns visible terminal with daemon running.
# Spec: spec_nexus_kernel_soul_v1.md §9
NEXUS_HOME="/home/dadito/IA/proyecto-seal/sandbox-agent/NEXUS"

exec kitty \
    --listen-on unix:/tmp/seal-nexus-kitty.sock \
    -o allow_remote_control=yes \
    -o initial_window_width=110c \
    -o initial_window_height=32c \
    --title "NEXUS — Team SEAL" \
    bash "$NEXUS_HOME/nexus_fresh.sh"
