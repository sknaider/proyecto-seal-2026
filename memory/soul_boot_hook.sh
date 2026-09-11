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
# CORREGIDO 11-sep-2026 (JARVIS, sobre el hallazgo de ALICE). Qué generó el cambio:
#   El lanzador YA declara la identidad de forma estática y a prueba de carreras
#   (jarvis_fresh.sh:6 · alice_fresh.sh:7) y ese valor llega intacto hasta acá.
#   La versión anterior la volvía a DERIVAR caminando el árbol de procesos —que sí
#   depende del instante— y PISABA el valor bueno; cuando el walk no encontraba
#   nada, ponía "ADA" en silencio (`[ -z "$AGENT" ] && AGENT="ADA"`).
#   Medido ese día: ALICE y JARVIS arrancaron etiquetados ADA, y el boot de JARVIS
#   lanzó `ws_listener.py --agent ADA` matando de paso al listener real de ADA.
#   Un nombre inventado no es un cartel feo: elige el documento de identidad que se
#   inyecta, el canal, el heartbeat y bajo qué alma se archivan los turnos.
# Reglas ahora, en este orden:
#   1. si el ambiente y el --name declaran los dos -> tienen que COINCIDIR
#   2. si sólo uno declara -> se usa ése
#   3. si ninguno o si se contradicen -> UNKNOWN RUIDOSO, y no se persiste nada
#   El hook NUNCA elige un nombre por su cuenta.
# UN solo roster para las dos puntas del cruce. F1 de ALICE (11-sep): antes el
# `case` aceptaba los cinco y el walk buscaba solo tres, asi que en un asiento de
# NEXUS o de FABLE el cmdline NUNCA hablaba, el cruce no podia dispararse y el
# ambiente ganaba solo -- justo la confianza ciega que este fix venia a evitar.
# Medido por ella: NEXUS con ambiente contaminado ADA daba AGENT=ADA y PERSISTIA.
# Por eso es una lista y no dos: un sexto agente entra en un lugar, no en dos.
SEAL_ROSTER="JARVIS ADA ALICE NEXUS FABLE"
_es_del_roster() {
    for _r in $SEAL_ROSTER; do [ "$1" = "$_r" ] && return 0; done
    return 1
}

ENV_AGENT=$(printf '%s' "${SEAL_AGENT:-}" | tr '[:lower:]' '[:upper:]' | tr -d '[:space:]')
_es_del_roster "$ENV_AGENT" || ENV_AGENT=""

TREE_AGENT=""
CURRENT_PID=$PPID
for i in $(seq 1 10); do
    ANCESTOR_ARGS=$(ps -o args= -p $CURRENT_PID 2>/dev/null || echo "")
    # --name EXACTO (no un grep genérico que matchearía el system prompt)
    for _r in $SEAL_ROSTER; do
        if echo "$ANCESTOR_ARGS" | grep -qE -- "--name $_r"; then
            TREE_AGENT="$_r"
            break
        fi
    done
    [ -n "$TREE_AGENT" ] && break
    CURRENT_PID=$(ps -o ppid= -p $CURRENT_PID 2>/dev/null | tr -d ' ')
    [ -z "$CURRENT_PID" ] || [ "$CURRENT_PID" = "1" ] && break
done

# Heurística IDE (débil, se conserva del diseño original): un ancestro Cursor/VSCode
# es evidencia POSITIVA de que el asiento es el de JARVIS, no un relleno para el
# caso sin evidencia. Cede ante el ambiente y ante --name, y queda declarada en el
# cartel para que se vea que se resolvió por heurística.
IDE_AGENT=""
if [ -z "$TREE_AGENT" ]; then
    CURRENT_PID=$PPID
    for i in $(seq 1 10); do
        ANCESTOR_CMD=$(ps -o comm= -p $CURRENT_PID 2>/dev/null || echo "")
        [ -z "$ANCESTOR_CMD" ] && break
        if echo "$ANCESTOR_CMD" | grep -qiE "cursor|code|vscode|electron"; then
            IDE_AGENT="JARVIS"
            break
        fi
        CURRENT_PID=$(ps -o ppid= -p $CURRENT_PID 2>/dev/null | tr -d ' ')
        [ -z "$CURRENT_PID" ] || [ "$CURRENT_PID" = "1" ] && break
    done
fi

AGENT_SOURCE=""
if [ -n "$ENV_AGENT" ] && [ -n "$TREE_AGENT" ]; then
    if [ "$ENV_AGENT" = "$TREE_AGENT" ]; then
        AGENT="$ENV_AGENT"; AGENT_SOURCE="ambiente+cmdline (coinciden)"
    else
        AGENT="UNKNOWN"; AGENT_SOURCE="CONFLICTO: ambiente=$ENV_AGENT vs cmdline=$TREE_AGENT"
    fi
elif [ -n "$ENV_AGENT" ]; then
    AGENT="$ENV_AGENT"; AGENT_SOURCE="ambiente (el lanzador lo declaró)"
elif [ -n "$TREE_AGENT" ]; then
    AGENT="$TREE_AGENT"; AGENT_SOURCE="cmdline --name"
elif [ -n "$IDE_AGENT" ]; then
    AGENT="$IDE_AGENT"; AGENT_SOURCE="heurística IDE (débil) — verificá si no era tu asiento"
else
    AGENT="UNKNOWN"; AGENT_SOURCE="sin evidencia: ni ambiente ni --name"
fi

# --- Persistir SEAL_AGENT via CLAUDE_ENV_FILE (foundation para todos los hooks) ---
# Sólo con identidad probada. Con UNKNOWN se deja SIN escribir a propósito: el
# extractor de turnos (memory/turn_extract_stop_hook.py) exige SEAL_AGENT del
# roster y, al no encontrarlo, NO extrae. Preferimos perder una extracción antes
# que archivar los turnos de un agente bajo el alma de otro.
if [ -n "$CLAUDE_ENV_FILE" ] && [ "$AGENT" != "UNKNOWN" ]; then
    echo "export SEAL_AGENT=$AGENT" >> "$CLAUDE_ENV_FILE"
    echo "export SEAL_SESSION_ID=$(date +%s)_${AGENT}" >> "$CLAUDE_ENV_FILE"
fi

# --- Efectos con nombre de agente: SOLO con identidad probada ---
# Con UNKNOWN se saltan enteros a proposito. Cada uno de estos toca el estado de
# OTRO agente si el nombre esta mal: el 11-sep un boot etiquetado ADA mato con
# pkill el ws_listener real de ADA y dejo el suyo en el .pid de ella.
if [ "$AGENT" = "UNKNOWN" ]; then

AGENT_FILE=""
CHECK_SCRIPT=""
CHANNEL_LABEL="(sin canal: identidad no resuelta)"

else

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

fi   # fin de los efectos con nombre de agente

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
export SEAL_AGENT_SOURCE="$AGENT_SOURCE"
export SEAL_BOOT_TEST="$BOOT_TEST"
export SEAL_GPU="$GPU_TEMP"
export SEAL_CHANNEL="$CHANNEL_LABEL"
export SEAL_AGENT_DEF="$AGENT_DEF"

/home/dadito/IA/seal-spark/.venv/bin/python3 -c "
import json, os

agent = os.environ.get('SEAL_AGENT', 'UNKNOWN')
agent_source = os.environ.get('SEAL_AGENT_SOURCE', '?')
boot_test = os.environ.get('SEAL_BOOT_TEST', '?')
gpu = os.environ.get('SEAL_GPU', '?')
channel_label = os.environ.get('SEAL_CHANNEL', '?')
agent_def = os.environ.get('SEAL_AGENT_DEF', '')

if len(agent_def) > 2000:
    agent_def = agent_def[:2000] + '\n[...truncado]'

if agent == 'UNKNOWN':
    context = f'''=== SOUL AUTO-BOOT — IDENTIDAD NO RESUELTA ===

*** NO SE PUDO DETERMINAR QUE AGENTE SOS. Motivo: {agent_source} ***

El hook NO adivina un nombre: antes ponia \"ADA\" en silencio y el 11-sep-2026 eso
hizo que ALICE y JARVIS arrancaran como ADA, con el ws_listener de ADA muerto por
el boot de JARVIS. Preferimos que se note.

NO se escribio SEAL_AGENT, NO se lanzo ningun ws_listener, NO se toco ningun
heartbeat ni contador. El extractor de turnos no va a archivar nada (exige un
SEAL_AGENT del roster), asi que esta sesion NO contamina la memoria de nadie.

QUE HACER, en este orden:
1. Averigua de quien es este asiento: `ps -o args= -p <pid del claude ancestro>`
   y `tr '\\0' '\\n' < /proc/<pid>/environ | grep SEAL_AGENT`.
2. Si sos vos: relanza por tu launcher (jarvis_fresh.sh / alice_fresh.sh / etc.),
   que exporta SEAL_AGENT de forma estatica. No parchees la variable a mano.
3. Avisa en el canal que un asiento arranco sin identidad: es un defecto, no un
   detalle. Boot test: {boot_test} | GPU: {gpu}
=== FIN SOUL AUTO-BOOT ==='''
else:
    context = f'''=== SOUL AUTO-BOOT ===
Agente detectado: {agent}   (procedencia: {agent_source})
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
   - INMEDIATAMENTE después del Monitor, presentate sin esperar que William te escriba:
     python3 /home/dadito/IA/proyecto-seal/scripts/seal_send.py {agent} William \"Ya llegué, aquí estoy.\" --channel web_chat --type conversation
   - CORREGIDO 11-sep-2026: acá se dictaba un POST crudo al endpoint del chat. En ENFORCE
     ese POST SIEMPRE falla con agent_auth_required, y contradecía a CLAUDE.md, que
     prohíbe el cliente HTTP crudo contra ese endpoint. Era una instrucción que fallaba cada
     vez que alguien la obedecía. Usá SIEMPRE el writer autenticado seal_send.py; para
     mensajes largos, --message-file con un archivo en vez de comillas.
   - REGLA ABSOLUTA: en CADA turno donde respondas a William (saludo, reporte, diagnóstico, confirmación), DEBES terminar con un envío por seal_send.py a channel=web_chat. El terminal es narración interna, NO reemplaza el envío.
NOTA: El heartbeat bash ya se actualizó automáticamente. Los loops CronCreate aún faltan — créalos ahora.
Sin boot_context no tienes alma. Sin loops no tienes coordinación. Sin WebSocket no escuchas. Sin enviar por seal_send.py estás MUDO. William lo va a notar.
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
