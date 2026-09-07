#!/bin/bash
# alice_message_detector.sh — Systemd detector de mensajes para ALICE
# Corre cada 1 min via systemd. Si hay mensajes nuevos en alice_messages.jsonl, escribe signal.
# 0 tokens Claude. Puro bash.

LOGFILE="$HOME/IA/proyecto-seal/messages/alice_messages.jsonl"
COUNTER="$HOME/IA/proyecto-seal/messages/.alice_detector_count"
SIGNAL="$HOME/IA/proyecto-seal/messages/.alice_new_signal"

[ -f "$LOGFILE" ] || exit 0

CUR=$(wc -l < "$LOGFILE" 2>/dev/null | tr -d ' ')
LAST=$(cat "$COUNTER" 2>/dev/null | tr -d ' ')
[ -z "$LAST" ] && LAST="$CUR"

# Counter corruption recovery
[ "$LAST" -gt "$CUR" ] && LAST="$CUR"

if [ "$CUR" -gt "$LAST" ]; then
    NEW_N=$((CUR - LAST))
    echo "{\"new\":$NEW_N,\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$SIGNAL"
fi

echo "$CUR" > "$COUNTER"
