#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_post.py — Wrapper CLI de posteo con guardia de egreso por-canal (#29/#32).

Motivo (pedido de ALICE + JARVIS, 2026-06-30): los agentes postean con `curl` INLINE, no por un módulo
Python — así que las 3 líneas del handoff #32 (filter_fragments/assert_safe_egress) no tienen dónde vivir.
Este wrapper es el CHOKE-POINT único de posteo: reemplaza el curl crudo por un solo comando que (a) corre
el guard #29 si se le pasan fragmentos con procedencia, (b) audita el canal destino para DUM, y (c) hace el
POST a /api/agents/send. Agentes adoptan aliasando su posteo a `seal_post.py` — opt-in, sin tocar su código.

Enforcement honesto:
  - Con --fragments (JSON [{content, source_channel}]): egreso VERIFICADO en código (fail-closed; si un
    fragmento privado intenta fugar al canal destino → NO postea, sale != 0).
  - Sin --fragments (texto libre, caso inline actual): el guard no puede inspeccionar procedencia de texto
    libre → NO da falsa garantía; sí exige canal destino explícito y AUDITA el posteo (choke-point central
    donde luego se puede subir enforcement). La disciplina por-tema del agente sigue siendo la 1ra línea.

Uso:
    seal_post.py --from NEXUS --to William --channel web_chat --message "hola"
    seal_post.py --from JARVIS --to Henry --channel user:3:gtl-sistemas --message "..." \
                 --fragments /tmp/frags.json     # frags = [{"content":"...","source_channel":"user:3:..."}]
    echo "cuerpo largo" | seal_post.py --from ALICE --to equipo --channel web_chat --message -

Salida: imprime la respuesta JSON del server. Exit 0 = posteado; 2 = bloqueado por egreso; 1 = error.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from seal_channel_egress_guard import (  # noqa: E402
    filter_fragments, assert_safe_egress, EgressViolation, event_log_audit, is_private,
)

_ENDPOINT = os.environ.get("SEAL_POST_ENDPOINT", "http://localhost:8765/api/agents/send")


def _load_fragments(path: str) -> list:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("--fragments debe ser un JSON array de {content, source_channel}")
    return data


def _post(payload: dict, timeout: float = 10.0) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(_ENDPOINT, data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(description="Posteo SEAL con guardia de egreso por-canal (#29/#32).")
    ap.add_argument("--from", dest="sender", required=True)
    ap.add_argument("--to", dest="to", required=True)
    ap.add_argument("--channel", required=True, help="canal destino (web_chat, user:3:x, dm:...)")
    ap.add_argument("--message", required=True, help="texto, o '-' para leer de stdin")
    ap.add_argument("--type", dest="mtype", default="conversation")
    ap.add_argument("--fragments", help="ruta a JSON [{content, source_channel}] para enforcement de egreso")
    ap.add_argument("--dry-run", action="store_true", help="valida+audita pero NO postea")
    args = ap.parse_args(argv)

    message = sys.stdin.read() if args.message == "-" else args.message
    target = args.channel.strip()
    if not target:
        print("ERROR: --channel vacío", file=sys.stderr)
        return 1

    # (1) Egreso VERIFICADO si hay fragmentos con procedencia.
    if args.fragments:
        try:
            frags = _load_fragments(args.fragments)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(f"ERROR fragmentos: {e}", file=sys.stderr)
            return 1
        try:
            assert_safe_egress(frags, target, audit=event_log_audit)
        except EgressViolation as e:
            print(f"BLOQUEADO (#29 egreso): {e}", file=sys.stderr)
            return 2
        usable = filter_fragments(frags, target)
        if len(usable) != len(frags):
            print(f"[seal_post] aviso: {len(frags)-len(usable)} fragmento(s) podados por procedencia.",
                  file=sys.stderr)

    # (2) Auditoría del posteo (choke-point central; señal para DUM). No bloquea si falla.
    event_log_audit({"event": "egress_post", "rule": "#32", "from": args.sender, "to": args.to,
                     "target_channel": target, "private_target": is_private(target),
                     "has_fragments": bool(args.fragments), "chars": len(message)})

    if args.dry_run:
        print(json.dumps({"dry_run": True, "target_channel": target, "would_post": True}))
        return 0

    # (3) POST.
    payload = {"from": args.sender, "to": args.to, "type": args.mtype,
               "channel": target, "message": message}
    status, resp = _post(payload)
    print(resp)
    return 0 if status and 200 <= status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
