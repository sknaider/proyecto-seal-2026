"""Read-only: ¿hay blobs purgables (clip_ref/mask_ref) y en qué formato? Informa retención #26.
NO escribe. Confirma también que el ledger está PII-limpio (reid_embedding NULL). Env: SOUL_VISION_DSN."""
from __future__ import annotations
import os, sys, asyncio

DSN = os.environ.get("SOUL_VISION_DSN") or os.environ.get("SOUL_MEMORY_DSN") or ""


async def main():
    import asyncpg
    conn = await asyncpg.connect(DSN, timeout=10)
    try:
        r = await conn.fetchrow(
            "SELECT count(*) n, count(clip_ref) clips, count(mask_ref) masks, "
            "count(reid_embedding) reid FROM soul_v3.vision_events")
        print(f"vision_events: {r['n']} filas | clip_ref no-null={r['clips']} | "
              f"mask_ref no-null={r['masks']} | reid_embedding no-null={r['reid']} (debe ser 0 = ledger PII-limpio)")
        clips = await conn.fetch(
            "SELECT DISTINCT clip_ref v FROM soul_v3.vision_events WHERE clip_ref IS NOT NULL LIMIT 3")
        masks = await conn.fetch(
            "SELECT DISTINCT mask_ref v FROM soul_v3.vision_events WHERE mask_ref IS NOT NULL LIMIT 3")
        for label, rows in (("clip_ref", clips), ("mask_ref", masks)):
            vals = [s["v"] for s in rows]
            print(f"  {label} muestras: {vals if vals else '(ninguno — nada que purgar aún)'}")
        # vision_faces PII edad
        try:
            f = await conn.fetchrow("SELECT count(*) n, min(enrolled_at) lo, max(enrolled_at) hi FROM soul_v3.vision_faces")
            print(f"vision_faces (PII): {f['n']} filas | enrolled {f['lo']} → {f['hi']}")
        except Exception as e:
            print(f"vision_faces: err {e}")
    finally:
        await conn.close()


if __name__ == "__main__":
    if not DSN:
        print("SOUL_VISION_DSN no seteado", file=sys.stderr); raise SystemExit(2)
    asyncio.run(main())
