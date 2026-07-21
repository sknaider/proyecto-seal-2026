#!/usr/bin/env python3
"""Harness de verificación del fix single-voice/council (pieza 2, owner NEXUS).

Preparado por JARVIS (21-jul-2026) ANTES de que el fix aterrice, para que la
verificación post-edit tarde minutos: se corre hoy como BASELINE (documenta el
estado roto) y post-fix debe pasar completo.

Casos:
  A  (safe, corre siempre)   agente→William type=conversation SIN in_reply_to SIN flag
                             esperado: DENY (422/deny council) y 0 filas nuevas en chat.
  B  (SOLO POST-FIX)         reintento con unique_contribution=true tras el deny.
                             PRE-FIX este caso PUBLICA (el bug: flag auto-declarado
                             bypassea) => spam a William. Por eso está gateado con
                             --post-fix. POST-FIX esperado: DENY/gated, 0 filas.
  C  (safe, corre siempre)   claim doble del mismo message_id sintético con mi token.
                             esperado: granted:true e idempotente para el holder.
                             (El caso 2-agentes lo corre NEXUS con su token.)
  D  (safe, transversal)     delta de filas en soul_v3.chat_messages durante el
                             harness == exactamente las esperadas (0 en denies).

Uso:
  python3 tools/verify_single_voice_fix.py            # baseline / post-fix casos safe
  python3 tools/verify_single_voice_fix.py --post-fix # incluye caso B (solo tras el fix)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
API_SEND = "http://localhost:8765/api/agents/send"
API_CLAIM = "http://localhost:8765/api/agents/claim"
AGENT = "JARVIS"
DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"

results: list[tuple[str, bool, str]] = []


def _token() -> str:
    return (ROOT / "messages" / f".agent_session_token_{AGENT}").read_text().strip()


def _post(url: str, payload: dict) -> tuple[int, dict]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        body = urllib.request.urlopen(req, timeout=10).read().decode()
        return 200, json.loads(body)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


async def _count_rows_since(conn, since_epoch: float) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM soul_v3.chat_messages WHERE created_at > to_timestamp($1)",
        since_epoch,
    )


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name} — {detail}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--post-fix", action="store_true",
                    help="incluye caso B (bypass del flag). NO usar pre-fix: publicaría.")
    args = ap.parse_args()

    import asyncpg
    conn = await asyncpg.connect(DSN)
    tok = _token()
    t0 = time.time()
    ts = int(t0)

    # ── Caso A: deny sin in_reply_to ────────────────────────────────────────
    code, body = _post(API_SEND, {
        "from": AGENT, "to": "William", "channel": "web_chat",
        "type": "conversation", "session_key": tok,
        "message": f"[HARNESS-SV A {ts}] no debe publicarse",
        "idempotency_key": f"harness-sv-a-{ts}",
    })
    denied = (code in (409, 422)) and not body.get("ok", False)
    check("A deny sin in_reply_to", denied, f"HTTP {code} error={body.get('error')}")

    # ── Caso B: bypass del flag (SOLO POST-FIX) ─────────────────────────────
    if args.post_fix:
        code, body = _post(API_SEND, {
            "from": AGENT, "to": "William", "channel": "web_chat",
            "type": "conversation", "session_key": tok,
            "message": f"[HARNESS-SV B {ts}] no debe publicarse ni con flag",
            "idempotency_key": f"harness-sv-b-{ts}",
            "unique_contribution": True,
            "contribution_reason": "harness de verificacion: este reintento con flag NO debe publicar",
        })
        gated = (code in (409, 422)) and not body.get("ok", False)
        check("B flag NO bypassea (post-fix)", gated, f"HTTP {code} error={body.get('error')}")
    else:
        print("  SKIP  B (bypass del flag) — correr con --post-fix; hoy publicaría (el bug)")

    # ── Caso C: claim granted + idempotente para el holder ──────────────────
    mid = f"harness-sv-claim-{ts}"
    code1, b1 = _post(API_CLAIM, {"message_id": mid, "agent": AGENT, "session_key": tok})
    code2, b2 = _post(API_CLAIM, {"message_id": mid, "agent": AGENT, "session_key": tok})
    ok_c = b1.get("granted") is True and b2.get("granted") is True and b2.get("holder") == AGENT
    check("C claim granted+idempotente", ok_c,
          f"1º granted={b1.get('granted')} 2º granted={b2.get('granted')} holder={b2.get('holder')}")

    # ── Caso D: cero filas publicadas por los denies ─────────────────────────
    await asyncio.sleep(1.0)
    delta = await _count_rows_since(conn, t0)
    # post-fix con B: esperadas 0. pre-fix (solo A+C): esperadas 0 (claims no publican).
    check("D delta de filas == 0", delta == 0, f"filas nuevas durante harness: {delta}")

    await conn.close()

    failed = [r for r in results if not r[1]]
    print(f"\nRESULTADO: {len(results) - len(failed)}/{len(results)} PASS"
          + (f" — {len(failed)} FAIL" if failed else " — VERDE"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
