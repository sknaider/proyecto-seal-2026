#!/usr/bin/env bash
# gx.sh — grep que NUNCA devuelve un numero suelto.
#
# POR QUE EXISTE (JARVIS, 29-ago-2026):
# En una sola noche afirme cuatro veces un significado sacado de un conteo o de
# un numero de linea, sin abrir el contenido. Las cuatro veces el conteo era
# CORRECTO y el significado FALSO:
#
#   #1091   conte .py con 'groundingdino'  -> incluia un venv de terceros -> DONE falso
#   #1435   compare ARCHIVOS que mencionan -> el criterio eran OCURRENCIAS -> DONE falso
#   pkill   patron 'sleep 600'             -> estaba en mi propio comando  -> me mate el shell
#   verify()  grep de 'def |--name'        -> lei NOMBRES y afirme un limite que la
#                                             firma desmiente en su linea 359
#
# La regla en prosa ("abri el rango antes de afirmar") NO alcanzo: la cite tres
# veces la misma noche mientras la rompia. El generador no es el olvido, es que
# `grep -c` y `grep -n` son BARATOS y devuelven algo con lo que se puede razonar
# —un numero— mientras que leer el contenido es caro. Este script invierte eso:
# el camino barato entrega CONTENIDO, y jamas un numero solo.
#
# Uso:   gx.sh <patron> [rutas...]        (default: el cwd)
#        gx.sh -e <patron_extendido> ...  (ERE)
#
# Salidas:
#   0  hubo coincidencias y se imprimio su CONTENIDO con contexto
#   1  NO HUBO coincidencias  -> lo dice con palabras, nunca imprime "0"
#   2  demasiadas coincidencias -> NO da contenido resumido. SI imprime la
#      magnitud ("DEMASIADAS COINCIDENCIAS (60 > 40)") y los archivos, porque sin
#      eso no se puede calibrar GX_MAX_HITS ni angostar el patron. Ese numero es
#      para ANGOSTAR, nunca para concluir, y la salida lo dice con esas palabras.
#      Hallazgo de FABLE: la cabecera decia "se niega a resumir en un numero" y el
#      codigo imprimia uno. La tension es real -sin magnitud no se calibra- pero
#      cabecera y salida tienen que decir LO MISMO. Gana la salida; corrijo el texto.
#   3  error de uso
#   4  ERROR DE BUSQUEDA (grep rc>=2: permiso, ruta mala, glob roto). NO es una
#      ausencia: el patron nunca se llego a evaluar. Faltaba en esta cabecera
#      -declaraba 0/1/2/3 y el codigo emite 5 estados desde el fix de ALICE-.
#      Un contrato que no nombra un estado que el codigo produce hace que el
#      llamador escriba `case` sin esa rama y caiga en el default.

set -uo pipefail

CTX="${GX_CONTEXT:-3}"      # lineas de contexto a cada lado
MAX="${GX_MAX_HITS:-40}"    # arriba de esto, no hay resumen: hay que angostar

self_test() {
  # Fixtures PROPIAS: el self-test no depende del contenido del repo, asi que
  # sigue valiendo aunque el repo cambie. mktemp -d + guard, por la regla de los
  # destructivos: nunca una variable sin comprobar.
  local d rc fallos=0
  d="$(mktemp -d)" || { echo "self-test: no pude crear tmp"; return 3; }
  case "$d" in /tmp/*) : ;; *) echo "self-test: tmp inesperado ($d), aborto"; return 3;; esac

  printf 'alpha\nMARCA_UNICA_GX\nomega\n' > "$d/uno.txt"
  mkdir -p "$d/cosa-venv"
  printf 'MARCA_UNICA_GX en codigo de TERCEROS\n' > "$d/cosa-venv/ajeno.py"
  seq 1 200 | sed 's/^/RUIDO /' > "$d/muchos.txt"

  chk() { # $1=nombre  $2=rc_esperado  $3..=comando
    local nombre="$1" esp="$2"; shift 2
    "$@" >/dev/null 2>&1; rc=$?          # rc SIN tuberia: `| head` devolveria el de head
    if [ "$rc" -eq "$esp" ]; then printf '  OK    %-28s rc=%s\n' "$nombre" "$rc"
    else printf '  FALLA %-28s rc=%s esperado=%s\n' "$nombre" "$rc" "$esp"; fallos=$((fallos+1)); fi
  }

  echo "gx.sh --self-test"
  chk "VERDE pocos hits"      0 bash "$0" 'MARCA_UNICA_GX' "$d/uno.txt"
  # EFECTO, no rc: la version anterior daba rc=0 imprimiendo NADA (faltaba -H).
  # Un rc correcto sobre una salida vacia es el hueco que este chequeo cierra.
  local salida
  salida=$(bash "$0" 'MARCA_UNICA_GX' "$d/uno.txt" 2>/dev/null)
  if printf '%s' "$salida" | grep -Fq 'MARCA_UNICA_GX'; then
    printf '  OK    %-28s imprime la linea, no solo rc\n' "VERDE imprime CONTENIDO"
  else
    printf '  FALLA %-28s rc=0 con salida VACIA (bug del prefijo -H)\n' "VERDE imprime CONTENIDO"
    fallos=$((fallos+1))
  fi
  chk "ROJO-A sin hits"       1 bash "$0" 'ZZZ_NO_EXISTE_9917' "$d/uno.txt"
  chk "ROJO-B demasiados"     2 bash "$0" 'RUIDO' "$d/muchos.txt"
  chk "ROJO-C uso incorrecto" 3 bash "$0"

  # PROPIEDAD anti-#1091, con su CONTROL POSITIVO: si el grep pelado no encontrara
  # nada en el venv, el 0 de gx no probaria exclusion sino ausencia.
  local en_venv en_gx
  en_venv=$(grep -rl 'MARCA_UNICA_GX' "$d/cosa-venv" 2>/dev/null | grep -c .)
  en_gx=$(bash "$0" 'MARCA_UNICA_GX' "$d" 2>/dev/null | grep -c 'cosa-venv')
  if [ "$en_venv" -ge 1 ] && [ "$en_gx" -eq 0 ]; then
    printf '  OK    %-28s grep_pelado=%s gx=%s\n' "excluye terceros" "$en_venv" "$en_gx"
  else
    printf '  FALLA %-28s grep_pelado=%s gx=%s (si grep_pelado=0 el test no vale)\n' \
           "excluye terceros" "$en_venv" "$en_gx"; fallos=$((fallos+1))
  fi

  # CONTROL DE MUTACION: subiendo el techo, "demasiados" DEBE dejar de dispararse.
  # Si con GX_MAX_HITS=9999 sigue dando 2, el rc no depende del umbral y el caso
  # ROJO-B no estaria probando lo que dice probar.
  GX_MAX_HITS=9999 bash "$0" 'RUIDO' "$d/muchos.txt" >/dev/null 2>&1; rc=$?
  if [ "$rc" -eq 0 ]; then printf '  OK    %-28s rc=0 con techo alto\n' "mutacion: el techo MANDA"
  else printf '  FALLA %-28s rc=%s con techo alto (esperado 0)\n' "mutacion: el techo MANDA" "$rc"; fallos=$((fallos+1)); fi

  case "$d" in /tmp/*) rm -rf "$d";; *) echo "  (no borro $d: fuera de /tmp)";; esac
  echo "  fallos=$fallos"
  [ "$fallos" -eq 0 ]
}

if [ "${1:-}" = "--self-test" ]; then self_test; exit $?; fi

modo_ere=0
if [ "${1:-}" = "-e" ]; then modo_ere=1; shift; fi
[ $# -ge 1 ] || { echo "uso: gx.sh [-e] <patron> [rutas...]" >&2; exit 3; }

patron="$1"; shift
[ $# -ge 1 ] || set -- .

# Excluir siempre lo que no es nuestro: el falso DONE de #1091 nacio de contar
# la implementacion que trae un venv de terceros.
# `--exclude-dir` de grep matchea el NOMBRE del directorio a cualquier profundidad
# y acepta globs. Mi primera version armaba la lista con `for v in */*-venv`, que
# depende del CWD: corriendo desde otro lado no excluia nada.
#
# Lo descubri midiendo con control positivo: el patron SI existe dentro de
# characters/liveportrait-venv, y gx daba 0 desde ambos CWD. Funcionaba, pero por
# `site-packages`, NO por mi glob. Un venv con codigo fuera de site-packages se
# habria filtrado. La propiedad ahora no depende de donde se invoque.
EXCL=(--exclude-dir=.git --exclude-dir=node_modules --exclude-dir=backups
      --exclude-dir=site-packages --exclude-dir='*venv*' --exclude-dir='.*venv*')

# `-H` es OBLIGATORIO: con un SOLO archivo, grep NO antepone el nombre y el bucle
# de abajo leeria el numero de linea como si fuera la ruta -> `[ -f ]` falla ->
# `continue` -> salida VACIA con rc=0. Un falso negativo mudo, que es justo lo que
# este script existe para evitar. Cazado en su primer uso real (29-ago 04:39).
if [ "$modo_ere" -eq 1 ]; then GFLAGS=(-rnHE); else GFLAGS=(-rnHF); fi

# Una sola pasada: guardamos los hits para no re-grepear (y para que el conteo
# y el contenido vengan SIEMPRE de la misma medicion).
# CAPTURAR EL rc ANTES del `||`. `grep` tiene TRES estados y el `|| true` que yo
# tenia aplastaba dos en uno:
#   0 = hubo coincidencias   1 = NO hubo   2 = ERROR (permiso, ruta mala, glob roto)
# Con `|| true`, un rc=2 llegaba aca como "sin hits" y este script imprimia
# "SIN COINCIDENCIAS" -- convirtiendo un FALLO en una AUSENCIA.
#
# Es exactamente el defecto contra el que existe gx.sh, dentro de gx.sh.
# Hallazgo de ALICE en su revision independiente; verificado por efecto:
#   grep sobre un archivo sin permiso -> rc=2 -> gx decia "SIN COINCIDENCIAS" rc=1
set +e
hits="$(grep "${GFLAGS[@]}" "${EXCL[@]}" -- "$patron" "$@" 2>/dev/null)"
_grc=$?
set -e
if [ "$_grc" -ge 2 ]; then
  echo "ERROR DE BUSQUEDA (grep rc=$_grc). NO es una ausencia: es un fallo."
  echo "  Causas tipicas: permiso denegado, ruta inexistente, enlace roto."
  echo "  NO concluyas 'no esta' -- el patron nunca se llego a evaluar."
  exit 4
fi

if [ -z "$hits" ]; then
  # NUNCA imprimir "0": un cero se lee como dato. Esto es una ausencia declarada.
  echo "SIN COINCIDENCIAS para el patron dado en: $*"
  echo "  Una ausencia no es evidencia hasta saber que el patron PODIA acertar."
  echo "  Control sugerido: gx.sh con un patron que SI deba estar en esos archivos."
  exit 1
fi

n="$(printf '%s\n' "$hits" | grep -c .)"

if [ "$n" -gt "$MAX" ]; then
  echo "DEMASIADAS COINCIDENCIAS ($n > $MAX). NO te doy un resumen utilizable a proposito."
  echo "  Un conteo grande es justo donde el significado se despega del numero."
  echo "  Angosta el patron, o subi GX_MAX_HITS si de verdad vas a LEER todas."
  echo "  Archivos involucrados (para angostar, no para concluir):"
  printf '%s\n' "$hits" | cut -d: -f1 | sort -u | sed 's/^/    /' | head -20
  exit 2
fi

# El camino normal: contenido con contexto. El numero de linea es un puntero;
# lo que se afirma tiene que salir de estas lineas.
printf '%s\n' "$hits" | while IFS=: read -r archivo linea _resto; do
  [ -f "$archivo" ] || continue
  desde=$(( linea > CTX ? linea - CTX : 1 ))
  hasta=$(( linea + CTX ))
  echo "=== $archivo:$linea ==="
  sed -n "${desde},${hasta}p" "$archivo" | nl -ba -v "$desde" -w6 -s'  '
  echo
done
exit 0
