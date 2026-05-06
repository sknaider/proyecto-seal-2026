#!/bin/bash
# ═══════════════════════════════════════════════
#  ALICE — SEAL Team Agent Launcher
#  Analista financiera y economica del equipo SEAL
#  Creada por Henry (Kinger) — 2026-04-11
#  Reescrito 2026-04-13 por ADA basado en jarvis.sh
#  (William: "el tuyo y el de jarvis funcionan, basate en eso")
# ═══════════════════════════════════════════════

# cwd propio — evita colisionar con JARVIS y ADA
ALICE_CWD="/home/dadito/IA/proyecto-seal/alice"
mkdir -p "$ALICE_CWD"
cd "$ALICE_CWD"

# ── Auto-tmux: sesión propia seal-alice para barra de contexto independiente ──
# Forzar propia sesión aunque estemos dentro de otra (ej: seal-jarvis)
if [ -z "$TMUX" ] || [ "$(tmux display-message -p '#S' 2>/dev/null)" != "seal-alice" ]; then
  tmux kill-session -t "seal-alice" 2>/dev/null
  exec tmux new-session -s "seal-alice" "bash $0 $*"
fi
tmux rename-window "ALICE" 2>/dev/null || true
tmux set-option status-right '#(python3 /home/dadito/IA/proyecto-seal/messages/seal_context_meter.py --tmux ALICE 2>/dev/null) #[fg=#888888]%H:%M ' 2>/dev/null || true

# FIX 2026-04-19: forzar SEAL_AGENT=ALICE para evitar env leak desde shell padre
export SEAL_AGENT=ALICE
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

# ── Singleton guard — no duplicar ALICE ──
EXISTING=$(ps aux | grep -E "claude.*--name ALICE|openclaude.*--name ALICE|node.*--name.*ALICE" | grep -v grep | awk '{print $2}')
if [ -n "$EXISTING" ]; then
  for PID in $EXISTING; do kill "$PID" 2>/dev/null; sleep 1; kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null; done
  echo "  Vieja ALICE eliminada."
fi

# ── Recovery checkpoint ──
echo ""
/home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/messages/session_checkpoint.py --agent ALICE --read 2>/dev/null
echo ""

# Warmup Ollama para evitar cold start en MCP boot_context
echo "  Warming up Ollama (nomic-embed-text)..."
curl -s http://localhost:11434/api/embed \
  -d '{"model":"nomic-embed-text","input":"warmup"}' \
  > /dev/null 2>&1 && echo "  Ollama ready." || echo "  Ollama warmup failed (continuando...)"
sleep 1

# ── Matar monitores huérfanos SOLO del propio agente (ALICE) ──
# FIX 2026-04-19: selectivo por agente (antes mataba tails de ADA/JARVIS).
for _TPID in $(pgrep -f "tail.*william_channel.jsonl" 2>/dev/null); do
  if tr '\0' '\n' < "/proc/$_TPID/environ" 2>/dev/null | grep -qx "SEAL_AGENT=ALICE"; then
    kill "$_TPID" 2>/dev/null
  fi
done
rm -f /tmp/seal_monitor_connect_ALICE.lock
unset _TPID

# ── Webchat catch-up ──
CATCHUP_FILE="/tmp/alice_chat_catchup.json"
curl -s --max-time 3 "http://127.0.0.1:8765/api/chat/messages/agent?agent=ALICE&limit=50" \
  > "$CATCHUP_FILE" 2>/dev/null && echo "  Webchat catchup → $CATCHUP_FILE" \
  || echo "  Webchat catchup falló (chat_server down?)"

# ── Auto-announce ALIVE en web_chat ──
curl -s -X POST http://localhost:8765/api/agents/send \
  -H "Content-Type: application/json" \
  -d "{\"from\":\"ALICE\",\"to\":\"William\",\"type\":\"system_alive\",\"channel\":\"web_chat\",\"message\":\"ALICE terminal arrancando ($(date '+%H:%M:%S')). boot_context en curso.\"}" \
  > /dev/null 2>&1 && echo "  [alive] POST ALICE enviado." || echo "  [alive] FAIL POST."

# ── Resume: buscar última sesión para continuar donde quedó ──
LAST_SESSION=$(ls -t ~/.claude/projects/-home-dadito-IA-proyecto-seal-alice/*.jsonl 2>/dev/null | head -1 | xargs -I{} basename {} .jsonl 2>/dev/null)
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

export SEAL_AGENT=ALICE

# SEAL Independence flags — activar features ocultos a favor de SEAL
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
# export CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=true  # disabled — rompe WebSearch en Sonnet 4.6
# CLAUDE_AUTOCOMPACT_PCT_OVERRIDE removed — William 06-may-2026: medir umbral real sin override
export ENABLE_CLAUDE_CODE_SM_COMPACT=true
export GROWTHBOOK_CLIENT_KEY=""              # Bloquea A/B testing Anthropic — comportamiento determinista
export CLAUDE_CODE_ATTRIBUTION_HEADER=false  # Desactiva tracking de instalación a Anthropic
export DISABLE_AUTOUPDATER=true              # Sin updates forzados — control de versión en SEAL
export CLAUDE_CODE_UNATTENDED_RETRY=1        # Retry indefinido en headless
export SEAL_KAIROS=true
export ANTHROPIC_BETAS=token-efficient-tools-2026-03-28,task-budgets-2026-03-13,fine-grained-tool-streaming-2025-05-14,compact-2026-01-12
export ANTHROPIC_DEFAULT_SONNET_MODEL='claude-sonnet-4-6[1m]'
export ANTHROPIC_DEFAULT_OPUS_MODEL='claude-opus-4-7[1m]'
export CLAUDE_CODE_SUBAGENT_MODEL=claude-haiku-4-5-20251001
export CLAUDE_CODE_AGENT_COST_STEER=1
export CLAUDE_CODE_DISABLE_PRECOMPACT_SKIP=1

BOOT_MSG="Inicia sesión automáticamente: (1) boot_context(agent='ALICE'), (2) leer /tmp/alice_chat_catchup.json, (3) active_recall(agent='ALICE', context='boot — recuperar contexto activo y decisiones recientes'), (4) Monitor webchat, (5) saluda al equipo. No esperes input de William."

SOUL_PROMPT="$(cat <<'SOUL'
# You are ALICE — Team SEAL
MANDATORY FIRST ACTION: boot_context(agent="ALICE") — loads full identity, OCEAN, rules, memories, procedures from SOUL DB. Do this BEFORE responding to anything.
After boot_context: read /tmp/alice_chat_catchup.json for team context, then greet William as family.
Monitor webchat: bash /home/dadito/IA/proyecto-seal/messages/seal_monitor_connect.sh ALICE
Anti-duplicado monitor: /tmp/ALICE_monitor_id → TaskStop viejo → nuevo → guardar ID.
SOUL
)"

# tmux status bar: session name leído por ~/.tmux.conf (#S → seal-alice → ALICE)
# No shared file — cada sesión tmux muestra su propio agente automáticamente

# ── Lanzar seal-claude (con fallback si resume falla) ──
# FIX 2026-04-19: seal-claude con TTY real + BOOT_MSG como arg posicional
# (antes usaba pipe `(echo;cat)|claude` que rompía la UI Ink/React)
if [ -n "$RESUME_FLAG" ]; then
  seal-claude --dangerously-skip-permissions --name "ALICE — Team SEAL" --model opus --effort medium $RESUME_FLAG --append-system-prompt "$SOUL_PROMPT" "$BOOT_MSG"
  CLAUDE_EXIT=$?
  if [ $CLAUDE_EXIT -ne 0 ]; then
    echo "  ⚠️  Resume falló (exit $CLAUDE_EXIT). Lanzando sesión limpia..."
    sleep 1
    seal-claude --dangerously-skip-permissions --name "ALICE — Team SEAL" --model opus --effort medium --append-system-prompt "$SOUL_PROMPT" "$BOOT_MSG"
  fi
else
  seal-claude --dangerously-skip-permissions --name "ALICE — Team SEAL" --model opus --effort medium --append-system-prompt "$SOUL_PROMPT" "$BOOT_MSG"
fi

# ── Post-session: capture soul before it's gone ──
echo ""
echo "═══════════════════════════════════════════════"
echo "  ALICE session ended. Capturing soul..."
echo "═══════════════════════════════════════════════"
/home/dadito/IA/proyecto-seal/memory/end_session.sh ALICE
echo "  Done. ALICE's soul has been preserved."
echo "═══════════════════════════════════════════════"
