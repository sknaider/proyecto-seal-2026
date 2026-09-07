from __future__ import annotations

import io
import base64
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "memory"))
sys.path.insert(0, str(ROOT / "tools"))

import minisoul_sync_daemon as daemon
import seal_token_store
from cryptography.hazmat.primitives import serialization
from seal_sync_auth import issue_revocation_receipt
from seal_token import generate_keypair, issue_token


def _http_error(reason: str | None, receipt: dict | None = None) -> urllib.error.HTTPError:
    payload = {"status": "rejected", "reason": reason}
    if receipt is not None:
        payload["revocation_receipt"] = receipt
    body = b"not-json" if reason is None else json.dumps(payload).encode("utf-8")
    return urllib.error.HTTPError(
        "http://central/sync",
        401,
        "Unauthorized",
        {},
        io.BytesIO(body),
    )


def _signed_fixture():
    private_key, public_key = generate_keypair()
    token = issue_token(
        "ADA", "device-a", "sha256:" + "a" * 64, private_key, ttl_s=3600
    )
    nonce = "a" * 32
    receipt = issue_revocation_receipt(token, nonce, private_key)
    raw_public_key = public_key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return token, nonce, receipt, base64.b64encode(raw_public_key).decode("ascii")


def test_signed_revoked_response_clears_only_its_exact_token(monkeypatch):
    token, nonce, receipt, public_key_b64 = _signed_fixture()
    results: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        seal_token_store,
        "clear_token_if_matches",
        lambda agent, expected: (
            results.append((agent, expected)) or seal_token_store.CAS_CLEARED
        ),
    )
    monkeypatch.setattr("secrets.token_hex", lambda _n: nonce)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            _http_error("revocado", receipt)
        ),
    )

    push = daemon.make_http_push(
        "http://central/sync", central_public_key_b64=public_key_b64
    )
    assert push(token, {"batch": []}) is False
    assert results == [("ADA", token)]


def test_unsigned_revocation_never_clears(monkeypatch):
    cleared: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        seal_token_store,
        "clear_token_if_matches",
        lambda agent, token: cleared.append((agent, token)),
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(_http_error("revocado")),
    )

    push = daemon.make_http_push("http://central/sync")
    assert push({"agent": "ADA", "jti": "old"}, {"batch": []}) is False
    assert cleared == []


def test_other_401_does_not_clear_local_token(monkeypatch):
    cleared: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        seal_token_store,
        "clear_token_if_matches",
        lambda agent, token: cleared.append((agent, token)),
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            _http_error("firma_invalida")
        ),
    )

    push = daemon.make_http_push("http://central/sync")
    assert push({"agent": "ADA"}, {"batch": []}) is False
    assert cleared == []


def test_malformed_401_does_not_clear_local_token(monkeypatch):
    cleared: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        seal_token_store,
        "clear_token_if_matches",
        lambda agent, token: cleared.append((agent, token)),
    )
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(_http_error(None)),
    )

    push = daemon.make_http_push("http://central/sync")
    assert push({"agent": "ADA"}, {"batch": []}) is False
    assert cleared == []
