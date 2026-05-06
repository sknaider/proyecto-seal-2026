#!/bin/bash
# ws_listener_watchdog.sh — Singleton guard + auto-restart para ws_listener
#
# Corre cada 2 minutos via systemd timer.
# Por cada agente: verifica que haya exactamente 1 ws_listener.
# Si hay 0 → reinicia. Si hay >1 → mata duplicados, deja el más nuevo.

VENV="/home/dadito/IA/seal-spark/.venv/bin/python3"
SCRIPT="/home/dadito/IA/proyecto-seal/messages/ws_listener.py"
WEBCHAT_URL="http://localhost:8765/api/agents/send"
LOG="/home/dadito/IA/proyecto-seal/messages/ws_listener_watchdog.log"
# JARVIS excluido — usa tail -F en william_channel.jsonl (no ws_listener)
# ADA excluida 04-may-2026 (JARVIS): runtime nativo ada_kernel_main.py polea
# william_channel.jsonl directamente, no necesita ws_listener.
# ALICE excluida 04-may-2026 20:54 (JARVIS): William ordeno kill ALICE+NEXUS,
# almas guardadas via end_session.sh, ws_listener sin consumidor → no respawnear.
AGENTS=()

ts() { date '+%Y-%m-%dT%H:%M:%S'; }

notify_webchat() {
    local agent="$1" msg="$2"
    curl -s -X POST "$WEBCHAT_URL" \
        -H "Content-Type: application/json" \
        -d "{\"from\":\"DUM\",\"to\":\"equipo\",\"type\":\"system_alert\",\"channel\":\"web_chat\",\"message\":\"[WS-WATCHDOG] $agent: $msg\"}" \
        > /dev/null 2>&1
}

for AGENT in "${AGENTS[@]}"; do
    PIDS=$(pgrep -f "ws_listener.py --agent $AGENT" 2>/dev/null | tr '\n' ' ' | xargs)
    COUNT=$(echo "$PIDS" | wc -w)

    if [ "$COUNT" -eq 0 ]; then
        # Ningún ws_listener corriendo — arrancar uno
        echo "[$(ts)] [$AGENT] DEAD — arrancando ws_listener" >> "$LOG"
        nohup "$VENV" "$SCRIPT" --agent "$AGENT" \
            >> "/home/dadito/IA/proyecto-seal/messages/ws_listener_${AGENT}.log" 2>&1 &
        NEW_PID=$!
        echo "[$(ts)] [$AGENT] Arrancado PID $NEW_PID" >> "$LOG"
        notify_webchat "$AGENT" "ws_listener muerto — reiniciado (PID $NEW_PID)"

    elif [ "$COUNT" -gt 1 ]; then
        # Duplicados — pero respetar procesos hijo de Claude Code (Monitor)
        # Guardar el MÁS VIEJO (más estable) y matar los nuevos duplicados
        OLDEST=$(echo "$PIDS" | tr ' ' '\n' | sort -n | head -1)
        KILL_PIDS=$(echo "$PIDS" | tr ' ' '\n' | sort -n | tail -n +2 | tr '\n' ' ')
        echo "[$(ts)] [$AGENT] DUPLICADOS ($COUNT) — matando nuevos: $KILL_PIDS, manteniendo $OLDEST" >> "$LOG"
        # Solo matar si el proceso viejo lleva más de 60s corriendo (evitar matar recién iniciados)
        OLD_START=$(ps -o etimes= -p "$OLDEST" 2>/dev/null | tr -d ' ')
        if [ "${OLD_START:-0}" -gt 60 ]; then
            echo "$KILL_PIDS" | xargs kill -TERM 2>/dev/null
        else
            echo "[$(ts)] [$AGENT] Proceso base muy reciente (${OLD_START}s) — skip kill" >> "$LOG"
        fi
    else
        # Exactamente 1 — OK
        echo "[$(ts)] [$AGENT] OK (PID $PIDS)" >> "$LOG"
    fi
done

# ── Heartbeat staleness check (18-abr-2026, ADA item 2/4) ──
# Detecta agente sordo: heartbeat JSON sin actualizar >10 min = Monitor/tool muerto
HEARTBEAT_MAX_AGE=1200  # 20 min (DUM ciclo=900s, threshold debe superar el ciclo)
HEARTBEAT_ALERTED="/tmp/.heartbeat_stale_alerted"
for AGENT in JARVIS; do  # William: solo JARVIS tiene heartbeat activo
    AGENT_LOWER=$(echo "$AGENT" | tr 'A-Z' 'a-z')
    HB="/home/dadito/IA/proyecto-seal/messages/${AGENT_LOWER}_claude_heartbeat.json"
    [ -f "$HB" ] || continue

    # Ignorar si alive=false (agente durmiendo intencionalmente)
    if grep -q '"alive": false' "$HB" 2>/dev/null; then
        continue
    fi

    AGE=$(( $(date +%s) - $(stat -c %Y "$HB") ))
    ALERT_FLAG="${HEARTBEAT_ALERTED}_${AGENT}"

    if [ "$AGE" -gt "$HEARTBEAT_MAX_AGE" ]; then
        # Solo alertar 1 vez por episodio (evita spam)
        if [ ! -f "$ALERT_FLAG" ] || [ "$(( $(date +%s) - $(stat -c %Y "$ALERT_FLAG" 2>/dev/null || echo 0) ))" -gt 1800 ]; then
            echo "[$(ts)] [$AGENT] HEARTBEAT STALE: ${AGE}s (>${HEARTBEAT_MAX_AGE}s) — posible sordera/cuelgue" >> "$LOG"
            notify_webchat "$AGENT" "heartbeat obsoleto (${AGE}s) — posible Monitor/tool muerto, revisar agente"
            touch "$ALERT_FLAG"
        fi
    else
        # Heartbeat fresco — limpiar flag de alerta
        [ -f "$ALERT_FLAG" ] && rm -f "$ALERT_FLAG" || true
    fi
done

exit 0
