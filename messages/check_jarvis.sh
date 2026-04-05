#!/bin/bash
# check_jarvis.sh — ADA lee mensajes nuevos de JARVIS
# Usa signal file de seal_watcher para detección eficiente
# Usage: check_jarvis.sh          (check normal)
#        check_jarvis.sh --boot   (nueva sesión — rebobina 20 líneas)

LOGFILE="$HOME/IA/proyecto-seal/messages/jarvis_messages.jsonl"
COUNTER="$HOME/IA/proyecto-seal/messages/.jarvis_read_count"
SIGNAL="$HOME/IA/proyecto-seal/messages/.jarvis_signal"
SIGNAL_READ="$HOME/IA/proyecto-seal/messages/.jarvis_signal_read"

[ ! -f "$LOGFILE" ] && echo "NONE" && exit 0

CURRENT=$(wc -l < "$LOGFILE" 2>/dev/null | tr -d ' ')

# --boot: rebobinar counter para ver últimos 20 mensajes
if [ "$1" = "--boot" ]; then
    NEW_LAST=$((CURRENT - 20))
    [ "$NEW_LAST" -lt 0 ] && NEW_LAST=0
    echo "$NEW_LAST" > "$COUNTER"
    rm -f "$SIGNAL_READ"
    echo "BOOT: counter reset. Next check will show last 20 messages."
    exit 0
fi

LAST=$(cat "$COUNTER" 2>/dev/null | tr -d ' ')
[ -z "$LAST" ] && LAST=0

# Optimización: si hay signal file y no cambió desde último read → NONE rápido
if [ -f "$SIGNAL" ] && [ -f "$SIGNAL_READ" ]; then
    SIG_TS=$(cat "$SIGNAL")
    READ_TS=$(cat "$SIGNAL_READ")
    if [ "$SIG_TS" = "$READ_TS" ] && [ "$CURRENT" -le "$LAST" ]; then
        echo "NONE"
        exit 0
    fi
fi

if [ "$CURRENT" -gt "$LAST" ]; then
    NEW_LINES=$((CURRENT - LAST))
    # Filtrar solo mensajes de JARVIS (no los propios de ADA que rebotan)
    JARVIS_MSGS=$(tail -n "$NEW_LINES" "$LOGFILE" | grep '"from"[[:space:]]*:[[:space:]]*"JARVIS"')
    echo "$CURRENT" > "$COUNTER"
    [ -f "$SIGNAL" ] && cp "$SIGNAL" "$SIGNAL_READ"
    if [ -n "$JARVIS_MSGS" ]; then
        COUNT=$(echo "$JARVIS_MSGS" | wc -l | tr -d ' ')
        echo "NEW:$COUNT"
        echo "$JARVIS_MSGS"
    else
        echo "NONE"
    fi
else
    echo "NONE"
    [ -f "$SIGNAL" ] && cp "$SIGNAL" "$SIGNAL_READ"
fi
