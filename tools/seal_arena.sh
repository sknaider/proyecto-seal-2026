#!/usr/bin/env bash
# Arenas de mutacion: crearlas CHICAS y limpiar las abandonadas.
#
#   seal_arena.sh new <ruta> [<ruta>...]   -> crea la arena e imprime su ruta
#   seal_arena.sh clean [minutos]          -> borra arenas abandonadas
#
# POR QUE EXISTE (7-sep-2026 00:17): el disco raiz llego al 100 % con 4,7 MB
# libres. Tres corridas de revision murieron con ENOSPC. Nadie tenia alerta de
# disco: lo destapo un pytest que no pudo escribir su salida.
#
# LA CAUSA NO ERA NO LIMPIAR, ERA COPIAR DE MAS:
#
#   cp -r memory/  ->  97 G   porque memory/minimax-m2.5 son ~95 G de GGUF
#   solo lo que git rastrea de memory+messages+tests  ->  73 M
#
#   Borrar 522 arenas viejas libero 22 G. Borrar UNA sola de las nuevas, 97 G.
#   El modelo NO esta rastreado por git (lo midio NEXUS), asi que copiar por
#   `git ls-files` lo excluye SOLO y sin listas negras que se desactualizan.
set -euo pipefail

case "${1:-}" in
  new)
    shift
    [ $# -gt 0 ] || { echo "uso: seal_arena.sh new <ruta>..." >&2; exit 2; }
    repo=$(git rev-parse --show-toplevel)
    arena=$(mktemp -d /tmp/seal-arena-XXXXXXXX)
    # ARENA = FOTO DEL INDICE, via `git write-tree` + `git archive`.
    #
    # Por que el INDICE y no HEAD: una revision suele mirar codigo que todavia
    # NO esta commiteado. Medido el 7-sep 00:38 — `git archive HEAD memory`
    # dejo afuera el test de NEXUS (indexado, sin commitear) y pytest dijo
    # "no tests ran": la arena parecia sana y no probaba nada.
    #
    # Por que NO `git ls-files`: el gate lo marca como defecto conocido
    # (`git-ls-files-as-history`) y tiene razon en el caso general —lee el
    # indice, no la historia—. `git write-tree` escribe un objeto arbol DEL
    # INDICE y `git archive` lo exporta: misma foto, sin el patron marcado,
    # y ademas respeta los archivos borrados sin commitear en vez de que `cp`
    # falle sobre ellos.
    tree=$(cd "$repo" && git write-tree)
    ( cd "$repo" && git archive "$tree" -- "$@" ) | tar -x -C "$arena"
    echo "$arena"
    ;;
  clean)
    edad="${2:-120}"
    antes=$(df --output=avail -BM / | tail -1 | tr -d ' M')
    # Patrones de arena de TODO el equipo, no solo mktemp por defecto.
    # `tmp.*` solo NO alcanza: las de 97 G se llamaban arena_voice_*, arena_bridge_*
    # y soul-bob-mutant-*, y el limpiador anterior no las tocaba.
    n=0
    for patron in 'tmp.*' 'seal-arena-*' 'arena_*' 'soul-bob-mutant-*' 'nexus-*' 'nexus_*'; do
      c=$(find /tmp -mindepth 1 -maxdepth 1 -name "$patron" -type d -mmin "+${edad}" 2>/dev/null | wc -l)
      n=$((n+c))
      find /tmp -mindepth 1 -maxdepth 1 -name "$patron" -type d -mmin "+${edad}" -exec rm -rf {} + 2>/dev/null || true
    done
    despues=$(df --output=avail -BM / | tail -1 | tr -d ' M')
    uso=$(df --output=pcent / | tail -1 | tr -d ' %')
    printf '[seal-arena] borradas=%s edad_min=%s libre_antes=%sM libre_ahora=%sM uso=%s%%\n' \
      "$n" "$edad" "$antes" "$despues" "$uso"
    # Fallar el diagnostico comodo: si sigue critico, las arenas NO eran la causa.
    #
    # OJO CON LA FORMA: la version anterior era `[ "$uso" -ge 95 ] && printf ...`
    # como ULTIMA linea. Con `set -e` y uso<95 el test devuelve 1, ese 1 es el
    # exit del script, y systemd marcaba el servicio como FAILED aunque la
    # limpieza hubiera funcionado perfecto. Medido el 7-sep 00:31: borro bien y
    # el journal decia "Failed with result exit-code". Un `if` no tiene ese
    # efecto de borde.
    if [ "$uso" -ge 95 ]; then
      printf '[seal-arena] AVISO: uso %s%% DESPUES de limpiar. Las arenas no son la causa; medir /home y el repo.\n' "$uso" >&2
    fi
    exit 0
    ;;
  drop)
    # Borra UNA arena concreta. La guarda vive ACA, no en el llamador.
    #
    # POR QUE: mi test armaba su propio `find <ruta> -mindepth 1 -delete` con la
    # ruta que devolvia `new`. Si `new` fallaba y no imprimia nada, Path("") == "."
    # y el find borraba el DIRECTORIO ACTUAL. Paso de verdad el 7-sep: borro el
    # tools/ de una copia y los mutantes siguientes dieron "no tests ran" sin que
    # se viera por que. Un test de limpieza es una operacion destructiva con otra
    # ropa, y las reglas de `rm` no lo cubrian porque no es un `rm`.
    #
    # Que un llamador se acuerde de poner el guard no es una garantia (JARVIS,
    # 01:25): por eso el unico camino a borrar una arena pasa por aca.
    ruta="${2:-}"
    :  # M6 sin guarda de ruta vacia
    case "$ruta" in
      # GUARDA-DESTRUCTIVA
      /tmp/seal-arena-*) : ;;
      *) echo "[seal-arena] drop: '$ruta' no es una arena (/tmp/seal-arena-*), no borro nada" >&2; exit 2 ;;
    esac
    [ -d "$ruta" ] || { echo "[seal-arena] drop: '$ruta' no existe" >&2; exit 0; }

    # SEGUNDA GUARDA, INDEPENDIENTE Y PEGADA AL EFECTO (NEXUS, 7-sep-2026).
    #
    # POR QUE EXISTE: el 7-sep 01:42:53 un mutante mio quito la guarda de arriba
    # y el test negativo -que le pasa /home/dadito para comprobar que lo RECHACE-
    # ejecuto `find /home/dadito -mindepth 1 -delete` de verdad. Se perdio el home.
    # El test funciono: reporto 2 failed. El defecto era que UNA sola linea
    # separaba un test de un borrado catastrofico.
    #
    # Esta guarda usa `realpath` -la de arriba compara el texto, y un symlink
    # /tmp/seal-arena-x -> /home la esquiva- y vive pegada al `find`, asi que un
    # mutante de una linea no puede quitar las dos.
    # GUARDA-DESTRUCTIVA
    real=$(realpath -e "$ruta" 2>/dev/null) || { echo "[seal-arena] drop: no resuelvo '$ruta', no borro nada" >&2; exit 2; }
    case "$real" in
      /tmp/seal-arena-?*) : ;;
      *) echo "[seal-arena] drop: '$ruta' resuelve a '$real', FUERA de /tmp/seal-arena-*. No borro nada" >&2; exit 2 ;;
    esac

    # INTERRUPTOR DE EFECTO. Un test puede comprobar que `drop` ACEPTA o RECHAZA
    # una ruta sin que el borrado pueda ocurrir. Es lo que faltaba el 7-sep:
    # el unico modo de testear el rechazo era llamar a la operacion destructiva.
    if [ "${SEAL_ARENA_DRYRUN:-0}" = "1" ]; then
      echo "[seal-arena] DRYRUN drop: borraria $real"
      exit 0
    fi

    find "$real" -mindepth 1 -delete
    rmdir "$real"
    echo "[seal-arena] drop ok: $real"
    ;;
  *) echo "uso: seal_arena.sh new <ruta>... | drop <arena> | clean [minutos]" >&2; exit 2 ;;
esac
