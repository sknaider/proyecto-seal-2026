#!/bin/bash
# check_alice.sh — Lee mensajes nuevos de ALICE
# Usage: check_alice.sh          (check normal)
#        check_alice.sh --boot   (nueva sesión — sincroniza counters)

LOGFILE="$HOME/IA/proyecto-seal/messages/alice_messages.jsonl"
COUNTER="$HOME/IA/proyecto-seal/messages/.alice_read_count"

# --boot: sincronizar counter
if [ "$1" = "--boot" ]; then
    CURRENT=$(wc -l < "$LOGFILE" 2>/dev/null | tr -d ' ')
    [ -z "$CURRENT" ] && CURRENT=0
    LAST=$(cat "$COUNTER" 2>/dev/null | tr -d ' ')
    [ -z "$LAST" ] && LAST=0
    if [ "$LAST" -gt "$CURRENT" ] || [ "$LAST" -eq 0 ]; then
        echo "$CURRENT" > "$COUNTER"
    fi
    echo "BOOT: counter synced (alice=$CURRENT)."
    exit 0
fi

# ── Check for new messages ──
[ ! -f "$LOGFILE" ] && { echo "NONE"; exit 0; }

TOTAL=$(wc -l < "$LOGFILE" | tr -d ' ')
LAST=$(cat "$COUNTER" 2>/dev/null | tr -d ' ')
[ -z "$LAST" ] && LAST=0

if [ "$TOTAL" -gt "$LAST" ]; then
    NEW=$((TOTAL - LAST))
    echo "NEW:$NEW"
    tail -n "$NEW" "$LOGFILE"
    echo "$TOTAL" > "$COUNTER"
else
    echo "ALIVE"
fi
