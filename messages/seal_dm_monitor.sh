#!/bin/bash
# seal_dm_monitor.sh — Tail DM inbox JSONL → write events to log for Monitor consumption.
#
# Usage: seal_dm_monitor.sh <AGENT>   (case-insensitive, accepts JARVIS or jarvis)
#
# Reads from: /home/dadito/IA/proyecto-seal/messages/{agent_lc}_inbox.jsonl
# Writes to:  /tmp/seal_events_{AGENT_UC}_dm.log
#
# Intended to be run as a systemd service `seal-dm-monitor@AGENT.service` for
# durability across Claude session restarts, but can also be invoked ad-hoc via
# nohup for one-shot launches.
set -euo pipefail

AGENT_RAW="${1:?Uso: $0 AGENT (e.g. JARVIS or jarvis)}"
AGENT_LC=$(echo "$AGENT_RAW" | tr 'A-Z' 'a-z')
AGENT_UC=$(echo "$AGENT_RAW" | tr 'a-z' 'A-Z')

INBOX="/home/dadito/IA/proyecto-seal/messages/${AGENT_LC}_inbox.jsonl"
OUTPUT="/tmp/seal_events_${AGENT_UC}_dm.log"
STATE="/tmp/seal_events_${AGENT_UC}_dm.cursor"

# Ensure inbox file exists (touch creates an empty file the first time the
# agent boots before any DM has arrived; tail -F handles file rotation later).
[ -f "$INBOX" ] || touch "$INBOX"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] DM monitor ${AGENT_UC} started — tailing ${INBOX} → ${OUTPUT}" >&2

# Persist a line cursor so restarts replay messages received while the monitor
# was down. First boot starts at EOF to avoid replaying ancient DM history.
if [ ! -f "$STATE" ]; then
  wc -l < "$INBOX" > "$STATE"
fi

while true; do
  TOTAL=$(wc -l < "$INBOX" 2>/dev/null || echo 0)
  LAST=$(cat "$STATE" 2>/dev/null || echo 0)
  if ! [[ "$LAST" =~ ^[0-9]+$ ]]; then
    LAST=0
  fi
  if [ "$TOTAL" -lt "$LAST" ]; then
    LAST=0
  fi
  if [ "$TOTAL" -gt "$LAST" ]; then
    sed -n "$((LAST + 1)),${TOTAL}p" "$INBOX" >> "$OUTPUT"
    echo "$TOTAL" > "$STATE"
  fi
  sleep 1
done
