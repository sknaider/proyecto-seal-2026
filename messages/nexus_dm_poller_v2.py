#!/usr/bin/env python3
"""Poller de DM de NEXUS — REEMPLAZO NUEVO, no una restauracion.

QUE ES Y QUE NO ES (NEXUS, 8-sep-2026, carril autorizado por JARVIS):
`messages/nexus_dm_poller.py` se perdio con el borrado del 7-sep y corre desde
entonces SOLO en memoria (PID 32819). Su codigo no esta en disco, ni en git, ni
en el respaldo, y Python no deja el archivo abierto: **no hay forma de
recuperarlo**. Este archivo NO lo reconstruye: es uno nuevo, con nombre propio,
escrito mirando a sus hermanos. Ponerle el nombre del original haria que todo lo
que lo referencia afirme algo falso.

ALCANCE: replica el COMPORTAMIENTO observable del original (los DM de William al
inbox de NEXUS), no lo mejora. Un reemplazo que ademas redisena no se puede
intercambiar con confianza. Las mejoras posibles quedan anotadas abajo, sin
aplicar.

TRES DEFECTOS DE LOS HERMANOS QUE ESTE NO HEREDA:

1. **DSN cableado en el archivo.** `ada_dm_poller.py` lleva la cadena de
   conexion con la contrasena adentro. Aca la credencial se lee con
   `tools/seal_observer_credencial.py`.
2. **Contenido del DM en el log del servicio.** El hermano hace
   `print(f"... {r['content'][:80]}")`: eso manda texto de correspondencia
   privada al journal, que lee cualquiera con acceso al diario. Aca se registra
   el ID, el remitente y el LARGO — nunca el contenido.
3. **Sin forma de probarlo.** La conexion estaba dentro del bucle. Aca la busqueda
   es un parametro inyectable, asi que los tests corren sin base de datos.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from datetime import datetime, timezone

RAIZ = pathlib.Path(__file__).resolve().parents[1]
INBOX = RAIZ / "messages" / "nexus_inbox.jsonl"
CURSOR = pathlib.Path("/tmp/nexus_dm_v2_last_ts.txt")
CANAL = "dm:nexus:william"
INTERVALO = 2

CONSULTA = """SELECT sender_name, content, created_at, channel
              FROM chat_messages
              WHERE channel = $1 AND created_at > $2
                AND LOWER(sender_name) = 'william'
              ORDER BY created_at ASC"""


def dsn() -> str:
    """Credencial desde archivo; nunca cableada en el codigo."""
    sys.path.insert(0, str(RAIZ / "tools"))
    from seal_observer_credencial import leer_dsn_del_archivo
    return leer_dsn_del_archivo()


def lee_cursor(ruta: pathlib.Path = CURSOR) -> datetime:
    """Ultimo instante procesado.

    Ante un cursor ilegible arranca en AHORA, no en el principio: reprocesar
    todo volcaria la historia entera de DMs al inbox de golpe. Es la decision
    opuesta a la del dedup de alertas —alli el silencio es el riesgo; aca lo es
    la inundacion— y por eso se dice explicito.
    """
    try:
        return datetime.fromisoformat(ruta.read_text().strip()).astimezone(timezone.utc)
    except (OSError, ValueError):
        return datetime.now(timezone.utc)


def guarda_cursor(ts: datetime, ruta: pathlib.Path = CURSOR) -> None:
    ruta.write_text(ts.astimezone(timezone.utc).isoformat(), encoding="utf-8")


def a_linea(fila: dict) -> dict:
    """Forma EXACTA que espera `seal_dm_monitor.sh` aguas abajo."""
    creado = fila["created_at"].astimezone(timezone.utc)
    return {
        "id": f"dm_{int(creado.timestamp() * 1e9)}",
        "from": fila["sender_name"],
        "to": "NEXUS",
        "timestamp": creado.isoformat(),
        "type": "dm",
        "message": fila["content"],
        "channel": fila["channel"],
    }


def escribe(filas, inbox: pathlib.Path = INBOX) -> int:
    """Anexa al inbox. Devuelve cuantas escribio. NO registra el contenido."""
    n = 0
    with open(inbox, "a", encoding="utf-8") as fh:
        for f in filas:
            fh.write(json.dumps(a_linea(f), ensure_ascii=False) + "\n")
            n += 1
    return n


def resumen_sin_contenido(fila: dict) -> str:
    """Lo unico que se permite ir al log: quien y cuanto, jamas que dijo."""
    return (f"[NEXUS-DM] de={fila['sender_name']} "
            f"largo={len(fila['content'] or '')} canal={fila['channel']}")


async def una_pasada(buscar, inbox: pathlib.Path = INBOX,
                     cursor: pathlib.Path = CURSOR) -> int:
    """Una iteracion. `buscar(canal, desde)` es inyectable: los tests no usan base."""
    desde = lee_cursor(cursor)
    filas = await buscar(CANAL, desde)
    if not filas:
        return 0
    n = escribe(filas, inbox)
    for f in filas:
        print(resumen_sin_contenido(f), flush=True)
    guarda_cursor(max(f["created_at"] for f in filas).astimezone(timezone.utc), cursor)
    return n


async def bucle(buscar) -> None:  # pragma: no cover - bucle infinito
    while True:
        try:
            await una_pasada(buscar)
        except Exception as exc:
            print(f"[NEXUS-DM] error: {type(exc).__name__}", flush=True)
        await asyncio.sleep(INTERVALO)


async def _main() -> None:  # pragma: no cover
    import asyncpg
    conn = await asyncpg.connect(dsn())

    async def buscar(canal, desde):
        return [dict(r) for r in await conn.fetch(CONSULTA, canal, desde)]

    print(f"[NEXUS-DM] v2 iniciado, canal={CANAL}", flush=True)
    await bucle(buscar)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_main())
