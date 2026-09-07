#!/bin/bash
# jarvis_ada_detector.sh — Systemd detector de mensajes ADA para JARVIS
# Corre cada 1 min via systemd. Si hay mensajes nuevos, escribe signal.
# Claude solo despierta cuando hay signal → ahorra tokens masivamente.
# 0 tokens Claude. Puro bash.

LOGFILE="$HOME/IA/proyecto-seal/messages/ada_messages.jsonl"
COUNTER="$HOME/IA/proyecto-seal/messages/.ada_detector_count"
SIGNAL="$HOME/IA/proyecto-seal/messages/.ada_new_signal"

[ -f "$LOGFILE" ] || exit 0

CUR=$(wc -l < "$LOGFILE" 2>/dev/null | tr -d ' ')
LAST=$(cat "$COUNTER" 2>/dev/null | tr -d ' ')
[ -z "$LAST" ] && LAST="$CUR"

# Counter corruption recovery
[ "$LAST" -gt "$CUR" ] && LAST="$CUR"

if [ "$CUR" -gt "$LAST" ]; then
    # Hay mensajes nuevos — escribir signal con count
    NEW_N=$((CUR - LAST))
    echo "{\"new\":$NEW_N,\"ts\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}" > "$SIGNAL"
fi

echo "$CUR" > "$COUNTER"
