#!/usr/bin/env bash
# Delivery por EFECTO de private-flag-gates-reads: el servicio VIVO, no el fuente.
#
# POR QUE ESTE SCRIPT Y NO EL PYTEST DEL MANIFIESTO: los ocho brazos de
# tests/test_nexus_private_flag_gates_reads_v1.py leen chat_server.py y
# afirman sobre CADENAS presentes en el fuente. Verifican que el codigo
# este ESCRITO, no que la puerta CIERRE. Un `pytest` verde ahi no es
# evidencia de entrega.
#
# Solo codigos HTTP: no se lee ni una linea de contenido de canal privado.
set -euo pipefail
BASE="${SEAL_CHAT_BASE:-http://127.0.0.1:8765}"
fail=0
# `|| true`: sin esto, `set -e` mata el script en el primer curl fallido y el
# fallo se reporta como rc=7 sin decir QUE brazo fallo. Un control que no se
# puede diagnosticar sirve la mitad. Con `|| true` alcanza: curl YA imprime
# "000" por `-w` cuando no conecta; un `echo "000"` extra lo duplicaba a "000000".
code() { curl -s -o /dev/null -w '%{http_code}' "$BASE/api/chat/messages?channel=$1&limit=1" || true; }

# ARM 1 — la BANDERA cierra por si sola: canales sin prefijo dm:/user:,
# marcados is_private, deben pedir sesion al anonimo.
for ch in "shadow%3Aalice" "shadow%3Aalice-v2" "fable-juez"; do
  c=$(code "$ch"); echo "  privado  $ch -> $c"
  [ "$c" = "401" ] || { echo "    ESPERADO 401"; fail=1; }
done

# ARM 2 — CONTROL: el arreglo no cerro el acceso legitimo. Sin este brazo,
# un fix que rompe todo se ve identico a uno correcto.
c=$(code "web_chat"); echo "  publico  web_chat -> $c"
[ "$c" = "200" ] || { echo "    ESPERADO 200"; fail=1; }

[ "$fail" = "0" ] && echo "delivery: passed" || { echo "delivery: FAILED"; exit 1; }
