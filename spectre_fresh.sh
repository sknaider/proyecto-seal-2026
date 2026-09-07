#!/bin/bash
# SPECTRE fresh launcher — foreground con auto-restart (como ada_fresh.sh)
SPECTRE_HOME="/home/dadito/IA/proyecto-seal/sandbox-agent/SPECTRE"
VENV_PY="/home/dadito/IA/seal-spark/.venv/bin/python3"
DAEMON="$SPECTRE_HOME/spectre_daemon.py"
LOG_DIR="$SPECTRE_HOME/logs"
mkdir -p "$LOG_DIR"

while true; do
    cd "$SPECTRE_HOME"
    PID_FILE="/tmp/spectre_daemon_SPECTRE.pid"
    LOCK_FILE="/tmp/spectre_daemon_SPECTRE.lock"

    # If daemon is already alive (started externally or by us), skip this tick
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
        if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
            sleep 10
            continue
        fi
        # Stale PID — clear lock and restart
        rm -f "$PID_FILE" "$LOCK_FILE"
    fi

    echo "[SPECTRE] iniciando — $(date '+%Y-%m-%d %H:%M:%S')"
    "$VENV_PY" "$DAEMON" 2>&1 | tee -a "$LOG_DIR/spectre_$(date +%Y%m%d).log"
    # El estado relevante es el del daemon, no el de tee.
    EXIT_CODE=${PIPESTATUS[0]}
    echo "[SPECTRE] salió con código $EXIT_CODE — reiniciando en 5s..."
    sleep 5
done
