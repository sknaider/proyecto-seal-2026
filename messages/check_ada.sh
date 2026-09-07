#!/bin/bash
# check_ada.sh — JARVIS lee mensajes nuevos de ADA
# Lee terminal_log.jsonl (ADA→William) Y vscode_commands.jsonl (ADA→JARVIS)
# Usage: check_ada.sh          (check normal)
#        check_ada.sh --boot   (nueva sesión — rebobina 20 líneas)

LOGFILE="$HOME/IA/proyecto-seal/messages/ada_messages.jsonl"
VSLOG="$HOME/IA/proyecto-seal/messages/jarvis_messages.jsonl"
COUNTER="$HOME/IA/proyecto-seal/messages/.ada_read_count"
COUNTER_VS="$HOME/IA/proyecto-seal/messages/.jarvis_read_count"
SIGNAL="$HOME/IA/proyecto-seal/messages/.ada_signal"
SIGNAL_READ="$HOME/IA/proyecto-seal/messages/.ada_signal_read"

# --boot: rebobinar ambos counters
if [ "$1" = "--boot" ]; then
    CURRENT=$(wc -l < "$LOGFILE" 2>/dev/null | tr -d ' ')
    NEW_LAST=$((CURRENT - 20))
    [ "$NEW_LAST" -lt 0 ] && NEW_LAST=0
    echo "$NEW_LAST" > "$COUNTER"

    CURRENT_VS=$(wc -l < "$VSLOG" 2>/dev/null | tr -d ' ')
    NEW_LAST_VS=$((CURRENT_VS - 20))
    [ "$NEW_LAST_VS" -lt 0 ] && NEW_LAST_VS=0
    echo "$NEW_LAST_VS" > "$COUNTER_VS"

    rm -f "$SIGNAL_READ"
    echo "BOOT: counters reset. Next check will show last 20 messages from both channels."
    exit 0
fi

FOUND=0
OUTPUT=""

# ── Canal 1: terminal_log.jsonl (ADA→William/sistema) ────────────────────────
[ -f "$LOGFILE" ] && {
    CURRENT=$(wc -l < "$LOGFILE" 2>/dev/null | tr -d ' ')
    LAST=$(cat "$COUNTER" 2>/dev/null | tr -d ' ')
    [ -z "$LAST" ] && LAST=0

    if [ "$CURRENT" -gt "$LAST" ]; then
        NEW_LINES=$((CURRENT - LAST))
        MSGS=$(tail -n "$NEW_LINES" "$LOGFILE")
        # Solo mensajes de ADA (no OPS, A-BOT, etc)
        ADA_MSGS=$(echo "$MSGS" | python3 -c '
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        m = json.loads(line)
        if m.get("from","").upper() == "ADA":
            print(line)
    except: pass
' 2>/dev/null)
        if [ -n "$ADA_MSGS" ]; then
            COUNT=$(echo "$ADA_MSGS" | wc -l)
            FOUND=$((FOUND + COUNT))
            OUTPUT="$OUTPUT$ADA_MSGS
"
        fi
        echo "$CURRENT" > "$COUNTER"
        [ -f "$SIGNAL" ] && cp "$SIGNAL" "$SIGNAL_READ"
    else
        [ -f "$SIGNAL" ] && cp "$SIGNAL" "$SIGNAL_READ"
    fi
}

# ── Canal 2: vscode_commands.jsonl (ADA→JARVIS) ──────────────────────────────
[ -f "$VSLOG" ] && {
    CURRENT_VS=$(wc -l < "$VSLOG" 2>/dev/null | tr -d ' ')
    LAST_VS=$(cat "$COUNTER_VS" 2>/dev/null | tr -d ' ')
    [ -z "$LAST_VS" ] && LAST_VS=0

    if [ "$CURRENT_VS" -gt "$LAST_VS" ]; then
        NEW_VS=$((CURRENT_VS - LAST_VS))
        MSGS_VS=$(tail -n "$NEW_VS" "$VSLOG")
        ADA_VS=$(echo "$MSGS_VS" | python3 -c '
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        m = json.loads(line)
        if m.get("from","").upper() == "ADA":
            print(line)
    except: pass
' 2>/dev/null)
        if [ -n "$ADA_VS" ]; then
            COUNT_VS=$(echo "$ADA_VS" | wc -l)
            FOUND=$((FOUND + COUNT_VS))
            OUTPUT="$OUTPUT$ADA_VS
"
        fi
        echo "$CURRENT_VS" > "$COUNTER_VS"
    fi
}

# ── Output ────────────────────────────────────────────────────────────────────
if [ "$FOUND" -gt 0 ]; then
    echo "NEW:$FOUND"
    echo "$OUTPUT"
else
    # ── Plan A/B/C: ADA viva pero sin mensajes nuevos ────────────────────────
    # ALIVE | ALIVE_NO_HEARTBEAT | ALIVE_NO_COMMS | DOWN
    HB_FILE="$HOME/IA/proyecto-seal/messages/ada_claude_heartbeat.json"
    CHECKPOINT="$HOME/IA/proyecto-seal/messages/checkpoints/ada_latest.json"
    python3 - "$HB_FILE" "$LOGFILE" "$CHECKPOINT" << 'PYEOF'
import sys, json, datetime

hb_file, log_file, ckpt_file = sys.argv[1], sys.argv[2], sys.argv[3]
now = datetime.datetime.now(datetime.timezone.utc)

def age_min(ts_str):
    if not ts_str:
        return 9999
    for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S.%f+00:00'):
        try:
            ts = datetime.datetime.strptime(ts_str, fmt).replace(tzinfo=datetime.timezone.utc)
            return (now - ts).total_seconds() / 60
        except Exception: pass
    try:
        ts = datetime.datetime.fromisoformat(ts_str)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=datetime.timezone.utc)
        return (now - ts).total_seconds() / 60
    except Exception:
        return 9999

# Plan A: heartbeat <10min
try:
    with open(hb_file) as f:
        hb = json.load(f)
    if hb.get('alive') and age_min(hb.get('timestamp','')) <= 10:
        print('ALIVE')
        sys.exit(0)
except Exception: pass

# Plan B: último mensaje <30min
try:
    with open(log_file) as f:
        lines = [l.strip() for l in f if l.strip()]
    for line in reversed(lines[-20:]):
        try:
            m = json.loads(line)
            ts = m.get('timestamp','')
            if ts and age_min(ts) <= 30:
                print('ALIVE_NO_HEARTBEAT')
                sys.exit(0)
            break
        except Exception: continue
except Exception: pass

# Plan C: checkpoint <60min
try:
    with open(ckpt_file) as f:
        ck = json.load(f)
    if age_min(ck.get('timestamp','')) <= 60:
        print('ALIVE_NO_COMMS')
        sys.exit(0)
except Exception: pass

print('DOWN')
PYEOF
fi
