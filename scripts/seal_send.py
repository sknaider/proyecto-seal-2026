#!/usr/bin/env python3
"""Authenticated SEAL webchat writer for agents and service identities.

Prevención (JARVIS/ADA, 2026-07-18): antes este script mostraba un traceback de
urllib cuando el server rechazaba (422 in_reply_to_required / 409
coordination_public_write_denied), dejando MUDO a cualquier agente que booteaba
sin saber por qué. Ahora:
  - Imprime el cuerpo JSON del error (motivo real), no un traceback.
  - Expone --unique-contribution / --contribution-reason para que un agente pueda
    declarar explícitamente un aporte único y auditable.
  - Falla cerrado ante 409/422: nunca convierte automáticamente un rechazo de
    coordinación en un override.
"""
import argparse, json, os, pathlib, sys, urllib.request, urllib.error

from seal_autonomy_guard import APPROVAL_GATES, autonomy_warning

API = 'http://localhost:8765/api/agents/send'

ap = argparse.ArgumentParser()
ap.add_argument("from_agent", nargs="?", default=os.environ.get("SEAL_AGENT"))
ap.add_argument("to_agent")
ap.add_argument("message")
ap.add_argument("--channel", default="web_chat")
ap.add_argument("--type", dest="msg_type", default="conversation")
ap.add_argument("--in-reply-to", default=None)
ap.add_argument("--multi-response", action="store_true")
ap.add_argument("--proactive", action="store_true")
ap.add_argument("--idempotency-key", default=None)
ap.add_argument("--unique-contribution", action="store_true",
                help="Marca el mensaje como aporte único (escape de coordinación ENFORCE).")
ap.add_argument("--contribution-reason", default=None,
                help=">=20 chars explicando qué aporta de nuevo (requerido por el server si unique_contribution).")
ap.add_argument(
    "--approval-gate",
    choices=APPROVAL_GATES,
    default=None,
    help="Gate real que justifica consultar antes: destructive, external_commitment, scope_change o human_only.",
)
args = ap.parse_args()
if not args.from_agent:
    ap.error("FROM_AGENT requerido (argumento o SEAL_AGENT)")

warning = autonomy_warning(args.to_agent, args.message, args.approval_gate)
if warning:
    sys.stderr.write(f"[seal_send][AUTONOMY BLOCKED] {warning}\n")
    raise SystemExit(2)

token_path = pathlib.Path(__file__).resolve().parents[1] / "messages" / f".agent_session_token_{args.from_agent.upper()}"
token = token_path.read_text(encoding="utf-8").strip()

payload = {"from":args.from_agent,"to":args.to_agent,"type":args.msg_type,
           "channel":args.channel,"message":args.message,"session_key":token}
if args.in_reply_to is not None:
    payload["in_reply_to"] = str(args.in_reply_to)
if args.multi_response:
    payload["multi_response"] = True
if args.proactive:
    payload["proactive"] = True
if args.idempotency_key:
    payload["idempotency_key"] = args.idempotency_key
if args.unique_contribution:
    payload["unique_contribution"] = True
if args.contribution_reason:
    payload["contribution_reason"] = args.contribution_reason


def _post(p):
    """POST payload. Returns (ok, status, body_text)."""
    data = json.dumps(p, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(API, data=data,
                                 headers={'Content-Type':'application/json; charset=utf-8'})
    try:
        return True, 200, urllib.request.urlopen(req).read().decode()
    except urllib.error.HTTPError as e:
        return False, e.code, e.read().decode()


ok, status, body = _post(payload)

print(body)
if not ok:
    sys.stderr.write(f"[seal_send] HTTP {status}: {body}\n")
    sys.exit(1)
