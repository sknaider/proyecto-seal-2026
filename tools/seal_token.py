#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_token.py — Token de auth device-bound para agentes SEAL distribuidos (Fase-1 seguridad, carril NEXUS).

Implementa el CORE del gate de autorizacion disenado en spec/FASE1_SECURITY_AUTH_GATE_NEXUS.md.
Cierra los 2 huecos que cazo el red-team del Workflow (2-jul):
  · HUECO #1 (token robado usable en otro device): el token ATA el device_fingerprint DENTRO del
    payload FIRMADO (Ed25519). Un token emitido para el device A FALLA la validacion en el device B
    (fingerprint mismatch) — probado por efecto en el self-test.
  · HUECO #2 (sin revocacion): validate_token chequea el `jti` (nonce) contra un set de revocados.

Formato de token (payload firmado):
  {agent, device_id, device_fingerprint, issued_at, expires_at, jti, sig}
  sig = base64( Ed25519.sign( canonical_json(payload sin 'sig') ) )  con la clave PRIVADA de SOUL central.

Determinista, sin red, sin estado. La clave privada vive SOLO en SOUL central (los devices solo verifican
con la publica). Cross-plataforma: fingerprint = SHA256(machine-id + MAC), sin root.
"""
from __future__ import annotations
import base64
import hashlib
import json
import time
import uuid
from typing import Iterable

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature

DEFAULT_TTL_S = 86400  # 24h (mitigado por fingerprint binding + revocacion)


# ── Device fingerprint (cross-plataforma, sin root, determinista) ─────────────
def device_fingerprint() -> str:
    """SHA256(machine-id + MAC). Determinista por device; cambia si cambia el HW/OS-install.
    machine-id: estable por instalacion de OS. MAC: por NIC. Ninguno requiere root."""
    parts = []
    try:
        with open("/etc/machine-id") as fh:
            parts.append(fh.read().strip())
    except Exception:
        pass
    try:
        parts.append(hex(uuid.getnode()))  # MAC
    except Exception:
        pass
    if not parts:
        raise RuntimeError("no se pudo derivar fingerprint del device")
    raw = "|".join(parts).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


# ── Firma canonica ────────────────────────────────────────────────────────────
def _canonical(payload: dict) -> bytes:
    """JSON deterministico del payload SIN 'sig' (sort_keys, separadores compactos)."""
    body = {k: v for k, v in payload.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def generate_keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Genera el par de claves de SOUL central. La privada FIRMA tokens; la publica los verifica."""
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()


def issue_token(agent: str, device_id: str, fingerprint: str,
                private_key: Ed25519PrivateKey, ttl_s: int = DEFAULT_TTL_S) -> dict:
    """Emite un token firmado que ata al agente + device + fingerprint. Solo SOUL central (tiene la privada)."""
    now = int(time.time())
    payload = {
        "agent": agent,
        "device_id": device_id,
        "device_fingerprint": fingerprint,
        "issued_at": now,
        "expires_at": now + int(ttl_s),
        "jti": uuid.uuid4().hex,   # id unico del token (para revocacion)
    }
    sig = private_key.sign(_canonical(payload))
    payload["sig"] = base64.b64encode(sig).decode("ascii")
    return payload


def validate_token(token: dict, public_key: Ed25519PublicKey, local_fingerprint: str,
                   revoked_jtis: Iterable[str] = ()) -> tuple[bool, str]:
    """Valida un token EN el device. Fail-closed: devuelve (False, razon) ante cualquier duda.
    Chequeos (en orden): estructura → firma → expiracion → FINGERPRINT del device → revocacion.
    """
    required = {"agent", "device_id", "device_fingerprint", "issued_at", "expires_at", "jti", "sig"}
    if not isinstance(token, dict) or not required <= set(token):
        return False, "estructura_invalida"

    # 1) Firma Ed25519 (no se puede forjar sin la privada de SOUL) — sobre el payload canonico sin 'sig'
    try:
        sig = base64.b64decode(token["sig"])
        public_key.verify(sig, _canonical(token))
    except (InvalidSignature, Exception):
        return False, "firma_invalida"

    # 2) Expiracion
    if int(time.time()) >= int(token["expires_at"]):
        return False, "expirado"

    # 3) HUECO #1: el token DEBE ser de ESTE device (fingerprint binding). Token robado en otro device -> falla.
    if token["device_fingerprint"] != local_fingerprint:
        return False, "fingerprint_mismatch"

    # 4) HUECO #2: revocacion por jti
    if token["jti"] in set(revoked_jtis):
        return False, "revocado"

    return True, "ok"


if __name__ == "__main__":
    # ── Self-test: verifica POR EFECTO los 2 huecos cerrados ──────────────────
    priv, pub = generate_keypair()
    FP_A = "sha256:" + "a" * 64      # device A
    FP_B = "sha256:" + "b" * 64      # device B (otro device)

    tok = issue_token("ADA", "device-A", FP_A, priv, ttl_s=3600)

    # (1) Camino feliz: el mismo device valida OK
    ok, why = validate_token(tok, pub, FP_A)
    assert ok and why == "ok", (ok, why)

    # (2) HUECO #1 CERRADO: token robado usado en OTRO device (FP_B) -> RECHAZADO por fingerprint
    ok, why = validate_token(tok, pub, FP_B)
    assert not ok and why == "fingerprint_mismatch", (ok, why)

    # (3) Forja: atacante altera el 'agent' -> la firma no cuadra -> RECHAZADO
    forged = dict(tok); forged["agent"] = "WILLIAM"
    ok, why = validate_token(forged, pub, FP_A)
    assert not ok and why == "firma_invalida", (ok, why)

    # (4) Forja sin la privada de SOUL: atacante genera su propio par y firma -> RECHAZADO con la pub real
    ev_priv, _ = generate_keypair()
    evil = issue_token("ADA", "device-A", FP_A, ev_priv, ttl_s=3600)
    ok, why = validate_token(evil, pub, FP_A)
    assert not ok and why == "firma_invalida", (ok, why)

    # (5) Expiracion: token vencido -> RECHAZADO
    exp = issue_token("ADA", "device-A", FP_A, priv, ttl_s=-1)
    ok, why = validate_token(exp, pub, FP_A)
    assert not ok and why == "expirado", (ok, why)

    # (6) HUECO #2 CERRADO: token revocado (por jti) -> RECHAZADO
    ok, why = validate_token(tok, pub, FP_A, revoked_jtis={tok["jti"]})
    assert not ok and why == "revocado", (ok, why)

    # (7) fingerprint real del device es derivable
    fp = device_fingerprint()
    assert fp.startswith("sha256:") and len(fp) == 71, fp

    print("seal_token: Ed25519 device-bound OK — hueco#1(fingerprint) + hueco#2(revocacion) + forja + expiracion, todo por efecto")
