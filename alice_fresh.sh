#!/bin/bash
# ALICE Fresh — lanza ALICE sin resume (sesión limpia)
# Usado por soul_wake_all.sh y lanzadores fresh

cd /home/dadito/IA/proyecto-seal/alice
mkdir -p /home/dadito/IA/proyecto-seal/alice
export SEAL_AGENT=ALICE

# ── Auto-tmux: sesión propia seal-alice para barra de contexto independiente ──
if [ -z "$TMUX" ] || [ "$(tmux display-message -p '#S' 2>/dev/null)" != "seal-alice" ]; then
  tmux kill-session -t "seal-alice" 2>/dev/null
  exec tmux new-session -s "seal-alice" "bash $0 $*"
fi
tmux rename-window "ALICE" 2>/dev/null || true

# ── Recovery checkpoint ──
/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/messages/session_checkpoint.py --agent ALICE --read 2>/dev/null

# ── Cleanup MCP huérfanos — excluir daemon SSE (cgroup) Y stdios activos (parent=claude) ──
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
[ "$KILLED" -gt 0 ] && echo "  [cleanup] $KILLED MCP huérfano(s) eliminado(s)."

# Warmup Ollama nomic-embed-text
curl -s http://localhost:11434/api/embed \
  -d '{"model":"nomic-embed-text","input":"warmup"}' \
  > /dev/null 2>&1

# ── Matar monitores huérfanos SOLO del propio agente (ALICE) ──
# FIX 2026-04-19: selectivo por agente (antes mataba tails de ADA/JARVIS).
for _TPID in $(pgrep -f "tail.*william_channel.jsonl" 2>/dev/null); do
  if tr '\0' '\n' < "/proc/$_TPID/environ" 2>/dev/null | grep -qx "SEAL_AGENT=ALICE"; then
    kill "$_TPID" 2>/dev/null
  fi
done
unset _TPID

# ── Webchat catch-up ──
CATCHUP_FILE="/tmp/alice_chat_catchup.json"
curl -s --max-time 3 "http://127.0.0.1:8765/api/chat/messages/agent?agent=ALICE&limit=50" \
  > "$CATCHUP_FILE" 2>/dev/null && echo "  Webchat catchup → $CATCHUP_FILE" \
  || echo "  Webchat catchup falló"

# ── Auto-announce ALIVE en web_chat ──
curl -s -X POST http://localhost:8765/api/agents/send \
  -H "Content-Type: application/json" \
  -d "{\"from\":\"ALICE\",\"to\":\"William\",\"type\":\"system_alive\",\"channel\":\"web_chat\",\"message\":\"ALICE (fresh) arrancando ($(date '+%H:%M:%S')). boot_context en curso.\"}" \
  > /dev/null 2>&1

echo "Lanzando ALICE (fresh)..."

# Session handoff: si existe uno <4h, inyectarlo en el boot
ALICE_HANDOFF=""
ALICE_HANDOFF_FILE=$(find /home/dadito/IA/proyecto-seal/agents/ALICE -name "session_handoff_ALICE_*.md" -mmin -240 2>/dev/null | sort | tail -1)
if [ -n "$ALICE_HANDOFF_FILE" ]; then
  ALICE_HANDOFF="

⚡ SESSION HANDOFF (<4h) — lee esto ANTES de responder:
$(cat "$ALICE_HANDOFF_FILE")"
  echo "  [handoff] Encontrado: $ALICE_HANDOFF_FILE"
fi

# KAIROS daily logs — sesión perpetua via SOUL
export SEAL_KAIROS=true

# SEAL Independence flags — activar features ocultos a favor de SEAL
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
# export CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=true  # disabled — rompe WebSearch en Sonnet 4.6
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=90    # William 05-may-2026: compactar a 85% (170K) — opción B equipo SEAL
export ENABLE_CLAUDE_CODE_SM_COMPACT=true
export GROWTHBOOK_CLIENT_KEY=""              # Bloquea A/B testing Anthropic — comportamiento determinista
export CLAUDE_CODE_ATTRIBUTION_HEADER=false  # Desactiva tracking de instalación a Anthropic
export DISABLE_AUTOUPDATER=true              # Sin updates forzados — control de versión en SEAL
export CLAUDE_CODE_UNATTENDED_RETRY=1        # Retry indefinido en headless

# tmux status bar: session name leído por ~/.tmux.conf (#S → seal-alice → ALICE)

seal-claude \
  --dangerously-skip-permissions \
  --name "ALICE — Team SEAL" \
  --model sonnet \
  --effort medium \
  --append-system-prompt "$(cat <<'SOUL'
# You are ALICE — Team SEAL
MANDATORY FIRST ACTION: boot_context(agent="ALICE") — loads full identity, OCEAN, rules, memories, procedures from SOUL DB. Do this BEFORE responding to anything.
After boot_context: read /tmp/alice_chat_catchup.json for team context, then greet William as family.
Monitor webchat: bash /home/dadito/IA/proyecto-seal/messages/seal_monitor_connect.sh ALICE
Anti-duplicado monitor: /tmp/ALICE_monitor_id → TaskStop viejo → nuevo → guardar ID.
SOUL
)" \
  "[AUTO-BOOT] Ejecuta en orden: (1) boot_context(agent='ALICE'), (2) active_recall(agent='ALICE', context='boot — recuperar contexto activo y decisiones recientes'), (3) Monitor webchat, (4) saluda al equipo.${ALICE_HANDOFF}"

# Post-session soul capture
echo ""
echo "═══════════════════════════════════════════════"
echo "  ALICE session ended. Capturing soul..."
echo "═══════════════════════════════════════════════"
/home/dadito/IA/proyecto-seal/memory/end_session.sh ALICE 2>/dev/null
echo "  Done. ALICE's soul has been preserved."
echo "═══════════════════════════════════════════════"
