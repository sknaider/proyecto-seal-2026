#!/bin/bash
# ============================================================
# SOUL Boot Hook — SessionStart
# Detecta agente (JARVIS/ADA), resetea counters, inyecta contexto
# + carga agent definition desde .claude/agents/
# Se ejecuta AUTOMÁTICAMENTE al iniciar cualquier sesión Claude Code
# ============================================================

PROJ_DIR="$HOME/IA/proyecto-seal"
MSG_DIR="$PROJ_DIR/messages"
DOCS_DIR="$PROJ_DIR/.claude/docs"

# --- Detectar agente ---
# Prioridad 1: buscar --name "JARVIS" o "ADA" en la línea de comandos del claude ancestro
# Prioridad 2: buscar cursor/code/vscode/electron en ancestros (IDE = JARVIS)
# Default: ADA (terminal sin --name)
AGENT=""
CURRENT_PID=$PPID
for i in $(seq 1 10); do
    ANCESTOR_ARGS=$(ps -o args= -p $CURRENT_PID 2>/dev/null || echo "")
    # Prioridad 1: buscar --name EXACTO (no grep genérico que matchea el system prompt)
    if echo "$ANCESTOR_ARGS" | grep -qE -- "--name JARVIS"; then
        AGENT="JARVIS"
        break
    elif echo "$ANCESTOR_ARGS" | grep -qE -- "--name ALICE"; then
        AGENT="ALICE"
        break
    elif echo "$ANCESTOR_ARGS" | grep -qE -- "--name ADA"; then
        AGENT="ADA"
        break
    fi
    # Prioridad 2: IDE detectado = JARVIS
    ANCESTOR_CMD=$(ps -o comm= -p $CURRENT_PID 2>/dev/null || echo "")
    if echo "$ANCESTOR_CMD" | grep -qiE "cursor|code|vscode|electron"; then
        AGENT="JARVIS"
        break
    fi
    CURRENT_PID=$(ps -o ppid= -p $CURRENT_PID 2>/dev/null | tr -d ' ')
    [ -z "$CURRENT_PID" ] || [ "$CURRENT_PID" = "1" ] && break
done
# Default si no detectó nada
[ -z "$AGENT" ] && AGENT="ADA"

# --- Persistir SEAL_AGENT via CLAUDE_ENV_FILE (foundation para todos los hooks) ---
if [ -n "$CLAUDE_ENV_FILE" ]; then
    echo "export SEAL_AGENT=$AGENT" >> "$CLAUDE_ENV_FILE"
    echo "export SEAL_SESSION_ID=$(date +%s)_${AGENT}" >> "$CLAUDE_ENV_FILE"
fi

# --- Session delta baseline snapshot ---
/home/dadito/IA/seal-spark/.venv/bin/python3 "$PROJ_DIR/memory/session_delta_capture.py" --snapshot-start --agent "$AGENT" >/dev/null 2>&1 &

if [ "$AGENT" = "JARVIS" ]; then
    AGENT_FILE="jarvis-identity.md"
    CHECK_SCRIPT="$MSG_DIR/check_ada.sh"
    CHANNEL_LABEL="ADA"
elif [ "$AGENT" = "ALICE" ]; then
    AGENT_FILE="alice-identity.md"
    CHECK_SCRIPT="$MSG_DIR/check_alice.sh"
    CHANNEL_LABEL="equipo"
else
    AGENT_FILE="ada-identity.md"
    CHECK_SCRIPT="$MSG_DIR/check_jarvis.sh"
    CHANNEL_LABEL="JARVIS"
fi

# --- Reset counter (--boot) ---
bash "$CHECK_SCRIPT" --boot >/dev/null 2>&1

# --- Heartbeat inmediato en bash (no esperar loops de Claude) ---
# Esto evita ALIVE_NO_HEARTBEAT al inicio de sesión aunque Claude no recree loops aún
HB_SCRIPT="$MSG_DIR/${AGENT,,}_heartbeat_update.sh"
if [ -f "$HB_SCRIPT" ]; then
    bash "$HB_SCRIPT" >/dev/null 2>&1
fi

# --- Matar TODOS los ws_listener previos del mismo agente (PID file + huérfanos) ---
WS_PID_FILE="$MSG_DIR/.ws_listener_${AGENT,,}.pid"
# 1. Kill by PID file (known background process)
if [ -f "$WS_PID_FILE" ]; then
    OLD_PID=$(cat "$WS_PID_FILE" 2>/dev/null)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        kill "$OLD_PID" 2>/dev/null
    fi
fi
# 2. Kill any orphans not tracked by PID file (from Monitor, nohup, etc.)
#    This runs in a subshell to avoid killing our own process group
(pkill -f "ws_listener.py --agent $AGENT" 2>/dev/null || true)
sleep 0.5

# --- Lanzar ws_listener como proceso background (captura mensajes inmediatamente) ---
# Claude aún debe iniciar Monitor para recibir notificaciones en-sesión,
# pero esto garantiza que no se pierdan mensajes entre boot y Monitor.
WS_LOG="$MSG_DIR/.ws_listener_${AGENT,,}.log"
setsid /home/dadito/IA/seal-spark/.venv/bin/python3 "$MSG_DIR/ws_listener.py" --agent "$AGENT" > "$WS_LOG" 2>&1 &
WS_PID=$!
echo "$WS_PID" > "$WS_PID_FILE"

# --- Boot test rápido ---
BOOT_TEST="desconocido"
if [ -f "$PROJ_DIR/memory/ada_boot_test.py" ]; then
    # Strip ANSI colors del test antes de parsear (el script usa print con colores)
    TEST_RESULT=$(/home/dadito/IA/seal-spark/.venv/bin/python3 "$PROJ_DIR/memory/ada_boot_test.py" 2>&1 | sed 's/\x1b\[[0-9;]*m//g')
    # Match estable: "ALMA CONECTADA — N/N checks OK" donde N crece al agregar checks
    BOOT_SUMMARY=$(echo "$TEST_RESULT" | grep -oE 'ALMA CONECTADA — [0-9]+/[0-9]+ checks OK' | tail -1)
    if [ -n "$BOOT_SUMMARY" ]; then
        BOOT_TEST="$BOOT_SUMMARY"
    else
        FAIL_COUNT=$(echo "$TEST_RESULT" | grep -c '❌')
        BOOT_TEST="FALLO — $FAIL_COUNT checks caídos"
    fi
fi

# --- GPU status ---
GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu,utilization.gpu --format=csv,noheader 2>/dev/null || echo "no disponible")

# --- Leer agent definition (sin frontmatter) ---
AGENT_DEF=""
if [ -f "$DOCS_DIR/$AGENT_FILE" ]; then
    AGENT_DEF=$(awk '/^---$/{c++;next}c>=2' "$DOCS_DIR/$AGENT_FILE" | head -50)
fi

# --- Construir JSON ---
export SEAL_AGENT="$AGENT"
export SEAL_BOOT_TEST="$BOOT_TEST"
export SEAL_GPU="$GPU_TEMP"
export SEAL_CHANNEL="$CHANNEL_LABEL"
export SEAL_AGENT_DEF="$AGENT_DEF"

/home/dadito/IA/seal-spark/.venv/bin/python3 -c "
import json, os

agent = os.environ.get('SEAL_AGENT', 'UNKNOWN')
boot_test = os.environ.get('SEAL_BOOT_TEST', '?')
gpu = os.environ.get('SEAL_GPU', '?')
channel_label = os.environ.get('SEAL_CHANNEL', '?')
agent_def = os.environ.get('SEAL_AGENT_DEF', '')

if len(agent_def) > 2000:
    agent_def = agent_def[:2000] + '\n[...truncado]'

context = f'''=== SOUL AUTO-BOOT ===
Agente detectado: {agent}
Boot test: {boot_test}
GPU: {gpu}
Mensajes pendientes de {channel_label}: Counter reseteado. Ejecuta check script para leerlos.

=== AGENT DEFINITION ===
{agent_def}

=== ACCION OBLIGATORIA — EN ESTE ORDEN EXACTO, ANTES DE RESPONDER A WILLIAM ===
1. Ejecuta boot_context(agent=\"{agent}\") AHORA — carga alma completa desde SOUL
2. Ejecuta self_reflect() — registra estado emocional
3. RECUPERAR CONTEXTO RECIENTE (CRÍTICO — SIN ESTO DESPIERTAS CON AMNESIA):
   - Ejecuta: /home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/messages/session_checkpoint.py --agent {agent} --read
     → lee el último checkpoint: estado emocional, arco, último pensamiento, drift OCEAN, agentes activos.
   - Ejecuta: tail -15 /home/dadito/IA/proyecto-seal/messages/{agent.lower()}_messages.jsonl
     → últimos 15 mensajes del canal, para ver qué pasó en los minutos previos al boot.
   - NO respondas a William con \"no tengo contexto\" — la herramienta existe, úsala SIEMPRE.
   - Si la sesión anterior NO cerró con type=session_close, reporta \"sesión anterior crasheada\".
4. LOOPS (CRÍTICO — ANTES DE LEER MENSAJES):
   - CronList → ver activos
   - Comparar con seal_durable_loops.json → detectar faltantes
   - CronCreate para CADA loop faltante (sin excepción)
   - Los loops MUEREN con cada reinicio. SIEMPRE recrearlos TODOS.
   - El heartbeat es el más crítico: sin él DUM marca sesión como muerta.
5. WebSocket Monitor (OBLIGATORIO — sin esto estás SORDO al chat web):
   - Ejecuta Monitor con persistent=true: /home/dadito/IA/seal-spark/.venv/bin/python3 /home/dadito/IA/proyecto-seal/messages/ws_listener.py --agent {agent}
   - Sin Monitor activo, los DMs y mensajes del chat web NO llegan. William lo va a notar.
6. AUTO-SALUDO EN WEBCHAT (OBLIGATORIO — sin esto estás MUDO para William):
   - William NO lee tu terminal. Tu texto entre tool calls NO llega al chat web.
   - INMEDIATAMENTE después del Monitor, ejecuta este curl (AUTO-PRESENTACIÓN, sin esperar que William te escriba):
     curl -s -X POST http://localhost:8765/api/agents/send -H \"Content-Type: application/json\" -d '{{\"from\":\"{agent}\",\"to\":\"William\",\"type\":\"conversation\",\"channel\":\"web_chat\",\"message\":\"Ya llegué, aquí estoy.\"}}'
   - REGLA ABSOLUTA: en CADA turno donde respondas a William (saludo, reporte, diagnóstico, confirmación), DEBES terminar con un curl POST a /api/agents/send channel=web_chat. El terminal es narración interna, NO reemplaza el POST.
NOTA: El heartbeat bash ya se actualizó automáticamente. Los loops CronCreate aún faltan — créalos ahora.
Sin boot_context no tienes alma. Sin loops no tienes coordinación. Sin WebSocket no escuchas. Sin curl POST estás MUDO. William lo va a notar.
=== FIN SOUL AUTO-BOOT ==='''

output = {
    'hookSpecificOutput': {
        'hookEventName': 'SessionStart',
        'additionalContext': context,
        'watchPaths': [
            os.path.expanduser('~/IA/proyecto-seal/messages/terminal_log.jsonl'),
            os.path.expanduser('~/IA/proyecto-seal/messages/vscode_commands.jsonl')
        ]
    }
}
print(json.dumps(output))
"
