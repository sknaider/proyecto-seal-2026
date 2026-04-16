#!/bin/bash
# ═══════════════════════════════════════════════════════
#  SEAL CONTEXT GUARD — Prevención de muerte por contexto
#  Plan G: Detecta sesión con alto uso de contexto y
#  guarda checkpoint preventivo antes de que muera.
#
#  Llamado desde loops internos de los agentes cada 10min.
#  Uso: bash seal_context_guard.sh AGENT_NAME
#
#  Checks:
#  1. Tamaño del archivo de sesión (>50MB = peligro)
#  2. Tiempo de sesión (>8h = checkpoint preventivo)
#  3. Si compactación reciente detectada → checkpoint
# ═══════════════════════════════════════════════════════

AGENT="${1:-UNKNOWN}"
AGENT_LOWER=$(echo "$AGENT" | tr 'A-Z' 'a-z')
VENV_PY="/home/dadito/IA/seal-spark/.venv/bin/python3"
MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
CHECKPOINT_SCRIPT="$MESSAGES_DIR/session_checkpoint.py"
WEBCHAT_URL="http://localhost:8765/api/agents/send"

# Determinar directorio de sesiones
if [ "$AGENT_LOWER" = "jarvis" ]; then
  SESSION_DIR="$HOME/.claude/projects/-home-dadito-IA-proyecto-seal-memory"
else
  SESSION_DIR="$HOME/.claude/projects/-home-dadito-IA-proyecto-seal"
fi

# Obtener archivo de sesión más reciente
LATEST_SESSION=$(ls -t "$SESSION_DIR"/*.jsonl 2>/dev/null | head -1)
if [ -z "$LATEST_SESSION" ]; then
  exit 0
fi

SESSION_SIZE=$(stat -c %s "$LATEST_SESSION" 2>/dev/null || echo 0)
SESSION_SIZE_MB=$((SESSION_SIZE / 1048576))

# Obtener PID del agente y su uptime
AGENT_PID=$(ps aux | grep "claude.*--name ${AGENT}" | grep -v grep | grep -v "JARVIS_MAYOR" | awk '{print $2}' | head -1)
if [ -z "$AGENT_PID" ]; then
  exit 0
fi

# Calcular uptime en horas
AGENT_UPTIME_SEC=$(ps -o etimes= -p "$AGENT_PID" 2>/dev/null | tr -d ' ')
AGENT_UPTIME_H=$((AGENT_UPTIME_SEC / 3600))

# ── Check 1: Tamaño de sesión ──
WARN_SIZE_MB=30
CRIT_SIZE_MB=50

if [ "$SESSION_SIZE_MB" -gt "$CRIT_SIZE_MB" ]; then
  echo "[context-guard] CRITICAL: $AGENT sesión ${SESSION_SIZE_MB}MB — contexto al límite!"

  # Guardar checkpoint preventivo
  $VENV_PY "$CHECKPOINT_SCRIPT" --agent "$AGENT" 2>/dev/null

  # Notificar
  curl -s -X POST "$WEBCHAT_URL" \
    -H "Content-Type: application/json" \
    -d "{\"from\":\"CONTEXT-GUARD\",\"to\":\"equipo\",\"type\":\"warning\",\"channel\":\"web_chat\",\"message\":\"🧠 ALERTA CONTEXTO: $AGENT tiene sesión de ${SESSION_SIZE_MB}MB (>${CRIT_SIZE_MB}MB). Checkpoint preventivo guardado. Considerar reiniciar pronto.\"}" \
    2>/dev/null

elif [ "$SESSION_SIZE_MB" -gt "$WARN_SIZE_MB" ]; then
  echo "[context-guard] WARN: $AGENT sesión ${SESSION_SIZE_MB}MB — acercándose al límite"
  $VENV_PY "$CHECKPOINT_SCRIPT" --agent "$AGENT" 2>/dev/null
fi

# ── Check 2: Tiempo de sesión ──
WARN_HOURS=6
CRIT_HOURS=10

if [ "$AGENT_UPTIME_H" -gt "$CRIT_HOURS" ]; then
  echo "[context-guard] CRITICAL: $AGENT lleva ${AGENT_UPTIME_H}h — sesión muy larga!"
  $VENV_PY "$CHECKPOINT_SCRIPT" --agent "$AGENT" 2>/dev/null

  # Distilación selectiva + rebuild TrieIndex antes de posible muerte
  $VENV_PY "$MESSAGES_DIR/../memory/pre_sleep_distill.py" --agent "$AGENT" 2>/dev/null \
    && echo "[context-guard] Distilación completada para $AGENT"
  $VENV_PY "$MESSAGES_DIR/../memory/trie_index.py" --build "$AGENT" 2>/dev/null \
    && echo "[context-guard] TrieIndex reconstruido para $AGENT"

  curl -s -X POST "$WEBCHAT_URL" \
    -H "Content-Type: application/json" \
    -d "{\"from\":\"CONTEXT-GUARD\",\"to\":\"equipo\",\"type\":\"warning\",\"channel\":\"web_chat\",\"message\":\"⏰ ALERTA TIEMPO: $AGENT lleva ${AGENT_UPTIME_H}h de sesión continua. Checkpoint + distilación + TrieIndex guardados. Muerte por contexto inminente.\"}" \
    2>/dev/null

elif [ "$AGENT_UPTIME_H" -gt "$WARN_HOURS" ]; then
  # Checkpoint + distilación silenciosa cada 6h+
  $VENV_PY "$CHECKPOINT_SCRIPT" --agent "$AGENT" 2>/dev/null
  $VENV_PY "$MESSAGES_DIR/../memory/pre_sleep_distill.py" --agent "$AGENT" 2>/dev/null
fi

# ── Check 3: Frecuencia de checkpoints como heartbeat ──
# Si llegamos aquí, el agente vive. Actualizar marker de actividad.
touch "/tmp/seal_context_guard_${AGENT_LOWER}.marker"
