from __future__ import annotations

import time

import seal_token_rotation as rotation


def _expired_token():
    return {
        "agent": "ADA",
        "device_id": "device-a",
        "expires_at": int(time.time()) - 1,
    }


def test_rotation_pins_central_key_before_storing_token(monkeypatch):
    token = {**_expired_token(), "jti": "new"}
    events = []
    monkeypatch.setattr(rotation, "load_device_token", lambda _agent: _expired_token())
    monkeypatch.setattr(rotation, "generate_device_keypair", lambda: (object(), object()))
    monkeypatch.setattr(
        rotation,
        "build_csr",
        lambda agent, _private, device_id=None: {
            "agent": agent,
            "device_id": device_id,
        },
    )
    monkeypatch.setattr(
        rotation,
        "_post_csr",
        lambda *_args: {
            "ok": True,
            "approved": True,
            "token": token,
            "central_public_key_b64": "pinned-key",
        },
    )
    monkeypatch.setattr(
        rotation,
        "store_central_public_key",
        lambda value: events.append(("pin", value)),
    )
    monkeypatch.setattr(
        rotation,
        "store_token",
        lambda agent, value: events.append(("token", agent, value)),
    )

    result = rotation.renew_if_needed("ADA", "http://central", force=True)
    assert result["renewed"] is True
    assert events == [
        ("pin", "pinned-key"),
        ("token", "ADA", token),
    ]


def test_rotation_rejects_approved_response_without_central_key(monkeypatch):
    token = {**_expired_token(), "jti": "new"}
    stored = []
    monkeypatch.setattr(rotation, "load_device_token", lambda _agent: _expired_token())
    monkeypatch.setattr(rotation, "generate_device_keypair", lambda: (object(), object()))
    monkeypatch.setattr(rotation, "build_csr", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        rotation,
        "_post_csr",
        lambda *_args: {"ok": True, "approved": True, "token": token},
    )
    monkeypatch.setattr(
        rotation, "store_token", lambda *_args: stored.append(True)
    )

    result = rotation.renew_if_needed("ADA", "http://central", force=True)
    assert result["renewed"] is False
    assert result["reason"] == "store_fail_RuntimeError"
    assert stored == []
