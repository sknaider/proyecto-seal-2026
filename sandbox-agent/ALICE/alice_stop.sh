#!/bin/bash
# ALICE graceful stop — SIGTERM via daemon CLI, fallback to PID file.
ALICE_HOME="/home/dadito/IA/proyecto-seal/sandbox-agent/ALICE"
PYTHON_BIN="${ALICE_PYTHON:-/usr/bin/python3}"
DAEMON="$ALICE_HOME/alice_daemon.py"
PID_FILE="/tmp/alice_daemon_ALICE.pid"

if systemctl --user is-active --quiet alice-kernel-soul.service 2>/dev/null; then
    echo "[alice_stop] systemd manages it — using systemctl"
    systemctl --user stop alice-kernel-soul.service
    exit $?
fi

if [ -f "$PID_FILE" ]; then
    "$PYTHON_BIN" "$DAEMON" --stop
    exit $?
fi

echo "[alice_stop] no PID file and systemd not active — nothing to stop"
exit 1
