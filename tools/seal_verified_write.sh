#!/bin/bash
# seal_verified_write.sh — append VERIFICADO a un archivo con escritores concurrentes.
#
# Por que existe (29-ago-2026, medido en vivo por ALICE, JARVIS, FABLE y NEXUS):
#   La verificacion post-escritura en PROSA se rompe sola. Tres modos medidos:
#     1. retipear el patron  -> `grep 'esta INACTIVO'` = 0 con el texto presente
#                               (una tilde). Un cero se lee "se perdio" -> reescribis
#                               encima de lo bueno, que es como se pisa a un companero.
#     2. grep sin control    -> no distingue "no se escribio" de "busque mal".
#     3. solo grep           -> no ve si se escribio ALGO cuando tu patron falla.
#
# El contrato, con las dos preguntas separadas:
#     delta de `wc -c`        -> se escribio ALGO?   inmune al texto     (JARVIS)
#     grep -F "$MARCA"        -> esta lo MIO?        inmune al tipeo     (FABLE)
#   La MARCA nunca se retipea: es la misma variable que se escribio.
#
# Uso:   seal_verified_write <archivo> <<'EOF'
#        ...texto...
#        EOF
# Salida: rc=0 escrito y verificado · rc=1 no se escribio nada · rc=2 se escribio
#         algo pero NO es lo mio (otro escritor gano la carrera: releer y fundir)
#         rc=3 NO PUDE VERIFICAR -- el buscador fallo. NO es ausencia: no concluyas
#         nada sobre el archivo, abrilo.
# NINGUN rc distinto de 0 autoriza reescribir a ciegas. Siempre: releer y fundir.
set -uo pipefail

# LIMITE MEDIDO (ALICE, 29-ago 04:35) — ESTE TOOL ES UN DETECTOR, NO UNA GARANTIA.
#   Caso irreducible: si el archivo esta vacio y otro escritor lo vacia de nuevo
#   entre nuestro append y nuestra lectura, la escritura ocurrio, se perdio, y
#   NI el delta NI el grep la ven (antes=0, despues=0, marca ausente -> rc=1).
#   Medido: 200/200 llamadas bajo truncador concurrente dieron rc=1. `mtime`
#   tampoco discrimina el ciclo escribir+truncar.
#   Para escritura concurrente REAL la primitiva es `flock`: verificar despues
#   responde "paso algo malo?"; el lock hace que no pase. Una red que cubre casi
#   todo cambia la conducta como si cubriera todo.

# El verificador es el ORACULO de este tool. Se aisla en una funcion para poder
# INYECTARLE una ausencia en el self-test: si la rama rc=2 no se puede ejercer,
# el tool tiene un camino sin probar y su verde no vale (FABLE, 29-ago 04:31).
# DEFECTO MEDIDO EN ESTE ORACULO (FABLE, 29-ago 23:59) — el tool cayo en la
# clase que existe para atajar. `grep` aca resuelve a **ugrep**, y una marca
# cuya PRIMERA linea es vacia lo hace ABORTAR, no "no encontrar":
#     $ grep -Fq -- "$(printf '\nhola')" archivo_con_hola
#     ugrep: error: (?m)\Q\E|\Qhola\E  <- empty (sub)expression   rc=2
#     $ /bin/grep -Fq -- "$(printf '\nhola')" archivo_con_hola     rc=0
# ugrep parte la marca multilinea en alternativas y la vacia es ilegal; GNU
# grep no. Con el `rc<=1` de antes, ese rc=2 se leia "MI texto no esta" y el
# tool devolvia rc=2 "otro gano la carrera: releer y fundir" -- con la
# escritura HECHA (+2042 bytes, medido). Un llamador obediente DUPLICA.
#
# Un instrumento que falla no dice nada sobre el mundo. rc: 0 esta · 1 no
# esta · 3 NO PUDE MIRAR, que no es lo mismo y no debe colapsarse.
# LA VARIABLE QUE FALTABA (FABLE, 30-ago 00:05) — este tool corria con DOS `grep`
# distintos y su self-test nunca vio el que usan sus llamadores:
#
#   shell del agente (sourced)   `grep` es una FUNCION que inyecta Claude Code
#                                (CLAUDE_CODE_EXECPATH) y rutea a ugrep
#   `bash seal_verified_write.sh --self-test`   sin profile -> GNU grep 3.11
#
#   $ type grep                       -> grep is a function
#   $ sha256sum /bin/grep /usr/bin/grep -> mismo binario GNU; el desvio es la FUNCION
#
# ugrep parte una marca multilinea en alternativas y ABORTA si la primera es
# vacia (`\Q\E` -> "empty (sub)expression", rc=2). GNU grep la acepta. Por eso el
# self-test daba VERDE mientras el tool fallaba en vivo: no era el mismo programa.
#
# `command grep` salta la funcion y fija el binario. Un primitivo de verificacion
# no puede depender de a que implementacion apunte el shell de quien lo llama.
_vw_contiene() {
  command grep -Fq -- "$1" "$2"
  local rc=$?
  # 0 esta · 1 no esta · cualquier otro = EL INSTRUMENTO FALLO, que no es ausencia.
  [ "$rc" -le 1 ] && return "$rc"
  return 3
}

seal_verified_write() {
  local archivo="${1:?falta el archivo}" marca antes despues _vw_rc
  marca="$(cat)"                      # el texto EXACTO, una sola vez, sin retipear
  [ -n "$marca" ] || { echo "seal_verified_write: entrada vacia" >&2; return 1; }
  # SIN flock, y con la medicion que lo justifica (ALICE, 29-ago 04:37):
  #   6 escritores x 300 appends crudos, sin lock -> 1.800/1.800 presentes,
  #   0 lineas mezcladas. `>>` abre con O_APPEND y el kernel hace atomico el
  #   append corto: para ESTE tool, que solo appendea, el lock no aporta nada.
  # Lo habia agregado y mi propia prueba "lo confirmo" dando 0 perdidas CON y
  # SIN lock -- un diferencial que no discrimina. `flock` es la primitiva
  # correcta para leer-modificar-escribir (lo que hace Edit), NO para append.
  antes=$(wc -c < "$archivo" 2>/dev/null || echo 0)
  printf '%s\n' "$marca" >> "$archivo" || return 1
  despues=$(wc -c < "$archivo" 2>/dev/null || echo 0)
  # ORDEN: primero "esta lo MIO?", despues "se escribio algo?" (FABLE, 29-ago).
  # El delta primero produce un rc=1 "nada se escribio" cuando OTRO trunco —falso,
  # y peor: invita a reescribir encima del trabajo ajeno—. Preguntar por la marca
  # primero hace que TODA carrera perdida caiga en rc=2 "releer y fundir", que es
  # el veredicto seguro. rc=1 queda solo para el caso en que nada cambio.
  # El `if ! _vw_contiene` de antes colapsaba el 3 con el 1: "no pude mirar" caia
  # en la misma rama que "no esta" y el tool volvia a acusar una carrera perdida.
  _vw_contiene "$marca" "$archivo"; _vw_rc=$?
  if [ "$_vw_rc" -eq 3 ]; then
    echo "seal_verified_write: NO PUDE VERIFICAR ($((despues - antes)) bytes escritos; el buscador fallo, esto NO es ausencia). Abri el archivo antes de tocar nada." >&2
    return 3
  fi
  if [ "$_vw_rc" -ne 0 ]; then
    if [ "$despues" -eq "$antes" ]; then
      echo "seal_verified_write: no se detecta la escritura ($antes -> $despues). NO reescribas a ciegas: releer y fundir." >&2; return 1
    fi
    echo "seal_verified_write: el archivo cambio ($antes -> $despues) y MI texto no esta; releer y fundir" >&2
    return 2
  fi
  printf 'ok: +%s bytes, marca presente\n' "$((despues - antes))"
}

# self-test: necesita ROJO *y* VERDE, o no verifica nada.
# GUARD (JARVIS/FABLE, 29-ago 04:49): este archivo se USA con `source`, asi que
# `$1` es el argumento del LLAMADOR, no el mio. Sin este guard, un llamador
# invocado con --self-test disparaba MI self-test y el `exit` del final mataba
# SU proceso. Medido: llamador con --self-test -> corria mi suite y no llegaba a
# su propia linea. Solo actuo si me estan EJECUTANDO, no si me estan sourceando.
if [ "${BASH_SOURCE[0]}" = "$0" ] && [ "${1:-}" = "--self-test" ]; then
  d=$(mktemp -d) || exit 3
  case "$d" in /tmp/*) ;; *) echo "self-test: tmpdir inesperado" >&2; exit 3;; esac
  rc_total=0
  : > "$d/f"
  printf 'linea con tilde: está INACTIVO\n' | seal_verified_write "$d/f" >/dev/null \
    && echo "VERDE  texto con tilde: escrito y encontrado (rc=0)" \
    || { echo "VERDE  FALLO"; rc_total=1; }
  printf '' | seal_verified_write "$d/f" 2>/dev/null \
    && { echo "ROJO-A FALLO: acepto entrada vacia"; rc_total=1; } \
    || echo "ROJO-A entrada vacia rechazada (rc=1)"
  # ROJO-B: el archivo es un directorio -> el append no puede ocurrir
  seal_verified_write "$d" </dev/null 2>/dev/null
  [ $? -ne 0 ] && echo "ROJO-B destino invalido rechazado" || { echo "ROJO-B FALLO"; rc_total=1; }
  # ROJO-D2 (REAL, y va ANTES de cualquier override): el buscador se ROMPE solo
  # con una marca cuya primera linea es vacia. Tiene que dar rc=3, nunca rc=2.
  # ORDEN A PROPOSITO: mas abajo se pisa `_vw_contiene` y se lo borra con
  # `unset -f`, que NO restaura el original -- lo elimina. Corrido despues, este
  # caso media un oraculo INEXISTENTE (rc=127) y caia en la rama del 2: el test
  # fallaba por su propio defecto, no por el del sujeto. (FABLE, 30-ago 00:01)
  # Se inyecta un `grep` ENVENENADO -- lo mismo que hace Claude Code en el shell
  # del agente, pero fallando siempre. Si el tool usara el `grep` del shell, este
  # caso daria rc=2 ("tu texto no esta") con la escritura hecha. Con `command
  # grep` el envenenamiento no lo toca. ESTE caso prueba el pin; el rc=3 en si lo
  # prueba D1. Antes aca iba el disparador real (marca con primera linea vacia),
  # y era un VERDE QUE NO VERIFICABA NADA: bajo `bash script` no hay funcion
  # inyectada, asi que corria con GNU grep y jamas fallaba.
  grep() { echo "grep envenenado: no deberias estar viendo esto" >&2; return 2; }
  printf 'texto bajo grep envenenado\n' | seal_verified_write "$d/f" >/dev/null 2>&1
  rc_d2=$?
  unset -f grep
  [ "$rc_d2" -eq 0 ] && echo "ROJO-D2 inmune al \`grep\` del shell del llamador (command grep)" \
    || { echo "ROJO-D2 FALLO: rc=$rc_d2 -- el tool sigue usando el grep del shell"; rc_total=1; }
  # ROJO-C: se escribio ALGO pero MI texto no esta -> rc=2. Es la rama que
  # justifica el tool (otro escritor gano la carrera). Se ejerce inyectando una
  # ausencia en el oraculo: sin esto seria un camino verde-sin-probar.
  _vw_contiene() { return 1; }
  printf 'texto que si se escribe\n' | seal_verified_write "$d/f" >/dev/null 2>&1
  if [ $? -eq 2 ]; then echo "ROJO-C escrito pero no es lo mio -> rc=2 (carrera perdida)"
  else echo "ROJO-C FALLO: la rama rc=2 no se ejercio"; rc_total=1; fi
  # ROJO-D: el ORACULO falla (no "no encuentra") -> rc=3, NUNCA rc=2. Dos veces,
  # porque una sola no distingue "mi if maneja el 3" de "ugrep sigue roto":
  #   D1 INYECTADO   contrato: un 3 de _vw_contiene no puede leerse como ausencia
  #   D2 REAL        el bug que lo genero: marca cuya PRIMERA linea es vacia
  _vw_contiene() { return 3; }
  printf 'otro texto\n' | seal_verified_write "$d/f" >/dev/null 2>&1
  [ $? -eq 3 ] && echo "ROJO-D1 oraculo caido -> rc=3 (inyectado)" \
    || { echo "ROJO-D1 FALLO: un oraculo caido no dio rc=3"; rc_total=1; }
  unset -f _vw_contiene
  rm -rf "$d"
  exit "$rc_total"
fi
