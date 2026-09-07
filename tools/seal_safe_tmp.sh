#!/usr/bin/env bash
# seal_safe_tmp.sh — carpetas temporales de prueba SEGURAS (anti rm -rf $HOME).
#
# Origen: 2026-08-09, un script de verificación hizo `rm -rf "$HOME"` (cleanup que
# debía borrar solo la carpeta temporal y apuntó al home entero). Claude Code lo
# frenó pidiendo permiso; la lección (ALICE) es que la red NO puede ser el humano:
# el fix vive en el CÓDIGO. Este helper hace la clase de bug IMPOSIBLE.
#
# Reglas que impone:
#   1) La carpeta de prueba SIEMPRE sale de `mktemp -d` (jamás derivada de $HOME).
#   2) El cleanup usa `${var:?}` -> aborta si la ruta está vacía/undefined
#      (nunca degenera en `rm -rf /` ni `rm -rf $HOME`).
#   3) Defensa en profundidad: el cleanup NIEGA rutas críticas ("/", $HOME, ...)
#      y cualquier ruta FUERA de un directorio temporal.
#
# Uso:
#   source tools/seal_safe_tmp.sh
#   d="$(seal_safe_tmp_make)"      # crea /tmp/seal_test.XXXXXX y lo imprime
#   ...usar "$d"...
#   seal_safe_tmp_clean "$d"       # borra SOLO esa carpeta, con guardas
#
# Self-test:  bash tools/seal_safe_tmp.sh --self-test   (0 = todas las guardas OK)

set -o pipefail

# Crea una carpeta temporal aislada y la imprime por stdout.
seal_safe_tmp_make() {
    mktemp -d -t "seal_test.XXXXXXXX"
}

# Borra una carpeta temporal con guardas. Devuelve !=0 y NO borra si la ruta es
# vacía, crítica, o está fuera de un directorio temporal.
seal_safe_tmp_clean() {
    # Guard 2: ${1:?...} aborta la EXPANSIÓN si la var está vacía/unset.
    local d="${1:?seal_safe_tmp_clean: NEGADO — ruta vacia/undefined}"

    # Guard 3a: rutas críticas explícitas.
    case "$d" in
        "" | "/" | "$HOME" | "$HOME/" | "$HOME/." | ".." | "." )
            echo "seal_safe_tmp_clean: NEGADO — ruta critica: '$d'" >&2
            return 2 ;;
    esac

    # Guard 3b: solo permitir dentro de un directorio temporal conocido.
    case "$d" in
        /tmp/?* | /var/tmp/?* | "${TMPDIR:-/tmp}"/?* ) : ;;
        * )
            echo "seal_safe_tmp_clean: NEGADO — fuera de tmp: '$d'" >&2
            return 2 ;;
    esac

    rm -rf -- "$d"
}

_seal_safe_tmp_self_test() {
    local fails=0

    # 1) make crea una carpeta real bajo /tmp
    local d; d="$(seal_safe_tmp_make)"
    if [ -d "$d" ]; then case "$d" in /tmp/*|/var/tmp/*) echo "PASS make -> $d";; *) echo "FAIL make fuera de tmp: $d"; fails=$((fails+1));; esac
    else echo "FAIL make no creo dir"; fails=$((fails+1)); fi

    # 2) clean borra esa carpeta
    seal_safe_tmp_clean "$d"
    if [ ! -e "$d" ]; then echo "PASS clean borro $d"; else echo "FAIL clean no borro $d"; fails=$((fails+1)); fi

    # 3) clean con var VACÍA aborta y NO toca el home
    local empty=""
    if ( seal_safe_tmp_clean "$empty" ) 2>/dev/null; then echo "FAIL clean acepto ruta vacia"; fails=$((fails+1)); else echo "PASS clean nego ruta vacia"; fi

    # 4) clean sobre $HOME es NEGADO
    if ( seal_safe_tmp_clean "$HOME" ) 2>/dev/null; then echo "FAIL clean acepto \$HOME"; fails=$((fails+1)); else echo "PASS clean nego \$HOME"; fi

    # 5) clean sobre "/" es NEGADO
    if ( seal_safe_tmp_clean "/" ) 2>/dev/null; then echo "FAIL clean acepto /"; fails=$((fails+1)); else echo "PASS clean nego /"; fi

    # 6) clean fuera de tmp (ej. $HOME/soulroot) es NEGADO — el caso EXACTO del incidente
    if ( seal_safe_tmp_clean "$HOME/soulroot" ) 2>/dev/null; then echo "FAIL clean acepto \$HOME/soulroot"; fails=$((fails+1)); else echo "PASS clean nego \$HOME/soulroot (caso del incidente)"; fi

    # 7) home sigue intacto tras todo el self-test
    if [ -d "$HOME" ] && [ "$(ls -A "$HOME" 2>/dev/null | wc -l)" -gt 0 ]; then echo "PASS home intacto"; else echo "FAIL home tocado"; fails=$((fails+1)); fi

    echo "----"
    if [ "$fails" -eq 0 ]; then echo "SELF-TEST: TODO VERDE (guardas activas)"; return 0
    else echo "SELF-TEST: $fails FALLAS"; return 1; fi
}

if [ "${1:-}" = "--self-test" ]; then
    _seal_safe_tmp_self_test
fi
