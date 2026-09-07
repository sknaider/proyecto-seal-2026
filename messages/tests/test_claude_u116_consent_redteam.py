"""Red-team of the u116 Claude broker CONSENT gate (ALICE, 2026-08-10).

Complements ``test_claude_u116_broker.py`` (which already covers per-field
contract fail-closed, exact-byte binding and live revocation). This file adds
the cases surfaced by the pre-activation red-team that were NOT yet pinned in
CI, so the Ed25519 gate cannot regress silently:

  * signature/public-key must match (substituted verifier key is rejected),
  * the validity window is enforced (an expired consent = replay is rejected),
  * scope widening is rejected,
  * and a CHARACTERIZATION test documenting that the trust root is *whatever
    public key sits next to the consent* — the gate proves integrity, not
    William's authenticity. Authenticity rests entirely on ROOT CUSTODY of
    /etc/seal/claude-u116/ (verified operationally, not in CI). If someone
    later pins William's public key, the characterization test below will fail
    and force a conscious update — which is the point.

Every case exercises the real ``load_signed_consent``; nothing is reimplemented.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from messages.claude_u116_broker import BrokerDenied, load_signed_consent


def _valid_payload() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "schema": "seal.external-model-consent.v2",
        "instance": "JARVIS-u116",
        "provider": "Anthropic",
        "model": "claude-sonnet-5",
        "subject_id": "seal-user-id:116",
        "consented": True,
        "scope": "u116-complete-prompt-to-anthropic",
        "data_classes": [
            "curated_technical_context",
            "professor_chat_history",
            "public_voice_few_shot",
            "system_prompt",
        ],
        "accepted_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
        "recorded_by": "William",
        "evidence_ref": "seal-chat:db_12345",
    }


def _canonical(payload: dict) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _private(path: Path, data: bytes, mode: int = 0o600) -> Path:
    path.write_bytes(data)
    path.chmod(mode)
    return path


def _materialize(
    tmp_path: Path,
    payload: dict,
    *,
    signer: Ed25519PrivateKey,
    publisher: Ed25519PrivateKey,
    tag: str = "c",
) -> tuple[Path, Path, Path]:
    """Write consent.json/.sig/public.pem. ``signer`` signs the bytes; the
    published verifier key belongs to ``publisher`` (usually the same key)."""
    raw = _canonical(payload)
    consent = _private(tmp_path / f"{tag}.json", raw)
    signature = _private(tmp_path / f"{tag}.sig", base64.b64encode(signer.sign(raw)) + b"\n")
    public = _private(
        tmp_path / f"{tag}.pem",
        publisher.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
        0o444,
    )
    return consent, signature, public


def test_valid_consent_is_accepted(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    c, s, p = _materialize(tmp_path, _valid_payload(), signer=key, publisher=key)
    payload = load_signed_consent(c, s, p)
    assert payload["recorded_by"] == "William"


def test_signature_and_public_key_must_match(tmp_path: Path) -> None:
    """Signed by KEY but the published verifier key belongs to ATTACKER: reject.

    This pins that the gate actually runs Ed25519.verify() against the published
    key — a substituted verifier that does not match the signature fails closed.
    """
    key = Ed25519PrivateKey.generate()
    attacker = Ed25519PrivateKey.generate()
    c, s, p = _materialize(tmp_path, _valid_payload(), signer=key, publisher=attacker)
    with pytest.raises(BrokerDenied, match="signature"):
        load_signed_consent(c, s, p)


def test_expired_window_is_rejected_replay(tmp_path: Path) -> None:
    """A correctly-signed but expired consent (a replayed old grant) is denied."""
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    payload = _valid_payload()
    payload["accepted_at"] = (now - timedelta(days=2)).isoformat()
    payload["expires_at"] = (now - timedelta(days=1)).isoformat()
    c, s, p = _materialize(tmp_path, payload, signer=key, publisher=key)
    with pytest.raises(BrokerDenied, match="validity window"):
        load_signed_consent(c, s, p)


def test_overlong_window_is_rejected(tmp_path: Path) -> None:
    """A consent whose validity window exceeds one year is denied."""
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    payload = _valid_payload()
    payload["accepted_at"] = (now - timedelta(minutes=1)).isoformat()
    payload["expires_at"] = (now + timedelta(days=400)).isoformat()
    c, s, p = _materialize(tmp_path, payload, signer=key, publisher=key)
    with pytest.raises(BrokerDenied, match="validity window"):
        load_signed_consent(c, s, p)


def test_window_one_second_over_365_days_is_rejected(tmp_path: Path) -> None:
    key = Ed25519PrivateKey.generate()
    now = datetime.now(timezone.utc)
    payload = _valid_payload()
    accepted = now - timedelta(minutes=1)
    payload["accepted_at"] = accepted.isoformat()
    payload["expires_at"] = (accepted + timedelta(days=365, seconds=1)).isoformat()
    c, s, p = _materialize(tmp_path, payload, signer=key, publisher=key)
    with pytest.raises(BrokerDenied, match="validity window"):
        load_signed_consent(c, s, p)


def test_scope_widening_is_rejected(tmp_path: Path) -> None:
    """A correctly-signed consent that widens scope beyond the pinned value is denied."""
    key = Ed25519PrivateKey.generate()
    payload = _valid_payload()
    payload["scope"] = "u116-anything-anywhere"
    c, s, p = _materialize(tmp_path, payload, signer=key, publisher=key)
    with pytest.raises(BrokerDenied, match="does not authorize"):
        load_signed_consent(c, s, p)


def test_trust_root_is_the_published_key_not_a_pinned_william_identity(tmp_path: Path) -> None:
    """CHARACTERIZATION (not a vulnerability within the threat model).

    A consent forged by an ATTACKER key, whose OWN public key is published next
    to it, is ACCEPTED. The signature therefore proves *integrity* (nobody
    tampered the bytes) but NOT *authenticity of William*: the code does not pin
    William's public key, it trusts whatever key sits in the config dir.

    Authenticity rests entirely on ROOT CUSTODY of /etc/seal/claude-u116/
    (root:seal-claude-u116-client 0750, verified operationally). A non-root
    actor cannot plant this key/consent, so this path is only reachable by root
    — who can already read the key directly and is out of scope by design
    (see claude_u116_broker module docstring).

    If William's public key is ever pinned (recommended hardening), THIS test
    must be updated to expect BrokerDenied — that update is the signal that the
    hardening landed.
    """
    attacker = Ed25519PrivateKey.generate()
    c, s, p = _materialize(tmp_path, _valid_payload(), signer=attacker, publisher=attacker)
    payload = load_signed_consent(c, s, p)  # accepted: trust root == published key
    assert payload["recorded_by"] == "William"  # the *field* says William; the *key* is the attacker's
