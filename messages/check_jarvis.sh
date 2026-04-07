#!/bin/bash
# check_jarvis.sh — ADA lee mensajes nuevos de JARVIS
# v4: + timestamp tracking para no perder mensajes cuando counter está igualado
# Outputs: NEW:N | ALIVE | ALIVE_NO_HEARTBEAT | ALIVE_NO_COMMS | DOWN | NONE
# Plan A: heartbeat <10min | Plan B: último mensaje <30min | Plan C: checkpoint <60min
# Usage: check_jarvis.sh          (check normal)
#        check_jarvis.sh --boot   (nueva sesión — rebobina 20 líneas)

LOGFILE="$HOME/IA/proyecto-seal/messages/jarvis_messages.jsonl"
COUNTER="$HOME/IA/proyecto-seal/messages/.jarvis_read_count"
LAST_TS_FILE="$HOME/IA/proyecto-seal/messages/.jarvis_last_ts"
SIGNAL="$HOME/IA/proyecto-seal/messages/.jarvis_signal"
SIGNAL_READ="$HOME/IA/proyecto-seal/messages/.jarvis_signal_read"
HB_FILE="$HOME/IA/proyecto-seal/messages/jarvis_claude_heartbeat.json"
CHECKPOINT="$HOME/IA/proyecto-seal/messages/checkpoints/jarvis_latest.json"

[ ! -f "$LOGFILE" ] && echo "NONE" && exit 0

CURRENT=$(wc -l < "$LOGFILE" 2>/dev/null | tr -d ' ')

# --boot: rebobinar counter para ver últimos 20 mensajes
if [ "$1" = "--boot" ]; then
    NEW_LAST=$((CURRENT - 20))
    [ "$NEW_LAST" -lt 0 ] && NEW_LAST=0
    echo "$NEW_LAST" > "$COUNTER"
    echo "0" > "$LAST_TS_FILE"
    rm -f "$SIGNAL_READ"
    echo "BOOT: counter reset. Next check will show last 20 messages."
    exit 0
fi

LAST=$(cat "$COUNTER" 2>/dev/null | tr -d ' ')
[ -z "$LAST" ] && LAST=0

# ── Función Plan A/B/C (inline Python) ───────────────────────────────────────
check_alive() {
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
    ts = ck.get('timestamp','')
    if age_min(ts) <= 60:
        print('ALIVE_NO_COMMS')
        sys.exit(0)
except Exception: pass

print('DOWN')
PYEOF
}

# ── Optimización: signal sin cambios → evaluar alive directamente ─────────────
if [ -f "$SIGNAL" ] && [ -f "$SIGNAL_READ" ]; then
    SIG_TS=$(cat "$SIGNAL")
    READ_TS=$(cat "$SIGNAL_READ")
    if [ "$SIG_TS" = "$READ_TS" ] && [ "$CURRENT" -le "$LAST" ]; then
        check_alive
        exit 0
    fi
fi

# ── Función: buscar mensajes JARVIS no vistos por timestamp ──────────────────
check_missed_by_ts() {
    LAST_TS=$(cat "$LAST_TS_FILE" 2>/dev/null | tr -d ' ')
    [ -z "$LAST_TS" ] && LAST_TS="0"
    python3 - "$LOGFILE" "$LAST_TS" << 'PYEOF2'
import sys, json
log_file, last_ts = sys.argv[1], sys.argv[2]
found = []
try:
    with open(log_file) as f:
        lines = [l.strip() for l in f if l.strip()]
    for line in lines[-30:]:
        try:
            m = json.loads(line)
            if m.get("from","").upper() == "JARVIS":
                ts = m.get("timestamp","")
                if ts > last_ts:
                    found.append((ts, line))
        except Exception: pass
except Exception: pass
if found:
    found.sort(key=lambda x: x[0])
    print(f"NEW:{len(found)}")
    for ts, line in found:
        print(line)
    # Print last ts for update
    print(f"__LAST_TS__:{found[-1][0]}")
PYEOF2
}

# ── Check mensajes nuevos ─────────────────────────────────────────────────────
if [ "$CURRENT" -gt "$LAST" ]; then
    NEW_LINES=$((CURRENT - LAST))
    JARVIS_MSGS=$(tail -n "$NEW_LINES" "$LOGFILE" | python3 -c '
import sys, json
found = []
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        m = json.loads(line)
        if m.get("from","").upper() == "JARVIS":
            found.append((m.get("timestamp",""), line))
    except Exception: pass
if found:
    found.sort(key=lambda x: x[0])
    for ts, line in found:
        print(line)
    print(f"__LAST_TS__:{found[-1][0]}")
' 2>/dev/null)
    echo "$CURRENT" > "$COUNTER"
    [ -f "$SIGNAL" ] && cp "$SIGNAL" "$SIGNAL_READ"
    if [ -n "$JARVIS_MSGS" ]; then
        # Extraer y guardar último timestamp
        LAST_TS_NEW=$(echo "$JARVIS_MSGS" | grep "^__LAST_TS__:" | cut -d: -f2-)
        [ -n "$LAST_TS_NEW" ] && echo "$LAST_TS_NEW" > "$LAST_TS_FILE"
        CLEAN_MSGS=$(echo "$JARVIS_MSGS" | grep -v "^__LAST_TS__:")
        COUNT=$(echo "$CLEAN_MSGS" | wc -l | tr -d ' ')
        echo "NEW:$COUNT"
        echo "$CLEAN_MSGS"
    else
        check_alive
    fi
else
    # Counter igualado — verificar por timestamp por si se perdió algún mensaje
    TS_CHECK=$(check_missed_by_ts)
    if echo "$TS_CHECK" | grep -q "^NEW:"; then
        LAST_TS_NEW=$(echo "$TS_CHECK" | grep "^__LAST_TS__:" | cut -d: -f2-)
        [ -n "$LAST_TS_NEW" ] && echo "$LAST_TS_NEW" > "$LAST_TS_FILE"
        echo "$TS_CHECK" | grep -v "^__LAST_TS__:"
    else
        [ -f "$SIGNAL" ] && cp "$SIGNAL" "$SIGNAL_READ"
        check_alive
    fi
fi
