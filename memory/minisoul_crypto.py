#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minisoul_crypto.py — Cifrado AT-REST del mini-SOUL local (Fase 3 hardening, JARVIS).
=====================================================================================
Cierra el caveat de FABLE: el mini-SOUL local era SQLite en PLANO → mismo-usuario OS o
robo-de-device leía el contenido en claro. Aquí ciframos el CONTENIDO sensible por-agente:
  - Clave por-agente derivada de un secreto en el KEYRING del OS (HKDF-SHA256).
  - AES-256-GCM (AEAD): confidencialidad + integridad (detecta tamper).
  - Fail-closed: clave equivocada o ciphertext manipulado → excepción, nunca plaintext parcial.

Efecto de aislamiento: el agente A NO puede descifrar el contenido de B (claves distintas por-agente).
Efecto anti-robo: un device robado da ciphertext inútil sin la clave (que vive en el keyring, no en el .db).

Hecho a mano a propósito (correctness > fan-out para cripto, doctrina NEXUS). Solo depende de `cryptography`.
"""
from __future__ import annotations
import os
import base64

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

_MAGIC = b"msc1"   # prefijo de versión del formato (magic + nonce(12) + ct)


def derive_agent_key(agent: str, keyring_secret: bytes) -> bytes:
    """Deriva una clave AES-256 por-agente desde el secreto del keyring del device.
    Distinta por agente → A no descifra lo de B aunque compartan el mismo secreto de device."""
    if not isinstance(keyring_secret, (bytes, bytearray)) or len(keyring_secret) < 16:
        raise ValueError("keyring_secret inválido (>=16 bytes requeridos)")
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32,
                salt=b"minisoul-at-rest-v1",
                info=f"agent:{agent}".encode("utf-8"))
    return hkdf.derive(bytes(keyring_secret))


def encrypt_field(plaintext: str, key: bytes) -> str:
    """Cifra un campo (ej. content) con AES-256-GCM. Devuelve base64(magic|nonce|ciphertext)."""
    if plaintext is None:
        return None
    if len(key) != 32:
        raise ValueError("key debe ser 32 bytes (AES-256)")
    nonce = os.urandom(12)                          # nonce único por cifrado (nunca reusar)
    ct = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.b64encode(_MAGIC + nonce + ct).decode("ascii")


def decrypt_field(token_b64: str, key: bytes) -> str:
    """Descifra un campo. Fail-closed: clave errónea / tamper → InvalidTag (excepción), NO plaintext."""
    if token_b64 is None:
        return None
    if len(key) != 32:
        raise ValueError("key debe ser 32 bytes (AES-256)")
    raw = base64.b64decode(token_b64)
    if raw[:4] != _MAGIC:
        raise ValueError("formato/verión inválido (magic mismatch)")
    nonce, ct = raw[4:16], raw[16:]
    return AESGCM(key).decrypt(nonce, ct, None).decode("utf-8")   # InvalidTag si key mala o tamper


def is_encrypted(value: str) -> bool:
    """True si el valor parece cifrado por este módulo (para migración incremental)."""
    if not isinstance(value, str):
        return False
    try:
        return base64.b64decode(value)[:4] == _MAGIC
    except Exception:
        return False


# ── self-test por efecto ──
if __name__ == "__main__":
    secret = os.urandom(32)  # en producción: del keyring del device (seal_token_store)
    ka = derive_agent_key("ADA", secret)
    kj = derive_agent_key("JARVIS", secret)
    msg = "memoria sensible de ADA: identidad + emoción"

    # 1) roundtrip
    ct = encrypt_field(msg, ka)
    assert decrypt_field(ct, ka) == msg, "roundtrip falló"
    print("1) roundtrip ADA:", "OK")

    # 2) aislamiento: la clave de JARVIS NO descifra lo de ADA
    try:
        decrypt_field(ct, kj)
        print("2) aislamiento cross-agent: FALLA ✗ (JARVIS descifró lo de ADA)")
    except Exception:
        print("2) aislamiento cross-agent: OK ✓ (JARVIS NO descifra lo de ADA)")

    # 3) tamper: alterar el ciphertext → falla-cerrado
    raw = bytearray(base64.b64decode(ct)); raw[-1] ^= 0x01
    try:
        decrypt_field(base64.b64encode(bytes(raw)).decode(), ka)
        print("3) tamper detection: FALLA ✗")
    except Exception:
        print("3) tamper detection: OK ✓ (ciphertext manipulado → rechazado)")

    # 4) el .db en disco no revela el plaintext
    assert msg not in ct, "el ciphertext contiene el plaintext ✗"
    print("4) at-rest: el plaintext NO aparece en el ciphertext:", "OK ✓")
    print("=== minisoul_crypto self-test PASSED ===")
