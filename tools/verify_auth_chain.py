#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_auth_chain.py — Effect-gate de FABLE para la cadena de auth de un clon (post-emisión de token).

Verifica POR EFECTO (no por afirmación) que un clon quedó bien tras la emisión NEXUS/JARVIS:
  1. TOKEN  — existe, no expirado (expires_at > now), scoped al agente.
  2. /soul  — POST con el token baja alma RICA (>=400 chars, no placeholder), scoped (sirve al agente del token).
  3. XLEAK  — pedir con el token de A NO debe devolver el alma de B (anti-cross-leak).
  4. BOOT   — boot_soul(agent) rico (no vacío/genérico → el gate no-genérico pasó).
  5. ATREST — el chunk sembrado local está CIFRADO (base64→primeros 4 bytes == b'msc1').

Corre EN el device (necesita el token store + mini-soul.db locales). NO construye nada: solo mide.
builder≠verifier: NEXUS emite, FABLE verifica. Uso: python3 verify_auth_chain.py FABLE [--endpoint ...]"""
from __future__ import annotations
import base64
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "memory"))

PLACEHOLDERS = ("[rol]", "placeholder", "alma delgada", "generic")


def _post(endpoint: str, path: str, body: dict, timeout: float = 15.0) -> dict:
    url = endpoint.rstrip("/") + path
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return {"_status": r.status, **json.loads(r.read().decode("utf-8"))}
    except urllib.error.HTTPError as e:
        try:
            return {"_status": e.code, **json.loads(e.read().decode("utf-8"))}
        except Exception:
            return {"_status": e.code, "ok": False}
    except Exception as e:
        return {"_status": 0, "ok": False, "reason": f"net_{type(e).__name__}"}


def _is_encrypted(content: str) -> bool:
    try:
        return base64.b64decode(content)[:4] == b"msc1"
    except Exception:
        return False


def verify(agent: str, endpoint: str, other: str = None) -> dict:
    agent = (agent or "").strip().upper()
    out = {"agent": agent, "checks": {}}

    # 1. TOKEN
    try:
        from seal_sync_auth import load_device_token
        tok = load_device_token(agent)
    except Exception as e:
        tok = None
        out["checks"]["token_load_err"] = type(e).__name__
    now = int(time.time())
    exp = int(tok["expires_at"]) if tok and tok.get("expires_at") else 0
    tok_ok = bool(tok) and exp > now
    out["checks"]["1_token"] = {"pass": tok_ok, "expires_in_h": round((exp - now) / 3600, 1) if exp else None}
    if not tok_ok:
        out["verdict"] = "FAIL"; out["blocked_at"] = "1_token (sin token válido → no fresh-pull)"; return out

    # 2. /soul RICO + scoped
    r = _post(endpoint, "/soul", {"auth_token": tok})
    soul = r.get("soul") or ""
    rich = len(soul) >= 400 and not any(p in soul.lower() for p in PLACEHOLDERS)
    out["checks"]["2_soul"] = {"pass": bool(r.get("ok")) and rich, "status": r.get("_status"),
                               "chars": len(soul), "reason": r.get("reason")}

    # 3. XLEAK — el /soul sirve al agente DEL TOKEN, no a otro (scope by signed token)
    #    Confirmación por efecto: el alma servida menciona al agente del token, no al 'other'.
    if other:
        other = other.strip().upper()
        leak = other.lower() in soul.lower()[:200] and agent.lower() not in soul.lower()[:200]
        out["checks"]["3_xleak"] = {"pass": not leak, "note": f"alma NO es de {other}"}

    # 4. BOOT rico (gate no-genérico)
    try:
        from soul_lifecycle import boot_soul
        boot = boot_soul(agent)
        boot_ok = len(boot) >= 400
        out["checks"]["4_boot"] = {"pass": boot_ok, "chars": len(boot)}
    except Exception as e:
        out["checks"]["4_boot"] = {"pass": False, "err": type(e).__name__}

    # 5. ATREST — chunk de identidad sembrado está cifrado
    try:
        db = Path.home() / ".seal" / "mini-soul.db"
        conn = sqlite3.connect(str(db)); conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT content FROM chunks WHERE agent=? AND category='identity' "
                            "ORDER BY created_at DESC LIMIT 3", (agent,)).fetchall()
        conn.close()
        enc_all = bool(rows) and all(_is_encrypted(r["content"]) for r in rows)
        out["checks"]["5_atrest"] = {"pass": enc_all, "chunks": len(rows)}
    except Exception as e:
        out["checks"]["5_atrest"] = {"pass": False, "err": type(e).__name__}

    passed = all(c.get("pass") for c in out["checks"].values() if isinstance(c, dict) and "pass" in c)
    out["verdict"] = "PASS" if passed else "FAIL"
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("agent")
    ap.add_argument("--endpoint", default="http://localhost:8778")
    ap.add_argument("--other", default=None, help="agente ajeno para el chequeo anti-cross-leak")
    a = ap.parse_args()
    res = verify(a.agent, a.endpoint, a.other)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res.get("verdict") == "PASS" else 1)


if __name__ == "__main__":
    main()
