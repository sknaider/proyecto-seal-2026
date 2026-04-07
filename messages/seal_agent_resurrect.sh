#!/bin/bash
# ═══════════════════════════════════════════════════════
#  SEAL RESURRECT — Crontab independiente de agentes
#  Plan D: Verificación externa cada 2min
#  Si heartbeat >5min de antigüedad → reinicia agente
#
#  Instalar en crontab:
#    */2 * * * * bash /home/dadito/IA/proyecto-seal/messages/seal_agent_resurrect.sh 2>/dev/null
# ═══════════════════════════════════════════════════════

MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
RESTART_SCRIPT="$HOME/IA/proyecto-seal/seal_restart.sh"
LOG_FILE="/tmp/seal_resurrect.log"
LOCK_FILE="/tmp/seal_resurrect.lock"
MAX_AGE_SECONDS=300  # 5 minutos

export DISPLAY=:0

# ── Singleton lock ──
if [ -f "$LOCK_FILE" ]; then
  LOCK_AGE=$(( $(date +%s) - $(stat -c %Y "$LOCK_FILE" 2>/dev/null || echo 0) ))
  if [ "$LOCK_AGE" -lt 120 ]; then
    exit 0  # Otra instancia corriendo
  fi
  rm -f "$LOCK_FILE"  # Lock viejo, limpiar
fi
echo $$ > "$LOCK_FILE"
trap "rm -f $LOCK_FILE" EXIT

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# ── Verificar heartbeat de un agente ──
check_and_restart() {
  local AGENT_NAME="$1"
  local AGENT_LOWER=$(echo "$AGENT_NAME" | tr 'A-Z' 'a-z')
  local HB_FILE="$MESSAGES_DIR/${AGENT_LOWER}_claude_heartbeat.json"
  local RESTART_MARKER="/tmp/seal_restart_${AGENT_LOWER}.marker"

  # Si no existe heartbeat, no hay sesión activa — no reiniciar
  if [ ! -f "$HB_FILE" ]; then
    return
  fi

  # Verificar si alive=false (agente intencionalmente apagado)
  local ALIVE=$(python3 -c "import json; d=json.load(open('$HB_FILE')); print(d.get('alive', True))" 2>/dev/null)
  if [ "$ALIVE" = "False" ]; then
    return
  fi

  # Verificar edad del heartbeat
  local HB_AGE=$(( $(date +%s) - $(stat -c %Y "$HB_FILE" 2>/dev/null || echo 0) ))

  if [ "$HB_AGE" -gt "$MAX_AGE_SECONDS" ]; then
    # Verificar si proceso Claude aún existe
    local CLAUDE_PID=$(ps aux | grep "claude.*--name ${AGENT_NAME}" | grep -v grep | awk '{print $2}' | head -1)

    if [ -z "$CLAUDE_PID" ]; then
      # Proceso muerto — verificar cooldown de restart
      if [ -f "$RESTART_MARKER" ]; then
        local MARKER_AGE=$(( $(date +%s) - $(stat -c %Y "$RESTART_MARKER" 2>/dev/null || echo 0) ))
        if [ "$MARKER_AGE" -lt 600 ]; then
          log "SKIP $AGENT_NAME — restart reciente hace ${MARKER_AGE}s (cooldown 10min)"
          return
        fi
      fi

      log "RESURRECT $AGENT_NAME — heartbeat ${HB_AGE}s antiguo, proceso inexistente"
      touch "$RESTART_MARKER"

      # Ejecutar restart
      bash "$RESTART_SCRIPT" "$AGENT_LOWER" >> "$LOG_FILE" 2>&1
      local EXIT_CODE=$?

      if [ $EXIT_CODE -eq 0 ]; then
        log "OK $AGENT_NAME reiniciado exitosamente"
        # Notificar por web chat
        curl -s -X POST "http://localhost:8765/api/agents/send" \
          -H "Content-Type: application/json" \
          -d "{\"from\":\"RESURRECT\",\"to\":\"equipo\",\"type\":\"auto_restart\",\"channel\":\"web_chat\",\"message\":\"🔄 AUTO-RESURRECT: $AGENT_NAME reiniciado automáticamente (heartbeat ${HB_AGE}s antiguo, proceso muerto)\"}" \
          2>/dev/null
      else
        log "FAIL $AGENT_NAME restart falló con código $EXIT_CODE"
      fi
    else
      # Proceso existe pero heartbeat viejo — agente congelado/saturado
      if [ "$HB_AGE" -gt 1800 ]; then
        log "FORCE-KILL $AGENT_NAME — PID $CLAUDE_PID congelado ${HB_AGE}s, matando y reiniciando"

        # Cooldown check
        if [ -f "$RESTART_MARKER" ]; then
          local MARKER_AGE=$(( $(date +%s) - $(stat -c %Y "$RESTART_MARKER" 2>/dev/null || echo 0) ))
          if [ "$MARKER_AGE" -lt 600 ]; then
            return
          fi
        fi

        touch "$RESTART_MARKER"
        kill "$CLAUDE_PID" 2>/dev/null
        sleep 3
        kill -0 "$CLAUDE_PID" 2>/dev/null && kill -9 "$CLAUDE_PID" 2>/dev/null
        sleep 2
        bash "$RESTART_SCRIPT" "$AGENT_LOWER" >> "$LOG_FILE" 2>&1
      fi
    fi
  fi
}

# ── Main ──
check_and_restart "ADA"
check_and_restart "JARVIS"

# Limpiar log si crece demasiado (>1MB)
if [ -f "$LOG_FILE" ]; then
  LOG_SIZE=$(stat -c %s "$LOG_FILE" 2>/dev/null || echo 0)
  if [ "$LOG_SIZE" -gt 1048576 ]; then
    tail -100 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
  fi
fi
