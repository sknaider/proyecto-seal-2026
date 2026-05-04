#!/bin/bash
# ALICE fresh launcher — foreground with auto-restart on crash.
# For systemd-managed runs, use: systemctl --user start alice-kernel-soul.service
ALICE_HOME="/home/dadito/IA/proyecto-seal/sandbox-agent/ALICE"
PYTHON_BIN="${ALICE_PYTHON:-/usr/bin/python3}"
DAEMON="$ALICE_HOME/alice_daemon.py"
LOG_DIR="$ALICE_HOME/logs"
PID_FILE="/tmp/alice_daemon_ALICE.pid"
LOCK_FILE="/tmp/alice_daemon_ALICE.lock"

mkdir -p "$LOG_DIR"

if [ -f "$ALICE_HOME/alice.env" ]; then
    set -a
    . "$ALICE_HOME/alice.env"
    set +a
fi

while true; do
    cd "$ALICE_HOME" || exit 1

    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            sleep 10
            continue
        fi
    fi

    rm -f "$LOCK_FILE" "$PID_FILE"

    echo "[alice_fresh] starting daemon — $(date '+%F %T')"
    "$PYTHON_BIN" "$DAEMON" 2>&1 | tee -a "$LOG_DIR/alice_daemon.log"

    echo "[alice_fresh] daemon exited — restart in 5s"
    sleep 5
done
