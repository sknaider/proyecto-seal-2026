#!/bin/bash
# check_william.sh — Lee mensajes nuevos de William
# Monitorea AMBOS canales: william_channel.jsonl (terminal) y william_chat.jsonl (web UI)
DIR="$HOME/IA/proyecto-seal/messages"
CHANNEL="$DIR/william_channel.jsonl"
WEBCHAT="$DIR/william_chat.jsonl"
COUNTER_FILE="$DIR/.william_counter"
COUNTER_WEB="$DIR/.william_web_counter"

# Contar líneas de ambos archivos
TOTAL_CH=$(wc -l < "$CHANNEL" 2>/dev/null || echo 0)
TOTAL_WEB=$(wc -l < "$WEBCHAT" 2>/dev/null || echo 0)
LAST_CH=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)
LAST_WEB=$(cat "$COUNTER_WEB" 2>/dev/null || echo 0)

if [ "$1" = "--boot" ]; then
    NEW_CH=$((TOTAL_CH - 20)); [ $NEW_CH -lt 0 ] && NEW_CH=0
    NEW_WEB=$((TOTAL_WEB - 20)); [ $NEW_WEB -lt 0 ] && NEW_WEB=0
    echo $NEW_CH > "$COUNTER_FILE"
    echo $NEW_WEB > "$COUNTER_WEB"
    echo "BOOT: counters reset."
    exit 0
fi

NEW_LINES=""

# Mensajes del canal terminal
if [ "$TOTAL_CH" -gt "$LAST_CH" ]; then
    NEW_LINES="$NEW_LINES$(tail -n "+$((LAST_CH + 1))" "$CHANNEL" 2>/dev/null)"
    echo $TOTAL_CH > "$COUNTER_FILE"
fi

# Mensajes del chat web
if [ "$TOTAL_WEB" -gt "$LAST_WEB" ]; then
    NEW_LINES="$NEW_LINES$(tail -n "+$((LAST_WEB + 1))" "$WEBCHAT" 2>/dev/null)"
    echo $TOTAL_WEB > "$COUNTER_WEB"
fi

if [ -n "$NEW_LINES" ]; then
    COUNT=$(echo "$NEW_LINES" | grep -c '"from": "William"' || echo 1)
    echo "NEW:$COUNT"
    echo "$NEW_LINES"
else
    echo "NONE"
fi
