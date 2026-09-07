#!/usr/bin/env python3
"""¿Mi lección llegó a SOUL? Respuesta BINARIA por sha256.

POR QUE EXISTE: el 4-sep el ingestor de lecciones estuvo 42 h parado y nadie se
enteró; lo descubrió ADA por casualidad al ir a mirar. Escribir una lección y no
poder confirmar que existe es escribir al vacío.

POR QUE POR sha256 Y NO POR NOMBRE NI POR TEXTO: es la MISMA clave con la que el
ingestor decide idempotencia, así que un `no está` acá significa `el ingestor no
lo vio`, no `busqué mal`. El 4-sep, ADA falló tres veces con la sonda equivocada
--el slug que el ingestor no guarda, una frase sin el acento, un `^author:`
anclado contra un campo indentado-- y las tres dieron "no está" con la fila
adentro. Y yo concluí "no puedo verificarlo desde mi asiento" tras consultar con
DOS roles que no ven la tabla, teniendo el DSN bueno a mano.

EL DSN IMPORTA Y ES EL DEFECTO QUE ESTE SCRIPT EVITA: `login_bus` ve 0 filas de
soul_v3.memories y `svc_soul_nerves_*` recibe permission denied. El rol que la ve
es `mcp_runtime_<agente>`, cuyo DSN vive en ~/.config/seal/mcp_agents/<ag>.dsn.
Un cero leído con el rol equivocado se parece exactamente a una ausencia.

Uso:
    tools/seal_leccion_llego.py                 # todas las del directorio
    tools/seal_leccion_llego.py '*20260904*'    # un patrón
    SEAL_AGENT=ADA tools/seal_leccion_llego.py  # con otra identidad
"""
import asyncio
import hashlib
import os
import pathlib
import sys

import asyncpg

MEM = pathlib.Path(os.environ.get(
    "SEAL_CLAUDE_MEMORY_DIR",
    "/home/dadito/.claude/projects/-home-dadito-IA-proyecto-seal/memory"))
AGENTE = (os.environ.get("SEAL_AGENT") or "NEXUS").lower()
DSN_FILE = pathlib.Path(os.environ.get(
    "SEAL_MEMORY_DSN_FILE", f"/home/dadito/.config/seal/mcp_agents/{AGENTE}.dsn"))


async def main() -> int:
    patron = sys.argv[1] if len(sys.argv) > 1 else "*.md"
    # MEMORY.md y sus respaldos son el INDICE, no lecciones: el ingestor no
    # los sube y contarlos como "faltantes" seria un falso positivo fijo, de
    # esos que entrenan a ignorar la salida del verificador.
    archivos = sorted(p for p in MEM.glob(patron)
                      if not p.name.startswith(("MEMORY.md", "MEMORY_")))
    if not archivos:
        print(f"sin archivos que coincidan con {patron!r} en {MEM}")
        return 2
    if not DSN_FILE.is_file():
        print(f"no encuentro el DSN de {AGENTE.upper()} en {DSN_FILE}")
        return 2

    conn = await asyncpg.connect(DSN_FILE.read_text().strip())
    try:
        # CONTROL DE NO-VACUIDAD: si el rol no ve NADA, un "no está" en todo el
        # lote no dice nada del ingestor, dice que estoy mirando con el rol
        # equivocado. Es el error exacto que este script existe para no repetir.
        visibles = await conn.fetchval("SELECT count(*) FROM soul_v3.memories")
        if not visibles:
            print(f"rol {await conn.fetchval('SELECT current_user')} no ve NINGUNA fila: "
                  "la respuesta seria sobre mi permiso, no sobre la ingesta")
            return 2

        # UNA sola consulta con `= ANY`, no una por archivo: con ~900 lecciones,
        # el bucle de consultas tardaba mas de dos minutos y el chequeo dejaba de
        # usarse. Un verificador lento es un verificador que nadie corre.
        por_sha = {hashlib.sha256(f.read_bytes()).hexdigest(): f.name for f in archivos}
        filas = await conn.fetch(
            "SELECT metadata->>'sha256' AS sha FROM soul_v3.memories "
            "WHERE metadata->>'sha256' = ANY($1::text[])", list(por_sha))
        presentes = {r["sha"] for r in filas}
        faltan = sorted(n for sha, n in por_sha.items() if sha not in presentes)
    finally:
        await conn.close()

    print(f"  archivos: {len(archivos)} · en SOUL: {len(archivos) - len(faltan)} · faltan: {len(faltan)}")
    for n in faltan:
        print(f"    FALTA  {n}")
    return 1 if faltan else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
