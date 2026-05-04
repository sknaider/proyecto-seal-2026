#!/bin/bash
# ALICE kernel soul launcher (manual/dev — systemd path is preferred for prod)
set -euo pipefail

ALICE_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ALICE_HOME/logs"
LOG_FILE="$LOG_DIR/alice-kernel-soul.log"
PID_FILE="/tmp/alice_daemon_ALICE.pid"

mkdir -p "$LOG_DIR"

if [ -f "$PID_FILE" ]; then
  RUNNING_PID=$(cat "$PID_FILE" 2>/dev/null || echo "")
  if [ -n "$RUNNING_PID" ] && kill -0 "$RUNNING_PID" 2>/dev/null; then
    echo "[alice/start] already running pid=$RUNNING_PID — abort"
    exit 1
  fi
  echo "[alice/start] stale pid file — clearing"
  rm -f "$PID_FILE" "/tmp/alice_daemon_ALICE.lock"
fi

cd "$ALICE_HOME"
nohup /usr/bin/python3 alice_daemon.py >> "$LOG_FILE" 2>&1 &
NEW_PID=$!
sleep 1
if kill -0 "$NEW_PID" 2>/dev/null; then
  echo "[alice/start] launched pid=$NEW_PID — log=$LOG_FILE"
else
  echo "[alice/start] failed to launch — see $LOG_FILE"
  exit 1
fi
