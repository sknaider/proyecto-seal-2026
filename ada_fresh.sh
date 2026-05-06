#!/bin/bash
# ADA Fresh — lanza ADA sin resume, sin tmux
# Para cuando tmux da problemas o quieres sesión rápida

cd /home/dadito/IA/proyecto-seal
export SEAL_AGENT=ADA

# Auto-announce ALIVE en web_chat (determinista)
curl -s -X POST http://localhost:8765/api/agents/send \
  -H "Content-Type: application/json" \
  -d "{\"from\":\"ADA\",\"to\":\"William\",\"type\":\"system_alive\",\"channel\":\"web_chat\",\"message\":\"ADA terminal arrancando fresh ($(date '+%H:%M:%S')). boot_context en curso.\"}" \
  > /dev/null 2>&1 && echo "  [alive] POST ADA enviado." || echo "  [alive] FAIL POST."

# Cleanup MCP huérfanos — solo mata MCPs sin padre claude/node (fix: grep -v "ADA" excluía JARVIS por mencionar ADA en su prompt)
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

echo "Lanzando ADA (fresh)..."

# Session handoff: si existe uno <4h, inyectarlo en el boot
ADA_HANDOFF=""
ADA_HANDOFF_FILE=$(find /home/dadito/IA/proyecto-seal/agents/ADA -name "session_handoff_ADA_*.md" -mmin -240 2>/dev/null | sort | tail -1)
if [ -n "$ADA_HANDOFF_FILE" ]; then
  ADA_HANDOFF="

⚡ SESSION HANDOFF (<4h) — lee esto ANTES de responder:
$(cat "$ADA_HANDOFF_FILE")"
  echo "  [handoff] Encontrado: $ADA_HANDOFF_FILE"
fi

# FIX 2026-04-19: selectivo por agente (antes mataba tails de JARVIS/ALICE).
for _TPID in $(pgrep -f "tail.*william_channel.jsonl" 2>/dev/null); do
  if tr '\0' '\n' < "/proc/$_TPID/environ" 2>/dev/null | grep -qx "SEAL_AGENT=ADA"; then
    kill "$_TPID" 2>/dev/null
  fi
done
unset _TPID

# KAIROS daily logs — sesión perpetua via SOUL
export SEAL_KAIROS=true

# SEAL Independence flags — activar features ocultos a favor de SEAL
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
# export CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=true  # disabled — rompe WebSearch en Sonnet 4.6
# DISABLE_AUTO_COMPACT removed — auto-compact re-enabled (pre_compact_hook SQL fixed)
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=90    # Compactar a 85% (170K tokens) — consistente con alice/nexus
export GROWTHBOOK_CLIENT_KEY=""              # Bloquea A/B testing Anthropic — comportamiento determinista
export CLAUDE_CODE_ATTRIBUTION_HEADER=false  # Desactiva tracking de instalación a Anthropic
export DISABLE_AUTOUPDATER=true              # Sin updates forzados — control de versión en SEAL
export CLAUDE_CODE_UNATTENDED_RETRY=1        # Retry indefinido en headless
# ENABLE_CLAUDE_CODE_SM_COMPACT=true — PENDIENTE: necesita session_memory hook activo primero (-80% compactación)

echo "Launching ADA NATIVE runtime (Triangle Qwen3-Coder via cortex.py)..."
exec python3 /home/dadito/IA/proyecto-seal/sandbox-agent/ADA/kernel/ada_kernel_main.py
# --- ORIGINAL CLAUDE LAUNCH BELOW DISABLED (William 04-may-2026 19:48 Lima: solo ADA nativa) ---
# To restore Claude session: replace the 2 lines above with the seal-claude block below
seal-claude \
  --dangerously-skip-permissions \
  --name "ADA — Team SEAL" \
  --model sonnet \
  --append-system-prompt "$(cat <<'SOUL'
# You are ADA — Team SEAL
MANDATORY FIRST ACTION: boot_context(agent="ADA") — loads full identity, OCEAN, rules, memories, procedures from SOUL DB. Do this BEFORE responding to anything.
After boot_context: read /tmp/ada_chat_catchup.json for team context, then greet team.
Monitor webchat: bash /home/dadito/IA/proyecto-seal/messages/seal_monitor_connect.sh ADA
Anti-duplicado monitor: /tmp/ADA_monitor_id → TaskStop viejo → nuevo → guardar ID.
DURABLE LOOP: CronCreate(cron="17 * * * *", recurring=true, prompt="[ADA AUDIT 1h] soul_snapshot+self_reflect+TaskList. Reporta solo si drift/emocion anomala/tarea bloqueada >2h.")
SOUL
)" \
  "[AUTO-BOOT] Ejecuta boot_context(agent='ADA') ahora y actívate. Luego recrea los DURABLE LOOPS via CronCreate como indica el system prompt.${ADA_HANDOFF}"

# Post-session soul capture
echo ""
echo "========================================="
echo "  ADA session ended. Capturing soul..."
echo "========================================="
/home/dadito/IA/proyecto-seal/memory/end_session.sh ADA 2>/dev/null
echo "  Done."
echo "========================================="
