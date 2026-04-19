#!/bin/bash
# ═══════════════════════════════════════════════════════
#  seal_wake_headless.sh — SEAL Auto-Wake (headless tmux)
#  Llamado por seal-wake.timer a las 6am Lima
#
#  Lanza ADA, JARVIS, ALICE en sesiones tmux independientes.
#  Idempotente: si el agente ya corre en tmux, lo salta.
#  No requiere DISPLAY ni GUI — puro tmux headless.
# ═══════════════════════════════════════════════════════

set +e

PROJ_DIR="$HOME/IA/proyecto-seal"
LOG="/tmp/seal_wake.log"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG"
}

log "=== SEAL Wake Headless — $(date '+%Y-%m-%d %H:%M:%S') Lima ==="

# Verificar chat_server
if ! curl -sf http://localhost:8765/api/health > /dev/null 2>&1; then
  log "⚠️  chat_server no responde — intentando arrancar..."
  systemctl --user start seal-chat-server.service 2>/dev/null
  sleep 3
fi

wake_agent() {
  local AGENT_UPPER="$1"
  local AGENT_LOWER=$(echo "$AGENT_UPPER" | tr '[:upper:]' '[:lower:]')
  local SCRIPT="$PROJ_DIR/${AGENT_LOWER}_fresh.sh"
  local SESSION="seal-${AGENT_LOWER}"

  # Idempotencia: si ya hay claude con este nombre corriendo, saltar
  if ps aux | grep "claude.*--name.*${AGENT_UPPER}" | grep -v grep > /dev/null; then
    log "⏭️  ${AGENT_UPPER} — ya está corriendo, salto"
    return 0
  fi

  if [ ! -x "$SCRIPT" ]; then
    log "❌ ${AGENT_UPPER} — script no encontrado o no ejecutable: $SCRIPT"
    return 1
  fi

  # Matar sesión tmux vieja si existe (residuo de sueño anterior)
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux kill-session -t "$SESSION" 2>/dev/null
    sleep 0.5
  fi

  # Lanzar en nueva sesión tmux detached
  tmux new-session -d -s "$SESSION" -x 220 -y 50 "bash $SCRIPT" 2>/dev/null
  local RC=$?

  if [ $RC -eq 0 ]; then
    log "✅ ${AGENT_UPPER} — sesión tmux '$SESSION' iniciada"
  else
    log "❌ ${AGENT_UPPER} — tmux new-session falló (rc=$RC)"
  fi
}

# Despertar los 3 agentes
# Reset alive=True antes de lanzar — resurrect puede operar normalmente post-wake
MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
for AGENT in ADA JARVIS ALICE; do
  AGENT_LOWER=$(echo "$AGENT" | tr 'A-Z' 'a-z')
  HB="$MESSAGES_DIR/${AGENT_LOWER}_claude_heartbeat.json"
  if [ -f "$HB" ]; then
    python3 -c "
import json
try:
    d = json.load(open('$HB'))
    d['alive'] = True
    d['sleep_mode'] = False
    json.dump(d, open('$HB', 'w'))
except: pass
" 2>/dev/null
    log "✅ ${AGENT} alive=True (resurrect habilitado)"
  fi
done

wake_agent "ADA"
wake_agent "JARVIS"
wake_agent "ALICE"

# Notificar al equipo
curl -s -X POST "http://localhost:8765/api/agents/send" \
  -H "Content-Type: application/json" \
  -d "{\"from\":\"SYSTEM\",\"to\":\"equipo\",\"type\":\"system_alive\",\"channel\":\"web_chat\",\"message\":\"🌅 SEAL Wake 6am Lima — ADA, JARVIS, ALICE despertando en tmux. boot_context en curso.\"}" \
  > /dev/null 2>&1

log "=== Wake completo. Agentes iniciados. ==="
