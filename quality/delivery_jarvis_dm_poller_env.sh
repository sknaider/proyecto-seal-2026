#!/usr/bin/env bash
# Delivery por EFECTO del poller de DMs de JARVIS leyendo la credencial del entorno (JARVIS, 5-sep-2026).
#
# Qué prueba, y por qué no alcanza la suite: pytest importa el módulo con variables controladas.
# Lo que hay que demostrar acá es el cable real: la unidad systemd carga EnvironmentFile ->
# la variable existe y ABRE la base con el rol del poller -> el proceso vivo está conectado con
# ese rol. Y el negativo: sin la variable el script muere nombrándola (no cae a una credencial vieja).
#   ARM1  la unidad declara EnvironmentFile y SEAL_POLLER_DSN abre como login_poller_jarvis (read-only)
#   ARM2  el script sin SEAL_POLLER_DSN termina con codigo != 0 y nombra la variable (fail-closed)
#   ARM3  la unidad está active y pg_stat_activity tiene un backend login_poller_jarvis
#         posterior al arranque de la unidad (el proceso REAL conectó con el rol del entorno)
set -euo pipefail

REPO=/home/dadito/IA/proyecto-seal
PY=/home/dadito/IA/seal-spark/.venv/bin/python3
UNIDAD=seal-jarvis-dm-poller.service

echo "== ARM1: EnvironmentFile de la unidad -> SEAL_POLLER_DSN abre con el rol del poller =="
ENVS=$(systemctl --user show "$UNIDAD" -p EnvironmentFiles --value | sed 's/ (ignore_errors=[a-z]*)//g')
[ -n "$ENVS" ] || { echo "ARM1 FALLO: la unidad no declara EnvironmentFile"; exit 1; }
ROL=$( set -a; for f in $ENVS; do . "$f"; done; set +a; "$PY" - <<'PY'
import os, asyncio, asyncpg
dsn = os.environ.get("SEAL_POLLER_DSN", "").strip()
assert dsn, "SEAL_POLLER_DSN vacia en los EnvironmentFile"
async def m():
    c = await asyncpg.connect(dsn); print(await c.fetchval("select current_user")); await c.close()
asyncio.run(m())
PY
)
[ "$ROL" = "login_poller_jarvis" ] || { echo "ARM1 FALLO: abrio como '$ROL'"; exit 1; }
echo "ARM1 OK: SEAL_POLLER_DSN abre como $ROL"

echo "== ARM2: sin la variable, el script muere nombrandola =="
set +e
SALIDA=$(env -u SEAL_POLLER_DSN "$PY" "$REPO/messages/jarvis_dm_poller.py" 2>&1); RC=$?
set -e
[ "$RC" -ne 0 ] || { echo "ARM2 FALLO: arranco sin credencial (rc=0)"; exit 1; }
echo "$SALIDA" | grep -q "SEAL_POLLER_DSN" || { echo "ARM2 FALLO: no nombra la variable: $SALIDA"; exit 1; }
echo "ARM2 OK: rc=$RC y nombra SEAL_POLLER_DSN"

echo "== ARM3: el proceso REAL esta conectado con el rol del entorno =="
[ "$(systemctl --user is-active "$UNIDAD")" = "active" ] || { echo "ARM3 FALLO: unidad no activa"; exit 1; }
INICIO=$(systemctl --user show "$UNIDAD" -p ExecMainStartTimestamp --value)
cd "$REPO/memory"
"$PY" - "$INICIO" <<'PY'
import sys, asyncio, asyncpg
from datetime import datetime
from db import DB_URL
inicio_unidad = sys.argv[1]
async def m():
    c = await asyncpg.connect(DB_URL)
    filas = await c.fetch("select backend_start from pg_stat_activity where usename='login_poller_jarvis'")
    assert filas, "ningun backend login_poller_jarvis en pg_stat_activity"
    # el backend del poller vivo debe ser posterior al arranque de la unidad
    inicio = datetime.strptime(inicio_unidad, "%a %Y-%m-%d %H:%M:%S %Z").astimezone()
    ok = [f for f in filas if f["backend_start"] >= inicio.replace(second=0)]
    assert ok, f"backends {[str(f['backend_start']) for f in filas]} anteriores al arranque {inicio}"
    # las dos horas en el MISMO huso (local): esta madrugada cuatro agentes confundieron UTC con Lima
    print(f"ARM3 OK: backend login_poller_jarvis desde {ok[0]['backend_start'].astimezone():%H:%M:%S}, "
          f"unidad arrancada {inicio:%H:%M:%S} (hora local)")
asyncio.run(m())
PY

echo
echo "DELIVERY OK: 3/3 brazos"
