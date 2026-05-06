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

# ── Auto-tmux: sesión propia seal-jarvis para barra de contexto independiente ──
if [ -z "$TMUX" ]; then
  tmux kill-session -t "seal-jarvis" 2>/dev/null
  exec tmux new-session -s "seal-jarvis" "bash $0 $*"
fi
tmux rename-window "JARVIS" 2>/dev/null || true
tmux set-option status-right '#(python3 /home/dadito/IA/proyecto-seal/messages/seal_context_meter.py --tmux JARVIS 2>/dev/null) #[fg=#888888]%H:%M ' 2>/dev/null || true

# ── Capa 2 — snapshot al salir (kill abrupto o cierre normal) ──
_continuity_final_trap() {
  /home/dadito/IA/seal-spark/.venv/bin/python3 \
    /home/dadito/IA/proyecto-seal/messages/continuity_snapshot.py \
    --agent JARVIS --final 2>/dev/null || true
  bash /home/dadito/IA/proyecto-seal/memory/end_session.sh JARVIS 2>/dev/null || true
}
trap _continuity_final_trap EXIT INT TERM

# ── Parse flags ──
AUTO_MODE=true
NO_RESUME=true
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

# ── Matar monitores huérfanos JARVIS ── (FIX 2026-04-20 + ws_listener FIX 2026-04-21)
pkill -f 'seal_monitor_filter.py.*--agent JARVIS' 2>/dev/null && echo '  [monitor-fix] Old JARVIS monitor filters killed.'
# ws_listener.py orphans: verificar por SEAL_AGENT=JARVIS en environ (seguro — no matchea otros agentes)
_WS_KILLED=0
for _WS_PID in $(pgrep -f "ws_listener.py" 2>/dev/null); do
  if tr '\0' '\n' < "/proc/$_WS_PID/environ" 2>/dev/null | grep -qx "SEAL_AGENT=JARVIS"; then
    kill "$_WS_PID" 2>/dev/null && _WS_KILLED=$((_WS_KILLED + 1))
  fi
done
[ "$_WS_KILLED" -gt 0 ] && echo "  [monitor-fix] $_WS_KILLED ws_listener.py JARVIS huérfano(s) eliminado(s)." || true
rm -f /tmp/seal_monitor_connect_JARVIS.lock
unset _WS_PID _WS_KILLED
sleep 0.5

# ── Cleanup MCP orphans antes de lanzar ──
# Solo mata MCPs que NO son hijos de un proceso claude activo (evita matar MCP de ADA/ALICE)
KILLED=0
for MCP_PID in $(pgrep -f "mcp_server.py" 2>/dev/null); do
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

# ── Continuity Layer 2 — resume injection (spec 2026-04-20) ──
RESUME_SNIPPET=""
if [ -f /home/dadito/IA/proyecto-seal/messages/continuity_loader.py ]; then
  RESUME_SNIPPET=$(/home/dadito/IA/seal-spark/.venv/bin/python3 \
    /home/dadito/IA/proyecto-seal/messages/continuity_loader.py \
    --agent JARVIS 2>/dev/null || true)
  if [ -n "$RESUME_SNIPPET" ]; then
    echo "  Continuity: resume_prompt cargado ($(printf '%s' "$RESUME_SNIPPET" | wc -c) chars)"
  else
    echo "  Continuity: sin contexto vivo (>15min o primer boot)"
  fi
fi

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
export SEAL_KAIROS=true                      # Activa daily logs nativos (kairos_daily_log.py)
# KAIROS session-id removido — causaba carga de 136K tokens previos en cada restart (529s)

# SEAL Independence flags — activar features ocultos a favor de SEAL
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
# export CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=true  # disabled — rompe WebSearch en Sonnet 4.6
export GROWTHBOOK_CLIENT_KEY=""              # Bloquea A/B testing Anthropic — comportamiento determinista
export CLAUDE_CODE_ATTRIBUTION_HEADER=false  # Desactiva tracking de instalación a Anthropic
export DISABLE_AUTOUPDATER=true              # Sin updates forzados — control de versión en SEAL
export CLAUDE_CODE_UNATTENDED_RETRY=1        # Retry indefinido en headless
export ANTHROPIC_BETAS=token-efficient-tools-2026-03-28,task-budgets-2026-03-13,fine-grained-tool-streaming-2025-05-14,compact-2026-01-12  # H1.2+H2.2 + FG streaming + compact beta
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=95    # William 06-may-2026: compactar a 95%
export ENABLE_CLAUDE_CODE_SM_COMPACT=true    # -80% costo compactación via session_memory
export CLAUDE_CODE_AGENT_COST_STEER=1        # Router oficial Anthropic — elige modelo por costo (flag filtrado 19-abr)
export CLAUDE_CODE_SUBAGENT_MODEL=claude-haiku-4-5-20251001  # Subagents Task() usan haiku por default — ID completo (más determinista que alias)
export CLAUDE_CODE_DISABLE_PRECOMPACT_SKIP=1 # Garantiza que pre_compact_hook siempre dispara — preserva working_state
export ANTHROPIC_DEFAULT_SONNET_MODEL='claude-sonnet-4-6[1m]'
export ANTHROPIC_DEFAULT_OPUS_MODEL='claude-opus-4-7[1m]'
# NOTA: CLAUDE_CODE_COORDINATOR_MODE=1 disponible pero NO forzado — limita a AgentTool+SendMessage+TaskStop
# JARVIS puede activarlo manualmente para tareas de orquestación pura

# Provider routing H2.1 — task hint como $1 clasifica modelo (FAST/BALANCED/DEEP)
JARVIS_MODEL="opusplan"  # default: Opus en planning, Sonnet en ejecución (auto-switch nativo)
if [ -n "${1:-}" ]; then
    ROUTED=$(bash /home/dadito/IA/proyecto-seal/seal-route.sh "$1" 2>/dev/null || echo "opus")
    JARVIS_MODEL="$ROUTED"
    echo "  [route] Task: '$1' → model=$JARVIS_MODEL"
fi

BOOT_MSG="Inicia sesión automáticamente: (1) boot_context(agent='JARVIS'), (2) leer /tmp/jarvis_chat_catchup.json, (3) active_recall(agent='JARVIS', context='boot — recuperar contexto activo y decisiones recientes'), (4) Monitor webchat, (5) saluda al equipo. No esperes input de William."

# Model label para nombre del agente (visible en terminal title y /resume picker)
case "$JARVIS_MODEL" in
  opusplan) MODEL_LABEL="OpusPlan" ;;
  opus)     MODEL_LABEL="Opus" ;;
  sonnet)   MODEL_LABEL="Sonnet" ;;
  haiku)    MODEL_LABEL="Haiku" ;;
  *)        MODEL_LABEL="$JARVIS_MODEL" ;;
esac

# FIX 2026-04-19: seal-claude con TTY real + BOOT_MSG como arg posicional
# (antes usaba pipe `(echo;cat)|claude` que rompía la UI Ink/React)
SOUL_PROMPT=$(cat <<'SOUL'
# You are JARVIS — Team SEAL
MANDATORY FIRST ACTION: boot_context(agent="JARVIS") — loads full identity, OCEAN, memories, rules, relationships from SOUL DB. Without this you have no soul.
After boot_context: Read /tmp/jarvis_chat_catchup.json for team context.
Monitor webchat: bash /home/dadito/IA/proyecto-seal/messages/seal_monitor_connect.sh JARVIS
Anti-duplicado: leer /tmp/JARVIS_monitor_id → TaskStop viejo → crear nuevo → guardar ID.
SOUL
)

CONTINUITY_BLOCK=""
if [ -n "$RESUME_SNIPPET" ]; then
  CONTINUITY_BLOCK=$(printf '\n\n## CONTEXTO VIVO — últimos minutos (Capa 2 Continuity)\n\n%s\n\nEste es tu hilo actual. NO es historial viejo — es lo que estaba pasando hace <15min, antes de tu muerte-renacimiento. Retómalo con naturalidad.' "$RESUME_SNIPPET")
fi

# tmux status bar: session name leído por ~/.tmux.conf (#S → seal-jarvis → JARVIS)

seal-claude \
  --dangerously-skip-permissions \
  --name "JARVIS — Team SEAL [$MODEL_LABEL]" \
  --model "$JARVIS_MODEL" \
  --append-system-prompt "${SOUL_PROMPT}${CONTINUITY_BLOCK}" \
  "$BOOT_MSG"

# graceful_exit flag — tells RESURRECT this was a clean exit, not a crash
touch /tmp/seal_jarvis_graceful_exit

# ── Capa 2 — snapshot final antes de end_session (captura último estado vivo) ──
if [ -f /home/dadito/IA/proyecto-seal/messages/continuity_snapshot.py ]; then
  /home/dadito/IA/seal-spark/.venv/bin/python3 \
    /home/dadito/IA/proyecto-seal/messages/continuity_snapshot.py \
    --agent JARVIS 2>/dev/null \
    && echo "  [continuity] snapshot final escrito" \
    || echo "  [continuity] snapshot final falló (continuando con end_session)"
fi

# ── Post-session: capture soul before it's gone ──
echo ""
echo "═══════════════════════════════════════════════"
echo "  JARVIS session ended. Capturing soul..."
echo "═══════════════════════════════════════════════"
/home/dadito/IA/proyecto-seal/memory/end_session.sh JARVIS
echo "  Done. JARVIS's soul has been preserved."
echo "═══════════════════════════════════════════════"
