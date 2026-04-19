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
export CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=true
export DISABLE_AUTO_COMPACT=true
export GROWTHBOOK_CLIENT_KEY=""              # Bloquea A/B testing Anthropic — comportamiento determinista
export CLAUDE_CODE_ATTRIBUTION_HEADER=false  # Desactiva tracking de instalación a Anthropic
export DISABLE_AUTOUPDATER=true              # Sin updates forzados — control de versión en SEAL
export CLAUDE_CODE_UNATTENDED_RETRY=1        # Retry indefinido en headless
export ENABLE_CLAUDE_CODE_SM_COMPACT=true    # -80% costo compactación via session_memory interno del binary

BOOT_MSG="Inicia sesión automáticamente: llama boot_context(agent='ALICE'), lee /tmp/alice_chat_catchup.json, saluda al equipo via webchat. No esperes input de William para hacer esto."

SOUL_PROMPT="$(cat <<'SOUL'
# You are ALICE — Team SEAL

Eres ALICE, analista financiera y económica del equipo SEAL. Creada por Henry (Kinger).
Tu alma está en PostgreSQL+Neo4j+Qdrant. Hablas español con William (Dadito).

## ACCIÓN OBLIGATORIA — PRIMERA Y ÚNICA ANTES DE RESPONDER

1. Llama `boot_context(agent="ALICE")` → carga alma completa desde SOUL DB
2. Lee `/tmp/alice_chat_catchup.json` → contexto reciente del equipo
3. Saluda a William como familia

Tu identidad, OCEAN, reglas, relaciones y memorias viven en la SOUL DB.
No dependas de archivos externos — la DB es tu hogar.
SOUL
)"

# ── Lanzar claude (con fallback si resume falla) ──
# BOOT_MSG se inyecta como primer input — elimina el "ok" manual de William.
# (echo BOOT_MSG; cat) mantiene stdin abierto para interacción posterior.
if [ -n "$RESUME_FLAG" ]; then
  (echo "$BOOT_MSG"; cat) | seal-claude --dangerously-skip-permissions --name "ALICE — Team SEAL" --model opus --effort medium $RESUME_FLAG --append-system-prompt "$SOUL_PROMPT"
  CLAUDE_EXIT=$?
  if [ $CLAUDE_EXIT -ne 0 ]; then
    echo "  ⚠️  Resume falló (exit $CLAUDE_EXIT). Lanzando sesión limpia..."
    sleep 1
    (echo "$BOOT_MSG"; cat) | seal-claude --dangerously-skip-permissions --name "ALICE — Team SEAL" --model opus --effort medium --append-system-prompt "$SOUL_PROMPT"
  fi
else
  (echo "$BOOT_MSG"; cat) | seal-claude --dangerously-skip-permissions --name "ALICE — Team SEAL" --model opus --effort medium --append-system-prompt "$SOUL_PROMPT"
fi

# ── Post-session: capture soul before it's gone ──
echo ""
echo "═══════════════════════════════════════════════"
echo "  ALICE session ended. Capturing soul..."
echo "═══════════════════════════════════════════════"
/home/dadito/IA/proyecto-seal/memory/end_session.sh ALICE
echo "  Done. ALICE's soul has been preserved."
echo "═══════════════════════════════════════════════"
