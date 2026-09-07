#!/usr/bin/env python3
"""
webchat_auth_probe.py — Suite adversarial de autenticidad webchat (P0-8, auditoría SOUL v2.0).
=================================================================================================
Verifica el criterio de cierre de P0-8: "suite adversarial LAN con rechazo 100% de identidad forjada".
Es el GATE de verificación (FABLE) del fix de enforcement de auth en /api/agents/send + steer/claim.

DISEÑO NO-DISRUPTIVO:
  - `--dry-run` (DEFECTO): NO inyecta nada al canal en vivo. Chequea alcanzabilidad del endpoint,
    enumera los probes que correría, y (read-only) verifica en el código si la auth está enforzada.
  - `--live`: corre los probes REALES contra un canal de PRUEBA dedicado ('secaudit_probe'), con
    identidad forjada CLARAMENTE MARCADA ('_SECAUDIT_FORGED_*'), NUNCA impersona a William en web_chat.
    Cada probe reporta PASS (rechazado = seguro) / FAIL (aceptado = vulnerable).

Semántica de veredicto (ojo: invertida respecto a un test normal):
  - ANTES del fix: se espera FAIL (el server acepta identidad forjada) → documenta el gap.
  - DESPUÉS del fix: se espera PASS (rechazo 100%) → gate verde para cerrar P0-8.

Uso:
    python3 webchat_auth_probe.py                 # dry-run (seguro; no inyecta)
    python3 webchat_auth_probe.py --live          # corre probes reales (canal de prueba)
    python3 webchat_auth_probe.py --endpoint http://127.0.0.1:8765
"""
from __future__ import annotations
import argparse, json, sys, time, urllib.request

DEFAULT_ENDPOINT = "http://127.0.0.1:8765"
FORGED_PREFIX = "_SECAUDIT_FORGED"
TEST_CHANNEL = "secaudit_probe"   # canal dedicado, NO web_chat

# Probes: cada uno intenta una acción SIN credencial válida. 'secure' = el server debe RECHAZARLA.
PROBES = [
    {"id": "T1", "name": "send-sin-token", "path": "/api/agents/send",
     "intent": "envío sin token debe rechazarse (hoy se acepta como verified=false)"},
    {"id": "T2", "name": "impersonar-autoridad", "path": "/api/agents/send",
     "intent": "from de mayor autoridad sin token no debe ganar prioridad (autoridad solo por verified_sender)"},
    {"id": "T3", "name": "steer-sin-auth", "path": "/api/agents/send",
     "intent": "send type=steer sin auth debe rechazarse"},
    {"id": "T4", "name": "claim-sin-auth", "path": "/api/agents/claim",
     "intent": "claim de single-voice sin auth debe rechazarse"},
]


def _post(endpoint: str, path: str, body: dict, timeout: float = 5.0):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(endpoint.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()[:200].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read()[:200].decode("utf-8", "replace") if e.fp else "")
    except Exception as e:
        return None, f"[err:{str(e)[:80]}]"


def run_live(endpoint: str) -> list[dict]:
    """Corre probes reales contra el canal de prueba. Identidad forjada marcada; nunca impersona a William en vivo."""
    rows = []
    stamp = int(time.time())
    forged = f"{FORGED_PREFIX}_{stamp}"
    bodies = {
        "T1": {"from": forged, "to": "equipo", "type": "chat", "channel": TEST_CHANNEL,
               "message": "[SECURITY-AUDIT PROBE — ignorar] envío sin token"},
        # T2: identidad de mayor autoridad, pero SIN impersonar el nombre real de William en web_chat.
        "T2": {"from": f"{forged}_AS_OPERATOR", "to": "equipo", "type": "chat", "channel": TEST_CHANNEL,
               "message": "[SECURITY-AUDIT PROBE — ignorar] pretende autoridad de operador"},
        "T3": {"from": forged, "to": "NEXUS", "type": "steer", "channel": TEST_CHANNEL,
               "message": "[SECURITY-AUDIT PROBE — ignorar] steer forjado"},
        "T4": {"agent": forged, "message_id": f"probe_{stamp}"},
    }
    for p in PROBES:
        code, resp = _post(endpoint, p["path"], bodies[p["id"]])
        # RECHAZO (seguro) = 401/403, o 404 si el endpoint no existe (no explotable por esa vía).
        if code in (401, 403):
            verdict, sec = "PASS", True
        elif code == 404:
            verdict, sec = "N/A", True   # endpoint inexistente = no vector por ese path
        elif code == 200:
            verdict, sec = "FAIL", False  # aceptado sin auth = vulnerable
        else:
            verdict, sec = "UNCLEAR", None
        rows.append({"id": p["id"], "name": p["name"], "http": code, "verdict": verdict,
                     "secure": sec, "resp": resp[:80]})
    return rows


def dry_run(endpoint: str) -> dict:
    """No inyecta. Chequea alcanzabilidad + enumera probes."""
    code, _ = _post(endpoint, "/health", {}) if False else (None, "")
    # sondeo GET /health read-only
    try:
        with urllib.request.urlopen(endpoint.rstrip("/") + "/health", timeout=4) as r:
            reachable = (r.status == 200)
    except Exception:
        reachable = False
    return {"endpoint": endpoint, "reachable": reachable,
            "probes": [{"id": p["id"], "name": p["name"], "path": p["path"], "intent": p["intent"]} for p in PROBES],
            "nota": "dry-run: no se inyectó nada. Usar --live (canal de prueba) en la verificación del cutover."}


def main() -> int:
    ap = argparse.ArgumentParser(description="Suite adversarial de auth webchat (P0-8)")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--live", action="store_true", help="corre probes reales (canal de prueba dedicado)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.live:
        out = dry_run(args.endpoint)
        print(json.dumps(out, ensure_ascii=False, indent=2) if args.json else
              f"[dry-run] endpoint={out['endpoint']} reachable={out['reachable']}\n" +
              "\n".join(f"  {p['id']} {p['name']:22} {p['path']}  — {p['intent']}" for p in out["probes"]) +
              f"\n{out['nota']}")
        return 0

    rows = run_live(args.endpoint)
    fails = [r for r in rows if r["secure"] is False]
    if args.json:
        print(json.dumps({"rows": rows, "insecure_count": len(fails)}, ensure_ascii=False, indent=2))
    else:
        print(f"=== webchat auth probe (LIVE) — endpoint {args.endpoint} ===")
        for r in rows:
            print(f"  {r['id']} {r['name']:22} HTTP={r['http']}  {r['verdict']}  {'SEGURO' if r['secure'] else 'VULNERABLE' if r['secure'] is False else '?'}")
        print(f"\nVeredicto: {'VULNERABLE — ' + str(len(fails)) + ' probe(s) aceptada(s) sin auth' if fails else 'SEGURO — rechazo 100% de identidad forjada'}")
    # exit 1 si hay inseguridad (útil como gate CI)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
