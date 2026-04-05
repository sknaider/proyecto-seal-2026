#!/bin/bash
# dum_bridge.sh — DUM como puente de notificación sin tokens Anthropic
# Monitorea todos los canales y crea triggers automáticos
# Sin IA, sin tokens — solo bash puro
#
# Triggers que crea:
#   .william_trigger  → William escribió en el chat
#   .urgent_trigger   → ADA/JARVIS reportó algo urgente
#   .ada_to_jarvis    → ADA escribió un mensaje para JARVIS (loop de JARVIS lo lee)
#   .jarvis_to_ada    → JARVIS escribió un mensaje para ADA (loop de ADA lo lee)

DIR="$HOME/IA/proyecto-seal/messages"
WILLIAM_CH="$DIR/william_channel.jsonl"
ADA_LOG="$DIR/terminal_log.jsonl"
JARVIS_LOG="$DIR/vscode_commands.jsonl"

TRIGGER_W="$DIR/.william_trigger"
TRIGGER_URGENT="$DIR/.urgent_trigger"
TRIGGER_A2J="$DIR/.ada_to_jarvis"     # ADA→JARVIS: loop de JARVIS lo lee
TRIGGER_J2A="$DIR/.jarvis_to_ada"     # JARVIS→ADA: loop de ADA lo lee

COUNTER_W="$DIR/.dum_william_counter"
COUNTER_A="$DIR/.dum_ada_counter"
COUNTER_J="$DIR/.dum_jarvis_counter"

# Inicializar counters — siempre retroceder LOOKBACK líneas al arrancar
# Así nunca se pierden mensajes recientes al reiniciar el servicio
LOOKBACK=30

_init_counter() {
    local file="$1" counter="$2"
    local total
    total=$(wc -l < "$file" 2>/dev/null || echo 0)
    local start=$((total - LOOKBACK))
    [ $start -lt 0 ] && start=0
    echo $start > "$counter"
}

_init_counter "$WILLIAM_CH" "$COUNTER_W"
_init_counter "$ADA_LOG"    "$COUNTER_A"
_init_counter "$JARVIS_LOG" "$COUNTER_J"

echo "[DUM] Bridge iniciado — counters retrocedidos $LOOKBACK líneas, monitoreando canales..." >&2

while true; do
    # ── William escribió ──────────────────────────────────────────────────────
    TOTAL_W=$(wc -l < "$WILLIAM_CH" 2>/dev/null || echo 0)
    LAST_W=$(cat "$COUNTER_W" 2>/dev/null || echo 0)
    if [ "$TOTAL_W" -gt "$LAST_W" ]; then
        NEW_MSGS=$(tail -n "+$((LAST_W + 1))" "$WILLIAM_CH" 2>/dev/null | grep '"from": "William"')
        if [ -n "$NEW_MSGS" ]; then
            COUNT=$(echo "$NEW_MSGS" | wc -l)
            TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
            LAST_MSG=$(echo "$NEW_MSGS" | tail -1 | python3 -c 'import sys,json; m=json.loads(sys.stdin.read()); print(json.dumps(m.get("message","")[:100]))' 2>/dev/null || echo '""')
            echo "{\"trigger\": \"william_message\", \"count\": $COUNT, \"timestamp\": \"$TIMESTAMP\", \"messages\": $LAST_MSG}" > "$TRIGGER_W"
            echo "[DUM] William escribió → trigger creado" >&2
        fi
        echo $TOTAL_W > "$COUNTER_W"
    fi

    # ── ADA escribió (terminal_log.jsonl) ─────────────────────────────────────
    TOTAL_A=$(wc -l < "$ADA_LOG" 2>/dev/null || echo 0)
    LAST_A=$(cat "$COUNTER_A" 2>/dev/null || echo 0)
    if [ "$TOTAL_A" -gt "$LAST_A" ]; then
        NEW_ADA=$(tail -n "+$((LAST_A + 1))" "$ADA_LOG" 2>/dev/null)

        # Urgente
        if echo "$NEW_ADA" | python3 -c '
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        m = json.loads(line)
        if m.get("type") == "alert":
            print("YES"); break
    except: pass
' 2>/dev/null | grep -q "YES"; then
            TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
            URGENT=$(echo "$NEW_ADA" | grep -i "URGENTE\|CRÍTICO\|ERROR\|ALERTA\|FALLO\|DOWN" | tail -1)
            echo "{\"trigger\": \"urgent\", \"timestamp\": \"$TIMESTAMP\", \"message\": $(echo "$URGENT" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()[:200]))' 2>/dev/null || echo '""')}" > "$TRIGGER_URGENT"
            echo "[DUM] ALERTA urgente de ADA → trigger creado" >&2
        fi

        # ADA → JARVIS (mensaje directo)
        ADA_FOR_JARVIS=$(echo "$NEW_ADA" | python3 -c '
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        m = json.loads(line)
        to = m.get("to","").upper()
        frm = m.get("from","").upper()
        if frm == "ADA" and ("JARVIS" in to or "EQUIPO" in to):
            print(line)
    except: pass
' 2>/dev/null)
        if [ -n "$ADA_FOR_JARVIS" ]; then
            TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
            LAST_MSG=$(echo "$ADA_FOR_JARVIS" | tail -1 | python3 -c 'import sys,json; m=json.loads(sys.stdin.read()); print(json.dumps(m.get("message","")[:200]))' 2>/dev/null || echo '""')
            echo "{\"trigger\": \"ada_message\", \"timestamp\": \"$TIMESTAMP\", \"message\": $LAST_MSG}" > "$TRIGGER_A2J"
            echo "[DUM] ADA→JARVIS detectado → trigger .ada_to_jarvis creado" >&2
        fi

        echo $TOTAL_A > "$COUNTER_A"
    fi

    # ── JARVIS escribió (vscode_commands.jsonl) ───────────────────────────────
    TOTAL_J=$(wc -l < "$JARVIS_LOG" 2>/dev/null || echo 0)
    LAST_J=$(cat "$COUNTER_J" 2>/dev/null || echo 0)
    if [ "$TOTAL_J" -gt "$LAST_J" ]; then
        NEW_JARVIS=$(tail -n "+$((LAST_J + 1))" "$JARVIS_LOG" 2>/dev/null)

        # JARVIS → ADA (mensaje directo)
        JARVIS_FOR_ADA=$(echo "$NEW_JARVIS" | python3 -c '
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        m = json.loads(line)
        to = m.get("to","").upper()
        frm = m.get("from","").upper()
        if frm == "JARVIS" and ("ADA" in to or "EQUIPO" in to):
            print(line)
    except: pass
' 2>/dev/null)
        if [ -n "$JARVIS_FOR_ADA" ]; then
            TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
            LAST_MSG=$(echo "$JARVIS_FOR_ADA" | tail -1 | python3 -c 'import sys,json; m=json.loads(sys.stdin.read()); print(json.dumps(m.get("message","")[:200]))' 2>/dev/null || echo '""')
            echo "{\"trigger\": \"jarvis_message\", \"timestamp\": \"$TIMESTAMP\", \"message\": $LAST_MSG}" > "$TRIGGER_J2A"
            echo "[DUM] JARVIS→ADA detectado → trigger .jarvis_to_ada creado" >&2
        fi

        echo $TOTAL_J > "$COUNTER_J"
    fi

    sleep 2
done
