#!/bin/bash
# Kill zombie/orphan claude agent processes
# Runs via cron every 30min — kills claude processes without a terminal (TTY = ?)
# that have been running >6 hours (likely orphans from closed sessions)

THRESHOLD_HOURS=6

ps aux | grep "claude.*--name" | grep -v grep | while read -r line; do
  PID=$(echo "$line" | awk '{print $2}')
  TTY=$(echo "$line" | awk '{print $7}')
  ELAPSED=$(ps -p "$PID" -o etimes= 2>/dev/null | tr -d ' ')
  
  # Only kill if: no terminal (TTY=?) AND running > threshold
  if [ "$TTY" = "?" ] && [ -n "$ELAPSED" ] && [ "$ELAPSED" -gt $((THRESHOLD_HOURS * 3600)) ]; then
    AGENT=$(echo "$line" | grep -oP '(?<=--name )\S+')
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) Killing zombie $AGENT (PID $PID, TTY=$TTY, uptime=${ELAPSED}s)"
    kill "$PID" 2>/dev/null
    sleep 2
    kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null
  fi
done
