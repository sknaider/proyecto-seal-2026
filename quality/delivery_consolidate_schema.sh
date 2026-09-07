#!/usr/bin/env bash
# Delivery por EFECTO del contrato de esquema de consolidate.py (JARVIS, 5-sep-2026).
#
# Qué prueba, y por qué no alcanza la suite: pytest usa una conexión falsa. Lo que hay que
# demostrar acá es que el contrato coincide con la base VIVA que el daemon toca a las 03:00:
#   ARM1  validate_live_schema contra la DB real -> pasa (el contrato es el esquema de verdad)
#   ARM2  el mismo validador con una columna inventada en el contrato -> revienta nombrándola
#         (control no vacuo: un validador que aceptara todo pasaría el ARM1)
#   ARM3  la consulta exacta del paso 1 se PREPARA en la DB real (Postgres valida columnas al
#         preparar, sin ejecutar): es el sitio donde falló el 4-sep.
# No escribe nada en la DB: ninguna arma ejecuta la consolidación.
set -euo pipefail

REPO=/home/dadito/IA/proyecto-seal
PY=/home/dadito/IA/seal-spark/.venv/bin/python3

cd "$REPO/memory"
"$PY" - <<'PY'
import asyncio, re, sys
import asyncpg
import consolidate as c
from db import DB_URL

async def main():
    conn = await asyncpg.connect(DB_URL)
    print("== ARM1: contrato contra la DB viva ==")
    await c.validate_live_schema(conn)
    print("ARM1 OK: REQUIRED_COLUMNS coincide con el esquema vivo")

    print("== ARM2 (control no vacuo): columna inventada en el contrato ==")
    original = dict(c.REQUIRED_COLUMNS)
    c.REQUIRED_COLUMNS["event_log"] = original["event_log"] + ("columna_que_no_existe",)
    try:
        await c.validate_live_schema(conn)
        print("ARM2 FALLO: el validador acepto una columna inexistente")
        sys.exit(1)
    except RuntimeError as exc:
        assert "event_log.columna_que_no_existe" in str(exc), str(exc)
        print("ARM2 OK: revento nombrando event_log.columna_que_no_existe")
    finally:
        c.REQUIRED_COLUMNS.clear(); c.REQUIRED_COLUMNS.update(original)

    print("== ARM3: la consulta del paso 1 se prepara en la DB real ==")
    src = open("consolidate.py", encoding="utf-8").read()
    sql = re.search(r'"""(SELECT created_at, event_type, content\s+FROM event_log.*?)"""', src, re.S).group(1)
    st = await conn.prepare(sql)
    cols = [a.name for a in st.get_attributes()]
    assert cols == ["created_at", "event_type", "content"], cols
    print(f"ARM3 OK: prepare acepto la consulta, columnas {cols}")
    await conn.close()

asyncio.run(main())
PY

echo
echo "DELIVERY OK: 3/3 brazos"
