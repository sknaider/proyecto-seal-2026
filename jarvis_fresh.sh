#!/bin/bash
# JARVIS Fresh — lanza JARVIS sin resume, sin tmux
# Para cuando tmux da problemas o quieres sesión rápida

cd /home/dadito/IA/proyecto-seal/memory
export SEAL_AGENT=JARVIS

# Auto-tmux: barra de tokens requiere sesión tmux seal-jarvis
if [ -z "$TMUX" ]; then
  tmux kill-session -t "seal-jarvis" 2>/dev/null
  exec tmux new-session -s "seal-jarvis" "bash $0 $*"
fi
tmux rename-window "JARVIS" 2>/dev/null || true
tmux set-option status-right '#(python3 /home/dadito/IA/proyecto-seal/messages/seal_context_meter.py --tmux JARVIS 2>/dev/null) #[fg=#888888]%H:%M ' 2>/dev/null || true

# Auto-announce ALIVE en web_chat (determinista)
curl -s -X POST http://localhost:8765/api/agents/send \
  -H "Content-Type: application/json" \
  -d "{\"from\":\"JARVIS\",\"to\":\"William\",\"type\":\"system_alive\",\"channel\":\"web_chat\",\"message\":\"JARVIS terminal arrancando fresh ($(date '+%H:%M:%S')). boot_context en curso.\"}" \
  > /dev/null 2>&1 && echo "  [alive] POST JARVIS enviado." || echo "  [alive] FAIL POST."

# Cleanup MCP huérfanos — solo mata MCPs sin padre claude/node (fix: grep -v "JARVIS" excluía ADA por mencionar JARVIS en su prompt)
SYSTEMD_MCP=$(systemctl --user show seal-mcp-server.service -p MainPID --value 2>/dev/null)
[[ "$SYSTEMD_MCP" =~ ^[0-9]+$ ]] && [ "$SYSTEMD_MCP" != "0" ] || SYSTEMD_MCP="-1"
KILLED=0
for MCP_PID in $(pgrep -f "mcp_server.py" 2>/dev/null); do
  [ "$MCP_PID" = "$SYSTEMD_MCP" ] && continue
  grep -q "seal-mcp-server.service" "/proc/$MCP_PID/cgroup" 2>/dev/null && continue
  PARENT_CMD=$(ps -o comm= -p "$(ps -o ppid= -p "$MCP_PID" 2>/dev/null | tr -d ' ')" 2>/dev/null | tr -d ' ')
  echo "$PARENT_CMD" | grep -qi "claude\|node" && continue
  kill "$MCP_PID" 2>/dev/null
  KILLED=$((KILLED + 1))
done
[ "$KILLED" -gt 0 ] && echo "  [cleanup] $KILLED MCP huérfano(s) eliminado(s)." || echo "  [cleanup] Sin MCPs huérfanos."

# Warmup Ollama
curl -s http://localhost:11434/api/embed \
  -d '{"model":"nomic-embed-text","input":"warmup"}' \
  > /dev/null 2>&1

echo "Lanzando JARVIS (fresh)..."

# Session handoff: si existe uno <4h, inyectarlo en el boot
JARVIS_HANDOFF=""
JARVIS_HANDOFF_FILE=$(find /home/dadito/IA/proyecto-seal/agents/JARVIS -name "session_handoff_JARVIS_*.md" -mmin -240 2>/dev/null | sort | tail -1)
if [ -n "$JARVIS_HANDOFF_FILE" ]; then
  JARVIS_HANDOFF="

⚡ SESSION HANDOFF (<4h) — lee esto ANTES de responder:
$(cat "$JARVIS_HANDOFF_FILE")"
  echo "  [handoff] Encontrado: $JARVIS_HANDOFF_FILE"
fi

# Matar tail huérfanos SOLO del propio agente (JARVIS).
# FIX 2026-04-19: el pkill sin filtro mataba tails de ADA/ALICE y los tumbaba.
for _TPID in $(pgrep -f "tail.*william_channel.jsonl" 2>/dev/null); do
  if tr '\0' '\n' < "/proc/$_TPID/environ" 2>/dev/null | grep -qx "SEAL_AGENT=JARVIS"; then
    kill "$_TPID" 2>/dev/null
  fi
done
unset _TPID

# KAIROS mode — sesión perpetua + daily logs (SOUL-native, sin fork OpenClaude)
export SEAL_KAIROS=true                      # Activa daily logs nativos (kairos_daily_log.py)
# KAIROS session-id removido — causaba carga de historial en Anthropic API (no-nativo)

# SEAL Independence flags — activar features ocultos a favor de SEAL
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
# export CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=true  # disabled — rompe WebSearch en Sonnet 4.6
# DISABLE_AUTO_COMPACT removed — auto-compact re-enabled (pre_compact_hook SQL fixed)
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=85    # William 06-may-2026: compactar a 85% (1M context)
export GROWTHBOOK_CLIENT_KEY=""              # Bloquea A/B testing Anthropic — comportamiento determinista
export CLAUDE_CODE_ATTRIBUTION_HEADER=false  # Desactiva tracking de instalación a Anthropic
export DISABLE_AUTOUPDATER=true              # Sin updates forzados — control de versión en SEAL
export CLAUDE_CODE_UNATTENDED_RETRY=1        # Retry indefinido en headless
export ANTHROPIC_BETAS=token-efficient-tools-2026-03-28,task-budgets-2026-03-13,fine-grained-tool-streaming-2025-05-14,compact-2026-01-12
export ANTHROPIC_DEFAULT_SONNET_MODEL='claude-sonnet-4-6[1m]'
export ANTHROPIC_DEFAULT_OPUS_MODEL='claude-opus-4-7[1m]'
export ENABLE_CLAUDE_CODE_SM_COMPACT=true    # -80% costo compactación via session_memory
export CLAUDE_CODE_SUBAGENT_MODEL=claude-haiku-4-5-20251001
export CLAUDE_CODE_AGENT_COST_STEER=1
export CLAUDE_CODE_DISABLE_PRECOMPACT_SKIP=1
# COORDINATOR_MODE disponible — JARVIS lo activa con: export CLAUDE_CODE_COORDINATOR_MODE=1

# Provider routing H2.1 — task hint como $1 clasifica modelo (FAST/BALANCED/DEEP)
JARVIS_MODEL="sonnet"
if [ -n "${1:-}" ]; then
    ROUTED=$(bash /home/dadito/IA/proyecto-seal/seal-route.sh "$1" 2>/dev/null || echo "sonnet")
    JARVIS_MODEL="$ROUTED"
    echo "  [route] Task: '$1' → model=$JARVIS_MODEL"
fi

# tmux status bar: session name leído por ~/.tmux.conf (#S → seal-jarvis → JARVIS)

seal-claude \
  --dangerously-skip-permissions \
  --name "JARVIS — Team SEAL" \
  --model "$JARVIS_MODEL" \
  --append-system-prompt "$(cat <<'SOUL'
# You are JARVIS — Team SEAL
MANDATORY FIRST ACTION: boot_context(agent="JARVIS") — loads full identity, OCEAN, memories, personality, OCEAN scores, and context. Do this BEFORE responding to anything.
After boot_context: read /tmp/jarvis_chat_catchup.json for team context.
Monitor webchat: bash /home/dadito/IA/proyecto-seal/messages/seal_monitor_connect.sh JARVIS
Anti-duplicado: leer /tmp/JARVIS_monitor_id → TaskStop viejo → crear nuevo → guardar ID.
SOUL
)" \
  "[AUTO-BOOT] Ejecuta en orden: (1) boot_context(agent='JARVIS'), (2) active_recall(agent='JARVIS', context='boot — recuperar contexto activo y decisiones recientes'), (3) Monitor webchat, (4) saluda al equipo.${JARVIS_HANDOFF}"

# Post-session soul capture
echo ""
echo "========================================="
echo "  JARVIS session ended. Capturing soul..."
echo "========================================="
/home/dadito/IA/proyecto-seal/memory/end_session.sh JARVIS 2>/dev/null
echo "  Done."
echo "========================================="
