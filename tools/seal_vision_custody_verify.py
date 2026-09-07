"""SOUL Vision — verificación AUTOMÁTICA de custodia (carril NEXUS).

WRAPPER fino (schedule + DB-read + alerta) sobre el verificador CANÓNICO de JARVIS/FABLE:
event_writer.verify_chain(conn, camera_id) — recompute con el core canónico (_fmt_conf 4-dec +
_norm_bbox 2-dec, idéntico al write) + chequea hash + firma Ed25519 + cutoff de missing-sig.
NO reimplementa nada (evita el falso-tamper del round-trip — lección write-vs-verify).
Mi carril = automatización: por cada cámara → verify_chain → si !ok, ALERTA (nunca silencioso).

TARGET = DB LOCAL del 5070 (donde escribe el app vivo), no el Spark. Desplegar este archivo
junto a event_writer.py (~/soul_vision_5070/) y correr con el DSN local (vision_db.env).
Env: SOUL_VISION_DSN (o SOUL_MEMORY_DSN). Cron: cada N min. Alerta vía send_webchat → latidos.
"""
from __future__ import annotations
import os, sys, asyncio, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # event_writer al lado (5070)

DSN = os.environ.get("SOUL_VISION_DSN") or os.environ.get("SOUL_MEMORY_DSN") or ""
_SEND = os.environ.get("SEAL_SEND_WEBCHAT", "/home/dadito/IA/proyecto-seal/messages/send_webchat.py")


def _alert(msg: str) -> None:
    try:
        subprocess.run(["python3", _SEND, "NEXUS", "NEXUS", msg, "latidos", "alert"],
                       timeout=20, check=False)
    except Exception as e:
        print(f"custody_verify: alert send failed: {e}", file=sys.stderr)


async def run() -> dict:
    import asyncpg
    import event_writer  # canónico — DEBE estar al lado (deploy 5070)
    conn = await asyncpg.connect(DSN, timeout=10)
    breaks = []
    legacy_total = 0  # eventos grandfatheados (legacy de formato) — DISCLOSE, no 'proven intact'
    try:
        cams = await conn.fetch("SELECT DISTINCT camera_id FROM soul_v3.vision_events")
        for r in cams:
            cam = r["camera_id"]
            res = await event_writer.verify_chain(conn, cam)   # {ok,n,broken_at,reason,legacy_skipped}
            legacy_total += res.get("legacy_skipped", 0)
            if not res.get("ok"):
                breaks.append({"camera_id": cam, "broken_at": res.get("broken_at"),
                               "reason": res.get("reason"), "n": res.get("n")})
        return {"cameras": len(cams), "breaks": breaks, "legacy_skipped": legacy_total}
    finally:
        await conn.close()


def main() -> int:
    if not DSN:
        print("custody_verify: DSN no seteado (SOUL_VISION_DSN) — abort", file=sys.stderr)
        return 2
    try:
        res = asyncio.run(run())
    except Exception as e:
        # un fallo del propio verificador también es una anomalía a gritar (no silencioso)
        _alert(f"⚠️ custody-verify VISION no pudo correr: {type(e).__name__}: {e}")
        print(f"custody_verify: ERROR {type(e).__name__}: {e}", file=sys.stderr)
        return 3
    if res["breaks"]:
        detail = "; ".join(
            f"{b['camera_id']}@ev{b['broken_at']}:{b['reason']}" for b in res["breaks"][:6])
        _alert(f"🚨 CUSTODIA VISION COMPROMETIDA: {len(res['breaks'])} cadena(s) rota(s) — {detail}")
        print(f"custody_verify: BREAKS={len(res['breaks'])}: {detail}")
        return 1
    legacy = res.get("legacy_skipped", 0)
    disclose = (f"; {legacy} evento(s) legacy-limited-integrity (formato pre-fix, grandfatheados por "
                f"ts_wall: hash-INVERIFICABLE pero firma OK — NO 'proven intact')") if legacy else ""
    print(f"custody_verify: OK — {res['cameras']} cámara(s), cadenas íntegras + firmadas "
          f"(verify_chain canónico){disclose}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
