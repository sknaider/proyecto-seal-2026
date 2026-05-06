#!/bin/bash
# NEXUS stop — graceful shutdown via SIGTERM + cleanup.
NEXUS_HOME="/home/dadito/IA/proyecto-seal/sandbox-agent/NEXUS"
VENV_PY="/home/dadito/IA/seal-spark/.venv/bin/python3"
PID_FILE="/tmp/nexus_daemon_NEXUS.pid"
LOCK_FILE="/tmp/nexus_daemon_NEXUS.lock"

# 1. SIGTERM via daemon's --stop flag
"$VENV_PY" "$NEXUS_HOME/nexus_daemon.py" --stop

# 2. Kill auto-restart loop (nexus_fresh.sh)
pkill -f "nexus_fresh.sh" 2>/dev/null && echo "[NEXUS] auto-restart loop killed"

# 3. Wait for graceful exit
sleep 2

# 4. Force kill if still alive
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE" 2>/dev/null)
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "[NEXUS] still alive — forcing SIGKILL"
        kill -9 "$PID"
    fi
fi

# 5. Clean stale files
rm -f "$LOCK_FILE" "$PID_FILE"
echo "[NEXUS] stopped — locks cleaned"
