#!/bin/bash
# NEXUS fresh launcher — foreground with auto-restart on crash.
# Spec: spec_nexus_kernel_soul_v1.md §8
NEXUS_HOME="/home/dadito/IA/proyecto-seal/sandbox-agent/NEXUS"
VENV_PY="/home/dadito/IA/seal-spark/.venv/bin/python3"
DAEMON="$NEXUS_HOME/nexus_daemon.py"
LOG_DIR="$NEXUS_HOME/logs"
PID_FILE="/tmp/nexus_daemon_NEXUS.pid"
LOCK_FILE="/tmp/nexus_daemon_NEXUS.lock"

mkdir -p "$LOG_DIR"

while true; do
    cd "$NEXUS_HOME"

    # Skip tick if a live daemon already holds the PID
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            sleep 10
            continue
        fi
    fi

    # Clean stale lock/pid before relaunch
    rm -f "$LOCK_FILE" "$PID_FILE"

    echo "[NEXUS] starting daemon — $(date '+%F %T')"
    "$VENV_PY" "$DAEMON" 2>&1 | tee -a "$LOG_DIR/nexus_daemon.log"

    EXIT_CODE=${PIPESTATUS[0]}
    echo "[NEXUS] daemon exited (code=$EXIT_CODE) — restarting in 5s..."
    sleep 5
done
