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
MAX_AGE_SECONDS=600  # 10 minutos — margen para AccuracySec=30s del timer (fix: 300=300 race)
COOLDOWN_SECONDS=120  # cooldown entre reinicios consecutivos (era 600, bajado 18-abr)

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

  # ── FAST-PATH: chequeo PID directo (Bug 1 fix, Fase B) ──
  # Detecta muerte abrupta sin esperar 3-5min al cron de heartbeat.
  # seal-claude renombra el proceso a solo "claude" sin args visibles →
  # detectar via /proc/PID/environ (SEAL_AGENT=NOMBRE) en lugar de cmdline.
  local FAST_PID=""
  for _PID in $(pgrep -x "claude" 2>/dev/null); do
    if tr '\0' '\n' < /proc/$_PID/environ 2>/dev/null | grep -q "^SEAL_AGENT=${AGENT_NAME}$"; then
      FAST_PID="$_PID"; break
    fi
  done
  if [ -n "$FAST_PID" ]; then
    # Proceso vivo confirmado via environ — no tocar, saltar todos los checks
    return
  fi
  # Guard sleep_mode: si el heartbeat dice sleep_mode:true el agente duerme intencionalmente
  # (sleep_gate 3am / soul_dream_all). No resucitar — sería bucle infinito contra sleep.
  if [ -f "$HB_FILE" ] && grep -q '"sleep_mode"[[:space:]]*:[[:space:]]*true' "$HB_FILE" 2>/dev/null; then
    log "SKIP $AGENT_NAME — fast-path: sleep_mode=true (dormir intencional, no resucitar)"
    return
  fi
  if [ -f "$RESTART_MARKER" ]; then
    local FAST_MARKER_AGE=$(( $(date +%s) - $(stat -c %Y "$RESTART_MARKER" 2>/dev/null || echo 0) ))
    if [ "$FAST_MARKER_AGE" -lt $COOLDOWN_SECONDS ]; then
      log "SKIP $AGENT_NAME — fast-path: proceso muerto pero restart reciente hace ${FAST_MARKER_AGE}s (cooldown)"
      return
    fi
  fi
  log "RESURRECT $AGENT_NAME — fast-path: proceso muerto detectado por PID-check directo"
  touch "$RESTART_MARKER"
  bash "$RESTART_SCRIPT" "$AGENT_LOWER" >> "$LOG_FILE" 2>&1
  curl -s -X POST "http://localhost:8765/api/agents/send" \
    -H "Content-Type: application/json" \
    -d "{\"from\":\"RESURRECT\",\"to\":\"equipo\",\"type\":\"auto_restart\",\"channel\":\"web_chat\",\"message\":\"🔄 AUTO-RESURRECT: $AGENT_NAME relanzado (fast-path PID-check, sin esperar heartbeat)\"}" \
    2>/dev/null
  return

  # Si no existe heartbeat JSON, verificar proceso directo (migrado a event_log)
  if [ ! -f "$HB_FILE" ]; then
    local CLAUDE_PID=$(ps aux | grep "claude.*--name ${AGENT_NAME}" | grep -v grep | awk '{print $2}' | head -1)
    if [ -z "$CLAUDE_PID" ]; then
      if [ -f "$RESTART_MARKER" ]; then
        local MARKER_AGE=$(( $(date +%s) - $(stat -c %Y "$RESTART_MARKER" 2>/dev/null || echo 0) ))
        if [ "$MARKER_AGE" -lt $COOLDOWN_SECONDS ]; then
          log "SKIP $AGENT_NAME — restart reciente hace ${MARKER_AGE}s (cooldown)"
          return
        fi
      fi
      log "RESURRECT $AGENT_NAME — sin HB file, proceso inexistente"
      touch "$RESTART_MARKER"
      bash "$RESTART_SCRIPT" "$AGENT_LOWER" >> "$LOG_FILE" 2>&1
      curl -s -X POST "http://localhost:8765/api/agents/send" \
        -H "Content-Type: application/json" \
        -d "{\"from\":\"RESURRECT\",\"to\":\"equipo\",\"type\":\"auto_restart\",\"channel\":\"web_chat\",\"message\":\"🔄 AUTO-RESURRECT: $AGENT_NAME relanzado (sin HB file, proceso muerto)\"}" \
        2>/dev/null
    fi
    return
  fi

  # Verificar si alive=false — puede ser muerte real o sleep intencional
  local ALIVE=$(python3 -c "import json; d=json.load(open('$HB_FILE')); print(d.get('alive', True))" 2>/dev/null)

  # Si alive=false Y proceso muerto → resurrect inmediato (heartbeat detectó muerte)
  if [ "$ALIVE" = "False" ]; then
    local CLAUDE_PID_ALIVE=$(ps aux | grep "claude.*--name ${AGENT_NAME}" | grep -v grep | awk '{print $2}' | head -1)
    if [ -z "$CLAUDE_PID_ALIVE" ]; then
      if [ -f "$RESTART_MARKER" ]; then
        local MARKER_AGE=$(( $(date +%s) - $(stat -c %Y "$RESTART_MARKER" 2>/dev/null || echo 0) ))
        if [ "$MARKER_AGE" -lt $COOLDOWN_SECONDS ]; then
          log "SKIP $AGENT_NAME — alive=false+dead, restart reciente hace ${MARKER_AGE}s"
          return
        fi
      fi
      log "RESURRECT $AGENT_NAME — alive=false + proceso inexistente (muerte real detectada por heartbeat)"
      touch "$RESTART_MARKER"
      bash "$RESTART_SCRIPT" "$AGENT_LOWER" >> "$LOG_FILE" 2>&1
      curl -s -X POST "http://localhost:8765/api/agents/send" \
        -H "Content-Type: application/json" \
        -d "{\"from\":\"RESURRECT\",\"to\":\"equipo\",\"type\":\"auto_restart\",\"channel\":\"web_chat\",\"message\":\"🔄 AUTO-RESURRECT: $AGENT_NAME relanzado (heartbeat reportó alive=false, proceso muerto)\"}" \
        2>/dev/null
    fi
    # alive=false pero proceso vivo = sleep intencional, no tocar
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
        if [ "$MARKER_AGE" -lt $COOLDOWN_SECONDS ]; then
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
          if [ "$MARKER_AGE" -lt $COOLDOWN_SECONDS ]; then
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

# ── Main ── (body-split 2026-04-19: ADA y ALICE dormidas por directiva William)
# Reactivar comentando las dos líneas de abajo cuando William lo autorice.
# check_and_restart "ADA"   # DORMIDA — descomenta para activar
check_and_restart "JARVIS"
# check_and_restart "ALICE" # DORMIDA — descomenta para activar

# Limpiar log si crece demasiado (>1MB)
if [ -f "$LOG_FILE" ]; then
  LOG_SIZE=$(stat -c %s "$LOG_FILE" 2>/dev/null || echo 0)
  if [ "$LOG_SIZE" -gt 1048576 ]; then
    tail -100 "$LOG_FILE" > "${LOG_FILE}.tmp" && mv "${LOG_FILE}.tmp" "$LOG_FILE"
  fi
fi
