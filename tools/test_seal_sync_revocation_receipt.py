from __future__ import annotations

from seal_sync_auth import (
    issue_revocation_receipt,
    verify_revocation_receipt,
)
from seal_token import generate_keypair, issue_token
from seal_sync_endpoint import _attach_revocation_receipt


NOW = 1_800_000_000


def _fixture():
    private_key, public_key = generate_keypair()
    token = issue_token(
        "ADA",
        "device-a",
        "sha256:" + "a" * 64,
        private_key,
        ttl_s=3600,
    )
    nonce = "n" * 32
    receipt = issue_revocation_receipt(
        token, nonce, private_key, now=NOW
    )
    return private_key, public_key, token, nonce, receipt


def test_signed_revocation_receipt_is_bound_and_fresh():
    _, public_key, token, nonce, receipt = _fixture()
    assert verify_revocation_receipt(
        receipt, public_key, token, nonce, now=NOW
    ) == (True, "ok")


def test_receipt_for_other_request_is_rejected():
    _, public_key, token, _, receipt = _fixture()
    ok, reason = verify_revocation_receipt(
        receipt, public_key, token, "x" * 32, now=NOW
    )
    assert not ok and reason == "receipt_binding_request_nonce"


def test_receipt_for_other_token_is_rejected():
    private_key, public_key, token, nonce, receipt = _fixture()
    other = issue_token(
        "ADA",
        "device-a",
        token["device_fingerprint"],
        private_key,
        ttl_s=3600,
    )
    ok, reason = verify_revocation_receipt(
        receipt, public_key, other, nonce, now=NOW
    )
    assert not ok and reason in {"receipt_binding_jti", "receipt_binding_token_digest"}


def test_expired_receipt_is_rejected():
    _, public_key, token, nonce, receipt = _fixture()
    assert verify_revocation_receipt(
        receipt, public_key, token, nonce, now=NOW + 61
    ) == (False, "receipt_vencido")


def test_tampered_receipt_is_rejected():
    _, public_key, token, nonce, receipt = _fixture()
    receipt["agent"] = "JARVIS"
    assert verify_revocation_receipt(
        receipt, public_key, token, nonce, now=NOW
    ) == (False, "receipt_firma_invalida")


def test_endpoint_attaches_receipt_only_to_nonce_bound_revocation():
    private_key, public_key, token, nonce, _ = _fixture()
    result = _attach_revocation_receipt(
        401,
        {"status": "rejected", "reason": "revocado"},
        {"request_nonce": nonce},
        token,
        private_key,
    )
    assert verify_revocation_receipt(
        result["revocation_receipt"], public_key, token, nonce
    ) == (True, "ok")

    unsigned = _attach_revocation_receipt(
        401,
        {"status": "rejected", "reason": "firma_invalida"},
        {"request_nonce": nonce},
        token,
        private_key,
    )
    assert "revocation_receipt" not in unsigned
