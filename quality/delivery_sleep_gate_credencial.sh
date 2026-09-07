#!/usr/bin/env bash
# Delivery por EFECTO del arreglo de credencial de la consolidación nocturna.
#
# Qué prueba, y por qué la suite no alcanza: los tests verifican que el DSN sale de
# `settings` y que no hay contraseña escrita en el código. Ninguno CONECTA. Lo que hay
# que demostrar acá es lo único que importaba: que el programa que fallaba todas las
# noches ahora corre de punta a punta contra la base real.
#
# ARM2 ES OBLIGATORIO Y ES EL QUE DA SENTIDO AL ARM1: corre una copia con el DSN viejo
# hardcodeado y comprueba que ESA sí falla. Sin él, el ARM1 sólo diría "el script anda",
# y no que el ARM REGLO sea la causa. Es la diferencia entre medir que funciona y medir
# que el cambio fue lo que lo hizo funcionar.
set -euo pipefail

REPO=/home/dadito/IA/proyecto-seal
PY=/home/dadito/IA/seal-spark/.venv/bin/python3
# Sin cleanup a propósito: `D /tmp 1777 root root 30d` en tmpfiles.d vacía /tmp al
# arrancar y barre lo viejo con systemd-tmpfiles-clean.timer. Una limpieza manual acá
# sería innecesaria y es justo la forma que frena el candado de destructivos.
TMP=$(mktemp -d /tmp/delivery_sleep_gate_XXXXXX)
cd "$REPO"

echo "== ARM1: el sujeto REAL corre de punta a punta contra la base =="
if "$PY" "$REPO/memory/sleep_gate_cron.py" --dry-run 2>&1 | tail -3 | grep -q "SleepGate complete"; then
  echo "ARM1 OK: termina con 'SleepGate complete' (antes moria con InvalidPasswordError)"
else
  echo "ARM1 FALLO: el script no llego al final"
  exit 1
fi

echo
echo "== ARM2 (control no vacuo): la MISMA lógica con el DSN viejo hardcodeado DEBE fallar =="
# Copia del sujeto con la credencial rotada de vuelta. Si esto NO fallara, el ARM1 no
# probaria nada: significaria que el script anda con cualquier credencial.
sed 's|^DB_URL = settings.pg_dsn$|DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"|' \
    "$REPO/memory/sleep_gate_cron.py" > "$TMP/con_credencial_vieja.py"
cp "$REPO/memory/config.py" "$TMP/" 2>/dev/null || true
# La salida se captura ANTES de buscar en ella. Con `set -o pipefail`, hacer
# `python ... | grep -q` devuelve el codigo de PYTHON --que aca sale 1 a proposito-- y la
# condicion daba falso aunque el grep encontrara la cadena. Mi primera version reporto
# "ARM2 FALLO" con el sistema comportandose exactamente como debia.
SALIDA2=$("$PY" "$TMP/con_credencial_vieja.py" --dry-run 2>&1 || true)
if printf '%s' "$SALIDA2" | grep -q "InvalidPasswordError"; then
  echo "ARM2 OK: la credencial vieja sigue muerta -> el arreglo ES la causa del ARM1"
else
  echo "ARM2 FALLO: la credencial vieja no fallo; el ARM1 no prueba nada"
  exit 1
fi

echo
echo "DELIVERY OK: 2/2 brazos"
