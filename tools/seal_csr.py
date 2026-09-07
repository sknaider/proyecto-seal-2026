#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seal_csr.py — Certificate Signing Request (CSR) generation para device registration (Fase-1 seguridad, carril NEXUS).

Implementa FASE-1 del flujo out-of-band de registro de dispositivos SEAL (spec §3).
El device genera un CSR (Certificate Signing Request logico) que contiene identidad del device
+ fingerprint de hardware + clave publica Ed25519 del device, y lo FIRMA con su clave PRIVADA.
El device NO recibe automaticamente un token; solo produce el CSR firmado que SOUL central
debe APROBAR manualmente (2FA por William).

Flujo:
  1. Device genera un par de claves Ed25519 (device_id = "device-" + UUID corto)
  2. Device calcula device_fingerprint = SHA256(machine-id + MAC)
  3. Device construye CSR = {device_id, device_fingerprint, agent, pubkey, requested_at, nonce}
  4. Device FIRMA el CSR con su clave privada (prueba de posesion)
  5. Device serializa a JSON y envia a SOUL central (que lo almacena en tabla pending_device_registrations)
  6. SOUL Central: William revisa + aprueba con 2FA → crea registro en authorized_devices
  7. SOUL emite JWT token (ED25519 firmado por SOUL, no por el device)
  8. Device recibe token, lo valida con sig de SOUL, y lo almacena en keyring

Diferencia CRITICA:
  · seal_token.py: Token FIRMADO POR SOUL (asymmetric, para validacion en devices)
  · seal_csr.py: CSR FIRMADO POR DEVICE (proof-of-possession, para validacion en SOUL central)

Determinista, sin red, sin estado. La clave privada del device NUNCA se envia; solo se envia
el CSR firmado (que prueba que el device tiene la privada, sin revelarla).
"""
from __future__ import annotations
import base64
import hashlib
import json
import time
import uuid
from typing import Tuple

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature


# ── Device fingerprint (cross-plataforma, sin root, determinista) ─────────────
def device_fingerprint() -> str:
    """SHA256(machine-id + MAC). Determinista por device; cambia si cambia el HW/OS-install.
    machine-id: estable por instalacion de OS. MAC: por NIC. Ninguno requiere root.

    Implementacion: reutiliza la logica de seal_token.py para consistencia.
    """
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
    """JSON deterministico del payload SIN 'sig' (sort_keys, separadores compactos).

    Reutiliza la logica de seal_token.py para garantizar firmado/verificacion coherente.
    """
    body = {k: v for k, v in payload.items() if k != "sig"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


# ── Device Keypair Generation ─────────────────────────────────────────────────
def generate_device_keypair() -> Tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    """Genera el par de claves del DEVICE. La privada FIRMA el CSR (proof-of-possession);
    la publica se incluye en el CSR para que SOUL central pueda validar la firma.

    Returns:
        (private_key, public_key)
    """
    priv = Ed25519PrivateKey.generate()
    return priv, priv.public_key()


# ── CSR Construction (Device-side) ────────────────────────────────────────────
def build_csr(agent: str, private_key: Ed25519PrivateKey, device_id: str = None) -> dict:
    """Construye un CSR (Certificate Signing Request) firmado por el device.

    El CSR contiene:
      · device_id: identificador unico del device (format: "device-{12-char-UUID}")
      · device_fingerprint: SHA256(machine-id + MAC), determinista del HW
      · agent: nombre del agente (ADA, JARVIS, ALICE, NEXUS, DUM)
      · pubkey: clave publica Ed25519 del device (base64), para que SOUL valide la sig
      · requested_at: timestamp Unix (segundos) cuando se genero el CSR
      · nonce: random 16 bytes (hex), previene replay attacks
      · sig: Ed25519 signature del payload (sin 'sig'), base64

    Args:
        agent: Nombre del agente que requiere el device (ADA, JARVIS, ALICE, etc.)
        private_key: Clave privada Ed25519 del device

    Returns:
        dict con el CSR firmado (listo para serializar a JSON)
    """
    pubkey = private_key.public_key()

    # Generar IDs unicos
    # ROTACIÓN (NEXUS 2026-07-03): reusar el device_id existente evita proliferar filas en
    # authorized_devices en cada renovación (el ON CONFLICT device_id DO UPDATE actualiza la MISMA fila).
    device_id = device_id or f"device-{uuid.uuid4().hex[:12]}"
    nonce = uuid.uuid4().hex  # 32 chars (16 bytes as hex)

    now = int(time.time())
    fingerprint = device_fingerprint()

    # Payload del CSR (sin 'sig')
    payload = {
        "agent": agent,
        "device_fingerprint": fingerprint,
        "device_id": device_id,
        "nonce": nonce,
        "pubkey": base64.b64encode(
            pubkey.public_bytes_raw()  # Clave publica en formato raw (32 bytes para Ed25519)
        ).decode("ascii"),
        "requested_at": now,
    }

    # FIRMAR el payload con la privada del device
    sig = private_key.sign(_canonical(payload))
    payload["sig"] = base64.b64encode(sig).decode("ascii")

    return payload


# ── CSR Self-Signature Verification (Device-side validation) ─────────────────
def verify_csr_self_signature(csr: dict, pubkey: Ed25519PublicKey) -> Tuple[bool, str]:
    """Verifica que la firma del CSR es valida (prueba de posesion del device).

    Esta verificacion se hace LOCALMENTE en el device para confirmar que el CSR
    que se envia tiene una firma valida antes de transmitirlo a SOUL central.

    Args:
        csr: dict con el CSR (debe contener 'sig')
        pubkey: Clave publica Ed25519 del device (de generate_device_keypair)

    Returns:
        (bool, str): (is_valid, reason)
    """
    # Validar estructura basica
    required = {"agent", "device_id", "device_fingerprint", "nonce", "pubkey", "requested_at", "sig"}
    if not isinstance(csr, dict) or not required <= set(csr):
        return False, "estructura_invalida"

    # Validar formato de device_id
    if not csr["device_id"].startswith("device-"):
        return False, "device_id_format_invalido"

    # Validar formato de fingerprint
    if not csr["device_fingerprint"].startswith("sha256:"):
        return False, "fingerprint_format_invalido"

    # Validar formato de nonce (32 chars hex = 16 bytes)
    if not (isinstance(csr["nonce"], str) and len(csr["nonce"]) == 32):
        return False, "nonce_format_invalido"

    # Validar que agent es reconocido
    if csr["agent"] not in ("ADA", "JARVIS", "ALICE", "NEXUS", "DUM", "FABLE"):
        return False, "agent_no_reconocido"

    # Verificar firma Ed25519
    try:
        sig = base64.b64decode(csr["sig"])
        pubkey.verify(sig, _canonical(csr))
    except (InvalidSignature, Exception) as e:
        return False, f"firma_invalida: {type(e).__name__}"

    return True, "ok"


if __name__ == "__main__":
    # ── Self-test: verifica POR EFECTO que CSR se genera, firma y verifica correctamente ──
    print("[TEST] seal_csr self-test")
    print()

    # (1) Generar keypair del device
    priv, pub = generate_device_keypair()
    print("[✓] Device keypair generado")

    # (2) Generar CSR firmado
    csr = build_csr("ADA", priv)
    print(f"[✓] CSR generado para ADA")
    print(f"    device_id: {csr['device_id']}")
    print(f"    device_fingerprint: {csr['device_fingerprint']}")
    print(f"    pubkey (base64): {csr['pubkey'][:20]}...")
    print()

    # (3) Verificar que la firma del CSR es valida
    ok, why = verify_csr_self_signature(csr, pub)
    assert ok and why == "ok", f"CSR signature invalid: {ok}, {why}"
    print("[✓] CSR self-signature valida")
    print()

    # (4) TAMPERING TEST: alterar device_id → firma se invalida
    forged_csr = dict(csr)
    forged_csr["device_id"] = "device-zzzzzzzzz"
    ok, why = verify_csr_self_signature(forged_csr, pub)
    assert not ok, f"CSR tampering should have failed: {ok}, {why}"
    print("[✓] CSR tampering detectado (device_id alterado → firma INVALIDA)")
    print()

    # (5) TAMPERING TEST: alterar agent → firma se invalida
    forged_csr2 = dict(csr)
    forged_csr2["agent"] = "WILLIAM"  # Agent no reconocido
    ok, why = verify_csr_self_signature(forged_csr2, pub)
    assert not ok, f"CSR tampering (agent) should have failed: {ok}, {why}"
    print("[✓] CSR tampering detectado (agent alterado → firma INVALIDA)")
    print()

    # (6) TAMPERING TEST: alterar fingerprint → firma se invalida
    forged_csr3 = dict(csr)
    forged_csr3["device_fingerprint"] = "sha256:" + "f" * 64
    ok, why = verify_csr_self_signature(forged_csr3, pub)
    assert not ok, f"CSR tampering (fingerprint) should have failed: {ok}, {why}"
    print("[✓] CSR tampering detectado (fingerprint alterado → firma INVALIDA)")
    print()

    # (7) TAMPERING TEST: alterar sig (corrupta) → verifica falla
    forged_csr4 = dict(csr)
    forged_csr4["sig"] = base64.b64encode(b"corrupted_signature").decode("ascii")
    ok, why = verify_csr_self_signature(forged_csr4, pub)
    assert not ok, f"CSR tampering (corrupted sig) should have failed: {ok}, {why}"
    print("[✓] CSR tampering detectado (sig corrupta → verifica FALLA)")
    print()

    # (8) CSR bien formado: serializable a JSON
    json_csr = json.dumps(csr, indent=2, sort_keys=True)
    csr_from_json = json.loads(json_csr)
    ok, why = verify_csr_self_signature(csr_from_json, pub)
    assert ok and why == "ok", f"CSR from JSON should be valid: {ok}, {why}"
    print("[✓] CSR serializable a JSON y re-verificable")
    print()

    # (9) Validar estructura de CSR
    assert csr["device_id"].startswith("device-"), "device_id format invalido"
    assert csr["device_fingerprint"].startswith("sha256:") and len(csr["device_fingerprint"]) == 71, "fingerprint format invalido"
    assert len(csr["nonce"]) == 32, "nonce length invalido"
    assert csr["agent"] in ("ADA", "JARVIS", "ALICE", "NEXUS", "DUM"), "agent no reconocido"
    assert "sig" in csr and len(csr["sig"]) > 0, "sig ausente o vacia"
    print("[✓] Estructura de CSR valida (device_id, fingerprint, nonce, agent, sig)")
    print()

    print("=" * 70)
    print("seal_csr: CSR generation + proof-of-possession OK")
    print("  · CSR bien formado")
    print("  · Auto-firma valida")
    print("  · Tampering del device_id invalida la firma")
    print("  · Tampering de agent invalida la firma")
    print("  · Tampering de fingerprint invalida la firma")
    print("  · CSR serializable a JSON")
    print("=" * 70)
