"""Cryptographic primitives for SSAI SHADOW M1.

This module has no key persistence and no production integration.  Private keys
are injected by the caller; :func:`generate_private_key` is intended for tests
and isolated SHADOW demonstrations only.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .canonical import canonicalize


DOMAIN_SEPARATOR = b"SOUL-ID-MANIFEST-V1\x00"
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]*$")


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str, *, expected_length: int) -> bytes:
    if type(value) is not str or not _B64URL_RE.fullmatch(value):
        raise ValueError("invalid base64url encoding")
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid base64url encoding") from exc
    if len(decoded) != expected_length or _b64url_encode(decoded) != value:
        raise ValueError("non-canonical base64url value or wrong length")
    return decoded


def generate_private_key() -> Ed25519PrivateKey:
    """Generate an ephemeral Ed25519 private key without persisting it."""

    return Ed25519PrivateKey.generate()


def public_key_b64(
    key: Ed25519PrivateKey | Ed25519PublicKey | bytes | str,
) -> str:
    """Return a raw 32-byte Ed25519 public key as unpadded base64url."""

    if isinstance(key, Ed25519PrivateKey):
        public_key = key.public_key()
    elif isinstance(key, Ed25519PublicKey):
        public_key = key
    elif type(key) is bytes:
        public_key = Ed25519PublicKey.from_public_bytes(key)
    elif type(key) is str:
        public_key = Ed25519PublicKey.from_public_bytes(
            _b64url_decode(key, expected_length=32)
        )
    else:
        raise TypeError("unsupported Ed25519 key type")

    raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _b64url_encode(raw)


def manifest_payload(manifest: Any) -> bytes:
    """Build the exact domain-separated bytes covered by the signature."""

    return DOMAIN_SEPARATOR + canonicalize(manifest)


def manifest_digest(manifest: Any) -> str:
    """Return the content address of the exact signed payload."""

    return "sha256:" + hashlib.sha256(manifest_payload(manifest)).hexdigest()


def sign_manifest(manifest: Any, private_key: Ed25519PrivateKey) -> str:
    """Sign a manifest and return an unpadded base64url Ed25519 signature."""

    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("private_key must be an Ed25519PrivateKey")
    return _b64url_encode(private_key.sign(manifest_payload(manifest)))


def _coerce_public_key(
    key: Ed25519PrivateKey | Ed25519PublicKey | bytes | str,
) -> Ed25519PublicKey:
    if isinstance(key, Ed25519PrivateKey):
        return key.public_key()
    if isinstance(key, Ed25519PublicKey):
        return key
    if type(key) is bytes:
        return Ed25519PublicKey.from_public_bytes(key)
    if type(key) is str:
        return Ed25519PublicKey.from_public_bytes(
            _b64url_decode(key, expected_length=32)
        )
    raise TypeError("unsupported Ed25519 public key type")


def verify_manifest(
    manifest: Any,
    signature: str,
    public_key: Ed25519PrivateKey | Ed25519PublicKey | bytes | str,
) -> bool:
    """Verify a manifest signature, returning ``False`` on any invalid input."""

    try:
        decoded_signature = _b64url_decode(signature, expected_length=64)
        key = _coerce_public_key(public_key)
        key.verify(decoded_signature, manifest_payload(manifest))
        return True
    except (InvalidSignature, TypeError, ValueError, UnicodeError):
        return False
