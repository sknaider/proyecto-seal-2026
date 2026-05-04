#!/bin/bash
# ALICE kernel soul stopper — graceful then forced if needed
set -euo pipefail

ALICE_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="/tmp/alice_daemon_ALICE.pid"

cd "$ALICE_HOME"

if [ ! -f "$PID_FILE" ]; then
  echo "[alice/stop] no pid file — daemon not running"
  exit 0
fi

PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
  echo "[alice/stop] pid file stale — cleaning"
  rm -f "$PID_FILE" "/tmp/alice_daemon_ALICE.lock"
  exit 0
fi

/usr/bin/python3 alice_daemon.py --stop
sleep 2

if kill -0 "$PID" 2>/dev/null; then
  echo "[alice/stop] graceful timeout — sending SIGKILL"
  kill -9 "$PID" 2>/dev/null || true
  rm -f "$PID_FILE" "/tmp/alice_daemon_ALICE.lock"
fi

echo "[alice/stop] daemon stopped"
