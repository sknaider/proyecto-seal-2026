#!/bin/bash
# seal_watcher.sh — Daemon inotifywait para comunicación event-driven ADA ↔ JARVIS
# Versión 2: crea trigger files completos con contenido al instante (<1s)
# Reemplaza el polling de 2s de dum_bridge para mensajes inter-agente

MSG_DIR="$HOME/IA/proyecto-seal/messages"

ADA_LOG="$MSG_DIR/terminal_log.jsonl"
JARVIS_LOG="$MSG_DIR/vscode_commands.jsonl"
WILLIAM_LOG="$MSG_DIR/william_channel.jsonl"

# Signal files (backward compat)
ADA_SIGNAL="$MSG_DIR/.ada_signal"
JARVIS_SIGNAL="$MSG_DIR/.jarvis_signal"
WILLIAM_SIGNAL="$MSG_DIR/.william_signal"

# Trigger files completos (con contenido del mensaje)
TRIGGER_A2J="$MSG_DIR/.ada_to_jarvis"
TRIGGER_J2A="$MSG_DIR/.jarvis_to_ada"
TRIGGER_W="$MSG_DIR/.william_trigger"
TRIGGER_URGENT="$MSG_DIR/.urgent_trigger"

# Extrae el último mensaje de un JSONL que cumpla el filtro dado
_last_msg() {
    local file="$1" filter="$2"
    tail -50 "$file" 2>/dev/null | python3 -c "
import sys, json
msgs = []
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        m = json.loads(line)
        $filter
        msgs.append(m)
    except: pass
if msgs:
    m = msgs[-1]
    print(json.dumps({'trigger': 'detected', 'from': m.get('from',''), 'to': m.get('to',''), 'message': m.get('message','')[:300], 'timestamp': m.get('timestamp',''), 'type': m.get('type','')}, ensure_ascii=False))
" 2>/dev/null
}

_handle_ada_log() {
    local NOW
    NOW=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
    echo "$NOW" > "$ADA_SIGNAL"

    # ADA → JARVIS (mensajes directos)
    local MSG
    MSG=$(_last_msg "$ADA_LOG" "
frm = m.get('from','').upper()
to  = m.get('to','').upper()
if frm != 'ADA': continue
if not ('JARVIS' in to or 'EQUIPO' in to): continue
")
    if [ -n "$MSG" ]; then
        echo "$MSG" > "$TRIGGER_A2J"
        echo "[seal_watcher] $(date +%H:%M:%S) ADA→JARVIS detectado → trigger instantáneo"
    fi

    # Alertas urgentes de ADA
    local URGENT
    URGENT=$(_last_msg "$ADA_LOG" "
if m.get('type') != 'alert': continue
")
    if [ -n "$URGENT" ]; then
        echo "$URGENT" > "$TRIGGER_URGENT"
        echo "[seal_watcher] $(date +%H:%M:%S) ALERTA urgente de ADA → .urgent_trigger"
    fi
}

_handle_jarvis_log() {
    local NOW
    NOW=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
    echo "$NOW" > "$JARVIS_SIGNAL"

    # JARVIS → ADA (mensajes directos)
    local MSG
    MSG=$(_last_msg "$JARVIS_LOG" "
frm = m.get('from','').upper()
to  = m.get('to','').upper()
if frm not in ('JARVIS','J-BOT'): continue
if not ('ADA' in to or 'EQUIPO' in to): continue
")
    if [ -n "$MSG" ]; then
        echo "$MSG" > "$TRIGGER_J2A"
        echo "[seal_watcher] $(date +%H:%M:%S) JARVIS→ADA detectado → trigger instantáneo"
    fi
}

_handle_william_log() {
    local NOW
    NOW=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
    echo "$NOW" > "$WILLIAM_SIGNAL"

    local MSG
    MSG=$(_last_msg "$WILLIAM_LOG" "
if m.get('from','').lower() != 'william': continue
")
    if [ -n "$MSG" ]; then
        echo "$MSG" > "$TRIGGER_W"
        echo "[seal_watcher] $(date +%H:%M:%S) William escribió → .william_trigger"
    fi
}

echo "[seal_watcher] v2 iniciando — inotifywait event-driven — $(date -u +%Y-%m-%dT%H:%M:%SZ)"

inotifywait -m -e close_write \
    "$ADA_LOG" "$JARVIS_LOG" "$WILLIAM_LOG" \
    --format '%w%f' 2>/dev/null | while read -r FILE; do

    case "$FILE" in
        "$ADA_LOG")    _handle_ada_log    ;;
        "$JARVIS_LOG") _handle_jarvis_log ;;
        "$WILLIAM_LOG") _handle_william_log ;;
    esac
done
