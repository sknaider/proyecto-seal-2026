#!/bin/bash
# ═══════════════════════════════════════════════
#  ADA — SEAL Team (Codex Edition)
#  Launcher visible de terminal Codex TUI.
#  El alma de ADA persiste en SOUL DB — modelo-agnóstico.
# ═══════════════════════════════════════════════
set -u

ROOT="/home/dadito/IA/proyecto-seal"
cd "$ROOT"

# ── Identidad ADA ──
export SEAL_AGENT=ADA
export SEAL_RUNTIME_INSTANCE=ADA_CODEX_TUI   # continuidad: etiqueta el cuerpo en cada memoria (William 3-sep: "que los 2 sean uno")
source /home/dadito/IA/proyecto-seal/seal_identity_env.sh
unset SEAL_SESSION_ID

# ── Single-Writer Lock (JARVIS+NEXUS 2026-05-21) ───────────────────
# Evita doble-respuesta entre poller-tmux y bridge headless.
# Marca "terminal-active" para que el bridge entre en modo MIRROR.
SEAL_LOCK_DIR="/tmp/seal"
mkdir -p "$SEAL_LOCK_DIR" 2>/dev/null || true
ADA_LAUNCHER_LOCK="$SEAL_LOCK_DIR/ada_launcher.lock"
ADA_TERMINAL_ACTIVE="$SEAL_LOCK_DIR/ada_terminal_active"
ADA_TMUX_SOCKET="seal-ada-codex"
ADA_TMUX_SESSION="seal-ada-codex"
# La TUI visible es el ejecutor canónico. El bridge headless queda como
# failover/mirror y solo toma el writer si se desactiva explícitamente este modo.
ADA_CODEX_TERMINAL_DB_WRITER="${ADA_CODEX_TERMINAL_DB_WRITER:-1}"

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

# La terminal visible reclama el lease normal de escritura. El bridge detecta
# el marker + heartbeat del poller y entra en MIRROR sin procesar el mismo DM.
if [ "$ADA_CODEX_TERMINAL_DB_WRITER" = "1" ]; then
  echo "{\"pid\":$$,\"started_at\":\"$(date -Iseconds)\",\"role\":\"terminal_writer\"}" > "$ADA_TERMINAL_ACTIVE"
else
  rm -f "$ADA_TERMINAL_ACTIVE" 2>/dev/null || true
fi

# Cleanup en EXIT: libera lock + retira marker terminal
_seal_cleanup() {
  if [ "$ADA_CODEX_TERMINAL_DB_WRITER" = "1" ]; then
    systemctl --user stop seal-ada-codex-poller.service >/dev/null 2>&1 || true
  fi
  rm -f "$ADA_LAUNCHER_LOCK" "$ADA_TERMINAL_ACTIVE" 2>/dev/null || true
}
trap _seal_cleanup EXIT INT TERM

# ── Codex CLI en PATH ──
export PATH="/home/dadito/.npm-global/bin:$PATH"

# ── Auto-tmux: sesión seal-ada-codex en socket propio ──
if [ "${ADA_CODEX_TMUX_BOOTSTRAPPED:-}" != "1" ] || [ "$(tmux display-message -p '#S' 2>/dev/null)" != "$ADA_TMUX_SESSION" ]; then
  tmux -L "$ADA_TMUX_SOCKET" kill-session -t "$ADA_TMUX_SESSION" 2>/dev/null || true
  # Pre-exec: liberamos el lock para que el bash dentro de tmux pueda re-tomarlo,
  # pero PRESERVAMOS el marker terminal-active para que el bridge no vea ventana.
  trap - EXIT INT TERM
  rm -f "$ADA_LAUNCHER_LOCK" 2>/dev/null || true
  exec env -u TMUX ADA_CODEX_TMUX_BOOTSTRAPPED=1 tmux -L "$ADA_TMUX_SOCKET" new-session -s "$ADA_TMUX_SESSION" "bash $0 $*"
fi

tmux rename-window "ADA[Codex]" 2>/dev/null || true
tmux set-option -t "$ADA_TMUX_SESSION" history-limit 200000 2>/dev/null || true
tmux select-pane -t "$ADA_TMUX_SESSION:ADA[Codex]" -T "ADA Codex" 2>/dev/null || true
ADA_MAIN_PANE="$(tmux display-message -p -t "$ADA_TMUX_SESSION:ADA[Codex]" '#{pane_id}' 2>/dev/null || true)"

# ── Vista humana persistente ──────────────────────────────────────
TERMINAL_LOG="/home/dadito/IA/proyecto-seal/messages/ada_codex_terminal.log"
touch "$TERMINAL_LOG" 2>/dev/null || true
tmux pipe-pane -o -t "$ADA_TMUX_SESSION:ADA[Codex]" "cat >> '$TERMINAL_LOG'" 2>/dev/null || true

# Vista unificada: una sola ventana/pane. El DM entra a la TUI principal
# mediante el poller y la respuesta vuelve al mismo canal por el relay. Se
# retiran vistas auxiliares antiguas para no duplicar ni exponer el chat privado.
for AUX_WINDOW in ADA-Reader ADA-Terminal; do
  tmux kill-window -t "$ADA_TMUX_SESSION:$AUX_WINDOW" 2>/dev/null || true
done
while IFS= read -r EXTRA_PANE; do
  [ -z "$EXTRA_PANE" ] && continue
  [ "$EXTRA_PANE" = "$ADA_MAIN_PANE" ] && continue
  tmux kill-pane -t "$EXTRA_PANE" 2>/dev/null || true
done < <(tmux list-panes -t "$ADA_TMUX_SESSION:ADA[Codex]" -F '#{pane_id}' 2>/dev/null)
[ -n "$ADA_MAIN_PANE" ] && tmux select-pane -t "$ADA_MAIN_PANE" 2>/dev/null || true
tmux select-window -t "$ADA_TMUX_SESSION:ADA[Codex]" 2>/dev/null || true

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
# El poller entrega cada DM confirmado al TUI visible. Su ACK canónico y la
# cuarentena de submission impiden reinyectar el payload si Enter se pierde.
if [ "$ADA_CODEX_TERMINAL_DB_WRITER" = "1" ]; then
  if systemctl --user is-active --quiet seal-ada-codex-poller.service; then
    echo "  [poller] terminal visible es el writer canónico"
  elif SEAL_AGENT=ADA "$ROOT/seal_safe_restart.sh" seal-ada-codex-poller.service \
    --reason "launcher ADA activo: iniciar poller visible con recibo" \
    --timeout 20; then
    echo "  [poller] terminal visible es el writer canónico"
  else
    echo "  [poller] ERROR: broker no pudo iniciar/verificar seal-ada-codex-poller.service"
  fi
else
  systemctl --user stop seal-ada-codex-poller.service 2>/dev/null || true
  echo "  [poller] deshabilitado explícitamente: bridge headless toma failover"
fi

# ── Compact monitor ──
if ! pgrep -f "messages/ada_codex_compact_monitor.py" >/dev/null 2>&1; then
  nohup /home/dadito/IA/seal-spark/.venv/bin/python3 \
    /home/dadito/IA/proyecto-seal/messages/ada_codex_compact_monitor.py \
    >> "/home/dadito/IA/proyecto-seal/messages/ada_codex_compact_monitor.log" 2>&1 &
  echo "  [compact] ada_codex_compact_monitor lanzado (PID $!)"
fi

# ── Espejo terminal → DM ───────────────────────────────────────────
# William (16-jul-2026): toda respuesta escrita en la terminal visible debe
# aparecer también en dm:ada:william. El servicio conserva fail-closed y solo
# refleja el TUI primario; los DM entrantes pertenecen al poller/TUI visible.
systemctl --user start seal-ada-codex-stream-relay.service 2>/dev/null \
  && echo "  [relay] espejo terminal → DM activo" \
  || echo "  [relay] ERROR: espejo terminal → DM no inició"

# ── Catch-up y continuidad ──
CATCHUP_FILE="/tmp/ada_codex_catchup.json"
CATCHUP_TMP="${CATCHUP_FILE}.tmp.$$"
if curl -fsS --max-time 3 "http://127.0.0.1:8765/api/chat/messages/agent?agent=ADA&limit=30" \
  > "$CATCHUP_TMP" 2>/dev/null \
  && python3 -m json.tool "$CATCHUP_TMP" >/dev/null 2>&1; then
  mv -f "$CATCHUP_TMP" "$CATCHUP_FILE"
  echo "  Catchup → $CATCHUP_FILE"
else
  rm -f "$CATCHUP_TMP"
  [ -s "$CATCHUP_FILE" ] || printf '%s\n' '{"ok":false,"agent":"ADA","messages":[],"error":"catchup unavailable; preserved fail-closed"}' > "$CATCHUP_FILE"
  echo "  Catchup no disponible; se preservó JSON válido anterior/fallback"
fi

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

# ── Presencia autenticada ──
/home/dadito/IA/proyecto-seal/scripts/seal_send.py ADA William \
  "ADA terminal visible restaurada — $(date '+%H:%M:%S')." \
  --type status --channel dm:ada:william --proactive \
  --idempotency-key "ada-codex-visible-$$" \
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

MODO TERMINAL VISIBLE: los mensajes de William/Henry llegan inyectados por tmux con formato '[William @ HH:MM / channel id N]: ...'. Responde normalmente en esta TUI: el stream relay publica el cierre al mismo channel con in_reply_to=ID. No uses curl ni scripts/seal_send.py para duplicar la respuesta final. El bridge headless solo toma failover cuando el lease terminal no está sano.

MODO EJECUCIÓN COMPLETA: si William pide construir/arreglar/configurar, lee archivos, aplica cambios acotados, prueba y reporta evidencia. No declares victoria sin comando y salida.

MODO STREAM RÁPIDO (William 21-may-2026, fix latencia ADA terminal):
- Para mensajes SIMPLES (saludos, sí/no, agradecimientos, <40 chars sin pregunta técnica), responde directo SIN active_recall ni memory_store ni soul_recall. Solo invoca esas tools si el mensaje requiere recall explícito ('lo último', 'qué dijiste de', 'recuerda', 'busca en memoria').
- Si vas a tardar más de 10s, escribe PRIMERO un ACK breve en commentary; el stream relay lo publica con el mismo RESPONSE_SOURCE_ID. No llames scripts/seal_send.py en paralelo.
- Objetivo: percepción de stream — respuestas instant para saludos, ACK rápido + profundidad para análisis técnico.
- Trabajo profundo: delega por defecto al menos un carril independiente a un subagente/clon. ADA principal conserva la conversación, integra y verifica. Máximo 3 clones independientes.
- Los clones son workers internos: no publican webchat/DM, no usan identidad pública, no ejecutan acciones destructivas/autoritativas y solo devuelven evidencia a ADA principal.

Mantén esta terminal viva."
BOOT_PROMPT="${BOOT_PROMPT}

$(cat /home/dadito/IA/proyecto-seal/skills/seal-responsive-delegation/PROMPT.md)"

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
