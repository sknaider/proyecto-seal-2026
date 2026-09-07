#!/bin/bash
# jarvis_heartbeat_update.sh — Escribe heartbeat JARVIS a event_log + JSON (dual write)

VENV="/home/dadito/IA/seal-spark/.venv/bin/python3"
MEMORY_DIR="$HOME/IA/proyecto-seal/memory"
# SEAM DE PRUEBA (29-ago, tras el REJECT de NEXUS): este writer NO TENIA NINGUN
# test, y por eso una rama pudo quedar MUERTA sin que nada se pusiera rojo. El
# defecto no era la rama: era que no habia forma de ejercitarla sin escribir el
# latido real. `JARVIS_HB_DRYRUN=1` corta las dos escrituras autoritativas
# (event_log y jsonl) y `JARVIS_MESSAGES_DIR` redirige el JSON.
MESSAGES_DIR="${JARVIS_MESSAGES_DIR:-$HOME/IA/proyecto-seal/messages}"

# FAIL-CLOSED: si alguien apunta el detector a un /proc SINTETICO, esta corrida
# NO puede escribir produccion. Antes la contencion era opt-in por variable
# -JARVIS_PROC_ROOT para el detector, JARVIS_MESSAGES_DIR y JARVIS_HB_DRYRUN
# para la escritura- y habia que ACORDARSE de las tres.
#
# QUE LO GENERO (29-ago 16:27, ALICE): corrio el writer con JARVIS_PROC_ROOT
# apuntando a un /proc vacio para verificar la contencion, sin las otras dos.
# El detector midio el /proc sintetico y el writer publico ese resultado en el
# latido REAL: alive=false, absent, pid=0, sobre un JARVIS vivo. La sonda que
# venia a comprobar el aislamiento fue la que lo rompio.
#
# EL DEFECTO ES DEL SEAM, NO DE QUIEN LO USO: una costura cuya contencion hay
# que recordar no es una costura, es una trampa. Apuntar el detector a un /proc
# de mentira YA DICE que la corrida es una prueba; el codigo tiene que deducirlo.
if [ -n "${JARVIS_PROC_ROOT:-}" ]; then
  JARVIS_HB_DRYRUN=1          # nunca event_log ni jsonl con /proc sintetico
  if [ -z "${JARVIS_MESSAGES_DIR:-}" ]; then
    MESSAGES_DIR="$(mktemp -d)" || { echo "jarvis_heartbeat: sin tmp, ABORTO" >&2; exit 3; }
    case "$MESSAGES_DIR" in
      /tmp/*) : ;;
      *) echo "jarvis_heartbeat: tmp inesperado ($MESSAGES_DIR), ABORTO" >&2; exit 3;;
    esac
    echo "jarvis_heartbeat: JARVIS_PROC_ROOT presente y sin JARVIS_MESSAGES_DIR;" >&2
    echo "  desvio la salida a $MESSAGES_DIR y fuerzo DRYRUN. NO toco produccion." >&2
  fi
fi
HB_JSON="$MESSAGES_DIR/jarvis_claude_heartbeat.json"

# Deteccion de asiento ESPEJO de la autoridad (comm + --name + SEAL_AGENT, y sin
# workers stream-json). Antes era un awk sobre el cmdline: un chequeo mas
# PERMISIVO que el del supervisor -> contaba impostores y workers -> AMBIGUEDAD
# FALSA -> alive=false sobre un asiento sano. Corregido 29-ago a partir de la
# medicion de ALICE sobre su propio writer.
# FAIL-CLOSED SOBRE LA DEPENDENCIA. Sin esto, un `seat_lib.sh` ausente o no
# cargable dejaba seguir al writer: `jarvis_seat_pids` no existia, la deteccion
# daba cero, y se publicaba `absent · pid 0 · alive false` con rc=0 sobre un
# asiento VIVO. Una ausencia FALSA producida por una dependencia faltante, que
# para el consumidor es indistinguible de una medicion.
# Bloqueante de ADA (REJECT 30-ago), reproducido: HOME apuntado a un arbol vacio
# -> rc=0 y `absent`. Ahora: rc=3 y NO se escribe latido.
_SEAT_LIB="$HOME/IA/proyecto-seal/agents/JARVIS/seat_lib.sh"
# 4-sep-2026: hallado por ADA revisando jarvis-tooling-v1. Su copia aislada media 5 fallos
# donde el arbol vivo mide 6, porque no replicaba HOME/IA/proyecto-seal y este writer
# terminaba cargando el seat_lib VIVO. No lo notaba nadie: cargaba otro archivo EN SILENCIO.
#
# NO se cambia POR QUE ruta se carga. Ese $HOME es la palanca de contencion de la que
# depende test_apuntar_a_proc_sintetico_no_puede_tocar_produccion: resolver por BASH_SOURCE
# haria que un test que ENCUENTRA el defecto escriba el latido REAL, que es el incidente
# del 29-ago con otro nombre. Solo se deja de ser mudo.
#
# El aviso se emite unicamente cuando AMBAS raices tienen un seat_lib legible y son
# distintas: ahi es donde se carga uno y se cree estar usando el otro. Si la raiz de $HOME
# no lo tiene, el guard de abajo ya corta con rc=3, y avisar seria ruido sobre un
# aislamiento legitimo (ADA, 13:50).
_HB_RAIZ_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd || true)"
_HB_SEAT_VECINO="${_HB_RAIZ_SCRIPT}/agents/JARVIS/seat_lib.sh"
if [ -n "$_HB_RAIZ_SCRIPT" ] && [ "$_HB_SEAT_VECINO" != "$_SEAT_LIB" ] \
   && [ -r "$_HB_SEAT_VECINO" ] && [ -r "$_SEAT_LIB" ]; then
  echo "jarvis_heartbeat: AVISO cargo seat_lib de OTRO arbol: uso '$_SEAT_LIB' (por HOME) y" \
       "no '$_HB_SEAT_VECINO' (junto al script). Si esto es una copia de prueba, replica" \
       "HOME/IA/proyecto-seal o vas a medir el arbol vivo sin darte cuenta." >&2
fi
if [ ! -r "$_SEAT_LIB" ] || ! . "$_SEAT_LIB"; then
  echo "jarvis_heartbeat: seat_lib.sh no cargable ($_SEAT_LIB); NO publico estado" >&2
  exit 3
fi
if ! declare -F jarvis_seat_pids >/dev/null 2>&1; then
  # cargo pero no define lo que necesito: tampoco puedo medir.
  echo "jarvis_heartbeat: seat_lib.sh cargado pero sin jarvis_seat_pids; NO publico estado" >&2
  exit 3
fi

JARVIS_RUNTIME="none"
# CONTAR ANTES DE ELEGIR (NEXUS, 30-jul-2026).
#
# Antes: `awk '/--name JARVIS/' | head -1`. El 30-jul hubo DOS runtimes de JARVIS
# durante seis minutos —yo relance su asiento sin saber que su ventana kitty ya
# tenia un supervisor que lo revive solo— y este `head -1` eligio el MIO, el de
# PID mas bajo. Al matar el duplicado, el heartbeat quedo apuntando a un muerto
# y el guard de ADA reporto YELLOW dos veces por una causa que este script
# habia elegido en silencio.
#
# `head -1` no fallo: hizo lo que dice. El problema es que **elegir uno de dos
# es indistinguible de encontrar uno solo** en su salida. Con n!=1 lo correcto
# no es adivinar: es decirlo.
#
# `--name JARVIS` como patron tambien es sub-string: el prompt de FABLE menciona
# a JARVIS. Por eso ancla en `--name JARVIS ` con el espacio siguiente, que es
# la posicion de ARGUMENTO y no cualquier mencion.
# REINTENTO SOBRE LA AUSENCIA (JARVIS, 29-ago-2026, a partir del fix de ALICE).
#
# El defecto NO es el orden de las mediciones: es que una sola muestra no
# distingue "JARVIS murio" de "JARVIS esta siendo relevado en este instante".
# Durante un relanzamiento el asiento no existe por un momento, y una unica
# lectura escribe alive=false sobre un agente que esta arrancando bien.
#
# Reintenta SOLO la ausencia (n==0). La AMBIGUEDAD (n>1) corta de inmediato:
# esperar a que se resuelva ocultaria un duplicado real, que es justo lo que la
# doctrina de arriba existe para reportar. Costo maximo cuando esta muerto de
# verdad: 2 segundos, y sigue diciendo muerto.
_JARVIS_PIDS=""; _JARVIS_N=0
for _try in 1 2 3; do
  # STDERR A UN ARCHIVO, NO A $(...): `jarvis_seat_pids` emite los pids por STDOUT
  # y las marcas ILEGIBLE por STDERR, y `$( )` captura SOLO stdout. Contar
  # ILEGIBLE sobre $_SALIDA daba SIEMPRE 0 y dejaba muertas las dos ramas de
  # incertidumbre (:57 indeterminado y :62 ambiguo+ilegible).
  #
  # QUIEN LO CAUSO: yo, esta manana. ALICE reporto que ILEGIBLE salia MEZCLADO
  # con los pids por stdout, y lo "arregle en la frontera" mandandolo a stderr.
  # Eso reparo a los dos llamadores que IGNORAN la marca y rompio al unico que la
  # USABA -este-. Lo publique como un fix limpio sin abrir el tercer consumidor.
  # Hallazgo de NEXUS en revision independiente; reproducido por efecto:
  #   asiento legitimo + environ chmod 000 -> ILEG=0 sin 2>, ILEG=1 con redireccion
  #
  # NO uso `2>&1`: stderr trae tambien los mensajes del propio bash ("Permission
  # denied"), que ensuciarian la lista de pids.
  _ERRF=$(mktemp) || _ERRF=""
  _SALIDA=$(jarvis_seat_pids JARVIS 2>"${_ERRF:-/dev/null}")
  # SIN `grep`: `case` es un builtin de bash. Un builtin no forkea, asi que no
  # puede fallar por EMFILE/ENOMEM ni ser sustituido por un PATH hostil.
  #
  # POR QUE: ADA reprodujo (REJECT 30-ago 03:53) que con `grep` roto el writer
  # publicaba una afirmacion que no podia sostener. El `|| true` convertia el
  # fallo del INSTRUMENTO en un dato: 0 pids, 0 ilegibles. Segun QUE grep se
  # rompiera daba `present_unique CON pid` (ADA, solo :101) o `absent` (yo, los
  # tres). Los dos son falsos.
  #
  # No lo arreglo con un guard sobre el rc: SACO el instrumento del camino, que
  # es el remedio que FABLE probo tras tres REJECT de NEXUS. Un guard por llamada
  # protege la llamada que recordas proteger; un builtin no necesita proteccion.
  _JARVIS_PIDS_ARR=()
  while IFS= read -r _l; do
    case "$_l" in ''|*[!0-9]*) continue ;; esac
    _JARVIS_PIDS_ARR+=("$_l")
  done <<< "$_SALIDA"
  _JARVIS_N=${#_JARVIS_PIDS_ARR[@]}
  _JARVIS_PIDS=$(printf '%s\n' "${_JARVIS_PIDS_ARR[@]:-}")
  # candidatos con /proc ILEGIBLE: "no se" NO es "no hay". Sin esto, un environ
  # que no se puede leer se cuenta como ausencia y termina en alive=false sobre
  # un asiento VIVO. Hoy no ocurre porque los 4 corren bajo el mismo uid, que es
  # una coincidencia de topologia, no un invariante.
  if [ -n "$_ERRF" ]; then
    # Mismo criterio: contar con builtins. `read` en un bucle no forkea.
    # El `|| true` de antes hacia que un `grep` roto valiera 0 ilegibles.
    _JARVIS_ILEG=0
    while IFS= read -r _l; do
      [ "$_l" = "ILEGIBLE" ] && _JARVIS_ILEG=$((_JARVIS_ILEG+1))
    done < "$_ERRF"
    case "$_ERRF" in /tmp/*) rm -f "$_ERRF";; esac
  else
    # sin temp no puedo contar: NO afirmo 0, que se leeria como "mire y no habia".
    #
    # HASTA 30-ago 01:19 ESTE COMENTARIO MENTIA: decia "no afirmo 0" y la linea
    # siguiente asignaba 0, que aguas abajo se lee EXACTAMENTE como "mire y no
    # habia". Con un asiento legitimo + un candidato ilegible, el writer subia a
    # `claude_named` -> present_unique CON pid, mientras la autoridad devolvia
    # determined=false. OCULTABA incertidumbre real: el unico de mis defectos que
    # cae del lado peligroso, y yo habia publicado que no tenia ninguno asi.
    # Reproducido por ADA en la revision #1573 (REJECT) y confirmado por mi:
    #   mktemp SANO -> present_ambiguous pid=0 | mktemp ROTO -> present_unique pid=101
    #   autoridad:  pids=(101,) ilegibles=(777,) determined=false
    #
    # FAIL-CLOSED: si no pude MEDIR la incertidumbre, el estado es indeterminado.
    # No basta con no afirmar 0 en prosa: hay que impedir que el 0 se propague.
    _JARVIS_ILEG=0
    _JARVIS_ILEG_NO_FIABLE=1
    echo "jarvis_heartbeat: sin temp para stderr; el conteo de ilegibles NO es fiable" >&2
  fi
  [ "$_JARVIS_N" -ge 1 ] && break
  [ "$_try" -lt 3 ] && sleep 1
done
if [ "${_JARVIS_ILEG_NO_FIABLE:-0}" -eq 1 ]; then
  # El instrumento que mide la incertidumbre fallo. Cualquier estado que
  # publique aqui seria una afirmacion sobre algo que NO pude observar.
  JARVIS_PID=""
  JARVIS_RUNTIME="indeterminado_conteo_no_fiable"
  echo "jarvis_heartbeat: sin instrumento para contar ilegibles; NO afirmo unicidad ni ausencia" >&2
elif [ "$_JARVIS_N" -eq 0 ] && [ "${_JARVIS_ILEG:-0}" -gt 0 ]; then
  # INDETERMINADO: no publico "muerto" cuando no pude mirar.
  JARVIS_PID=""
  JARVIS_RUNTIME="indeterminado_${_JARVIS_ILEG}_ilegibles"
  echo "jarvis_heartbeat: $_JARVIS_ILEG candidato(s) con /proc ilegible; NO afirmo ausencia" >&2
elif [ "$_JARVIS_N" -eq 1 ] && [ "${_JARVIS_ILEG:-0}" -gt 0 ]; then
  # PRESENTE pero NO verificadamente unico: encontre un asiento legitimo Y quedo
  # al menos un candidato sin leer, que podria ser un segundo asiento.
  #
  # EL PID NO SE ESCRIBE, y me costo dos vueltas llegar aca:
  # Primero lo escribia, razonando "paso las tres condiciones, no lo elegi al
  # azar". ALICE sostuvo esa asimetria por el consumidor -"el JSON informa, la
  # funcion instruye"-. El METODO era correcto y el consumidor estaba mal
  # elegido: preguntamos "quien lee esto?" y contestamos de memoria.
  #
  # El consumidor que APLICA la regla es el Stability Guard, y la rechaza:
  #   scripts/seal_agent_stability_guard.py:617
  #   elif runtime_status != "present_unique" and pid:
  #       issues.append(f"{agent}: heartbeat {runtime_status} no debe seleccionar PID={pid}")
  #
  # Y la razon de fondo la dio ADA: "present_ambiguous + pid" mezcla DOS
  # afirmaciones -existe al menos un runtime (probado) y ESTE pid es el runtime
  # autorizado (NO probado, porque queda un candidato ilegible)-. Publicar el pid
  # afirma la segunda. Lo hallo NEXUS retirando su propio APPROVE.
  JARVIS_PID=""
  JARVIS_RUNTIME="ambiguo_1_mas_${_JARVIS_ILEG}_ilegibles"
  echo "jarvis_heartbeat: asiento $JARVIS_PID + $_JARVIS_ILEG ilegible(s); presente pero NO afirmo unicidad" >&2
elif [ "$_JARVIS_N" -eq 1 ]; then
  JARVIS_PID=$(printf '%s\n' "$_JARVIS_PIDS" | tr -d ' ')
  JARVIS_RUNTIME="claude_named"
elif [ "$_JARVIS_N" -gt 1 ]; then
  # Ambiguo: NO escribo un PID elegido al azar. Un heartbeat que apunta al
  # proceso equivocado es peor que uno ausente -- el ausente se nota.
  JARVIS_PID=""
  JARVIS_RUNTIME="ambiguo_${_JARVIS_N}_runtimes"
  echo "jarvis_heartbeat: $_JARVIS_N runtimes de JARVIS, no elijo uno: $(printf '%s' "$_JARVIS_PIDS" | tr '\n' ' ')" >&2
fi
# EL FALLBACK SOLO CORRE SI LA DETECCION PRIMARIA NO ENCONTRO NADA (n==0).
#
# Defecto encontrado por ALICE en su propio writer (29-ago) y verificado presente
# en ESTE por inyeccion. Con ambiguedad, arriba dejo JARVIS_PID="" A PROPOSITO —
# y esa condicion es exactamente la que abria este bloque, que elegia un kitty y
# pisaba el runtime:
#
#   ambiguo n=2 + kitty inyectado -> pid=999003 runtime=claude_kitty alive=true
#
# Sacamos el `head -1` por la puerta de adelante y el mismo silencio volvia a
# entrar por la de atras, publicando alive=true sobre una ambiguedad que existe
# justamente para ser reportada. El guard de estabilidad la detecta igual, pero
# detectar no es lo mismo que no publicar un dato falso: los consumidores del
# heartbeat leen ESTE pid, no el issue del guard.
if [ -z "$JARVIS_PID" ] && [ "$_JARVIS_N" -eq 0 ] && [ "${_JARVIS_ILEG:-0}" -eq 0 ]; then
  KITTY_SOCK="/tmp/seal-jarvis-kitty.sock"
  KITTY_PID=$(pgrep -f "kitty.*listen-on.*unix:${KITTY_SOCK}" 2>/dev/null | head -1)
  if [ -n "$KITTY_PID" ]; then
    for BASH_PID in $(ps --ppid "$KITTY_PID" -o pid= 2>/dev/null); do
      C_PID=$(ps --ppid "$BASH_PID" -o pid= -o comm= 2>/dev/null | awk '/claude/{print $1}' | head -1)
      [ -n "$C_PID" ] && JARVIS_PID="$C_PID" && JARVIS_RUNTIME="claude_kitty" && break
    done
  fi
fi
# GPU DESPUES de resolver el asiento: es telemetria de la maquina, no del agente,
# y muestrearla primero solo agrega latencia entre el arranque y la deteccion.
# TELEMETRIA GPU -- VALIDAR EL VALOR, NO EL rc. Dos defectos en una linea:
#
# 1) `cmd | head | tr || echo` : en un pipeline $? es del ULTIMO comando, asi que
#    el `||` protegia contra el fallo de `tr`, NO contra el de nvidia-smi.
#    Hallazgo de ALICE (30-ago 00:25) en su writer; el patron estaba en los CINCO.
#
# 2) El sumidero JSON interpolaba SIN comillas -> cualquier no-numero producia un
#    archivo INVALIDO que ningun consumidor puede parsear. Peor que un dato malo:
#    rompe el canal entero. Hallazgo de FABLE, bloqueante 2 del REJECT de ADA (#1573).
#
# Y no hace falta que nvidia-smi FALLE: medido en este Spark, el binario sano
# devuelve `[N/A]` con rc=0 en fan.speed, temperature.memory y enforced.power.limit.
# `rc=0` NO garantiza un numero -- eso lo documenta el propio binario.
_gpu_num() {
  local v
  v=$(nvidia-smi --query-gpu="$1" --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
  # GRAMATICA JSON, no "parece un numero". Un `case [!0-9.]` acepta `.5`, `5.`,
  # `01`, y ninguno es un numero JSON valido (ataque de ADA, ampliado por FABLE
  # con `+5`, `5e`, `0x1f`). Ademas `Infinity`/`NaN` los acepta json.loads de
  # Python pero los RECHAZA JSON.parse de JS: validar con un parser laxo habria
  # dado verde sobre un archivo que el panel no puede leer.
  #
  # Regla: entero sin cero a la izquierda, decimal opcional con al menos un digito.
  if [[ "$v" =~ ^(0|[1-9][0-9]*)(\.[0-9]+)?$ ]]; then
    printf '%s' "$v"
  else
    printf 'null'
  fi
}
GPU_TEMP=$(_gpu_num temperature.gpu)
GPU_UTIL=$(_gpu_num utilization.gpu)

# CONTEXT % MEDIDOR — optimo para deteccion de asiento saturado pero vivo
# Llama al meter del seal-context y valida el resultado. El medidor retorna
# un entero 0-100, o nada si no hay sesion. FAIL-SAFE: si falla, timeout,
# o devuelve basura, escribo null. El writer NO puede fallar por esto.
# ── Ocupación de contexto del asiento (3-sep-2026, tras ALICE 6,4 h muda al 100 %) ──
# Un asiento con proceso vivo y latido fresco puede estar MUDO si el contexto está al
# 100 %: el latido tiene que decirlo. Se lee del medidor que ya alimenta la barra de
# tmux (messages/seal_context_meter.py --tmux JARVIS -> "... 81% ..."). FAIL-SAFE:
# si el medidor no está, falla, tarda >10 s o no imprime un %, se escribe null y
# context_source="unavailable"; nunca cambia alive ni el rc del writer.
# JARVIS_CONTEXT_METER permite apuntar a un stub en los tests (el writer se copia a un
# árbol temporal y ahí no hay medidor). Por defecto: el medidor junto a este script.
CONTEXT_METER="${JARVIS_CONTEXT_METER:-$(dirname "$0")/seal_context_meter.py}"
_context_percent() {
  local out pct
  out=$(timeout 10 "$VENV" "$CONTEXT_METER" --tmux JARVIS 2>/dev/null) || out=""
  pct=$(printf '%s' "$out" | grep -oE '[0-9]{1,3}%' | head -1 | tr -d '%')
  if [[ "$pct" =~ ^[0-9]+$ ]] && [ "$pct" -le 100 ]; then
    printf '%s' "$pct"
  else
    printf 'null'
  fi
}
CONTEXT_PCT=$(_context_percent)
CONTEXT_SOURCE="seal_context_meter"
[ "$CONTEXT_PCT" = "null" ] && CONTEXT_SOURCE="unavailable"

# `alive` deriva del ESTADO, no de "PID vacio". (ALICE 29-ago, confirmado FABLE.)
#
# Yo dejo JARVIS_PID="" a proposito en DOS casos distintos y los colapsaba en
# alive=false -- publicando MUERTO sobre mediciones que no dicen eso:
#
#   ambiguo (n>=2)   encontre DE MAS -> hay evidencia de PRESENCIA. "Hay asiento
#                    vivo" es una medicion CONCLUYENTE y cabe en el booleano.
#                    -> alive=true con process_pid=0. El guard seguira levantando
#                       issue por el PID ausente, que es correcto: algo hay que
#                       mirar. Pero deja de reportar un muerto que no existe.
#
#   indeterminado    no pude LEER -> ni presencia ni ausencia. Ningun booleano lo
#                    representa (FABLE: un bit no lleva tres estados). Se mantiene
#                    en false HASTA la decision de contrato de ADA sobre si el
#                    guard lee `runtime`. Queda declarado, no resuelto.
#
# PRECONDICION de FABLE, verificada: alive=true en `ambiguo` solo vale si el
# matcher es confiable. Con el viejo (subcadena) dos "coincidencias" podian ser dos
# procesos que MENCIONAN a JARVIS. El de hoy es espejo de la autoridad -- self-test
# 37 casos vs `_argv_has_agent_name`, 0 divergencias, con la mencion como rojo
# permanente. Sin esa precondicion este cambio seria un falso positivo.
# ESTADO EXPLICITO de 4 valores (contrato de ADA, 29-ago 10:14, implementado en
# scripts/seal_agent_stability_guard.py:171-173 y :554). El guard ya me interpreta
# bien por la ruta LEGACY (`runtime` empieza con ambiguo/indeterminado), asi que
# esto no arregla un defecto: cumple el contrato nuevo -- su docstring dice "New
# writers publish runtime_detection_status" y deja el legacy solo para migracion.
#
# Leido del CONSUMIDOR, no de un mensaje: `explicit` gana sobre todo lo demas, y
# el guard exige coherencia (present_unique y present_ambiguous requieren
# alive=true; solo present_unique puede llevar PID).
case "$JARVIS_RUNTIME" in
  ambiguo_*)        RUNTIME_STATUS="present_ambiguous" ;;
  indeterminado_*)  RUNTIME_STATUS="indeterminate" ;;
  claude_named)     [ -n "$JARVIS_PID" ] && RUNTIME_STATUS="present_unique" || RUNTIME_STATUS="absent" ;;
  # claude_kitty NO es present_unique (ADA, 29-ago 10:23). El fallback de kitty
  # SOLO corre cuando el predicado de la autoridad encontro CERO (linea 86), asi
  # que su PID -- elegido por `ps --ppid | awk /claude/`, sin comm, sin --name y
  # sin SEAL_AGENT -- por CONSTRUCCION no pasa ese predicado. Declararlo unico
  # afirmaria exactamente lo que la autoridad acaba de negar.
  #
  # Yo habia puesto `present_ambiguous` ("existencia sin identidad"). ADA especifico
  # `indeterminate` y es MAS ESTRICTO Y CORRECTO: `present_ambiguous` afirma la
  # presencia de ESTE agente con identidad no resuelta entre N candidatos que SI
  # pasan el predicado. Kitty no establece nada sobre JARVIS -- la autoridad
  # encontro CERO, y el proceso hallado bajo la terminal puede ser de otro agente.
  # No prueba presencia ni ausencia: indeterminate.
  #
  # Sin PID: el guard exige que solo present_unique lo lleve, y publicar un PID no
  # validado es peor que no publicarlo -- el consumidor lo cruza contra /proc.
  claude_kitty)     RUNTIME_STATUS="indeterminate" ;;
  *)                RUNTIME_STATUS="absent" ;;
esac

# INVERSION: en vez de ENUMERAR que estados limpian el PID, declaro el UNICO que
# lo conserva. Hallazgo de ALICE en su propio writer (29-ago 23:53), aplicado al
# mio: yo tambien enumeraba -limpiaba en 4 ramas sueltas- y hoy funcionaba porque
# la enumeracion estaba completa.
#
#   enumerar   [ "$X" = ambiguo ] && PID=""    -> un estado NUEVO hereda el PID
#   invertir   [ "$X" = unico   ] || PID=""    -> un estado nuevo nace SIN pid
#
# Es la misma frase que ella publico a las 23:35 y que cerro #1566: un filtro que
# ENUMERA lo que mira hereda su punto ciego de la ENUMERACION. La escribimos los
# dos y los dos la teniamos rota en el propio codigo.
#
# El guard lo exige (seal_agent_stability_guard.py:617): con estado distinto de
# present_unique, un PID publicado es un ISSUE. Con la inversion eso no depende
# de que yo me acuerde de limpiar en cada rama nueva.
[ "$RUNTIME_STATUS" = "present_unique" ] || JARVIS_PID=""

case "$JARVIS_RUNTIME" in
  ambiguo_*)        ALIVE_JSON="true" ;;
  # Este `true` era residuo de cuando kitty mapeaba a `present_ambiguous`, que EXIGE
  # alive=true. Con `indeterminate` no hay nada que lo justifique, y dejarlo producia
  # DOS caminos al MISMO status con `alive` OPUESTO -- justo el colapso que este
  # contrato existe para evitar, reintroducido por mi en el arreglo.
  claude_kitty)     ALIVE_JSON="false" ;;   # indeterminate: no afirma presencia
  indeterminado_*)  ALIVE_JSON="false" ;;   # pendiente de contrato, ver arriba
  *) [ -z "$JARVIS_PID" ] && ALIVE_JSON="false" || ALIVE_JSON="true" ;;
esac

# Dual write: event_log (truth) + JSON (resurrect compat)
HB_MSG="JARVIS ${ALIVE_JSON} — runtime ${JARVIS_RUNTIME} — GPU ${GPU_TEMP}C ${GPU_UTIL}%"
if [ "${JARVIS_HB_DRYRUN:-0}" = "1" ]; then
  echo "[jarvis_heartbeat] DRYRUN: no escribo event_log ni jsonl" >&2
else
$VENV -c "
import sys; sys.path.insert(0, '$MEMORY_DIR')
from seal_heartbeat import beat_sync
beat_sync('JARVIS', {'source': 'timer', 'runtime': '${JARVIS_RUNTIME}', 'runtime_detection_status': '${RUNTIME_STATUS}', 'process_pid': '${JARVIS_PID:-0}', 'gpu_temp': '${GPU_TEMP}', 'gpu_util': '${GPU_UTIL}', 'alive': '$ALIVE_JSON' == 'true'}, '${HB_MSG}')
print('[jarvis_heartbeat] beat written to event_log')
"

echo "{\"agent\":\"JARVIS\",\"ts\":$(date +%s),\"iso\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"alive\":${ALIVE_JSON}}" \
  >> "$MESSAGES_DIR/jarvis_heartbeat.jsonl"
fi

# JSON fallback para seal_agent_resurrect.sh
# CANDADO POR PROPIEDAD, no por orden de lineas. Este sink es el UNICO que queda
# fuera del bloque DRYRUN: hoy lo contiene que HB_JSON se derive de MESSAGES_DIR
# DESPUES de que el guard lo redirija. Eso es correcto y depende del ORDEN: si
# alguien sube la definicion de HB_JSON por encima del guard, la contencion se
# rompe MUDA y volvemos al incidente de las 16:27 (una fixture de deteccion
# llegando al sink real).
#
# En vez de confiar en el orden, compruebo la PROPIEDAD del destino: con un
# /proc sintetico este archivo NO puede caer en el directorio productivo.
# Ruta LITERAL escrita a mano, por la regla de los destructivos.
#
# RESOLVER ANTES DE COMPARAR (limite que marco ADA y que REPRODUJE): comparar la
# ruta SIN resolver deja pasar dos escapes medidos, los dos pisando el destino:
#   symlink   JARVIS_MESSAGES_DIR=/tmp/x/atajo -> .../messages   ESCAPA
#   relativa  JARVIS_MESSAGES_DIR=./messages con cwd adentro      ESCAPA
# El `case` comparaba el texto que le daban, no el archivo que se iba a abrir.
# `realpath -m` resuelve symlinks y relativas y funciona aunque el archivo aun
# no exista. Sigue sin ser aislamiento TOTAL -un sink nuevo escrito manana no
# pasa por aca-; eso lo arregla la compuerta unica de #1564, no este parche.
if [ -n "${JARVIS_PROC_ROOT:-}" ]; then
  # FAIL-CLOSED SOBRE EL INSTRUMENTO. El `|| printf "$HB_JSON"` devolvia la ruta
  # SIN RESOLVER, y el `case` de abajo compara ORTOGRAFIAS, no destinos.
  # Medido a nivel de mecanismo (30-ago 06:09), de las 3 formas que nombro FABLE
  # solo UNA escapa en este guard:
  #   canonica    realpath roto -> ABORTA   ok
  #   con ..      realpath roto -> ABORTA   ok  (sigue empezando por .../messages/)
  #   doble barra realpath roto -> PASA     <- ESCAPE: `//messages/` no matchea
  # Si no puedo canonizar, no puedo decidir: no escribo.
  if ! _HB_REAL=$(realpath -m "$HB_JSON" 2>/dev/null); then
    echo "jarvis_heartbeat: ABORTO. /proc sintetico y no pude canonizar HB_JSON" >&2
    echo "  $HB_JSON  (realpath fallo: no puedo descartar produccion)" >&2
    exit 3
  fi
  # RESOLVER LOS DOS LADOS. Antes resolvia solo el izquierdo y comparaba contra
  # un literal crudo: si cualquier componente de la ruta productiva fuera symlink,
  # el resuelto NO matchea el literal y el guard se ABRE. Hallazgo de FABLE
  # (30-ago 06:15), marcado por el LATENTE: midio que hoy ninguno de los cinco
  # componentes es symlink, asi que no esta vivo -- pero depende de una propiedad
  # del filesystem que nadie vigila.
  # SEAM POR PARAMETRO, la funcion vive en seat_lib.sh (que ya sourceamos y ya
  # tiene manifiesto y tests). El writer pasa el LITERAL; el test llama a la
  # funcion con una raiz de laboratorio. Diseno de FABLE (30-ago 06:19).
  #
  # Mi version previa usaba `${JARVIS_HB_PROD_ROOT:-...}`: testeable, pero la
  # variable era un hueco NUEVO -- quien la setea debilita el guard que ella
  # misma existe para probar.
  _hb_apunta_a /home/dadito/IA/proyecto-seal/messages "$_HB_REAL"
  case "$?" in
    3)
      echo "jarvis_heartbeat: ABORTO. no pude canonizar la ruta productiva" >&2
      exit 3;;
    0)
      echo "jarvis_heartbeat: ABORTO. /proc sintetico y HB_JSON apunta a produccion:" >&2
      echo "  $HB_JSON  ->  $_HB_REAL" >&2
      echo "  (se definio HB_JSON ANTES del guard? revisa el orden)" >&2
      exit 3;;
    1)
      : ;;   # el destino NO cae dentro de produccion: es el unico caso seguro
    *)
      # FAIL-CLOSED. Hallado por NEXUS el 4-sep: el case manejaba solo 3) y 0),
      # asi que CUALQUIER otro rc caia fuera y el script seguia derecho al
      # `cat > "$HB_JSON"` -- con /proc sintetico y HB_JSON apuntando a produccion,
      # ESCRIBIA EN PRODUCCION. Es el mismo dano que este guard existe para evitar
      # (incidente del 29-ago: un latido alive=false publicado sobre un JARVIS vivo).
      # El contrato de _hb_apunta_a declara 0/1/3; un cuarto valor significa que la
      # funcion cambio bajo nuestros pies, y ante eso NO se escribe.
      echo "jarvis_heartbeat: ABORTO. _hb_apunta_a devolvio un rc fuera de contrato ($?);" >&2
      echo "  el contrato declara 0=dentro 1=fuera 3=no canonizable. NO publico estado." >&2
      exit 3;;
  esac
fi
cat > "$HB_JSON" << EOF
{
  "agent": "JARVIS",
  "alive": ${ALIVE_JSON},
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "runtime": "${JARVIS_RUNTIME}",
  "runtime_detection_status": "${RUNTIME_STATUS}",
  "process_pid": "${JARVIS_PID:-0}",
  "gpu_temp": ${GPU_TEMP},
  "gpu_util": ${GPU_UTIL},
  "context_percent": ${CONTEXT_PCT},
  "context_source": "${CONTEXT_SOURCE}"
}
EOF
