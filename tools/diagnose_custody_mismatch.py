"""Diagnóstico por EFECTO de un hash_mismatch de custodia (JARVIS, para correr donde esté el DB).

NO asume legacy ni tamper: para cada event_id sospechoso (+ vecinos), imprime el hash STORED vs
el RECOMPUTADO con el formato actual, y los campos crudos (confidence/bbox/ts) — para ver el PATRÓN:

  • Si fallan en BLOQUE ≤N (todos los viejos) y pasan los nuevos → cambio-de-formato (legacy de ESTE
    DB; su cutoff es distinto al del Spark=35; setear HASH_FORMAT_CUTOFF al N de ESTE DB).
  • Si fallan SUELTOS (ev36 y ev134 con vecinos OK) → NO es formato: investigar esos 2 (valor que
    round-trippea mal, o escritos en transición, o tamper real). Un mismatch aislado con firma válida
    pero hash distinto = sospechoso; con firma que tampoco verifica = más sospechoso.

Uso: SOUL_VISION_DSN=... python3 diagnose_custody_mismatch.py <ev1> <ev2> ...
Requiere event_writer.py al lado (usa su _fmt_conf/_norm_bbox/compute_chain_hash canónicos).
"""
from __future__ import annotations
import os, sys, asyncio, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DSN = os.environ.get("SOUL_VISION_DSN") or os.environ.get("SOUL_MEMORY_DSN") or ""


async def main(event_ids):
    import asyncpg
    import event_writer as ew
    conn = await asyncpg.connect(DSN, timeout=10)
    # ampliar con vecinos ±1 para ver si el fallo es bloque o aislado
    targets = sorted(set(e for i in event_ids for e in (i - 1, i, i + 1) if e > 0))
    rows = await conn.fetch(
        "SELECT event_id, camera_id, ts_wall, ts_mono, detector, obj_class, confidence, bbox, "
        "frame_hash, track_id, prev_hash, chain_hash, chain_sig FROM soul_v3.vision_events "
        "WHERE event_id = ANY($1::bigint[]) ORDER BY event_id", targets)
    print(f"DSN ok. Diagnóstico de {event_ids} (+vecinos):\n")
    for r in rows:
        core = {"camera_id": r["camera_id"], "ts_wall": r["ts_wall"], "ts_mono": r["ts_mono"],
                "detector": r["detector"], "obj_class": r["obj_class"],
                "confidence": ew._fmt_conf(r["confidence"]),
                "bbox": ew._norm_bbox(json.loads(r["bbox"]) if r["bbox"] else None),
                "frame_hash": r["frame_hash"], "track_id": r["track_id"]}
        recomputed = ew.compute_chain_hash(core, r["prev_hash"])
        match = recomputed == r["chain_hash"]
        sig_ok = None
        if r["chain_sig"] is not None:
            try:
                import custody_sign
                sig_ok = custody_sign.verify_signature(r["chain_hash"], r["chain_sig"])
            except Exception as e:
                sig_ok = f"err:{e}"
        flag = "OK " if match else "MISMATCH"
        tsw = r["ts_wall"].strftime("%Y-%m-%d %H:%M:%S") if hasattr(r["ts_wall"], "strftime") else str(r["ts_wall"])
        # ts_wall = el DISCRIMINADOR: escrito ANTES del fix decimal-string = legacy (mismatch esperado);
        # DESPUÉS del fix = debe verificar limpio → mismatch = anomalía REAL a investigar.
        print(f"ev{r['event_id']:>4} [{r['camera_id']}] {flag} | ts_wall={tsw} | sig={sig_ok} | "
              f"conf={r['confidence']!r}→{core['confidence']} | bbox={r['bbox']} | "
              f"sig_present={r['chain_sig'] is not None}")
        if not match:
            print(f"        stored={r['chain_hash'][:24]}… recomp={recomputed[:24]}…")
    await conn.close()
    print("\nLeé el patrón: ¿los MISMATCH son un bloque ≤N (formato→cutoff de ESTE db) o sueltos "
          "(investigar c/u)? La firma OK con hash distinto = el contenido cambió DESPUÉS de firmar.")


if __name__ == "__main__":
    if not DSN:
        print("SOUL_VISION_DSN no seteado", file=sys.stderr); raise SystemExit(2)
    ids = [int(a) for a in sys.argv[1:]] or [36, 134]
    asyncio.run(main(ids))
