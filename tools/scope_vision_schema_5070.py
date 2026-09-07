"""Read-only scoping del schema de visión del 5070 — informa retención + RLS (NEXUS).
NO escribe nada. Lista tablas vision_*, conteos, columnas PII/purgables, y rango de edad.
Env: SOUL_VISION_DSN (o SOUL_MEMORY_DSN)."""
from __future__ import annotations
import os, sys, asyncio, re

_IDENT = re.compile(r"^vision[a-z0-9_]*$")  # valida identificador (no inyección; igual viene de information_schema)

DSN = os.environ.get("SOUL_VISION_DSN") or os.environ.get("SOUL_MEMORY_DSN") or ""


async def main():
    import asyncpg
    conn = await asyncpg.connect(DSN, timeout=10)
    try:
        tabs = await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='soul_v3' AND table_name LIKE 'vision%' ORDER BY table_name")
        print("TABLAS vision_* en soul_v3:")
        for t in tabs:
            name = t["table_name"]
            if not _IDENT.match(name):
                print(f"\n=== {name}: (identificador no validado, salto) ==="); continue
            try:
                n = await conn.fetchval(f"SELECT count(*) FROM soul_v3.{name}")  # name validado por _IDENT
            except Exception as e:
                n = f"err:{type(e).__name__}"
            print(f"\n=== {name}: {n} filas ===")
            cols = await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='soul_v3' AND table_name=$1 ORDER BY ordinal_position", name)
            # marcar columnas PII / purgables / temporales
            pii = {"reid_embedding", "embedding", "face_token", "frame_hash"}
            purge = {"clip_ref", "mask_ref", "snapshot", "snapshot_ref", "frame_ref", "image", "thumb"}
            tcol = {"ts_wall", "created_at", "ts", "event_time"}
            for c in cols:
                tag = ""
                if c["column_name"] in pii: tag = " ← PII"
                elif c["column_name"] in purge: tag = " ← purgable (blob/ref)"
                elif c["column_name"] in tcol: tag = " ← temporal (edad)"
                print(f"   {c['column_name']} : {c['data_type']}{tag}")
            # RLS status
            rls = await conn.fetchval(
                "SELECT relrowsecurity FROM pg_class WHERE oid = ('soul_v3.'||$1)::regclass", name)
            print(f"   [RLS habilitado: {rls}]")
        # rango de edad de vision_events
        try:
            r = await conn.fetchrow("SELECT min(ts_wall) lo, max(ts_wall) hi, count(*) n FROM soul_v3.vision_events")
            print(f"\nvision_events edad: {r['lo']} → {r['hi']} ({r['n']} eventos)")
        except Exception as e:
            print(f"\nedad: err {e}")
    finally:
        await conn.close()


if __name__ == "__main__":
    if not DSN:
        print("SOUL_VISION_DSN no seteado", file=sys.stderr); raise SystemExit(2)
    asyncio.run(main())
