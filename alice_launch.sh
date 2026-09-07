#!/bin/bash
# ALICE Kitty terminal launcher — idempotent visible terminal for ALICE.
#
# This launcher must not kill/recreate ALICE just because William asks to see her
# terminal. The long-running agent lives in tmux; Kitty is only the viewer.
set -u

SEAL_HOME="/home/dadito/IA/proyecto-seal"
TMUX_L="seal-alice"
SESSION="seal-alice"
LOG="/tmp/seal-alice-terminal.log"
SOCKET="/tmp/seal-alice-kitty.sock"

export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"

if [ -S "$SOCKET" ]; then
  if kitten @ --to="unix:$SOCKET" ls >/dev/null 2>&1; then
    kitten @ --to="unix:$SOCKET" focus-window >/dev/null 2>&1 || true
    exit 0
  fi
  rm -f "$SOCKET"
fi

if ! tmux -L "$TMUX_L" has-session -t "$SESSION" 2>/dev/null; then
  tmux -L "$TMUX_L" new-session -d -s "$SESSION" -n ALICE \
    -c "$SEAL_HOME/alice" "bash $SEAL_HOME/alice_fresh.sh"
fi

setsid -f kitty \
    --listen-on "unix:$SOCKET" \
    -o allow_remote_control=yes \
    -o initial_window_width=130c \
    -o initial_window_height=40c \
    --title "ALICE — Team SEAL" \
    bash -lc "unset TMUX; exec tmux -L $TMUX_L attach -t $SESSION" \
    </dev/null >> "$LOG" 2>&1
