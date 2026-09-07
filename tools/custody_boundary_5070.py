"""Custody boundary+timing discriminator (NEXUS) — cierra legacy-vs-tamper por el dato CONFIABLE.

Responde las 2 preguntas de JARVIS (court-grade, no block+sig):
  Q1 TIMING : ts_wall de cada evento vs mtime de event_writer.py (proxy del fix-deploy en ESTA DB).
              ts_wall < fix  → legacy (mismatch esperado, confiado por proveniencia/tiempo).
              ts_wall ≥ fix  → DEBE recomputar limpio; mismatch ahí = anomalía REAL.
  Q2 BOUNDARY: por cámara, recompute de TODOS sus eventos por id → ¿el MÁS NUEVO verifica limpio?
              ¿dónde está el corte OK/MISMATCH? (viejo abajo=legacy, nuevo arriba=limpio = corte sano).

Requiere event_writer.py al lado. Env: SOUL_VISION_DSN (o SOUL_MEMORY_DSN).
Uso: SOUL_VISION_DSN=... python3 custody_boundary_5070.py [cam1 cam2 ...]
"""
from __future__ import annotations
import os, sys, asyncio, json, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DSN = os.environ.get("SOUL_VISION_DSN") or os.environ.get("SOUL_MEMORY_DSN") or ""


def _tsw(r):
    v = r["ts_wall"]
    return v.strftime("%Y-%m-%d %H:%M:%S") if hasattr(v, "strftime") else str(v)


async def main(cams):
    import asyncpg
    import event_writer as ew
    conn = await asyncpg.connect(DSN, timeout=10)

    ewpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "event_writer.py")
    mt = datetime.datetime.fromtimestamp(os.path.getmtime(ewpath))
    print(f"event_writer.py mtime (proxy fix-deploy en el 5070) = {mt:%Y-%m-%d %H:%M:%S}\n")

    if not cams:
        rows = await conn.fetch("SELECT DISTINCT camera_id FROM soul_v3.vision_events ORDER BY camera_id")
        cams = [r["camera_id"] for r in rows]

    # Q1: suspects explícitos
    sus = await conn.fetch(
        "SELECT event_id, camera_id, ts_wall FROM soul_v3.vision_events "
        "WHERE event_id = ANY($1::bigint[]) ORDER BY event_id", [35, 36, 37, 133, 134, 135])
    print("Q1 TIMING — ts_wall de los sospechosos (comparar con el mtime de arriba):")
    for r in sus:
        print(f"  ev{r['event_id']:>4} [{r['camera_id']}] ts_wall={_tsw(r)}")
    print()

    print("Q2 BOUNDARY — recompute de TODOS los eventos por cámara:")
    for cam in cams:
        rows = await conn.fetch(
            "SELECT event_id, camera_id, ts_wall, ts_mono, detector, obj_class, confidence, bbox, "
            "frame_hash, track_id, prev_hash, chain_hash FROM soul_v3.vision_events "
            "WHERE camera_id=$1 ORDER BY event_id", cam)
        results = []
        for r in rows:
            core = {"camera_id": r["camera_id"], "ts_wall": r["ts_wall"], "ts_mono": r["ts_mono"],
                    "detector": r["detector"], "obj_class": r["obj_class"],
                    "confidence": ew._fmt_conf(r["confidence"]),
                    "bbox": ew._norm_bbox(json.loads(r["bbox"]) if r["bbox"] else None),
                    "frame_hash": r["frame_hash"], "track_id": r["track_id"]}
            match = ew.compute_chain_hash(core, r["prev_hash"]) == r["chain_hash"]
            results.append((r, match))
        n_ok = sum(1 for _, m in results if m)
        n_bad = len(results) - n_ok
        print(f"\n=== {cam}: {len(results)} eventos — {n_ok} OK / {n_bad} MISMATCH ===")
        # transiciones (corte)
        prev = None
        for r, m in results:
            if m != prev:
                print(f"   corte→ ev{r['event_id']} ts_wall={_tsw(r)} : {'OK' if m else 'MISMATCH'}")
                prev = m
        if results:
            r, m = results[-1]
            print(f"   NEWEST ev{r['event_id']} ts_wall={_tsw(r)} : "
                  f"{'OK ✅ (post-fix verifica limpio)' if m else 'MISMATCH ⚠️ (aún viejo o anomalía)'}")
    await conn.close()
    print("\nLectura: si TODO mismatch tiene ts_wall < mtime y NO hay post-fix → bloque legacy puro "
          "(grandfather por TIMING). Si hay post-fix OK → corte sano. Si algún ts_wall ≥ mtime y MISMATCH "
          "→ anomalía REAL a investigar.")


if __name__ == "__main__":
    if not DSN:
        print("SOUL_VISION_DSN no seteado", file=sys.stderr); raise SystemExit(2)
    asyncio.run(main(sys.argv[1:]))
