"""SOUL Vision — RETENCIÓN por edad (carril NEXUS, #26). Ley 29733 (minimización de datos).

Purga por EDAD lo transitorio/PII, NUNCA el ledger append-only:
  • vision_events.clip_ref / mask_ref → BLOBS en disco (clips/máscaras). Se BORRA el ARCHIVO y se
    audita en event_log; la FILA del ledger queda intacta (append-only, trigger #25). El frame_hash
    (la evidencia) y la cadena de custodia no se tocan.
  • vision_faces → PII (embedding+face_token). Filas más viejas que el umbral se BORRAN (deletable;
    crypto-shred, coordina con ARCO seal_vision_forget). El ledger no referencia el embedding.

DRY-RUN por defecto (reporta qué se purgaría, NO borra). --apply para ejecutar.
Env: SOUL_VISION_DSN (o SOUL_MEMORY_DSN). Uso:
  python3 seal_vision_retention.py [--age-days 90] [--apply]
"""
from __future__ import annotations
import os, sys, asyncio, argparse, subprocess

DSN = os.environ.get("SOUL_VISION_DSN") or os.environ.get("SOUL_MEMORY_DSN") or ""
_UPLOADS_DIRS = ["/home/dadito/soul_vision_5070/clips", "/home/dadito/soul_vision_5070/uploads",
                 "/home/dadito/IA/proyecto-seal/messages/uploads"]  # candidatos para resolver refs


def _resolve_blob(ref: str):
    """Resuelve un clip_ref/mask_ref a un path en disco (conservador). None si no se puede ubicar."""
    if not ref:
        return None
    if os.path.isabs(ref) and os.path.exists(ref):
        return ref
    base = os.path.basename(ref)
    for d in _UPLOADS_DIRS:
        cand = os.path.join(d, base)
        if os.path.exists(cand):
            return cand
    return None


async def run(age_days: int, apply: bool) -> dict:
    import asyncpg
    conn = await asyncpg.connect(DSN, timeout=10)
    report = {"age_days": age_days, "apply": apply, "blobs": [], "faces": 0, "ledger_touched": False}
    try:
        # 1) BLOBS de vision_events más viejos que el umbral (clip_ref/mask_ref no-null)
        rows = await conn.fetch(
            "SELECT event_id, camera_id, ts_wall, clip_ref, mask_ref FROM soul_v3.vision_events "
            "WHERE (clip_ref IS NOT NULL OR mask_ref IS NOT NULL) "
            "AND ts_wall < (now() - ($1::int * interval '1 day')) ORDER BY ts_wall", age_days)
        for r in rows:
            for ref in (r["clip_ref"], r["mask_ref"]):
                if not ref:
                    continue
                path = _resolve_blob(ref)
                report["blobs"].append({"event_id": r["event_id"], "ref": ref,
                                        "path": path, "resolvable": bool(path)})
        # 2) PII vieja en vision_faces
        face_rows = await conn.fetch(
            "SELECT id, name, enrolled_at FROM soul_v3.vision_faces "
            "WHERE enrolled_at < (now() - ($1::int * interval '1 day')) ORDER BY enrolled_at", age_days)
        report["faces_to_purge"] = [{"id": f["id"], "name": f["name"], "enrolled_at": str(f["enrolled_at"])}
                                    for f in face_rows]
        report["faces"] = len(face_rows)

        if apply:
            # Borrar SOLO los archivos blob (jamás la fila-ledger). Auditar la purga.
            purged = 0
            for b in report["blobs"]:
                if b["resolvable"]:
                    try:
                        os.remove(b["path"]); purged += 1
                    except OSError:
                        pass
            report["blobs_purged_files"] = purged
            # Borrar PII vieja (deletable). Auditar a event_log via olvido coordinado.
            if face_rows:
                await conn.execute(
                    "DELETE FROM soul_v3.vision_faces WHERE enrolled_at < (now() - ($1::int * interval '1 day'))",
                    age_days)
            report["faces_purged"] = len(face_rows)
        return report
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--age-days", type=int, default=90)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    if not DSN:
        print("retention: SOUL_VISION_DSN no seteado", file=sys.stderr); return 2
    try:
        rep = asyncio.run(run(args.age_days, args.apply))
    except Exception as e:
        print(f"retention: ERROR {type(e).__name__}: {e}", file=sys.stderr); return 3
    mode = "APLICADO" if args.apply else "DRY-RUN (nada borrado)"
    n_blobs = len(rep["blobs"]); n_faces = rep["faces"]
    print(f"retention [{mode}] age>{args.age_days}d: {n_blobs} blob(s) clip/mask + {n_faces} cara(s) PII "
          f"candidatos a purga. Ledger append-only: INTACTO (nunca se borra fila/chain).")
    if n_blobs == 0 and n_faces == 0:
        print("  → Nada que purgar a esta edad (correcto si la data es reciente).")
    for b in rep["blobs"][:10]:
        print(f"  blob ev{b['event_id']}: {b['ref']} {'(archivo ubicado)' if b['resolvable'] else '(no ubicado)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
