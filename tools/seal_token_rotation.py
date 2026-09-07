#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_token_rotation.py — Rotación del token del device (renueva ANTES de expirar). Carril NEXUS.

MOTIVO (pendiente de clon #9): el token del device tiene TTL 24h. Si el device queda offline >24h,
el token expira → el clon queda INERTE (no puede sync). Este helper lo renueva antes de expirar:
  build_csr (PoP, fingerprint HW constante) → POST /csr → submit_csr_smart auto-aprueba
  (máquina ya autorizada por William) → token fresco 24h → store_token.

CLAVES de diseño (verify-by-effect):
 · keypair EFÍMERO por CSR: el token lo firma CENTRAL (no el device); la clave device es solo PoP.
 · REUSA el device_id del token actual → no prolifera filas en authorized_devices.
 · SIN gate humano: el human-gate ya se pasó UNA vez al autorizar la máquina; renovar es auto.
 · Recuperación: incluso offline >24h, al reconectar el fingerprint sigue autorizado → auto-aprueba.

Corre EN el device (build_csr usa el fingerprint HW real). Wire: llamar en cada sync + al arrancar.
"""
from __future__ import annotations
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from seal_csr import build_csr, generate_device_keypair          # noqa: E402
from seal_sync_auth import load_device_token                     # noqa: E402
from seal_token_store import (                                  # noqa: E402
    store_central_public_key,
    store_token,
)

RENEW_THRESHOLD_S = 6 * 3600  # renovar si quedan <6h de vida (margen amplio dentro de las 24h)


def _post_csr(endpoint: str, csr: dict, timeout: float = 10.0) -> dict:
    url = endpoint.rstrip("/") + "/csr"
    data = json.dumps(csr).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "reason": f"http_{e.code}"}
    except Exception as e:
        return {"ok": False, "reason": f"net_{type(e).__name__}"}


def renew_if_needed(agent: str, endpoint: str, threshold_s: int = RENEW_THRESHOLD_S,
                    force: bool = False) -> dict:
    """Renueva el token del device si le quedan < threshold_s de vida (o si no hay/está expirado).
    Idempotente: si el token está fresco, NO hace nada (no toca la red)."""
    agent = (agent or "").strip().upper()
    now = int(time.time())
    try:
        token = load_device_token(agent)
    except Exception:
        token = None
    exp = int(token["expires_at"]) if token and token.get("expires_at") else 0
    remaining = exp - now
    if token and not force and remaining > threshold_s:
        return {"renewed": False, "reason": "token_fresco", "expires_in_h": round(remaining / 3600, 1)}

    # RENOVAR: keypair efímero (solo PoP), reusar device_id del token actual (no prolifera)
    priv, _ = generate_device_keypair()
    device_id = token.get("device_id") if token else None
    csr = build_csr(agent, priv, device_id=device_id)
    res = _post_csr(endpoint, csr)
    if res.get("ok") and res.get("approved") and isinstance(res.get("token"), dict):
        try:
            public_key = res.get("central_public_key_b64")
            if not isinstance(public_key, str):
                raise RuntimeError("respuesta sin clave pública central")
            store_central_public_key(public_key)
            store_token(agent, res["token"])
        except Exception as e:
            return {"renewed": False, "reason": f"store_fail_{type(e).__name__}", "token_issued": True}
        new_exp = int(res["token"].get("expires_at", 0))
        return {"renewed": True, "reason": "rotado", "new_expires_in_h": round((new_exp - now) / 3600, 1),
                "was_expired": remaining <= 0, "device_id": res["token"].get("device_id")}
    return {"renewed": False, "reason": res.get("reason", "no_aprobado"), "detail": res}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("agent")
    ap.add_argument("--endpoint", default="http://localhost:8778")
    ap.add_argument("--threshold-h", type=float, default=6.0)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    res = renew_if_needed(a.agent, a.endpoint, int(a.threshold_h * 3600), a.force)
    print(json.dumps(res, ensure_ascii=False))
    sys.exit(0 if res.get("renewed") or res.get("reason") == "token_fresco" else 1)


if __name__ == "__main__":
    main()
