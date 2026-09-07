#!/bin/bash
# alice_heartbeat_update.sh — Escribe heartbeat ALICE a event_log + JSON (dual write)

VENV="/home/dadito/IA/seal-spark/.venv/bin/python3"
MEMORY_DIR="$HOME/IA/proyecto-seal/memory"
MESSAGES_DIR="$HOME/IA/proyecto-seal/messages"
HB_JSON="$MESSAGES_DIR/alice_claude_heartbeat.json"

ALICE_RUNTIME="none"
# 29-ago: `head -1` elegia por orden de PID cuando habia mas de un match -> un
# verde falso silencioso (ficha #1332, helper seat_pid de JARVIS). Ahora la
# ambiguedad NO se resuelve: se reporta como ausencia y el guard lo ve.
seat_pid() {   # $1 = AGENTE en mayusculas
  # Criterio ALINEADO con la autoridad (seal_agent_runtime_supervisor.py:60):
  # comm==claude Y --name <AG> (prefijo de argumento) Y SEAL_AGENT=<AG>, y
  # excluye workers cuyo ARGUMENTO sea `--output-format stream-json`.
  #
  # CONTRATO #1564: rc=0 present_unique · rc=1 absent ·
  # rc=2 present_ambiguous (existencia, NO identidad) · rc=4 indeterminate.
  #
  # REJECT de ADA (30-ago): la version anterior aplanaba el cmdline con
  # `tr '\0' ' '` y comparaba SUBSTRINGS. Al aplanar se destruye la frontera
  # entre argumentos -- el unico dato que distingue un flag REAL de su mencion
  # dentro de un valor -- y eso producia TRES divergencias con la autoridad:
  #   `--prompt "--name ALICE"`        -> yo present_unique, autoridad ausente
  #   `--prompt "...stream-json..."`   -> yo absent, autoridad PID valido
  #   `--name ALICE—Team SEAL`         -> yo absent (exigia un espacio que no
  #                                       esta), autoridad PID valido
  # Ahora camino argv respetando el NUL: la frontera no se pierde nunca.
  local ag="$1" root="${PROC_ROOT:-/proc}" pid ilegibles=0 n=0 uno="" _comm="" _env=""
  for d in "$root"/[0-9]*; do
    pid=${d##*/}
    # Glob sin coincidencias: bash deja el patron LITERAL. Sin este guard, un
    # /proc vacio producia un "candidato ilegible" inexistente -> rc=4 en vez de
    # rc=1. Lo introduje arreglando lo de abajo y lo caza test_sin_asientos.
    [ -d "$d" ] || continue
    # "DESAPARECIO" y "no pude mirar" NO son lo mismo. Regresion viva del
    # 30-ago 00:50 que detecto ADA en produccion: mi fix anterior contaba como
    # INCERTIDUMBRE a los procesos que dejaron de existir entre el glob y la
    # lectura. En /proc real eso pasa siempre (medido: 1 de 1125 en un barrido),
    # asi que un asiento UNICO se publicaba present_ambiguous con pid=0.
    #   proceso que se fue      -> NO es candidato: no existe
    #   existe y no lo puedo leer -> SI es incertidumbre
    if [ ! -r "$d/comm" ]; then
      [ -e "$d/comm" ] && ilegibles=$((ilegibles + 1))
      continue
    fi
    # `supervisor:113` hace `.strip()` sobre comm. `$(cat ...)` quita SOLO los
    # saltos finales, asi que " claude", "claude " y "claude\t" divergian
    # (medido). Es la misma clase que NEXUS encontro en el writer de JARVIS,
    # en otro campo: espejar _argv_has_agent_name no alcanza -- CADA LECTURA
    # tiene su propio contrato y hay que ir a leerlo.
    _comm=$(cat "$d/comm" 2>/dev/null)
    _comm="${_comm#"${_comm%%[![:space:]]*}"}"   # strip inicial
    _comm="${_comm%"${_comm##*[![:space:]]}"}"   # strip final
    [ "$_comm" = "claude" ] || continue
    local argv=() val name_ok=0 worker=0 i
    mapfile -d '' -t argv < "$d/cmdline" 2>/dev/null || continue
    [ "${#argv[@]}" -gt 0 ] || continue
    i=0
    while [ "$i" -lt "${#argv[@]}" ]; do
      val="${argv[$i]}"
      # worker: el ARGUMENTO es el flag, no un texto que lo menciona
      if [ "$val" = "--output-format" ] && [ "${argv[$((i+1))]:-}" = "stream-json" ]; then
        worker=1
      fi
      [ "$val" = "--output-format=stream-json" ] && worker=1
      # ESPEJO de _argv_has_agent_name (supervisor:72-86), no mi version del
      # conjunto. Las divergencias 8 y 9 nacieron de elegir YO los separadores
      # -- una vez de menos (exigia espacio), otra de mas (cualquier
      # no-identificador). Al leer la funcion ENTERA aparecieron dos partes que
      # nadie me habia reportado: strip("\"'") y upper(). Espejo las cuatro:
      #   1. --name <X>  o  --name=<X>
      #   2. quitar comillas simples y dobles de los extremos
      #   3. comparar en MAYUSCULAS
      #   4. seguido de whitespace, raya em, o fin de cadena
      cand=""
      if [ "$val" = "--name" ]; then cand="${argv[$((i+1))]:-}"
      else case "$val" in "--name="*) cand="${val#--name=}" ;; esac
      fi
      if [ -n "$cand" ]; then
        cand="${cand%\"}"; cand="${cand#\"}"; cand="${cand%\'}"; cand="${cand#\'}"
        cand="${cand^^}"
        case "$cand" in
          "$ag") name_ok=1 ;;
          # `\s` de Python es UNICODE-AWARE: incluye NBSP (U+00A0), espacio
          # fino (U+202F), EM SPACE (U+2003) y toda la clase Zs. Una clase de
          # bash con los 6 ASCII coincide en el caso comun y DIVERGE en el raro
          # (medido: 3 divergencias). Control negativo: el guion suave U+00AD
          # NO es whitespace y los dos lo rechazan.
          "$ag"[$' \t\n\r\f\v']*) name_ok=1 ;;
          # 29 separadores DERIVADOS del motor de Python:
          #   [c for c in range(0x110000) if re.match(r'\\s', chr(c))]
          # NO de una lista a mano: me faltaban 0x1c-0x1f, que `\\s`
          # acepta y no PARECEN whitespace.
          # ASCII por clase (\\xNN es byte, no depende del charmap);
          # el resto como BYTES UTF-8 literales, nunca $'\\uXXXX'.
          "$ag"[$'\x09\x0a\x0b\x0c\x0d\x1c\x1d\x1e\x1f\x20']*) name_ok=1 ;;
          "$ag"*|"$ag" *|"$ag" *|"$ag" *) name_ok=1 ;;
          "$ag" *|"$ag" *|"$ag" *|"$ag" *) name_ok=1 ;;
          "$ag" *|"$ag" *|"$ag" *|"$ag" *) name_ok=1 ;;
          "$ag" *|"$ag" *|"$ag" *|"$ag" *) name_ok=1 ;;
          "$ag" *|"$ag" *|"$ag"　*) name_ok=1 ;;
          "$ag"—*) name_ok=1 ;;
        esac
      fi
      i=$((i + 1))
    done
    [ "$worker" -eq 1 ] && continue
    [ "$name_ok" -eq 1 ] || continue
    if [ ! -r "$d/environ" ]; then ilegibles=$((ilegibles + 1)); continue; fi
    # `supervisor:144-145` arma un DICT y hace .strip().upper(). Mi `sed -n p`
    # imprimia TODAS las coincidencias y no normalizaba: divergia en minusculas,
    # en espacios, y -- la peligrosa -- con SEAL_AGENT repetido, donde el dict
    # se queda con la ULTIMA y yo obtenia dos lineas que no comparan con nada.
    _env=$(tr '\0' '\n' < "$d/environ" 2>/dev/null | sed -n "s/^SEAL_AGENT=//p" | tail -1)
    _env="${_env#"${_env%%[![:space:]]*}"}"
    _env="${_env%"${_env##*[![:space:]]}"}"
    [ "${_env^^}" = "$ag" ] || continue
    n=$((n + 1)); uno="$pid"
  done
  [ "$n" -eq 0 ] && [ "$ilegibles" -gt 0 ] && return 4
  [ "$n" -eq 0 ] && return 1
  [ "$n" -gt 1 ] && return 2
  [ "$ilegibles" -gt 0 ] && return 2
  printf '%s' "$uno"; return 0
}
# 29-ago: el muestreo de GPU (~20-50 ms de nvidia-smi) corria ANTES de buscar el
# asiento, gastando la ventana justo en el arranque -> alive=false transitorio
# (YELLOW falso del 28-ago 04:2x). Ahora el asiento se busca PRIMERO y una
# ausencia se reintenta antes de declararse: un proceso realmente muerto sigue
# dando false, solo que 2 s mas tarde sobre una cadencia de minutos.
# Reintenta SOLO la AUSENCIA (rc=1). Un rc=2 es ambiguedad (dos asientos con el
# mismo --name) y corta de inmediato: esperar a que se resuelva sola OCULTARIA
# justo el incidente que seat_pid existe para reportar (30-jul, `head -1`
# eligiendo en silencio). Limite conocido: la ventana de reintento es ~2 s; un
# relanzamiento mas lento sigue escribiendo alive=false sobre un asiento vivo.
ALICE_PID=""
DETECTION="absent"          # contrato ADA 29-ago: runtime_detection_status
for _try in 1 2 3; do
  ALICE_PID=$(seat_pid ALICE); _rc=$?
  [ "$_rc" -eq 0 ] && [ -n "$ALICE_PID" ] && break
  ALICE_PID=""
  [ "$_rc" -eq 2 ] && ALICE_RUNTIME="ambiguo" && DETECTION="present_ambiguous" && break
  # rc=4 = candidatos con /proc ILEGIBLE. Reintentar no lo cura (los permisos no
  # cambian en 2 s) y tratarlo como ausencia escribe alive=false sobre un asiento
  # posiblemente VIVO. Es el mismo defecto que arreglamos DENTRO de seat_pid y que
  # se perdia aca: un fix que no llega al consumidor no existe (JARVIS, 05:45).
  [ "$_rc" -eq 4 ] && ALICE_RUNTIME="indeterminado" && DETECTION="indeterminate" && break
  [ "$_try" -lt 3 ] && sleep 1
done
if [ -n "$ALICE_PID" ]; then
  ALICE_RUNTIME="claude_named"
fi
# La ambiguedad NO cae al fallback: este camino elige con dos `head -1` propios,
# o sea reintroduciria por la puerta de atras el mismo silencio que seat_pid
# quita por la de adelante. Ambiguo se reporta, no se resuelve.
if [ -z "$ALICE_PID" ] && [ "$ALICE_RUNTIME" != "ambiguo" ] && [ "$ALICE_RUNTIME" != "indeterminado" ]; then
  KITTY_SOCK="/tmp/seal-alice-kitty.sock"
  KITTY_PID=$(pgrep -f "kitty.*listen-on.*unix:${KITTY_SOCK}" 2>/dev/null | head -1)
  if [ -n "$KITTY_PID" ]; then
    for BASH_PID in $(ps --ppid "$KITTY_PID" -o pid= 2>/dev/null); do
      C_PID=$(ps --ppid "$BASH_PID" -o pid= -o comm= 2>/dev/null | awk '/claude/{print $1}' | head -1)
      # NO adopto el pid: el fallback kitty NO aplica un solo criterio de la
      # autoridad -- ni SEAL_AGENT, ni --name, ni la exclusion de workers
      # stream-json. Prueba que hay UN claude bajo un socket LLAMADO
      # seal-alice-kitty, que es una convencion de NOMBRES, no una identidad.
      #
      # CORRECCION DE ADA (29-ago 23:46), mas fina que la mia: yo puse
      # present_ambiguous, que significa "existe ALICE, la unicidad no se probo".
      # Aca no esta probada NI la existencia DE ALICE -- solo la de un claude.
      # El estado honesto es INDETERMINATE: no pude medir, y alive=false.
      # Ascenderlo exigiria que #1564 declare y pruebe que la procedencia por
      # socket es evidencia de identidad. Hoy no lo dice.
      [ -n "$C_PID" ] && ALICE_RUNTIME="claude_kitty" && DETECTION="indeterminate" && break
    done
  fi
fi
# present_unique / present_ambiguous prueban EXISTENCIA -> alive=true.
# absent e indeterminate no la prueban -> alive=false. `alive` es proyeccion
# de compatibilidad; la fuente de verdad es runtime_detection_status (ADA).
# Solo el camino de seat_pid rc=0 justifica present_unique: es el UNICO que
# ejercio el criterio completo de la autoridad. Antes esta linea sellaba
# present_unique por el mero hecho de tener un pid, sin mirar de donde venia,
# y le estampaba el sello de la autoridad al fallback kitty (medido 29-ago).
[ -n "$ALICE_PID" ] && [ "$DETECTION" = "absent" ] && DETECTION="present_unique"
# SOLO present_unique conserva el pid. Escrito como LISTA BLANCA a proposito:
# antes decia `[ "$DETECTION" = "present_ambiguous" ] && ALICE_PID=""`, o sea
# enumeraba QUE estados limpiar -- y se olvidaba de indeterminate, que salia
# `alive=false` CON un pid al lado (hallazgo de ADA, 29-ago 23:51; medido:
# detection=indeterminate pid=12345 alive=false). Un estado que se contradice.
#
# Es la MISMA clase que arregle esta noche en el guard de #1566: un filtro que
# enumera lo que mira hereda su punto ciego de la enumeracion. Invertirlo hace
# que el estado que NO midio identidad no pueda emitir un pid por construccion,
# incluso uno que no existe todavia.
[ "$DETECTION" = "present_unique" ] || ALICE_PID=""
case "$DETECTION" in
  present_unique|present_ambiguous) ALIVE_JSON="true" ;;
  *)                                ALIVE_JSON="false" ;;
esac

# ── Ocupacion de contexto del asiento (3-sep-2026) ───────────────────────────
# YO fui el caso: 6,4 h muda (06:01-12:36) con proceso vivo, latido fresco y
# bridge activo, mientras mi barra de tmux decia "100% context used". El latido
# decia `alive:true` y era CIERTO -- y aun asi no podia contestarle a William.
# Un asiento saturado es indistinguible de uno sano si solo se mira `alive`.
# Patron de JARVIS (commit 2db9c63e2), adoptado aca sobre MI asiento.
#
# FAIL-SAFE: si el medidor no esta, falla, tarda >10 s o no imprime un %, se
# escribe null y context_source="unavailable". NUNCA cambia alive ni el rc: el
# latido no puede caerse por el instrumento que le agregue.
# ALICE_CONTEXT_METER permite apuntar a un stub en pruebas.
CONTEXT_METER="${ALICE_CONTEXT_METER:-$(dirname "$0")/seal_context_meter.py}"
_context_percent() {
  local out pct
  out=$(timeout 10 "$VENV" "$CONTEXT_METER" --tmux ALICE 2>/dev/null) || out=""
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

GPU_TEMP=$(nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")
GPU_UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ' || echo "null")

HB_MSG="ALICE ${ALIVE_JSON} — runtime ${ALICE_RUNTIME} — GPU ${GPU_TEMP}C ${GPU_UTIL}% — ctx ${CONTEXT_PCT}%"
$VENV -c "
import sys; sys.path.insert(0, '$MEMORY_DIR')
from seal_heartbeat import beat_sync
beat_sync('ALICE', {'source': 'timer', 'runtime': '${ALICE_RUNTIME}', 'runtime_detection_status': '${DETECTION}', 'process_pid': '${ALICE_PID:-0}', 'gpu_temp': '${GPU_TEMP}', 'gpu_util': '${GPU_UTIL}', 'context_percent': '${CONTEXT_PCT}', 'context_source': '${CONTEXT_SOURCE}', 'alive': '$ALIVE_JSON' == 'true'}, '${HB_MSG}')
print('[alice_heartbeat] beat written to event_log')
"

echo "{\"agent\":\"ALICE\",\"ts\":$(date +%s),\"iso\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"alive\":${ALIVE_JSON},\"runtime_detection_status\":\"${DETECTION}\"}" \
  >> "$MESSAGES_DIR/alice_heartbeat.jsonl"

cat > "$HB_JSON" << EOF
{
  "agent": "ALICE",
  "alive": ${ALIVE_JSON},
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "runtime": "${ALICE_RUNTIME}",
  "runtime_detection_status": "${DETECTION}",
  "process_pid": "${ALICE_PID:-0}",
  "gpu_temp": ${GPU_TEMP},
  "gpu_util": ${GPU_UTIL},
  "context_percent": ${CONTEXT_PCT},
  "context_source": "${CONTEXT_SOURCE}"
}
EOF
