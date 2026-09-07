#!/usr/bin/env bash
# Borra las arenas de mutacion abandonadas en /tmp.
#
# POR QUE EXISTE: el 7-sep-2026 00:17 el disco raiz llego al 100 % con 4,7 MB
# libres. Causa medida: 561 directorios `/tmp/tmp.*` con 168 G — copias enteras
# del repo que cada revision independiente hace con `mktemp -d` para mutar sin
# tocar el arbol vivo. Copiar esta BIEN; lo que faltaba es borrar despues.
# Nadie tenia alerta de disco: lo destapo un pytest que no pudo escribir su
# salida (ENOSPC). Un sistema que se entera asi, se entera tarde.
#
# FORMA DEL BORRADO (regla de oro de William, 9-ago-2026): base LITERAL /tmp,
# `-mindepth 1 -maxdepth 1`, y `-exec rm -rf {} +`. NO se usa `rm -rf $VAR/*`
# ni un glob sobre variable: esa forma congela la sesion aunque tenga guard.
#
# EL FILTRO DE TIEMPO ES LA SEGURIDAD, no un detalle: una arena de menos de
# EDAD_MIN minutos puede ser la de alguien que esta midiendo AHORA, y borrarsela
# le rompe la corrida a mitad. Por eso el umbral es alto y configurable.
set -euo pipefail

EDAD_MIN="${SEAL_ARENAS_EDAD_MIN:-120}"

antes=$(df --output=avail -BM / | tail -1 | tr -d ' M')
victimas=$(find /tmp -mindepth 1 -maxdepth 1 -name 'tmp.*' -type d -mmin "+${EDAD_MIN}" 2>/dev/null | wc -l)

find /tmp -mindepth 1 -maxdepth 1 -name 'tmp.*' -type d -mmin "+${EDAD_MIN}" -exec rm -rf {} + 2>/dev/null || true

despues=$(df --output=avail -BM / | tail -1 | tr -d ' M')
libre_pct=$(df --output=pcent / | tail -1 | tr -d ' %')

printf '[seal-tmp-arenas] borradas=%s edad_min=%s libre_antes=%sM libre_ahora=%sM uso=%s%%\n' \
  "$victimas" "$EDAD_MIN" "$antes" "$despues" "$libre_pct"

# Aviso cuando el disco sigue critico DESPUES de limpiar: significa que el
# problema no son las arenas y hay que mirar otra cosa.
if [ "$libre_pct" -ge 95 ]; then
  printf '[seal-tmp-arenas] AVISO: el disco sigue al %s%% despues de limpiar. Las arenas no son la causa principal.\n' "$libre_pct" >&2
  exit 0
fi
