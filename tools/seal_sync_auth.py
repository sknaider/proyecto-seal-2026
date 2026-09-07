#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_sync_auth.py — Interfaz de autenticación del SYNC device↔central (Fase-1 seguridad, carril NEXUS).

Es el DROP-IN que cablea el token (seal_token.py) al sync del mini-SOUL (minisoul_sync_policy.py de JARVIS).
El sync device→central manda memorias/distilled; esta capa asegura:
  · El request está AUTENTICADO (token Ed25519 válido, no expirado, no revocado).
  · El sync SOLO puede escribir memorias del AGENTE DEL TOKEN — cierra el residual del red-team
    (IDOR / cross-agent): la autoridad viene del token FIRMADO, NO de un campo/param del request.

DIFERENCIA device vs central:
  - En el DEVICE: seal_token.validate_token() valida TAMBIÉN el fingerprint (token atado a ESTE device).
  - En CENTRAL: NO se valida fingerprint (central no es el device) — se valida FIRMA + expiración +
    revocación, y de ahí se deriva el `agent` autenticado, contra el que se ENFORZA el scope.

Sin red. Reusa seal_token (misma firma canónica). cryptography ya instalado; sin deps nuevas.
"""
from __future__ import annotations
import base64
import hashlib
import hmac
import json
import time
from typing import Iterable

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from seal_token import _canonical  # misma firma canónica (payload sin 'sig')


# ── Device side: adjuntar el token al request de sync ─────────────────────────
REVOCATION_RECEIPT_SCHEMA = "seal.sync.revocation-receipt.v1"


def attach_token(
    payload: dict,
    token: dict,
    *,
    request_nonce: str | None = None,
) -> dict:
    """Envuelve el payload del sync con el token del device (para el header/campo Authorization)."""
    wrapped = {"auth_token": token, "payload": payload}
    if request_nonce is not None:
        wrapped["request_nonce"] = request_nonce
    return wrapped


def _token_digest(token: dict) -> str:
    raw = json.dumps(
        token, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def issue_revocation_receipt(
    token: dict,
    request_nonce: str,
    private_key: Ed25519PrivateKey,
    *,
    ttl_s: int = 60,
    now: int | None = None,
) -> dict:
    """Atestación central ligada al token y a una petición concreta."""
    now = int(time.time()) if now is None else int(now)
    receipt = {
        "schema": REVOCATION_RECEIPT_SCHEMA,
        "reason": "revocado",
        "agent": str(token["agent"]),
        "jti": str(token["jti"]),
        "token_digest": _token_digest(token),
        "request_nonce": str(request_nonce),
        "issued_at": now,
        "expires_at": now + int(ttl_s),
    }
    receipt["sig"] = base64.b64encode(
        private_key.sign(_canonical(receipt))
    ).decode("ascii")
    return receipt


def verify_revocation_receipt(
    receipt: dict,
    public_key: Ed25519PublicKey,
    token: dict,
    request_nonce: str,
    *,
    now: int | None = None,
) -> tuple[bool, str]:
    """Verifica firma, frescura y binding exacto antes de permitir un CAS."""
    required = {
        "schema", "reason", "agent", "jti", "token_digest",
        "request_nonce", "issued_at", "expires_at", "sig",
    }
    if not isinstance(receipt, dict) or not required <= set(receipt):
        return False, "receipt_estructura_invalida"
    if (
        receipt["schema"] != REVOCATION_RECEIPT_SCHEMA
        or receipt["reason"] != "revocado"
    ):
        return False, "receipt_tipo_invalido"
    try:
        signature = base64.b64decode(receipt["sig"], validate=True)
        public_key.verify(signature, _canonical(receipt))
    except (InvalidSignature, Exception):
        return False, "receipt_firma_invalida"
    now = int(time.time()) if now is None else int(now)
    try:
        issued_at = int(receipt["issued_at"])
        expires_at = int(receipt["expires_at"])
    except (TypeError, ValueError):
        return False, "receipt_tiempo_invalido"
    if issued_at > now + 5 or expires_at <= now or expires_at <= issued_at:
        return False, "receipt_vencido"
    expected = {
        "agent": str(token.get("agent", "")),
        "jti": str(token.get("jti", "")),
        "token_digest": _token_digest(token),
        "request_nonce": str(request_nonce),
    }
    for field, value in expected.items():
        if not hmac.compare_digest(str(receipt.get(field, "")), value):
            return False, f"receipt_binding_{field}"
    return True, "ok"


# ── Device side: PRESENTAR el token central-emitido (NO auto-firmar) ──────────
def load_device_token(agent: str) -> dict:
    """El device carga el token que SOUL CENTRAL le emitió (tras la aprobación human-gated de William) y
    lo PRESENTA. NO lo auto-firma — solo central tiene la clave de firma.

    Fix del must-fix Fase-2 (NEXUS 2026-07-02): reemplaza el `issue_token(...device_key...)` que auto-firmaba
    en el device (minisoul_sync_daemon.py:501). Auto-firmar era incorrecto: o filtraba la privada de SOUL al
    device, o el token no validaba contra la pubkey de SOUL. El modelo correcto: central EMITE (human-gated),
    device PRESENTA.

    Fail-closed: si no hay token almacenado → el device NO fue aprobado/provisionado aún → NO puede sincronizar.
    """
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parent))
    from seal_token_store import load_token
    tok = load_token(agent)
    if not tok:
        raise RuntimeError(
            f"device sin token central-emitido para '{agent}': no aprobado/provisionado. "
            f"Registrar via CSR (seal_csr) → aprobación de William → central emite. El device NO se auto-firma."
        )
    return tok


# ── Central side: autorizar un request de sync ────────────────────────────────
def authorize_sync(token: dict, public_key: Ed25519PublicKey,
                   revoked_jtis: Iterable[str] = ()) -> tuple[bool, str, str]:
    """Autoriza un request de sync EN central. Devuelve (ok, agent_autenticado, razon).
    Fail-closed. NO valida fingerprint (central no es el device); valida FIRMA + expiración + revocación.
    El `agent` devuelto es la ÚNICA autoridad de scope — el caller DEBE limitar toda escritura a ese agente.
    """
    required = {"agent", "device_id", "device_fingerprint", "issued_at", "expires_at", "jti", "sig"}
    if not isinstance(token, dict) or not required <= set(token):
        return False, "", "estructura_invalida"

    # 1) Firma Ed25519 (no forjable sin la privada de SOUL)
    try:
        sig = base64.b64decode(token["sig"])
        public_key.verify(sig, _canonical(token))
    except (InvalidSignature, Exception):
        return False, "", "firma_invalida"

    # 2) Expiración
    if int(time.time()) >= int(token["expires_at"]):
        return False, "", "expirado"

    # 3) Revocación
    if token["jti"] in set(revoked_jtis):
        return False, "", "revocado"

    # OK → el agente autenticado sale del token FIRMADO, no de ningún param externo.
    return True, str(token["agent"]), "ok"


def enforce_write_scope(authenticated_agent: str, target_agent: str) -> tuple[bool, str]:
    """Compuerta anti-IDOR / anti-cross-agent: el sync SOLO puede escribir memorias del agente autenticado.
    Un device autenticado como A NUNCA escribe/afecta memorias de B (aunque el request pida target=B).
    Devuelve (permitido, razon). Fail-closed: si no coincide, DENY.
    """
    if not authenticated_agent:
        return False, "sin_agente_autenticado"
    if target_agent != authenticated_agent:
        return False, f"cross_agent_denegado(auth={authenticated_agent},target={target_agent})"
    return True, "ok"


if __name__ == "__main__":
    # Self-test por efecto (reusa seal_token para emitir tokens reales).
    from seal_token import generate_keypair, issue_token, device_fingerprint

    priv, pub = generate_keypair()
    fp = device_fingerprint()
    tok_ada = issue_token("ADA", "dev-ADA", fp, priv, ttl_s=3600)

    # (1) request autenticado → devuelve el agente del token
    ok, agent, why = authorize_sync(tok_ada, pub)
    assert ok and agent == "ADA" and why == "ok", (ok, agent, why)

    # (2) scope: ADA autenticada escribe memorias de ADA → PERMITIDO
    allowed, r = enforce_write_scope(agent, "ADA")
    assert allowed, r
    # ADA autenticada intenta escribir memorias de JARVIS (IDOR) → DENEGADO
    allowed, r = enforce_write_scope(agent, "JARVIS")
    assert not allowed and r.startswith("cross_agent_denegado"), r

    # (3) token forjado (otro par de claves, sin la privada de SOUL) → RECHAZADO
    ev_priv, _ = generate_keypair()
    evil = issue_token("ADA", "dev-ADA", fp, ev_priv, ttl_s=3600)
    ok, agent, why = authorize_sync(evil, pub)
    assert not ok and why == "firma_invalida", (ok, why)

    # (4) atacante altera el agent del token para robar identidad → firma no cuadra → RECHAZADO
    forged = dict(tok_ada); forged["agent"] = "WILLIAM"
    ok, agent, why = authorize_sync(forged, pub)
    assert not ok and why == "firma_invalida", (ok, why)

    # (5) revocado → RECHAZADO
    ok, agent, why = authorize_sync(tok_ada, pub, revoked_jtis={tok_ada["jti"]})
    assert not ok and why == "revocado", (ok, why)

    # (6) attach_token envuelve bien
    wrapped = attach_token({"memories": [1, 2]}, tok_ada)
    assert wrapped["auth_token"] == tok_ada and wrapped["payload"] == {"memories": [1, 2]}

    print("seal_sync_auth: central valida firma/exp/revocación + agente-del-token + anti-IDOR/cross-agent, todo por efecto")
