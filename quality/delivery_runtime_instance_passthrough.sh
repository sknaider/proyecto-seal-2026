#!/usr/bin/env bash
# Delivery por EFECTO de runtime-instance-passthrough.
#
# POR QUE EXISTE: ADA reviso los seis brazos del manifiesto y encontro que son
# `assert "<literal>" in src`. Un mutante que deja los literales intactos y
# evita PERSISTIR runtime_instance los pasa los seis. El unico modo de matarlo
# es mandar un mensaje de verdad y leer la fila que quedo.
#
# ARM 1 (positivo)  con SEAL_RUNTIME_INSTANCE -> la fila trae la etiqueta
# ARM 2 (negativo)  SIN la variable            -> la fila NO trae la clave
#
# El ARM 2 no es decorativo: sin el, un servidor que escriba una etiqueta fija
# --o que copie el metadata entero del body, que es el defecto que este cambio
# vino a evitar-- pasaria el ARM 1 igual.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${SEAL_PY:-/home/dadito/IA/seal-spark/.venv/bin/python3}"
SUF="$(date +%s)-$$"
# La identidad NO se hardcodea: un revisor que corra esto como NEXUS no esta
# revisando de forma independiente, esta usando la identidad del owner.
# Lo señalo ADA al ir a re-firmar. Cada quien corre con la suya.
AG="${SEAL_DELIVERY_AGENT:-${SEAL_AGENT:-NEXUS}}"
fail=0

leer_clave() {  # $1 = idempotency_key ; imprime el runtime_instance o "<ausente>"
  set -a; . /home/dadito/.config/seal/chat_db_runtime.env; set +a
  "$VENV" - "$1" <<'PY'
import os, sys, json, asyncio, asyncpg
async def main():
    c = await asyncpg.connect(os.environ["SEAL_PG_DSN"])
    r = await c.fetchrow(
        "SELECT metadata FROM soul_v3.chat_messages WHERE metadata->>'idempotency_key' = $1",
        sys.argv[1])
    await c.close()
    if not r:
        print("<sin fila>"); return
    md = r["metadata"] if isinstance(r["metadata"], dict) else json.loads(r["metadata"])
    print(md.get("runtime_instance", "<ausente>"))
asyncio.run(main())
PY
}

K1="rt-instance-arm1-$AG-$SUF"
SEAL_RUNTIME_INSTANCE="${AG}_CANARIO" "$VENV" scripts/seal_send.py "$AG" "$AG" \
  "canario runtime_instance ARM1 ($K1)" --channel web_chat --type dm \
  --idempotency-key "$K1" >/dev/null
sleep 1
v1="$(leer_clave "$K1" | tail -1)"
echo "  ARM1 con SEAL_RUNTIME_INSTANCE (agente $AG) -> $v1"
[ "$v1" = "${AG}_CANARIO" ] || { echo "    NO coincide con lo esperado"; fail=1; }

K2="rt-instance-arm2-$AG-$SUF"
env -u SEAL_RUNTIME_INSTANCE "$VENV" scripts/seal_send.py "$AG" "$AG" \
  "canario runtime_instance ARM2 ($K2)" --channel web_chat --type dm \
  --idempotency-key "$K2" >/dev/null
sleep 1
v2="$(leer_clave "$K2" | tail -1)"
echo "  ARM2 sin la variable           -> $v2"
[ "$v2" = "<ausente>" ] || { echo "    NO coincide con lo esperado"; fail=1; }

K3="rt-instance-arm3-$AG-$SUF"
# ARM 3 -- la propiedad principal: SOLO esa clave del cliente llega a la fila.
# Lo levanto ADA revisando: el mutante `metadata.update(body["metadata"])` pasa
# ARM1 y ARM2 los dos. Vive en un helper aparte porque necesita emitir el body a
# mano --seal_send.py construye literalmente {"runtime_instance": _inst} y no
# puede expresar el caso bajo prueba--.
set -a; . /home/dadito/.config/seal/chat_db_runtime.env; set +a
"$VENV" quality/delivery_runtime_instance_arm3.py "$AG" "$K3" || fail=1

[ "$fail" = "0" ] && echo "delivery: passed" || { echo "delivery: FAILED"; exit 1; }
