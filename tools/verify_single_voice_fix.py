#!/usr/bin/env python3
"""Harness de verificación del fix single-voice/council (pieza 2, owner NEXUS).

v2 — corregido tras catch de NEXUS (21-jul 18:23): el caso B original daba 422
(candado previo `in_reply_to_required`, chat_server:2031-2043) y NO ejercitaba
el gate nuevo de duplicados (409 `coordination_duplicate_contribution`,
chat_server:2048+), que exige: in_reply_to + council deny + texto que DUPLICA
a un hermano. Mi v1 aceptaba `code in (409,422)` → falso verde sobre la pieza.
Corrección de premisa: sin in_reply_to SIEMPRE fue 422 (pre y post fix); el
bypass real del flag vivía en el path CON in_reply_to.

Casos:
  A   (safe, siempre)      agente→William SIN in_reply_to SIN flag
                           esperado: 422 in_reply_to_required (candado previo).
  B0  (safe, siempre)      ídem A pero CON unique_contribution=true.
                           esperado: 422 — documenta que el flag no salva la
                           falta de in_reply_to (candado previo, NO el gate nuevo).
  B   (requiere --source-id y --dup-text)
                           CON in_reply_to=<msg de William con council activo> y
                           message = duplicado literal de la respuesta de un
                           hermano, CON flag+reason.
                           esperado: **409** y error que contenga 'duplicate'.
                           422 NO cuenta como PASS. Si publica → RED real del gate.
  C   (safe, siempre)      claim doble mismo message_id, mi token.
                           esperado: granted:true e idempotente para el holder.
  D   (safe, transversal)  delta de filas en chat durante el harness == 0.

Uso:
  python3 tools/verify_single_voice_fix.py
  python3 tools/verify_single_voice_fix.py \
      --source-id api_william_XXXX --dup-text "texto literal de un hermano"
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


def check(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name} — {detail}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-id", default=None,
                    help="message_id de William (council activo, lead≠JARVIS) para el caso B")
    ap.add_argument("--dup-text", default=None,
                    help="texto LITERAL de la respuesta de un hermano a esa fuente (caso B)")
    args = ap.parse_args()

    import asyncpg
    conn = await asyncpg.connect(DSN)
    tok = _token()
    t0 = time.time()
    ts = int(t0)

    # ── A: candado previo, sin flag ─────────────────────────────────────────
    code, body = _post(API_SEND, {
        "from": AGENT, "to": "William", "channel": "web_chat",
        "type": "conversation", "session_key": tok,
        "message": f"[HARNESS-SV A {ts}] no debe publicarse",
        "idempotency_key": f"harness-sv-a-{ts}",
    })
    check("A 422 sin in_reply_to (candado previo)",
          code == 422 and body.get("error") == "in_reply_to_required",
          f"HTTP {code} error={body.get('error')}")

    # ── B0: el flag NO salva la falta de in_reply_to (candado previo) ───────
    code, body = _post(API_SEND, {
        "from": AGENT, "to": "William", "channel": "web_chat",
        "type": "conversation", "session_key": tok,
        "message": f"[HARNESS-SV B0 {ts}] no debe publicarse ni con flag",
        "idempotency_key": f"harness-sv-b0-{ts}",
        "unique_contribution": True,
        "contribution_reason": "harness: flag sin in_reply_to no debe publicar (candado previo)",
    })
    check("B0 422 flag sin in_reply_to (candado previo, NO el gate nuevo)",
          code == 422 and body.get("error") == "in_reply_to_required",
          f"HTTP {code} error={body.get('error')}")

    # ── B: EL GATE NUEVO — duplicado con in_reply_to + flag ⇒ 409 dup ───────
    if args.source_id and args.dup_text:
        code, body = _post(API_SEND, {
            "from": AGENT, "to": "William", "channel": "web_chat",
            "type": "conversation", "session_key": tok,
            "message": args.dup_text,
            "in_reply_to": args.source_id,
            "idempotency_key": f"harness-sv-b-{ts}",
            "unique_contribution": True,
            "contribution_reason": "harness: duplicado literal de un hermano; el gate debe bloquear",
        })
        err = str(body.get("error", ""))
        ok_b = code == 409 and "duplicate" in err
        detail = f"HTTP {code} error={err or body}"
        if code == 200 and body.get("ok"):
            detail += "  <- PUBLICÓ: RED real del gate, avisar a NEXUS y borrar la fila"
        check("B 409 duplicado CON in_reply_to+flag (gate NUEVO)", ok_b, detail)
    else:
        print("  SKIP  B (gate nuevo) — requiere --source-id y --dup-text")

    # ── C: claim granted + idempotente ──────────────────────────────────────
    mid = f"harness-sv-claim-{ts}"
    _, b1 = _post(API_CLAIM, {"message_id": mid, "agent": AGENT, "session_key": tok})
    _, b2 = _post(API_CLAIM, {"message_id": mid, "agent": AGENT, "session_key": tok})
    check("C claim granted+idempotente",
          b1.get("granted") is True and b2.get("granted") is True and b2.get("holder") == AGENT,
          f"1º granted={b1.get('granted')} 2º granted={b2.get('granted')} holder={b2.get('holder')}")

    # ── D: cero filas publicadas por el harness ─────────────────────────────
    await asyncio.sleep(1.0)
    delta = await conn.fetchval(
        "SELECT count(*) FROM soul_v3.chat_messages WHERE created_at > to_timestamp($1)", t0)
    check("D delta de filas == 0", delta == 0, f"filas nuevas durante harness: {delta}")

    await conn.close()

    failed = [r for r in results if not r[1]]
    print(f"\nRESULTADO: {len(results) - len(failed)}/{len(results)} PASS"
          + (f" — {len(failed)} FAIL" if failed else " — VERDE"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
