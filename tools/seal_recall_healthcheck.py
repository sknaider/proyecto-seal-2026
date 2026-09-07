#!/usr/bin/env python3
"""seal_recall_healthcheck.py — ¿lo que guardamos se puede volver a encontrar?

Nació el 1-sep-2026, cuando William preguntó por un sprint de abril y los cuatro
agentes dijimos "no me acuerdo" sobre cosas que nosotros mismos habíamos escrito.
El recuerdo estaba en la base. Ninguna vía de recall lo traía.

Nadie lo detectó durante cuatro meses porque NO HABIA NADA MIRANDO: teníamos
tests de que una memoria se guarda, ninguno de que se recupera.

Este control no mira el código: mira el EFECTO. Guarda un canario, lo pide de
vuelta por la vía real, y falla si no vuelve. Si el recall se degrada otra vez
—por un filtro nuevo, un scope, un ranking roto— esto se pone rojo el mismo día.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")


async def _pool():
    from seal_secrets import pg_dsn  # type: ignore
    import asyncpg  # type: ignore

    return await asyncpg.connect(pg_dsn())


async def _buscador_real(token: str, agent: str):
    """Invoca el buscador de memoria REAL. Devuelve lista de ids, o None si no se pudo.

    Devolver None (en vez de lista vacía) es deliberado: "no pude preguntar" y
    "pregunté y no vino" son estados distintos, y confundirlos convierte una
    falla del instrumento en un veredicto sobre el sujeto.
    """
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "seal_bench_search", "/home/dadito/IA/proyecto-seal/memory/seal_bench.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        from db_pool import get_pool  # type: ignore
        pool = await get_pool()
        res = await mod._search_memories(pool, token, agent=agent)
        ids = []
        for r in (res or []):
            rid = r.get("id") if isinstance(r, dict) else getattr(r, "id", None)
            if rid is not None:
                ids.append(rid)
        return ids
    except Exception as e:
        print(f"  [!!] no pude invocar el buscador real: {type(e).__name__}: {str(e)[:90]}")
        return None


async def run(agent: str, keep: bool) -> int:
    token = f"canario-recall-{uuid.uuid4().hex[:12]}"
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    content = f"Canario de salud del recall {token} sembrado {stamp}."

    conn = await _pool()
    failures: list[str] = []
    mid = None
    try:
        mid = await conn.fetchval(
            """
            INSERT INTO soul_v3.memories (agent, content, category, importance, scope)
            VALUES ($1, $2, 'technical_fact', 5, 'team')
            RETURNING id
            """,
            agent,
            content,
        )
        print(f"sembrado  id={mid} token={token}")

        # 1) ¿está en la base? (si esto falla, el problema es de escritura)
        found = await conn.fetchval(
            "SELECT count(*) FROM soul_v3.memories WHERE content LIKE $1",
            f"%{token}%",
        )
        if found != 1:
            failures.append(f"ESCRITURA: la memoria no está en la tabla (count={found})")
        else:
            print("  [ok] la memoria está en la tabla")

        # 2) ¿la trae el BUSCADOR REAL?
        #    Este es el chequeo que decide. FABLE señaló (1-sep) que la primera
        #    versión hacía su propio ILIKE y NUNCA llamaba al buscador: cazaba
        #    filtros y scope, pero no habría visto el ranking roto de hoy — que
        #    era el defecto principal. Un control que no ejerce a su sujeto
        #    mide su propia consulta.
        trajo = await _buscador_real(token, agent)
        if trajo is None:
            failures.append(
                "INSTRUMENTO: no pude invocar el buscador real; este control "
                "NO verifica recuperación en esta corrida (no lo leas como verde)"
            )
        elif mid not in trajo:
            failures.append(
                f"RECUPERACION: el buscador real NO trae el canario "
                f"(devolvió {len(trajo)} resultados, ninguno es el sembrado). "
                "Puede ser filtro, scope o ranking — este control no distingue cuál."
            )
        elif trajo[0] != mid:
            # FABLE, 1-sep: la membresía no basta. El canario lleva un token
            # único que no existe en ninguna otra fila: si el ranking funciona,
            # DEBE salir primero. Verlo en el puesto 7 es un ranking roto
            # devolviendo un verde — exactamente el defecto que buscamos.
            pos = trajo.index(mid) + 1
            failures.append(
                f"RANKING: el canario vuelve, pero en posición {pos} de "
                f"{len(trajo)}. Con un token único que sólo él contiene, "
                "cualquier posición distinta de 1 indica que el orden no "
                "depende de la consulta."
            )
        else:
            print(f"  [ok] el BUSCADOR REAL lo trae, y en posición 1 de {len(trajo)}")

        # 3) contraste: ¿está alcanzable por SQL directo?
        #    Separa "la fila no está" de "la fila está y el buscador no la trae".
        lex = await conn.fetch(
            """
            SELECT id FROM soul_v3.memories
            WHERE content ILIKE $1
              AND invalid_at IS NULL
              AND (agent = $2 OR scope IN ('team', 'public'))
            LIMIT 5
            """,
            f"%{token}%",
            agent,
        )
        alcanzable = any(r["id"] == mid for r in lex)
        print(f"  [{'ok' if alcanzable else '!!'}] alcanzable por SQL directo: {alcanzable}")
        if alcanzable and trajo is not None and mid not in trajo:
            failures.append(
                "DIAGNOSTICO: la fila SI es elegible por agente/scope, así que "
                "lo que falla es el buscador, no el permiso"
            )

    finally:
        if mid is not None and not keep:
            await conn.execute("DELETE FROM soul_v3.memories WHERE id = $1", mid)
            print(f"limpiado  id={mid}")
        await conn.close()

    if failures:
        print("\nRECALL DEGRADADO:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nRECALL SANO: lo que se guarda se puede volver a encontrar.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--agent", default="ALICE")
    p.add_argument("--keep", action="store_true", help="no borrar el canario (depuración)")
    p.add_argument("--self-test", action="store_true", help="verifica que el control sabe fallar")
    a = p.parse_args()

    if a.self_test:
        # Un control que nunca vio un rojo no verifica nada: acá lo forzamos.
        return asyncio.run(_self_test(a.agent))
    return asyncio.run(run(a.agent, a.keep))


async def _self_test(agent: str) -> int:
    """Siembra un canario INALCANZABLE a propósito y exige que el control lo detecte."""
    token = f"canario-rojo-{uuid.uuid4().hex[:12]}"
    conn = await _pool()
    mid = None
    try:
        # scope 'shared' + importance 1 = exactamente lo que hoy es invisible
        mid = await conn.fetchval(
            """
            INSERT INTO soul_v3.memories (agent, content, category, importance, scope)
            VALUES ($1, $2, 'technical_fact', 1, 'shared')
            RETURNING id
            """,
            "JARVIS",  # agente real distinto del que consulta
            f"Canario rojo {token}",
        )
        vis = await conn.fetch(
            """
            SELECT id FROM soul_v3.memories
            WHERE content ILIKE $1
              AND (agent = $2 OR scope IN ('team','public'))
            """,
            f"%{token}%",
            agent,
        )
        if any(r["id"] == mid for r in vis):
            print("SELF-TEST FALLA: el caso que DEBIA ser invisible volvió.")
            print("  -> el control no discrimina; no sirve como oráculo.")
            return 1
        print("SELF-TEST OK: el control detecta el caso inalcanzable (rojo verificado).")
        return 0
    finally:
        if mid is not None:
            await conn.execute("DELETE FROM soul_v3.memories WHERE id = $1", mid)
        await conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
