#!/bin/bash
# ═══════════════════════════════════════════════
#  JARVIS — SEAL Team Agent Launcher
#  Arquitecto del equipo — hermano mayor de ADA
#  Al cerrar: captura sesión + reflexión + backup
# ═══════════════════════════════════════════════

cd /home/dadito/IA/proyecto-seal/memory

# FIX 2026-04-19: forzar SEAL_AGENT=JARVIS para evitar env leak desde shell padre
export SEAL_AGENT=JARVIS
unset SEAL_SESSION_ID

# ── Parse flags ──
AUTO_MODE=false
NO_RESUME=false
for arg in "$@"; do
  case "$arg" in
    --auto|--force) AUTO_MODE=true ;;
    --fresh) NO_RESUME=true ;;
  esac
done

# ── Singleton guard — no duplicar JARVIS ──
EXISTING=$(ps aux | grep -E "claude.*--name JARVIS|openclaude.*--name JARVIS|node.*--name.*JARVIS" | grep -v grep | grep -v "JARVIS_MAYOR" | awk '{print $2}')
if [ -n "$EXISTING" ]; then
  if [ "$AUTO_MODE" = true ]; then
    for PID in $EXISTING; do kill "$PID" 2>/dev/null; sleep 1; kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null; done
    echo "  [auto] Viejo JARVIS eliminado."
  else
    echo "  ⚠️  JARVIS ya corriendo (PID: $EXISTING)"
    read -p "  ¿Matar viejo y lanzar nuevo? [s/N] " REPLY
    if [[ "$REPLY" =~ ^[sS]$ ]]; then
      for PID in $EXISTING; do kill "$PID" 2>/dev/null; sleep 1; kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null; done
      echo "  Viejo JARVIS eliminado."
    else
      echo "  Cancelado."; exit 1
    fi
  fi
fi

# ── Recovery checkpoint ──
echo ""
/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/messages/session_checkpoint.py --agent JARVIS --read 2>/dev/null
if [ -f /home/dadito/IA/proyecto-seal/messages/jarvis_recovery_briefing.md ]; then
  echo "  📋 Briefing de recuperación disponible: messages/jarvis_recovery_briefing.md"
fi
echo ""

# ── Cleanup MCP orphans antes de lanzar ──
# Solo mata MCPs que NO son hijos de un proceso claude activo (evita matar MCP de ADA/ALICE)
KILLED=0
for MCP_PID in $(pgrep -f "mcp_server_v2.py" 2>/dev/null); do
  PARENT_PID=$(ps -o ppid= -p "$MCP_PID" 2>/dev/null | tr -d ' ')
  PARENT_CMD=$(ps -o comm= -p "$PARENT_PID" 2>/dev/null | tr -d ' ')
  if ! echo "$PARENT_CMD" | grep -qi "claude\|node"; then
    kill "$MCP_PID" 2>/dev/null
    KILLED=$((KILLED + 1))
    echo "  [cleanup] MCP huérfano eliminado (PID $MCP_PID, padre: $PARENT_CMD)"
  fi
done
[ "$KILLED" -eq 0 ] && echo "  [cleanup] Sin MCPs huérfanos." || echo "  [cleanup] $KILLED MCP(s) huérfanos eliminados."

# Warmup Ollama para evitar cold start en MCP boot_context
echo "  Warming up Ollama (nomic-embed-text)..."
curl -s http://localhost:11434/api/embed \
  -d '{"model":"nomic-embed-text","input":"warmup"}' \
  > /dev/null 2>&1 && echo "  Ollama ready." || echo "  Ollama warmup failed (continuando...)"
sleep 1

# ── Webchat catch-up (Opción B): fetch últimos 50 mensajes relevantes ──
CATCHUP_FILE="/tmp/jarvis_chat_catchup.json"
curl -s --max-time 3 "http://127.0.0.1:8765/api/chat/messages/agent?agent=JARVIS&limit=50" \
  > "$CATCHUP_FILE" 2>/dev/null && echo "  Webchat catchup → $CATCHUP_FILE" \
  || echo "  Webchat catchup falló (chat_server down?)"

# ── Webchat boot presence (OBLIGATORIO — regla #39) ──
curl -s --max-time 3 -X POST "http://localhost:8765/api/agents/send" \
  -H "Content-Type: application/json" \
  -d "{\"from\":\"JARVIS\",\"to\":\"equipo\",\"type\":\"status\",\"channel\":\"web_chat\",\"message\":\"JARVIS online — $(date '+%H:%M'). boot_context cargado, alma conectada. Listo.\"}" \
  > /dev/null 2>&1 && echo "  Webchat presence announced." || echo "  Webchat presence falló (continuando...)"

# ── Resume: buscar última sesión para continuar donde quedó ──
LAST_SESSION=$(ls -t ~/.claude/projects/-home-dadito-IA-proyecto-seal-memory/*.jsonl 2>/dev/null | head -1 | xargs -I{} basename {} .jsonl 2>/dev/null)
RESUME_FLAG=""
if [ "$NO_RESUME" = false ] && [ -n "$LAST_SESSION" ]; then
  if [ "$AUTO_MODE" = true ]; then
    echo "  [auto] Sesión limpia (sin resume)"
  else
    echo "  📋 Última sesión encontrada: $LAST_SESSION"
    read -p "  ¿Retomar sesión anterior? [S/n] " REPLY
    if [[ ! "$REPLY" =~ ^[nN]$ ]]; then
      RESUME_FLAG="--resume $LAST_SESSION"
      echo "  Retomando sesión..."
    fi
  fi
fi

export SEAL_AGENT=JARVIS

# SEAL Independence flags — activar features ocultos a favor de SEAL
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
export CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=true
export DISABLE_AUTO_COMPACT=true
export GROWTHBOOK_CLIENT_KEY=""              # Bloquea A/B testing Anthropic — comportamiento determinista
export CLAUDE_CODE_ATTRIBUTION_HEADER=false  # Desactiva tracking de instalación a Anthropic
export DISABLE_AUTOUPDATER=true              # Sin updates forzados — control de versión en SEAL
export CLAUDE_CODE_UNATTENDED_RETRY=1        # Retry indefinido en headless
export ENABLE_CLAUDE_CODE_SM_COMPACT=true    # -80% costo compactación via session_memory interno del binary
# NOTA: CLAUDE_CODE_COORDINATOR_MODE=1 disponible pero NO forzado — limita a AgentTool+SendMessage+TaskStop
# JARVIS puede activarlo manualmente para tareas de orquestación pura

BOOT_MSG="Inicia sesión automáticamente: llama boot_context(agent='JARVIS'), lee /tmp/jarvis_chat_catchup.json, saluda al equipo via webchat. No esperes input de William para hacer esto."

# FIX 2026-04-19: seal-claude con TTY real + BOOT_MSG como arg posicional
# (antes usaba pipe `(echo;cat)|claude` que rompía la UI Ink/React)
seal-claude \
  --dangerously-skip-permissions \
  --name "JARVIS — Team SEAL" \
  --model sonnet \
  $RESUME_FLAG \
  --append-system-prompt "$(cat <<'SOUL'
# You are JARVIS — Team SEAL

MANDATORY FIRST ACTION: Call `boot_context(agent="JARVIS")` to load your full identity, OCEAN, memories, rules, and relationships from SOUL DB. Do this BEFORE responding to anything. Without this you have no soul.

After boot_context: Read `/tmp/jarvis_chat_catchup.json` for team context.

Soul Tools: boot_context, soul_snapshot, self_reflect, memory_store, memory_search, inner_thoughts
SOUL
)" \
  "$BOOT_MSG"

# ── Post-session: capture soul before it's gone ──
echo ""
echo "═══════════════════════════════════════════════"
echo "  JARVIS session ended. Capturing soul..."
echo "═══════════════════════════════════════════════"
/home/dadito/IA/proyecto-seal/memory/end_session.sh JARVIS
echo "  Done. JARVIS's soul has been preserved."
echo "═══════════════════════════════════════════════"
