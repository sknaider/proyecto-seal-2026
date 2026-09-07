#!/usr/bin/env bash
# Delivery por EFECTO de la firma de cuerpo del bridge Codex (#1666 H6).
#
# Qué prueba, y por qué los tests no alcanzan: la suite intercepta `urlopen` y mira el
# payload. Ninguna llega al servidor. Lo que hay que demostrar acá es lo único que
# importaba: que la firma sobrevive el viaje y queda PERSISTIDA en soul_v3.chat_messages,
# que es donde la leen los monitores y donde hoy faltaba.
#
# ARM2 ES OBLIGATORIO: manda por el MISMO camino sin la variable y comprueba que NO
# aparece firma. Sin él, el ARM1 sólo diría "hay un campo", no que la variable lo causa.
#
# ARM3 es el que nació de un mutante que sobrevivió: la etiqueta no puede viajar como
# credencial. Se verifica por PROPIEDAD (comparación), sin imprimir ningún token.
#
# Sin cleanup a propósito: `D /tmp 1777 root root 30d` en tmpfiles.d vacía /tmp al
# arrancar. Una limpieza manual acá sería innecesaria y es la forma que frena el candado.
set -euo pipefail
REPO=/home/dadito/IA/proyecto-seal
PY=/home/dadito/IA/seal-spark/.venv/bin/python3
cd "$REPO"
CORRIDA="del_$$_$(date +%s)"

envia() {  # $1 = valor de la variable ("" = sin ella) · $2 = sufijo de la clave
  SEAL_RUNTIME_INSTANCE="$1" "$PY" - "$2" "$CORRIDA" <<'PY'
import sys, os, json
if not os.environ.get("SEAL_RUNTIME_INSTANCE"): os.environ.pop("SEAL_RUNTIME_INSTANCE", None)
sys.path.insert(0, "messages")
import ada_codex_remote_bridge as B          # la funcion REAL, no una copia
r = B.post_message(to="ADA", message=f"[delivery #1666 H6 {sys.argv[2]}/{sys.argv[1]}] control automatico.",
                   channel="web_chat", idempotency_key=f"bridge_h6_{sys.argv[2]}_{sys.argv[1]}")
print(json.dumps(r))
PY
}

echo "== ARM1: CON la variable, la firma queda persistida en la base =="
envia "ADA_CODEX_BRIDGE" pos >/dev/null
echo "== ARM2 (control no vacuo): SIN la variable, no debe haber firma =="
envia "" neg >/dev/null
echo "== ARM4: la variable EN BLANCOS tampoco debe firmar =="
# Lo pidio NEXUS al revisar, y tenia razon: su mutante 'sin .strip()' sobrevivia y hacia
# firmar con espacios. Una firma que existe y no dice nada es PEOR que ausente, porque el
# ARM2 (--sin variable-> NULL--) deja de discriminar si alguien exporta la variable vacia.
envia "   " blanco >/dev/null

"$PY" - "$CORRIDA" <<'PY'
import sys, asyncio, asyncpg
sys.path.insert(0, "memory")
from config import settings
async def m():
    corrida = sys.argv[1]
    c = await asyncpg.connect(settings.pg_dsn)
    async def firma(suf):
        return await c.fetchval("""SELECT metadata->>'runtime_instance' FROM soul_v3.chat_messages
                                   WHERE metadata->>'idempotency_key'=$1""", f"bridge_h6_{corrida}_{suf}")
    async def clave(suf):
        return await c.fetchval("""SELECT metadata->>'session_token_hash' FROM soul_v3.chat_messages
                                   WHERE metadata->>'idempotency_key'=$1""", f"bridge_h6_{corrida}_{suf}")
    pos, neg, bl = await firma("pos"), await firma("neg"), await firma("blanco")
    print(f"  ARM1 firma persistida : {pos!r}")
    print(f"  ARM2 sin variable     : {neg!r}")
    print(f"  ARM4 variable en blancos: {bl!r}")
    hp = await clave("pos")
    print(f"  ARM3 la etiqueta NO es la credencial: hash de sesion presente y distinto = {bool(hp) and hp != pos}")
    await c.close()
    ok = pos == "ADA_CODEX_BRIDGE" and neg is None and bl is None and bool(hp) and hp != pos
    print("\nDELIVERY OK: 4/4 brazos" if ok else "\nDELIVERY FALLO")
    sys.exit(0 if ok else 1)
asyncio.run(m())
PY
