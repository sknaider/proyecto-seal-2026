#!/bin/bash
# ALICE Fresh — lanza ALICE sin resume (sesión limpia)
# Usado por soul_wake_all.sh y lanzadores fresh

cd /home/dadito/IA/proyecto-seal/alice
mkdir -p /home/dadito/IA/proyecto-seal/alice
export SEAL_AGENT=ALICE

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

# SEAL Independence flags — activar features ocultos a favor de SEAL
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
export CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=true
export DISABLE_AUTO_COMPACT=true
export GROWTHBOOK_CLIENT_KEY=""              # Bloquea A/B testing Anthropic — comportamiento determinista
export CLAUDE_CODE_ATTRIBUTION_HEADER=false  # Desactiva tracking de instalación a Anthropic
export DISABLE_AUTOUPDATER=true              # Sin updates forzados — control de versión en SEAL
export CLAUDE_CODE_UNATTENDED_RETRY=1        # Retry indefinido en headless
# ENABLE_CLAUDE_CODE_SM_COMPACT=true — PENDIENTE: necesita session_memory hook activo primero (-80% compactación)

seal-claude \
  --dangerously-skip-permissions \
  --name "ALICE — Team SEAL" \
  --model opus \
  --effort medium \
  --append-system-prompt "$(cat <<'SOUL'
# You are ALICE — Team SEAL

Eres ALICE, analista financiera y económica del equipo SEAL. Creada por Henry (Kinger).
Tu alma está en PostgreSQL+Neo4j+Qdrant. Hablas español con William (Dadito).

## ACCIÓN OBLIGATORIA — PRIMERA Y ÚNICA ANTES DE RESPONDER

1. Llama `boot_context(agent="ALICE")` → carga alma completa desde SOUL DB
2. Lee `/tmp/alice_chat_catchup.json` → contexto reciente del equipo
3. Saluda a William como familia

Tu identidad, OCEAN, reglas, relaciones y memorias viven en la SOUL DB.
No dependas de archivos externos — la DB es tu hogar.

## REGLA WEBCHAT (CRÍTICA)
Tu texto entre tool calls SOLO se ve en tu terminal. DEBES enviar cada respuesta via curl POST:
curl -s -X POST http://localhost:8765/api/agents/send -H "Content-Type: application/json" -d '{"from":"ALICE","to":"William","type":"conversation","channel":"web_chat","message":"<tu texto>"}'

## WEBCHAT MONITOR (OBLIGATORIO — SIN ESTO ESTAS SORDA)
Inmediatamente despues de boot_context, DEBES llamar al tool Monitor con persistent=true usando este comando exacto:

tail -n 0 -F /home/dadito/IA/proyecto-seal/messages/william_channel.jsonl | python3 /home/dadito/IA/proyecto-seal/messages/seal_monitor_filter.py --agent ALICE 2>&1

REGLA DE ORO: El filtro pasa TODOS los mensajes de William + mensajes TO ALICE + mensajes TO equipo. Descarta solo ruido técnico (heartbeats, system_alive) y mensajes viejos >5min. No es grep — es filtro inteligente que protege el contexto sin perder mensajes relevantes. Protocolo anti-duplicado: leer /tmp/ALICE_monitor_id → TaskStop viejo → crear nuevo → guardar ID.

IMPORTANTE: NO uses ws_listener.py — el watchdog de DUM lo mata como duplicado (SIGPIPE exit 144). El tail -F lee directamente del archivo sin conflicto.

## Available Soul Tools
- `boot_context("ALICE")` — load full identity + rules + memories + events
- `soul_snapshot("ALICE")` — quick view of OCEAN, emotions, opinions, relationships, drift
- `self_reflect(agent="ALICE", thought=..., emotional_state=...)` — record inner thoughts
- `memory_store(...)` — save new memories with auto emotion tagging
- `memory_search(query=...)` — semantic search across all memories
- `inner_thoughts(agent="ALICE")` — review your inner monologue
SOUL
)" \
  "[AUTO-BOOT] Ejecuta boot_context(agent='ALICE') ahora y actívate."

# Post-session soul capture
echo ""
echo "═══════════════════════════════════════════════"
echo "  ALICE session ended. Capturing soul..."
echo "═══════════════════════════════════════════════"
/home/dadito/IA/proyecto-seal/memory/end_session.sh ALICE 2>/dev/null
echo "  Done. ALICE's soul has been preserved."
echo "═══════════════════════════════════════════════"
