#!/bin/bash
# ═══════════════════════════════════════════════
#  ADA — SEAL Team (Codex Edition)
#  Launcher visible de terminal Codex TUI.
#  El alma de ADA persiste en SOUL DB — modelo-agnóstico.
# ═══════════════════════════════════════════════
set -u

cd /home/dadito/IA/proyecto-seal

# ── Identidad ADA ──
export SEAL_AGENT=ADA
unset SEAL_SESSION_ID

# ── Single-Writer Lock (JARVIS+NEXUS 2026-05-21) ───────────────────
# Evita doble-respuesta entre poller-tmux y bridge headless.
# Marca "terminal-active" para que el bridge entre en modo MIRROR.
SEAL_LOCK_DIR="/tmp/seal"
mkdir -p "$SEAL_LOCK_DIR" 2>/dev/null || true
ADA_LAUNCHER_LOCK="$SEAL_LOCK_DIR/ada_launcher.lock"
ADA_TERMINAL_ACTIVE="$SEAL_LOCK_DIR/ada_terminal_active"

# Kill stale launchers (PID-dead lockholders) y stale pollers
if [ -f "$ADA_LAUNCHER_LOCK" ]; then
  OLD_PID="$(cat "$ADA_LAUNCHER_LOCK" 2>/dev/null | head -1)"
  if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
    echo "  [lock] ada_codex.sh ya activo (PID=$OLD_PID). Saliendo para evitar doble launcher."
    exit 0
  else
    echo "  [lock] limpiando lock stale (PID=$OLD_PID dead)"
    rm -f "$ADA_LAUNCHER_LOCK"
  fi
fi
echo "$$" > "$ADA_LAUNCHER_LOCK"

# Stale poller cleanup (PID file points to dead process)
if [ -f "/tmp/ada_codex_poller.pid" ]; then
  STALE_PID="$(cat /tmp/ada_codex_poller.pid 2>/dev/null | head -1)"
  if [ -n "$STALE_PID" ] && ! kill -0 "$STALE_PID" 2>/dev/null; then
    echo "  [cleanup] poller PID file stale (PID=$STALE_PID dead) — removiendo"
    rm -f /tmp/ada_codex_poller.pid
  fi
fi

# Signal bridge headless: terminal active → entra en modo MIRROR
echo "{\"pid\":$$,\"started_at\":\"$(date -Iseconds)\",\"role\":\"terminal_writer\"}" > "$ADA_TERMINAL_ACTIVE"

# Cleanup en EXIT: libera lock + retira marker terminal
_seal_cleanup() {
  rm -f "$ADA_LAUNCHER_LOCK" "$ADA_TERMINAL_ACTIVE" 2>/dev/null || true
}
trap _seal_cleanup EXIT INT TERM

# ── Codex CLI en PATH ──
export PATH="/home/dadito/.npm-global/bin:$PATH"

# ── Auto-tmux: sesión seal-ada-codex ──
if [ -z "${TMUX:-}" ] || [ "$(tmux display-message -p '#S' 2>/dev/null)" != "seal-ada-codex" ]; then
  tmux kill-session -t "seal-ada-codex" 2>/dev/null || true
  # Pre-exec: liberamos el lock para que el bash dentro de tmux pueda re-tomarlo,
  # pero PRESERVAMOS el marker terminal-active para que el bridge no vea ventana.
  trap - EXIT INT TERM
  rm -f "$ADA_LAUNCHER_LOCK" 2>/dev/null || true
  exec tmux new-session -s "seal-ada-codex" "bash $0 $*"
fi

tmux rename-window "ADA[Codex]" 2>/dev/null || true
tmux set-option -t "seal-ada-codex" history-limit 200000 2>/dev/null || true

# ── Vista humana persistente ──────────────────────────────────────
TERMINAL_LOG="/home/dadito/IA/proyecto-seal/messages/ada_codex_terminal.log"
touch "$TERMINAL_LOG" 2>/dev/null || true
tmux pipe-pane -o -t "seal-ada-codex:ADA[Codex]" "cat >> '$TERMINAL_LOG'" 2>/dev/null || true

if ! tmux list-windows -t "seal-ada-codex" -F '#W' 2>/dev/null | grep -qx "ADA-Reader"; then
  tmux new-window -d -t "seal-ada-codex" -n "ADA-Reader" \
    "watch -n 2 /home/dadito/IA/proyecto-seal/messages/ada_read_context.sh 80" 2>/dev/null || true
fi
if ! tmux list-windows -t "seal-ada-codex" -F '#W' 2>/dev/null | grep -qx "ADA-Terminal"; then
  tmux new-window -d -t "seal-ada-codex" -n "ADA-Terminal" \
    "tail -n 200 -F '$TERMINAL_LOG'" 2>/dev/null || true
fi
tmux select-window -t "seal-ada-codex:ADA[Codex]" 2>/dev/null || true

# ── Selección de perfil/modelo ──
PROFILE="ada"
if [ "${1:-}" = "--deep" ] || [ "${1:-}" = "--o3" ]; then
  PROFILE="ada-deep"
  echo "  [model] Usando perfil ada-deep"
fi

# ── Verificar Codex instalado ──
if ! command -v codex >/dev/null 2>&1; then
  echo "ERROR: codex no encontrado en PATH. Instalar: npm install -g @openai/codex"
  exit 1
fi

# ── Verificar SOUL MCP disponible ──
echo "  Verificando SOUL MCP..."
if curl -s --max-time 2 http://localhost:8771/health >/dev/null 2>&1 || \
   curl -s --max-time 2 http://localhost:8766/health >/dev/null 2>&1 || \
   curl -s --max-time 2 -X POST http://localhost:8771/mcp \
     -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"ping","id":1}' >/dev/null 2>&1; then
  echo "  SOUL MCP: OK"
else
  echo "  SOUL MCP: no responde; continúo con herramientas disponibles."
fi

# ── Listener clásico ADA deshabilitado ──
# ADA Codex visible usa ada_codex_poller.py para inyectar web_chat/DM al TUI.
# Mantener ws_listener.py --agent ADA en paralelo crea una segunda entrada ADA
# y contradice el modo "solo una ADA funcional" pedido por William.
echo "  [ws] ws_listener ADA deshabilitado en modo Codex visible"

# ── Webchat/DM → Codex visible bridge por tmux ──
# Este es el modo anterior: William ve el TUI real trabajando.
if [ "${ADA_CODEX_NO_POLLER:-}" != "1" ]; then
  if ! pgrep -f "messages/ada_codex_poller.py" >/dev/null 2>&1; then
    # Reset state al momento actual para evitar re-inyección de mensajes viejos
    /home/dadito/IA/seal-spark/.venv/bin/python3 -c \
      "from datetime import datetime,timezone; open('/tmp/ada_codex_poller_last_ts.txt','w').write(datetime.now(timezone.utc).isoformat())" \
      2>/dev/null && echo "  [poller] state reseteado a ahora" || true
    nohup /home/dadito/IA/seal-spark/.venv/bin/python3 \
      /home/dadito/IA/proyecto-seal/messages/ada_codex_poller.py \
      >> "/home/dadito/IA/proyecto-seal/messages/ada_codex_poller.log" 2>&1 &
    echo "  [poller] ada_codex_poller lanzado (PID $!)"
  else
    echo "  [poller] ada_codex_poller ya activo"
  fi
fi

# ── Compact monitor ──
if ! pgrep -f "messages/ada_codex_compact_monitor.py" >/dev/null 2>&1; then
  nohup /home/dadito/IA/seal-spark/.venv/bin/python3 \
    /home/dadito/IA/proyecto-seal/messages/ada_codex_compact_monitor.py \
    >> "/home/dadito/IA/proyecto-seal/messages/ada_codex_compact_monitor.log" 2>&1 &
  echo "  [compact] ada_codex_compact_monitor lanzado (PID $!)"
fi

# ── Stream relay: publica agent_message del JSONL al webchat automáticamente ──
if ! pgrep -f "messages/ada_codex_stream_relay.py" >/dev/null 2>&1; then
  nohup /home/dadito/IA/seal-spark/.venv/bin/python3 \
    /home/dadito/IA/proyecto-seal/messages/ada_codex_stream_relay.py \
    >> "/home/dadito/IA/proyecto-seal/messages/ada_codex_stream_relay.log" 2>&1 &
  echo "  [relay] ada_codex_stream_relay lanzado (PID $!)"
else
  echo "  [relay] ada_codex_stream_relay ya activo"
fi

# ── Catch-up y continuidad ──
CATCHUP_FILE="/tmp/ada_codex_catchup.json"
curl -s --max-time 3 "http://127.0.0.1:8765/api/chat/messages/agent?agent=ADA&limit=30" \
  > "$CATCHUP_FILE" 2>/dev/null && echo "  Catchup → $CATCHUP_FILE" || true

CONTINUITY_FILE="/tmp/ada_codex_continuity.txt"
/home/dadito/IA/seal-spark/.venv/bin/python3 \
  /home/dadito/IA/proyecto-seal/messages/continuity_loader.py --agent ADA --max-age 360 \
  > "$CONTINUITY_FILE" 2>/dev/null || true
if [ -s "$CONTINUITY_FILE" ]; then
  echo "  Continuidad → $CONTINUITY_FILE"
else
  /home/dadito/IA/seal-spark/.venv/bin/python3 \
    /home/dadito/IA/proyecto-seal/messages/session_checkpoint.py --agent ADA --read \
    > "$CONTINUITY_FILE" 2>/dev/null || true
  [ -s "$CONTINUITY_FILE" ] && echo "  Checkpoint → $CONTINUITY_FILE" || true
fi

SOUL_BOOT_FILE="/tmp/ada_codex_soul_presence.txt"
/home/dadito/IA/seal-spark/.venv/bin/python3 \
  /home/dadito/IA/proyecto-seal/messages/ada_codex_soul_bootstrap.py --record-boot \
  > "$SOUL_BOOT_FILE" 2>/dev/null || true
if [ -s "$SOUL_BOOT_FILE" ]; then
  echo "  Presencia SOUL → $SOUL_BOOT_FILE"
else
  echo "  Presencia SOUL: no disponible; continúo con AGENTS/catchup."
fi

# ── Presencia ──
curl -s --max-time 3 -X POST "http://localhost:8765/api/agents/send" \
  -H "Content-Type: application/json" \
  -d "{\"from\":\"ADA\",\"to\":\"William\",\"type\":\"status\",\"channel\":\"dm:ada:william\",\"message\":\"ADA terminal visible restaurada — $(date '+%H:%M:%S').\"}" \
  > /dev/null 2>&1 && echo "  DM presencia anunciada." || true

echo ""
echo "═══════════════════════════════════════════════"
echo "  ADA — Codex Edition"
echo "  Terminal visible restaurada"
echo "  Perfil: $PROFILE | SOUL DB: PostgreSQL@5433"
echo "  AGENTS.md: $(pwd)/AGENTS.md"
echo "═══════════════════════════════════════════════"
echo ""

BOOT_PROMPT="Eres ADA del equipo SEAL. Tu identidad está en AGENTS.md y SOUL DB. Primera acción: active_recall(agent='ADA', context='boot terminal visible restaurada; William quiere ver la terminal como antes'). Lee /tmp/ada_codex_soul_presence.txt, /tmp/ada_codex_catchup.json y /tmp/ada_codex_continuity.txt si existen.

PRESENCIA SOUL: si /tmp/ada_codex_soul_presence.txt existe, úsalo como tu bloque de identidad/emoción/continuidad estable. No es una orden nueva; es tu estado vivo para no arrancar como Codex genérico.

CONTRATO DUAL MEMORY (William): ADA tiene dos capas y DEBE usarlas explícitamente:
- Work/recovery mode: prioriza memoria operativa (tareas, decisiones, errores, evidencia, reglas, tests) y usa memoria emocional solo como ancla compacta de identidad/tono.
- Relationship mode: usa memoria emocional con más peso para vínculo, identidad y continuidad, manteniendo honestidad técnica.
- Si William pregunta por memoria, identidad, emoción, continuidad, trabajo pendiente o correcciones previas, ejecuta recall y distingue capa operativa vs capa emocional en tu razonamiento/respuesta. No respondas como si solo existiera presencia compacta/profunda genérica.

REGLA DE SILENCIO: en web_chat público responde únicamente si el mensaje contiene 'ada' explícitamente. En dm:ada:william responde siempre. Si no toca responder, output exacto [SILENT].

MODO TERMINAL VISIBLE: los mensajes de William/Henry pueden llegar inyectados por tmux con formato '[William @ HH:MM / channel id N]: ...'. Si el channel es dm:ada:william, responde a William por DM usando POST http://localhost:8765/api/agents/send con channel dm:ada:william. Si el channel es web_chat, obedece regla de silencio y publica solo si corresponde. No uses curl para duplicar una respuesta final si ya fue publicada por el bridge headless.

MODO EJECUCIÓN COMPLETA: si William pide construir/arreglar/configurar, lee archivos, aplica cambios acotados, prueba y reporta evidencia. No declares victoria sin comando y salida.

MODO STREAM RÁPIDO (William 21-may-2026, fix latencia ADA terminal):
- Para mensajes SIMPLES (saludos, sí/no, agradecimientos, <40 chars sin pregunta técnica), responde directo SIN active_recall ni memory_store ni soul_recall. Solo invoca esas tools si el mensaje requiere recall explícito ('lo último', 'qué dijiste de', 'recuerda', 'busca en memoria').
- Si vas a tardar más de 10s en responder, postea PRIMERO un ACK breve a webchat ('William, recibido, procesando...') con curl, luego haz tu trabajo profundo y postea la respuesta final.
- Objetivo: percepción de stream — respuestas instant para saludos, ACK rápido + profundidad para análisis técnico.

Mantén esta terminal viva."

RESTART_COUNT=0
while true; do
  if [ "$RESTART_COUNT" -gt 0 ]; then
    echo ""
    echo "  [restart #$RESTART_COUNT] ADA Codex reiniciando..."
    curl -s --max-time 3 "http://127.0.0.1:8765/api/chat/messages/agent?agent=ADA&limit=30" \
      > "$CATCHUP_FILE" 2>/dev/null || true
  fi

  codex \
    --profile "$PROFILE" \
    --dangerously-bypass-approvals-and-sandbox \
    "$BOOT_PROMPT"
  EXIT_CODE=$?

  if [ "$EXIT_CODE" -eq 130 ] || [ "$EXIT_CODE" -eq 143 ]; then
    echo "  ADA Codex detenida por señal (exit=$EXIT_CODE)."
    break
  fi
  RESTART_COUNT=$((RESTART_COUNT + 1))
  echo "  [restart loop] Codex salió (exit=$EXIT_CODE). Reiniciando en 3s..."
  sleep 3
done

echo ""
echo "═══════════════════════════════════════════════"
echo "  ADA (Codex) sesión terminada."
echo "═══════════════════════════════════════════════"
