#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seal_channel_registry.py — Registro de canales-tema (cura #19, NEXUS).
================================================================================
Backend que la UI de canales de ALICE (TopicChannels.tsx) consume para listar:
  • COMPARTIDOS  → 'topic:<slug>'      (visibles a todos los miembros)
  • PRIVADOS     → 'user:<uid>:<slug>' (SOLO del usuario <uid> autenticado)

Capa DATOS/LÓGICA (mía). El ENDPOINT HTTP que la expone vive en chat_server
(transporte = carril JARVIS) y llama a estas funciones — mismo contrato que #17
(yo lógica/datos, él transporte). La baranda DB es rls_chat_messages_topics.sql.

SEGURIDAD: list_private_channels EXIGE user_id (del usuario AUTENTICADO de la
sesión). NUNCA derivar el user_id del input del cliente sin auth. Esta función es
la conveniencia; la RLS es el backstop que impide fuga aunque el caller se equivoque.
"""
from __future__ import annotations


def _slug(channel: str) -> str:
    # 'topic:gtl_pipeline' -> 'gtl_pipeline' ; 'user:7:paper' -> 'paper'
    parts = channel.split(":", 2)
    return parts[-1] if parts else channel


async def list_shared_channels(conn, limit: int = 200) -> list[dict]:
    """Canales compartidos por asunto: channel LIKE 'topic:%'. Visibles a todos."""
    # case-insensitive (consistente con la baranda RLS case-robusta, F1/F2 de FABLE)
    rows = await conn.fetch(
        """SELECT channel, count(*) AS n, max(created_at) AS last_at
           FROM soul_v3.chat_messages
           WHERE lower(channel) LIKE 'topic:%'
           GROUP BY channel ORDER BY last_at DESC NULLS LAST LIMIT $1""",
        limit,
    )
    return [{"channel": r["channel"], "slug": _slug(r["channel"]),
             "type": "shared", "messages": r["n"], "last_at": r["last_at"]} for r in rows]


async def list_private_channels(conn, user_id, limit: int = 200) -> list[dict]:
    """Canales privados del usuario AUTENTICADO: channel LIKE 'user:<uid>:%'.
    user_id NO puede venir crudo del cliente sin auth — es la identidad de sesión."""
    if user_id is None or str(user_id).strip() == "":
        return []                     # sin identidad → nada privado (fail-safe)
    prefix = f"user:{user_id}:".lower()
    # case-insensitive: 'User:7:x' también cuenta como privado de 7 (no se escapa)
    rows = await conn.fetch(
        """SELECT channel, count(*) AS n, max(created_at) AS last_at
           FROM soul_v3.chat_messages
           WHERE lower(channel) LIKE $1 || '%'
           GROUP BY channel ORDER BY last_at DESC NULLS LAST LIMIT $2""",
        prefix, limit,
    )
    return [{"channel": r["channel"], "slug": _slug(r["channel"]),
             "type": "private", "messages": r["n"], "last_at": r["last_at"]} for r in rows]


async def list_channels_for(conn, user_id=None) -> dict:
    """Lo que la UI pinta: compartidos (todos) + privados (solo del usuario)."""
    return {
        "shared":  await list_shared_channels(conn),
        "private": await list_private_channels(conn, user_id),
    }


# ── self-test POR EFECTO (read-only contra la BD viva; no escribe nada) ────────
if __name__ == "__main__":
    import asyncio, importlib.util, os, sys
    spec = importlib.util.spec_from_file_location(
        "fdp", os.path.join(os.path.dirname(__file__), "fable_dm_poller.py"))
    m = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(m)
    except Exception:
        pass
    DSN = getattr(m, "DSN", None) or getattr(m, "PG_DSN", None)
    import asyncpg

    async def main():
        if not DSN:
            print("no DSN — skip"); return 0
        c = await asyncpg.connect(DSN)
        ok = True
        try:
            res = await list_channels_for(c, user_id=7)
            # 1) shape correcto
            t1 = set(res) == {"shared", "private"} and isinstance(res["shared"], list)
            ok &= t1; print("1 shape {shared,private}:", "ok" if t1 else "FAIL")
            # 2) ningún 'shared' es privado (no fuga de user:* en compartidos)
            t2 = all(not x["channel"].startswith("user:") for x in res["shared"])
            ok &= t2; print("2 shared no contiene user:*:", "ok" if t2 else "FAIL")
            # 3) sin user_id → privados vacío (fail-safe)
            empty = await list_private_channels(c, None)
            t3 = (empty == []); ok &= t3; print("3 sin user_id -> privados []:", "ok" if t3 else "FAIL")
            # 4) privados de un user_id SOLO contienen su prefijo
            p7 = await list_private_channels(c, 7)
            t4 = all(x["channel"].startswith("user:7:") for x in p7)
            ok &= t4; print(f"4 privados de 7 solo user:7:* ({len(p7)} hallados):", "ok" if t4 else "FAIL")
            # 5) clasificación: shared son todos topic:*
            t5 = all(x["channel"].startswith("topic:") for x in res["shared"])
            ok &= t5; print(f"5 shared todos topic:* ({len(res['shared'])} hallados):", "ok" if t5 else "FAIL")
            print("\n", "✅ #19 REGISTRO de canales OK por efecto (read-only; clasifica y aísla privados)"
                  if ok else "⚠️ revisar registro")
        finally:
            await c.close()
        return 0 if ok else 1
    sys.exit(asyncio.run(main()))
