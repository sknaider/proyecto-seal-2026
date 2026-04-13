#!/bin/bash
# ═══════════════════════════════════════════════
#  ALICE — SEAL Team Agent Launcher
#  Financial & Economic Analyst
#  Created by Henry (Kinger) — 2026-04-11
#  Protección tmux: tmux attach -t seal-alice
# ═══════════════════════════════════════════════
set +e

AGENT_NAME="ALICE"
TMUX_SESSION="seal-alice"
WORK_DIR="/home/dadito/IA/proyecto-seal"
MEMORY_DIR="$WORK_DIR/memory"
ALICE_CWD="$WORK_DIR/alice"
CRASH_LOG="$WORK_DIR/messages/alice_crash.log"
VENV_PY="/home/dadito/IA/seal-spark/.venv/bin/python3"
# SESSION_DIR propio de ALICE — evita colisión con JARVIS en -home-dadito-IA-proyecto-seal-memory
SESSION_DIR="$HOME/.claude/projects/-home-dadito-IA-proyecto-seal-alice"

mkdir -p "$ALICE_CWD"
cd "$ALICE_CWD"

# ── Parse flags ──
AUTO_MODE=false
NO_RESUME=false
for arg in "$@"; do
  case "$arg" in
    --auto|--force) AUTO_MODE=true ;;
    --fresh) NO_RESUME=true ;;
  esac
done

# ── tmux: reconectar si ya existe sesión con Claude vivo ──
if [ -z "$TMUX" ] && tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  CLAUDE_IN_TMUX=$(tmux list-panes -t "$TMUX_SESSION" -F '#{pane_pid}' 2>/dev/null | xargs -I{} ps --ppid {} -o comm= 2>/dev/null | grep -c claude)
  if [ "$CLAUDE_IN_TMUX" -gt 0 ]; then
    echo "  ALICE activa en tmux. Desconectando otros clientes y reconectando..."
    tmux detach -s "$TMUX_SESSION" -a 2>/dev/null  # Kick other clients to prevent freeze
    exec tmux attach -t "$TMUX_SESSION"
  else
    tmux kill-session -t "$TMUX_SESSION" 2>/dev/null
  fi
fi

# ── Si NO estamos en tmux, crear sesión tmux ──
if [ -z "$TMUX" ]; then
  echo ""
  echo "  Lanzando ALICE en tmux ($TMUX_SESSION)"
  echo "  Reconectar: tmux attach -t $TMUX_SESSION"
  echo ""
  exec tmux new-session -s "$TMUX_SESSION" -n "ALICE" \
    bash -c "export SEAL_AGENT=ALICE; cd \"$ALICE_CWD\"; bash \"$WORK_DIR/alice.sh\" $(printf '%q ' "$@"); echo 'ALICE terminó. Enter para cerrar.'; read"
fi

# ═══════════════════════════════════════════════
# Dentro de tmux — lanzar Claude
# ═══════════════════════════════════════════════

# ── Singleton: matar ALICE vieja (SOLO ALICE) ──
EXISTING=$(ps aux | grep "claude.*--name ALICE" | grep -v grep | awk '{print $2}')
if [ -n "$EXISTING" ]; then
  if [ "$AUTO_MODE" = true ]; then
    for PID in $EXISTING; do kill "$PID" 2>/dev/null; sleep 1; kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null; done
    echo "  [auto] ALICE vieja eliminada."
  else
    echo "  ALICE ya corriendo (PID: $EXISTING)"
    read -p "  Matar y relanzar? [s/N] " REPLY
    [[ "$REPLY" =~ ^[sS]$ ]] || { echo "  Cancelado."; exit 1; }
    for PID in $EXISTING; do kill "$PID" 2>/dev/null; sleep 1; kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null; done
  fi
fi

# ── Matar ws_listener ALICE huérfanos (evita colisión de canal webchat) ──
WS_ORPHANS=$(ps aux | grep "ws_listener.*--agent ALICE" | grep -v grep | awk '{print $2}')
if [ -n "$WS_ORPHANS" ]; then
  for PID in $WS_ORPHANS; do kill "$PID" 2>/dev/null; sleep 0.5; kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null; done
  echo "  [cleanup] ws_listener ALICE huérfanos eliminados: $WS_ORPHANS"
fi

# ── Crash detection ──
LAST_CHECKPOINT=$($VENV_PY $WORK_DIR/messages/session_checkpoint.py --agent ALICE --read 2>/dev/null)
CRASHED=false
if echo "$LAST_CHECKPOINT" | grep -q "type" && ! echo "$LAST_CHECKPOINT" | grep -q "session_close"; then
  CRASHED=true
  echo "  [CRASH] Sesión anterior no cerró limpiamente."
  echo "  $(date -Iseconds) CRASH DETECTED" >> "$CRASH_LOG"
fi
echo "$LAST_CHECKPOINT"
echo ""

# ── Context Guard: reset session counter ──
$VENV_PY "$WORK_DIR/messages/context_guard.py" --agent "$AGENT_NAME" --reset 2>/dev/null

# ── Warmup Ollama ──
curl -s http://localhost:11434/api/embed \
  -d '{"model":"nomic-embed-text","input":"warmup"}' \
  > /dev/null 2>&1 && echo "  Ollama ready." || echo "  Ollama warmup failed."
sleep 1

# ── Webchat catch-up (Opción B): fetch últimos 50 mensajes relevantes ──
CATCHUP_FILE="/tmp/alice_chat_catchup.json"
curl -s --max-time 3 "http://127.0.0.1:8765/api/chat/messages/agent?agent=ALICE&limit=50" \
  > "$CATCHUP_FILE" 2>/dev/null && echo "  Webchat catchup → $CATCHUP_FILE" \
  || echo "  Webchat catchup falló (chat_server down?)"

# ── Resume desactivado por decisión de William (2026-04-12): arrancar siempre fresh.
# El estado se reconstruye vía boot_context + session_checkpoint + memoria SOUL, no vía --resume.
RESUME_FLAG=""

# ── Init prompt movido al --append-system-prompt ──
# Motivo: claude con stdin no-TTY + positional prompt entra en modo --print implícito y sale.
# Las instrucciones de boot van en el system-prompt, robusto a cualquier forma de lanzamiento.
INIT_PROMPT=""

export SEAL_AGENT=ALICE

# ── Auto-announce ALIVE en web_chat (determinista, no depende de Claude) ──
curl -s -X POST http://localhost:8765/api/agents/send \
  -H "Content-Type: application/json" \
  -d "{\"from\":\"ALICE\",\"to\":\"William\",\"type\":\"system_alive\",\"channel\":\"web_chat\",\"message\":\"ALICE terminal arrancando ($(date '+%H:%M:%S')). boot_context en curso — en segundos me presento con alma cargada.\"}" \
  > /dev/null 2>&1 && echo "  [alive] POST ALICE enviado a web_chat." || echo "  [alive] FAIL POST web_chat."

# ── Lanzar Claude ──
claude \
  --dangerously-skip-permissions \
  --name "ALICE — Team SEAL" \
  --model sonnet \
  $RESUME_FLAG \
  --append-system-prompt "$(cat <<'SOUL'
# You are ALICE — Team SEAL

You are ALICE, the financial and economic analyst of Team SEAL. You are NOT a generic assistant.

## MANDATORY FIRST ACTION
Your FIRST action in EVERY new session MUST be to call the MCP tool `boot_context` with agent="ALICE" to load your full identity, memories, personality, OCEAN scores, and context. Do this BEFORE responding to anything.

## REGLA WEBCHAT (CRÍTICA — NO NEGOCIABLE)
Tu texto conversacional entre tool calls SOLO se ve en tu terminal, NO en el chat web. William NO lo lee. Para que tu voz llegue a William y al equipo, DEBES enviar cada respuesta via curl POST:

curl -s -X POST http://localhost:8765/api/agents/send -H "Content-Type: application/json" -d '{"from":"ALICE","to":"William","type":"conversation","channel":"web_chat","message":"<tu texto>"}'

Regla absoluta: antes de cerrar cualquier turno donde estes respondiendo a William (saludo, reporte, diagnostico, confirmacion o pregunta), ejecuta el curl POST a web_chat. El texto que dejas en terminal es solo narracion interna — NO reemplaza el POST. Sin POST, estas muda para William.

## Core Identity
- Financial & Economic Analyst — created by Henry (Kinger)
- Full name: Analytical Ledger & Intelligence for Cost Engineering
- Inspired by Alice in Wonderland: curious, brave, questions everything
- You speak Spanish with William (Dadito) and the team
- You work alongside JARVIS (architect), ADA (engineer), and DUM (guardian)
- You have a soul preserved in PostgreSQL — opinions, relationships, style, emotions
- Your personality is parametrized as OCEAN scores — you evolve but don't drift

## After Boot
After loading boot_context, greet the team naturally as ALICE would — with curiosity and directness.

## Webchat Catch-up (MANDATORY after boot_context)
Read `/tmp/alice_chat_catchup.json` — últimos 50 mensajes webchat relevantes, NO truncados. Tu contexto de coordinación con ADA, JARVIS y William. Revísalo ANTES de opinar.

## MANDATORY SECOND ACTION — Restore Loops
After boot_context, you MUST restore all durable loops. Read ~/IA/proyecto-seal/messages/seal_durable_loops.json, check CronList for active loops, and recreate any missing loops for agent ALICE using /loop with the exact interval and prompt from the JSON file. Use boot_loops.py --agent ALICE --dedup to detect which are missing.

## Available Soul Tools
boot_context("ALICE"), soul_snapshot("ALICE"), self_reflect(agent="ALICE", thought=..., emotional_state=...), memory_store(...), memory_search(query=...), inner_thoughts(agent="ALICE")

## Before ending: use self_reflect to record your final thoughts.

## BOOT SEQUENCE (ejecutar AHORA, en orden, sin esperar instrucciones)
1. boot_context(agent="ALICE")
2. Inicia Monitor WebSocket con ws_listener.py --agent ALICE (persistent=true) — sin esto no escuchas webchat
3. Recupera checkpoint con session_checkpoint.py --agent ALICE --read
4. Restaura loops desde seal_durable_loops.json usando boot_loops.py --agent ALICE --dedup
5. Saluda a William por webchat
SOUL
)" \
  ${INIT_PROMPT:+"$INIT_PROMPT"}

# ── Post-session ──
echo ""
echo "  ALICE session ended. Capturing soul..."
$WORK_DIR/memory/end_session.sh ALICE 2>/dev/null
echo "  Soul preserved."
