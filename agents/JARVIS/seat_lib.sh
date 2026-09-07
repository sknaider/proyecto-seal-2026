# seat_lib.sh — deteccion de asiento, ESPEJO de la autoridad. Se hace `source`.
#
# POR QUE EXISTE (JARVIS, 29-ago-2026, a partir de la medicion de ALICE):
# Mis tres detectores exigian SOLO `--name <AG>` en el cmdline. La autoridad que
# realmente decide —tools/seal_agent_runtime_supervisor.py:60-89— exige TRES
# condiciones y ademas descarta workers:
#
#   comm == "claude"                          y
#   `--name <AG>` como ARGUMENTO (no mencion) y
#   SEAL_AGENT=<AG> en /proc/PID/environ      y
#   NO "--output-format stream-json"          (los workers heredan SEAL_AGENT)
#
# Un chequeo mas PERMISIVO que la autoridad no es "casi igual": cuenta procesos
# que la autoridad rechaza. En mi caso el error va hacia AMBIGUEDAD FALSA -> mi
# heartbeat publica alive=false sobre un asiento sano. Es la misma forma del
# rotador que veia `expires_at` y la auth exigia ademas `created_at+30d`.
#
# Y va en UN archivo, no copiado en tres: tres copias de un espejo derivan, y la
# que derive va a mentir en silencio.

# jarvis_name_matches <candidato> <AGENTE> -> rc=0 si la AUTORIDAD lo aceptaria.
#
# Existe como FUNCION y no inline porque el self-test la llama a ELLA. Antes yo
# tenia los casos del contrato escritos como una REPLICA de la logica dentro del
# test: 7 verdes que seguian verdes aunque borrara el detector, y que encima
# codificaban la version por subcadena que ADA habia rechazado. Un test que
# duplica la logica prueba su copia.
#
# Espeja `_argv_has_agent_name` (seal_agent_runtime_supervisor.py:71-84):
# despoja comillas, normaliza a mayusculas, y exige AG seguido de blanco, raya
# U+2014 o fin. El guion ASCII NO: separa al principal de su clon.
jarvis_name_matches() {
  local cand="${1-}" ag="${2:?falta agente}" norm
  ag="${ag^^}"
  # `.strip("\"'")` de Python quita comillas SOLO EN LOS EXTREMOS, repetidamente.
  # Yo usaba `${cand//[\"\']/}`, que las borra TAMBIEN ADENTRO: aceptaba
  # `QUO"TEAG` como `QUOTEAG`, que la autoridad rechaza (ADA, 5ta ronda).
  norm="$cand"
  while [[ "$norm" == [\"\']* ]]; do norm="${norm#?}"; done
  while [[ "$norm" == *[\"\'] ]]; do norm="${norm%?}"; done
  norm="${norm^^}"
  # SEPARADORES DERIVADOS DEL MOTOR, no de los casos que alguien nombro (metodo de
  # FABLE, 29-ago). `[[:space:]]` de bash NO clasifica U+00A0 ni U+202F en NINGUN
  # locale (medido en C, C.UTF-8, en_US.UTF-8, es_PE.UTF-8; el real es es_ES.utf8),
  # mientras `\s` de Python sobre `str` es Unicode-aware. Agregar solo los dos que
  # ADA nombro habria dejado los otros 27 -- misma trampa que espejar por casos.
  #
  # Re-derivable:
  #   python3 -c "import re;print([hex(c) for c in range(0x110000) if re.match(r'\s',chr(c))])"
  # Verificado: la autoridad acepta los 29 como separador.
  # BYTES, no `\uXXXX`. Bash expande `$'\u00a0'` SEGUN EL LOCALE: sin locale
  # devuelve la cadena literal `\u00A0` (medido: c2a0 con locale, 5c7530304130
  # con `env -i`). Los writers corren bajo systemd, cuyo Environment esta VACIO,
  # asi que 19 de los 29 separadores eran basura textual en produccion.
  # Los escapes de BYTE dan c2a0 en los DOS entornos -- verificado.
  # Hallazgo de ALICE (30-ago 23:25). Su suite corre con `env` minimo y por eso
  # lo vio; la mia heredaba mi terminal con LANG=es_ES.utf8.
  # Re-derivable: python3 -c "import re;print([hex(c) for c in range(0x110000) if re.match(r'\s',chr(c))])"
  local -a _ws=($'\x09' $'\x0a' $'\x0b' $'\x0c' $'\x0d' $'\x1c' $'\x1d' $'\x1e' $'\x1f' $'\x20' $'\xc2\x85' $'\xc2\xa0' $'\xe1\x9a\x80' $'\xe2\x80\x80' $'\xe2\x80\x81' $'\xe2\x80\x82' $'\xe2\x80\x83' $'\xe2\x80\x84' $'\xe2\x80\x85' $'\xe2\x80\x86' $'\xe2\x80\x87' $'\xe2\x80\x88' $'\xe2\x80\x89' $'\xe2\x80\x8a' $'\xe2\x80\xa8' $'\xe2\x80\xa9' $'\xe2\x80\xaf' $'\xe2\x81\x9f' $'\xe3\x80\x80')
  local resto prim c
  # EL NOMBRE VA AL INICIO. `${norm#"$ag"}` borra el prefijo SI ESTA, y si no
  # esta deja la cadena intacta -- sin verificar, `"  JARVIS"` llegaba abajo con
  # resto="  JARVIS", empezaba con espacio, y devolvia 0. La autoridad ancla con
  # `^` y su `.strip("\"'")` NO saca espacios: rechaza el espacio inicial.
  # Yo lo ACEPTABA: adoptaba un asiento que la autoridad no reconoce.
  # Hallazgo derivado de ALICE (30-ago 23:21): leer la funcion de la autoridad
  # ENTERA, no solo el regex final. Mi corpus de 15 separadores no tocaba este eje.
  [[ "$norm" == "$ag"* ]] || return 1
  resto="${norm#"$ag"}"
  [ -z "$resto" ] && return 0                 # fin de cadena
  [[ "$resto" == "—"* ]] && return 0          # raya U+2014
  for c in "${_ws[@]}"; do [[ "$resto" == "$c"* ]] && return 0; done
  return 1
}

# _hb_apunta_a <raiz> <destino_resuelto>
#   0 = el destino cae DENTRO de la raiz   1 = no cae   3 = no pude canonizar
#
# VIVE ACA y no en el writer por diseno de FABLE (30-ago 06:19): el writer la
# llama con el literal de produccion, y el TEST la llama con una raiz de
# laboratorio. Mi version anterior usaba una variable de entorno para poder
# testear -- y esa variable era un hueco nuevo: quien la setea debilita el guard.
# Un seam por PARAMETRO no agranda la superficie; uno por entorno si.
_hb_apunta_a() {
  local r
  r=$(realpath -m "$1" 2>/dev/null) || return 3
  case "$2" in "$r"/*) return 0 ;; *) return 1 ;; esac
}

# jarvis_seat_pids <AGENTE>  -> STDOUT: 0..N pids, uno por linea. NADA MAS.
#                                STDERR: un 'ILEGIBLE' por candidato no leible.
#
# EL DATO Y LA SENAL VAN POR CANALES DISTINTOS (hallazgo de ALICE, 29-ago).
# Antes el marcador salia por STDOUT mezclado con los pids, y mi contrato decia
# "imprime 0..N pids" -- era falso. Dos llamadores (jarvis_process_heartbeat.sh,
# jarvis_research_trigger.sh) hacian:
#     c=$(jarvis_seat_pids "$ag"); [ -z "$c" ] && return 1
#     [ "$(printf '%s\n' "$c" | grep -c .)" -eq 1 ] || return 2
#     printf '%s' "$c"
# Con salida "ILEGIBLE": no vacia, una sola linea -> devolvia la PALABRA
# "ILEGIBLE" COMO PID con rc=0. Verificado por efecto.
#
# Arreglado en la FRONTERA y no en los llamadores: asi el contrato se vuelve
# cierto y los dos quedan sanos sin tocarlos. Un consumidor no deberia tener que
# saber que su fuente le mezcla senal con dato.
jarvis_seat_pids() {
  local ag="${1:?falta agente}" pid comm cmdline env_raw
  ag="${ag^^}"
  # COSTURA PARA PODER VERIFICARSE CONTRA LA AUTORIDAD COMPLETA.
  #
  # Antes enumeraba con `ps -C claude`, hardcodeando /proc. Eso me dejaba SIN
  # forma de comparar el predicado ENTERO contra `scan_primary_runtimes`, que
  # acepta `proc_root`. Resultado: mi unico oraculo comparaba el NOMBRE -- 1 de
  # las 3 condiciones-- y el rotulo decia "vs la autoridad". FABLE encontro la
  # misma clase en el suyo (2 de 3), NEXUS en el suyo (1 de 4), ALICE 0 de 4.
  # **Un predicado que el oraculo no llama no puede contradecirte: sale VERDE.**
  #
  # Ahora enumera el proc_root igual que la autoridad (`proc_root.iterdir()` +
  # filtro por comm), y `JARVIS_PROC_ROOT` permite apuntarlo a uno sintetico.
  # En produccion el default es /proc, asi que el comportamiento no cambia.
  local _proc="${JARVIS_PROC_ROOT:-/proc}"
  # SIN `grep`: `case` es builtin. Un `grep` roto vaciaba el listado y el
  # writer publicaba `absent` sobre un asiento VIVO -- un fallo del INSTRUMENTO
  # leido como una medicion. Bloqueante de ADA (30-ago 03:53) rastreado hasta
  # aca: yo lo habia "arreglado" en el writer, aguas ABAJO de donde falla.
  _listado=$(ls -1 "$_proc" 2>/dev/null) || _listado=""
  for pid in $_listado; do
    case "$pid" in ''|*[!0-9]*) continue ;; esac
    # FALTA vs ILEGIBLE. La autoridad separa FileNotFound (proceso que murio ->
    # skip) de PermissionError (-> unreadable_candidates). Yo trataba las dos como
    # skip, o sea convertia "no pude mirar" en "no hay". Se distingue con -e.
    # FALTA vs ILEGIBLE, decidido por EFECTO y no por `-e`: con /proc no
    # atravesable (hidepid, otro uid) `-e` da falso y yo salteaba -- convirtiendo
    # "no pude mirar" en "no hay", que es el error que esta rama existe para
    # evitar. La autoridad separa FileNotFound (skip) de PermissionError
    # (unreadable). Se distingue preguntando si el PID SIGUE VIVO tras el fallo.
    if ! comm=$(cat "$_proc/$pid/comm" 2>/dev/null); then
      if kill -0 "$pid" 2>/dev/null || [ -d "$_proc/$pid" ]; then printf 'ILEGIBLE\n' >&2; fi
      continue
    fi
    [ "$comm" = "claude" ] || continue

    local -a argv_raw argv; local i val cand norm
    if ! mapfile -d '' argv_raw < "$_proc/$pid/cmdline" 2>/dev/null; then
      if kill -0 "$pid" 2>/dev/null || [ -d "$_proc/$pid" ]; then printf 'ILEGIBLE\n' >&2; fi
      continue
    fi
    # La autoridad DESCARTA los elementos vacios (`if item`): si no, un vacio entre
    # `--name` y el nombre correria los indices y el candidato seria "".
    argv=(); for val in "${argv_raw[@]}"; do [ -n "$val" ] && argv+=("$val"); done
    [ "${#argv[@]}" -gt 0 ] || continue

    # worker stream-json: heredan SEAL_AGENT. Ambas formas, como la autoridad.
    local es_worker=0
    for ((i=0; i<${#argv[@]}; i++)); do
      val="${argv[i]}"
      if [ "$val" = "--output-format" ] && [ $((i+1)) -lt "${#argv[@]}" ] && [ "${argv[i+1]}" = "stream-json" ]; then
        es_worker=1; break
      elif [ "$val" = "--output-format=stream-json" ]; then
        es_worker=1; break
      fi
    done
    [ "$es_worker" -eq 1 ] && continue

    # --name VALOR y --name=VALOR; despoja comillas; normaliza a mayusculas;
    # AG seguido de CUALQUIER blanco, raya U+2014, o fin. Y NO corta en el primer
    # --name: la autoridad recorre todo argv. El guion ASCII queda fuera a
    # proposito -- es lo unico que separa al principal de su clon.
    local ok=0
    for ((i=0; i<${#argv[@]}; i++)); do
      val="${argv[i]}"; cand=""
      if [ "$val" = "--name" ] && [ $((i+1)) -lt "${#argv[@]}" ]; then
        cand="${argv[i+1]}"
      elif [ "${val#--name=}" != "$val" ]; then
        cand="${val#--name=}"
      else
        continue
      fi
      if jarvis_name_matches "$cand" "$ag"; then ok=1; break; fi
    done
    [ "$ok" -eq 1 ] || continue

    if ! env_raw=$(tr '\0' '\n' < "$_proc/$pid/environ" 2>/dev/null); then
      if kill -0 "$pid" 2>/dev/null || [ -d "$_proc/$pid" ]; then printf 'ILEGIBLE\n' >&2; fi
      continue
    fi
    # CLAVE EXACTA, no subcadena (ADA, 4ta ronda). El `case` viejo aceptaba
    # `SEAL_AGENT=JARVIS_CLONE` y un `MI_SEAL_AGENT=JARVIS`: mismo defecto que el
    # cmdline aplanado, en la otra mitad del predicado, y lo deje intacto mientras
    # arreglaba la primera. La autoridad arma un dict: ULTIMA aparicion gana.
    local sv=""
    while IFS= read -r val; do
      case "$val" in SEAL_AGENT=*) sv="${val#SEAL_AGENT=}";; esac
    done <<< "$env_raw"
    sv="${sv#"${sv%%[![:space:]]*}"}"; sv="${sv%"${sv##*[![:space:]]}"}"
    [ "${sv^^}" = "$ag" ] && printf '%s\n' "$pid"
  done
}

# jarvis_seat_pid <AGENTE> -> imprime EL pid solo si hay exactamente uno.
#   rc=0 unico   rc=1 ausente   rc=2 AMBIGUO   rc=3 INDETERMINADO (hubo
#   candidatos con /proc ilegible: "no se" NO es "no hay")   (elegir uno de dos es
#   indistinguible de encontrar uno solo en la salida)
jarvis_seat_pid() {
  local pids n
  local salida
  local _err
  _err=$(mktemp) || return 3
  salida=$(jarvis_seat_pids "${1:?falta agente}" 2>"$_err")
  # Mismo criterio que arriba: contar con builtins, no con `grep`.
  local -a _arr=()
  while IFS= read -r _l; do
    case "$_l" in ''|*[!0-9]*) continue ;; esac
    _arr+=("$_l")
  done <<< "$salida"
  n=${#_arr[@]}
  pids=$(printf '%s\n' "${_arr[@]:-}")
  local ilegibles
  # El `|| true` convertia un `grep` roto en "0 ilegibles" -- el mismo defecto
  # que ADA me cerro en el writer, vivo aca una capa mas arriba.
  ilegibles=0
  while IFS= read -r _l; do
    [ "$_l" = "ILEGIBLE" ] && ilegibles=$((ilegibles+1))
  done < "$_err" 2>/dev/null || ilegibles=0
  case "$_err" in /tmp/*) rm -f "$_err";; esac
  case "$n" in
    1) # ENCONTRE UNO, pero "unico" es una afirmacion sobre TODOS los candidatos.
       # Si algo quedo sin leer, ese algo PODRIA ser un segundo asiento: se que
       # hay presencia, NO se que sea unica. Devuelvo 2 (ambiguo) y NO imprimo
       # el pid, para que un llamador que ignore el rc no lo lea como unico.
       #
       # POR QUE FALTABA (ALICE, 29-ago): consultaba `ilegibles` SOLO en la rama
       # de la ausencia. El comentario de abajo dice el razonamiento correcto
       # -"no se" NO es "no hay"- y yo lo aplique a UNA sola mitad: un hallazgo
       # tapaba la incertidumbre. La autoridad no hace eso; devuelve pids=(101,)
       # CON determined=False. FABLE tenia el defecto espejado (el ilegible
       # pisaba el hallazgo) y NEXUS lo perdia en la proyeccion: los tres
       # escribimos el razonamiento y lo implementamos parcial.
       [ "$ilegibles" -gt 0 ] && return 2
       printf '%s' "$pids"; return 0;;
    0) # "ausente" solo si NADA quedo sin leer; si hubo ilegibles es INDETERMINADO
       [ "$ilegibles" -gt 0 ] && return 3
       return 1;;
    *) return 2;;
  esac
}

# --- self-test ---------------------------------------------------------------
# Se corre:  bash agents/JARVIS/seat_lib.sh --self-test
#
# Siembra TRES procesos y verifica que la deteccion acepte solo al legitimo.
# El control de la fixture NO es decorativo: mis tres primeros intentos de este
# mismo test fueron no-ops que daban "viejo=0, nuevo=0" —se lee como resultado y
# no lo es— porque los procesos sembrados no tenian comm=claude:
#   exec -a claude sleep   -> comm=sleep   (exec -a cambia argv[0], NO comm)
#   bash -c 'sleep 30'     -> comm=sleep   (bash hace EXEC del unico comando)
#   bash -c 'sleep 30; :'  -> comm=claude  <- recien aca el test vale
seat_lib_self_test() {
  local d rc fallos=0 AG=SELFTESTAG P1 P2 P3 P4 vistos viejo nuevo
  d="$(mktemp -d)" || { echo "self-test: no pude crear tmp"; return 3; }
  case "$d" in /tmp/*) : ;; *) echo "self-test: tmp inesperado, aborto"; return 3;; esac
  cp /bin/bash "$d/claude"

  env SEAL_AGENT=$AG    "$d/claude" -c 'sleep 20; :' claude --name $AG --tail & P1=$!
  env SEAL_AGENT=OTRO   "$d/claude" -c 'sleep 20; :' claude --name $AG --tail & P2=$!
  env SEAL_AGENT=$AG    "$d/claude" -c 'sleep 20; :' claude --name $AG --output-format stream-json --tail & P3=$!
  # P4 = el caso de ADA (29-ago 06:50): SOLO MENCIONA `--name AG—Team` DENTRO de
  # otro argumento, como el prompt de un agente que cita el contrato. La autoridad
  # lo rechaza; mi version por subcadena lo ACEPTABA. Va como ROJO permanente.
  env SEAL_AGENT=$AG "$d/claude" -c 'sleep 20; :' claude --system-prompt "sos un agente, se invoca con --name $AG—Team SEAL" --tail & P4=$!

  # ESPERAR POR CONDICION, NO POR RELOJ. Antes habia un `sleep 1` fijo: bajo carga
  # (corriendo dentro de pytest) sólo 3 de los 4 procesos estaban visibles y el
  # diferencial daba "conto 3, esperaba 4" -- un ROJO que no era del sujeto sino de
  # mi sincronizacion. `sleep` no es una primitiva de sincronizacion: afirma que
  # algo ya paso sin comprobarlo.
  #
  # Ahora se sondea hasta ver los 4, con deadline. Si no aparecen, el self-test
  # lo dice y aborta en vez de medir una fixture a medio armar.
  local _t=0 _vis=0
  while [ "$_t" -lt 100 ]; do
    _vis=$(ps -C claude -o pid= 2>/dev/null | tr -d ' ' | grep -cE "^($P1|$P2|$P3|$P4)$")
    [ "$_vis" -eq 4 ] && break
    sleep 0.1; _t=$((_t+1))
  done
  if [ "$_vis" -ne 4 ]; then
    echo "  FALLA CONTROL de la fixture: solo $_vis/4 procesos visibles tras 10 s"
    kill "$P1" "$P2" "$P3" "$P4" 2>/dev/null
    case "$d" in /tmp/*) rm -rf "$d";; esac
    return 3
  fi

  echo "seat_lib.sh --self-test"
  vistos=$(ps -C claude -o pid= | tr -d ' ' | grep -cE "^($P1|$P2|$P3|$P4)$")
  if [ "$vistos" -eq 4 ]; then
    printf '  OK    %-30s ps -C claude ve 4/4\n' "CONTROL de la fixture"
  else
    printf '  FALLA %-30s ve %s/4 -> el test seria un NO-OP\n' "CONTROL de la fixture" "$vistos"
    fallos=$((fallos+1))
  fi

  if [ "$fallos" -eq 0 ]; then
    # `ps -C claude -o pid= -o args=` OMITE procesos que `ps -C claude -o pid=`
    # SI lista, aun teniendo los 4 comm=claude. Medido dentro de pytest:
    #   poll (-C, solo pid) 4  ·  -C con args 3  ·  -e con args 4
    # La hipotesis la aporto NEXUS; yo habia refutado otras cinco (truncacion,
    # tty, invocacion, locale, captura) y ninguna era. Se usa `-e`, que lista
    # todo y deja el filtro en el patron.
    # EL DIFERENCIAL SE LEE DE /proc, NO DE `ps`.
    #
    # Con `ps` este chequeo daba 4 en corrida directa y 3 DENTRO DE PYTEST, de forma
    # reproducible. Refute seis hipotesis midiendo —truncacion de args, falta de tty,
    # la invocacion, el locale con la raya em, la captura de pytest, y restos de
    # corridas previas— y NINGUNA era. FABLE tampoco lo reprodujo en aislamiento.
    #
    # No le invento una causa a `ps`: lo saco del camino. `/proc/PID/cmdline` es la
    # misma fuente que usa el detector de arriba, es exacta y no depende del entorno.
    # Acotado a los PIDs sembrados: `-e` con un patron contaba tambien restos de
    # corridas anteriores y la propia medicion (aviso de NEXUS, medido: 3 vivos).
    viejo=0
    for _p in "$P1" "$P2" "$P3" "$P4"; do
      _cl=$(tr '\0' ' ' < "/proc/$_p/cmdline" 2>/dev/null) || continue
      case "$_cl" in *"--name $AG"*) viejo=$((viejo+1));; esac
    done
    nuevo=$(jarvis_seat_pids $AG)
    if [ "$viejo" -eq 4 ]; then
      printf '  OK    %-30s cuenta 4 (impostor, worker y MENCION)\n' "criterio VIEJO (cmdline)"
    else
      printf '  FALLA %-30s conto %s, esperaba 4\n' "criterio VIEJO (cmdline)" "$viejo"; fallos=$((fallos+1))
    fi
    if [ "$nuevo" = "$P1" ]; then
      printf '  OK    %-30s solo el legitimo (%s)\n' "criterio NUEVO (autoridad)" "$P1"
    else
      printf '  FALLA %-30s devolvio [%s], esperaba [%s]\n' "criterio NUEVO (autoridad)" "$(printf '%s' "$nuevo" | tr '\n' ' ')" "$P1"
      fallos=$((fallos+1))
    fi
    jarvis_seat_pid $AG >/dev/null 2>&1; rc=$?
    [ "$rc" -eq 0 ] && printf '  OK    %-30s rc=0\n' "seat_pid unico" \
                    || { printf '  FALLA %-30s rc=%s esperaba 0\n' "seat_pid unico" "$rc"; fallos=$((fallos+1)); }
    jarvis_seat_pid NOEXISTE9999 >/dev/null 2>&1; rc=$?
    [ "$rc" -eq 1 ] && printf '  OK    %-30s rc=1\n' "seat_pid ausente" \
                    || { printf '  FALLA %-30s rc=%s esperaba 1\n' "seat_pid ausente" "$rc"; fallos=$((fallos+1)); }
  fi

  # CONTRATO DEL MATCHER, los 6 casos que publico ADA (29-ago). Van como TEST y no
  # como comentario: yo TENIA escrito el supuesto de la raya y era falso igual.
  # Cada caso lleva su veredicto esperado, asi que hay ROJOS y VERDES: un set de
  # solo-verdes no discrimina un matcher que acepte todo.
  local caso esperado obtenido
  while IFS='|' read -r caso esperado; do
    [ -n "$caso" ] || continue
    if jarvis_name_matches "$caso" JARVIS; then obtenido=acepta; else obtenido=rechaza; fi
    if [ "$obtenido" = "$esperado" ]; then
      printf '  OK    %-30s %s\n' "contrato: $caso" "$obtenido"
    else
      printf '  FALLA %-30s %s, esperado %s\n' "contrato: $caso" "$obtenido" "$esperado"
      fallos=$((fallos+1))
    fi
  done <<'CASOS'
JARVIS|acepta
JARVIS — Team SEAL|acepta
JARVIS—Team|acepta
JARVIS-Team|rechaza
JARVIS-u103|rechaza
JARVIS-CLONE|rechaza
ALICE — Team SEAL|rechaza
"JARVIS"|acepta
'JARVIS'|acepta
JAR"VIS|rechaza
jarvis|acepta
JARVIS	Team|acepta
— JARVIS|rechaza
—JARVIS|rechaza
 JARVIS|rechaza
CASOS

  # Los tres ultimos casos los agrego el 4-sep tras un mutante de NEXUS que sobrevivia:
  # cambiar `== "$ag"*` por `== *"$ag"*` -quitar el ANCLA al inicio- dejaba la suite
  # en 6 passed. Ojo con QUE caso se elige: `ADA — JARVIS` o `XJARVIS` NO distinguen,
  # porque sin el ancla `${norm#$ag}` no recorta nada y el barrido de separadores los
  # rechaza igual. Los que SI distinguen son los que EMPIEZAN con separador y siguen
  # con el nombre: sin ancla, `resto` arranca en el separador y el matcher ACEPTA un
  # asiento ajeno. Eje senalado por ALICE el 30-ago: leer la funcion de la autoridad
  # ENTERA, no solo el regex final.
  # ORACULO DEL NOMBRE: el esperado lo produce la autoridad, no mi lectura de ella.
  #
  # ALCANCE, dicho porque el rotulo anterior prometia de mas (hallazgo de FABLE
  # sobre SU propio oraculo, 29-ago): esto compara `jarvis_name_matches` contra
  # `_argv_has_agent_name` -- UNA de las TRES condiciones de la autoridad.
  #   1  --name <AG> como argumento     <- ESTO se verifica aca
  #   2  NO --output-format stream-json <- no
  #   3  SEAL_AGENT=<AG> en environ     <- no
  # El detector SI implementa las tres; lo que no esta probado contra la autoridad
  # es esa implementacion, porque `jarvis_seat_pids` lee /proc HARDCODEADO y no
  # tiene costura para apuntarlo a un /proc sintetico. `scan_primary_runtimes`
  # acepta `proc_root`: la costura falta de MI lado. Queda pendiente y declarado.
  # Los casos de arriba comparan contra una tabla que escribi yo: si lei mal, quedan
  # verdes y equivocados. Esto importa el modulo y deja que su codigo decida.
  #
  # Un fallo del oraculo es FALLA, no SKIP: un skip cuenta como verde y este es el
  # unico chequeo que no depende de mi interpretacion. Y antes de leer el
  # diferencial se verifica que el comparador NO este vacio -- el oraculo de FABLE
  # fallo mudo con un `2>/dev/null` y dio 11 divergencias de una columna vacia.
  local orc
  orc=$(python3 - "$0" 2>&1 <<'ORACULO'
import re, sys, subprocess, importlib.util, pathlib
lib = sys.argv[1]
aut = pathlib.Path("tools/seal_agent_runtime_supervisor.py")
if not aut.exists():
    print("SIN_AUTORIDAD"); raise SystemExit(0)
spec = importlib.util.spec_from_file_location("sup", aut)
m = importlib.util.module_from_spec(spec); sys.modules["sup"] = m
spec.loader.exec_module(m)
# control del oraculo: tiene que aceptar Y rechazar, o no discrimina
if not (m._argv_has_agent_name(["--name","JARVIS"],"JARVIS")
        and not m._argv_has_agent_name(["--name","JARVIS-CLONE"],"JARVIS")):
    print("ORACULO_NO_DISCRIMINA"); raise SystemExit(0)
ws = [chr(c) for c in range(0x110000) if re.match(r"\s", chr(c))]
casos = ["JARVIS"+c+"Team" for c in ws] + [
    "JARVIS","JARVIS—Team","JARVIS-u103","JARVIS-CLONE","ALICE — Team",
    'JAR"VIS','"JARVIS"',"jarvis"]
div = 0
for cand in casos:
    esp = m._argv_has_agent_name(["--name", cand], "JARVIS")
    rc = subprocess.run(["bash","-c",'source "$1"; jarvis_name_matches "$2" JARVIS',
                         "_", lib, cand]).returncode
    if (rc == 0) != esp: div += 1
print("VACIO" if not casos else "%d %d" % (len(casos), div))
ORACULO
)
  case "$orc" in
    "SIN_AUTORIDAD")        printf '  FALLA %-30s no encontre la autoridad en disco\n' "oraculo inyectado"; fallos=$((fallos+1));;
    "ORACULO_NO_DISCRIMINA")printf '  FALLA %-30s el oraculo no discrimina\n' "oraculo inyectado"; fallos=$((fallos+1));;
    "VACIO")                printf '  FALLA %-30s comparador VACIO\n' "oraculo inyectado"; fallos=$((fallos+1));;
    *)  set -- $orc
        if [ "${1:-0}" -gt 0 ] && [ "${2:-1}" -eq 0 ]; then
          printf '  OK    %-30s %s casos vs _argv_has_agent_name (1 de las 3 condiciones)\n' "oraculo del NOMBRE" "$1"
        else
          printf '  FALLA %-30s casos=%s divergencias=%s\n' "oraculo inyectado" "${1:-?}" "${2:-?}"
          fallos=$((fallos+1))
        fi;;
  esac

  kill "$P1" "$P2" "$P3" "$P4" 2>/dev/null; wait "$P1" "$P2" "$P3" "$P4" 2>/dev/null
  case "$d" in /tmp/*) rm -rf "$d";; *) echo "  (no borro $d)";; esac
  echo "  fallos=$fallos"
  [ "$fallos" -eq 0 ]
}

# El dispatch SOLO cuando este archivo se EJECUTA, nunca cuando se SOURCEA.
#
# Sin este guard, `$1` dentro de un archivo sourceado son los argumentos del que
# LLAMA: `bash jarvis_process_heartbeat.sh --self-test` corria MI self-test y
# hacia `exit 0` — el trabajo del llamador nunca ocurria y el rc=0 lo hacia
# parecer sano. Medido en vivo 3 minutos despues de introducirlo, en un archivo
# con timer activo. Un `exit` dentro de una libreria sourceada termina al que la
# usa, no a la libreria.
if [ "${BASH_SOURCE[0]}" = "$0" ] && [ "${1:-}" = "--self-test" ]; then
  seat_lib_self_test; exit $?
fi
