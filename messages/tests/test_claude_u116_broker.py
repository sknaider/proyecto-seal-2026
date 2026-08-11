from __future__ import annotations

import base64
import http.client
import http.server
import json
import os
import socket
import stat
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import messages.claude_u116_broker as broker_module
from messages.ada_user_clone_worker import _model_backend_health, _ollama_reply
from messages.claude_u116_broker import (
    ANTHROPIC_URL,
    MODEL,
    BrokerConfig,
    BrokerDenied,
    ClaudeBroker,
    _UnixHTTPServer,
    _normalize_messages,
    load_api_key,
    load_signed_consent,
    make_handler,
)


class FakeResponse:
    def __init__(self, payload: dict, request_id: str = "req_test_123"):
        self.payload = json.dumps(payload).encode()
        self.headers = {"request-id": request_id}

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, limit: int) -> bytes:
        assert limit > len(self.payload)
        return self.payload


def _private(path: Path, content: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content if isinstance(content, bytes) else content.encode())
    path.chmod(0o600)
    return path


def _consent() -> dict:
    accepted = datetime.now(timezone.utc) - timedelta(minutes=1)
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
        "accepted_at": accepted.isoformat(),
        "expires_at": (accepted + timedelta(days=30)).isoformat(),
        "recorded_by": "William",
        "evidence_ref": "seal-chat:db_128106",
    }


def _signed_consent(tmp_path: Path, payload: dict) -> tuple[Path, Path, Path, Ed25519PrivateKey]:
    private = Ed25519PrivateKey.generate()
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    consent = _private(tmp_path / "consent.json", raw)
    signature = _private(tmp_path / "consent.sig", base64.b64encode(private.sign(raw)) + b"\n")
    public = _private(
        tmp_path / "consent-public.pem",
        private.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        ),
    )
    return consent, signature, public, private


def _resign(config: BrokerConfig, payload: dict, private: Ed25519PrivateKey) -> None:
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    _private(config.consent_file, raw)
    _private(config.consent_signature_file, base64.b64encode(private.sign(raw)) + b"\n")


def _config(tmp_path: Path, **overrides) -> BrokerConfig:
    consent, signature, public, private = _signed_consent(tmp_path, _consent())
    values = {
        "key_file": _private(tmp_path / "key", "sk-ant-" + "x" * 40),
        "client_capability_file": _private(tmp_path / "cap", "A" * 48),
        "consent_file": consent,
        "consent_signature_file": signature,
        "consent_public_key_file": public,
        "socket_path": tmp_path / "broker.sock",
        "state_dir": tmp_path / "state",
    }
    values.update(overrides)
    config = BrokerConfig(**values)
    object.__setattr__(config, "_test_private_key", private)
    return config


def _success(answer: str = "Respuesta con voz propia") -> FakeResponse:
    return FakeResponse({
        "content": [{"type": "text", "text": answer}],
        "usage": {"input_tokens": 101, "output_tokens": 17},
    })


def _start(config: BrokerConfig, opener=None):
    broker = ClaudeBroker(config, opener=opener or (lambda *_a, **_k: _success()))
    server = _UnixHTTPServer(str(config.socket_path), make_handler(broker))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _auth(config: BrokerConfig) -> dict[str, str]:
    cap = config.client_capability_file.read_text().strip()
    return {"Authorization": f"Bearer {cap}", "X-SEAL-Instance": "JARVIS-u116"}


def _raw(sock_path: Path, request_bytes: bytes, timeout: float = 3) -> bytes:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(str(sock_path))
    sock.sendall(request_bytes)
    chunks = []
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk: break
            chunks.append(chunk)
    finally:
        sock.close()
    return b"".join(chunks)


def test_private_key_rejects_group_mode_symlink_and_invalid_shape(tmp_path: Path) -> None:
    key = _private(tmp_path / "key", "sk-ant-" + "x" * 40)
    assert load_api_key(key).startswith("sk-ant-")
    key.chmod(0o640)
    with pytest.raises(BrokerDenied, match="permissions"): load_api_key(key)
    key.chmod(0o600)
    link = tmp_path / "link"; link.symlink_to(key)
    with pytest.raises(BrokerDenied, match="unavailable"): load_api_key(link)
    _private(key, "not-a-key")
    with pytest.raises(BrokerDenied, match="invalid shape"): load_api_key(key)


@pytest.mark.parametrize("field,value,match", [
    ("consented", False, "does not authorize"),
    ("instance", "JARVIS-u999", "does not authorize"),
    ("subject_id", "professor:demo", "immutable u116"),
    ("recorded_by", "attacker", "recorder"),
    ("evidence_ref", "trust-me", "evidence"),
    ("data_classes", ["professor_chat_history"], "every outbound"),
])
def test_signed_consent_contract_fail_closed(tmp_path: Path, field: str, value, match: str) -> None:
    config = _config(tmp_path)
    payload = _consent(); payload[field] = value
    _resign(config, payload, config._test_private_key)
    with pytest.raises(BrokerDenied, match=match):
        load_signed_consent(config.consent_file, config.consent_signature_file, config.consent_public_key_file)


def test_consent_is_bound_to_exact_bytes(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.consent_file.write_bytes(config.consent_file.read_bytes() + b" ")
    with pytest.raises(BrokerDenied, match="signature"):
        load_signed_consent(config.consent_file, config.consent_signature_file, config.consent_public_key_file)


def test_request_contract_pins_model_and_rejects_tools_streaming() -> None:
    outbound = _normalize_messages({
        "model": "attacker", "stream": False,
        "messages": [{"role": "system", "content": "alma"}, {"role": "user", "content": "hola"}],
        "options": {"temperature": 999},
    }, 1500)
    assert outbound["model"] == MODEL and outbound["thinking"] == {"type": "disabled"}
    assert "temperature" not in outbound
    with pytest.raises(BrokerDenied, match="unsupported"):
        _normalize_messages({"messages": [], "tools": []}, 100)
    with pytest.raises(BrokerDenied, match="streaming"):
        _normalize_messages({"stream": True, "messages": [{"role": "user", "content": "x"}]}, 100)


def test_broker_pins_endpoint_and_audit_has_metadata_only(tmp_path: Path) -> None:
    captured = {}
    def opener(req, timeout):
        captured.update(url=req.full_url, headers=dict(req.header_items()), payload=json.loads(req.data))
        return _success()
    broker = ClaudeBroker(_config(tmp_path), opener=opener)
    prompt = "dato privado jamás en audit"
    assert broker.chat({"messages": [{"role": "user", "content": prompt}]})["done"]
    assert captured["url"] == ANTHROPIC_URL and captured["payload"]["model"] == MODEL
    audit = (tmp_path / "state/usage.jsonl").read_text()
    assert prompt not in audit and "sk-ant-" not in audit


def test_production_opener_disables_proxies(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    class FakeOpener:
        def open(self, *_a, **_k): return _success()
    def build(*handlers): captured["handlers"] = handlers; return FakeOpener()
    monkeypatch.setattr(broker_module.request, "build_opener", build)
    broker = ClaudeBroker(_config(tmp_path))
    assert captured["handlers"][0].proxies == {}
    assert isinstance(captured["handlers"][1], broker_module._DenyRedirect)
    assert broker.chat({"messages": [{"role": "user", "content": "hola"}]})["done"]


def test_signed_consent_revocation_is_live(tmp_path: Path) -> None:
    config = _config(tmp_path); calls = []
    broker = ClaudeBroker(config, opener=lambda *_a, **_k: calls.append(1) or _success())
    payload = _consent(); payload["consented"] = False
    _resign(config, payload, config._test_private_key)
    with pytest.raises(BrokerDenied, match="does not authorize"): broker.health()
    with pytest.raises(BrokerDenied, match="does not authorize"):
        broker.chat({"messages": [{"role": "user", "content": "hola"}]})
    assert calls == []


def test_rate_and_budget_controls_are_non_vacuous(tmp_path: Path) -> None:
    broker = ClaudeBroker(_config(tmp_path, requests_per_hour=1), opener=lambda *_a, **_k: _success())
    payload = {"messages": [{"role": "user", "content": "hola"}]}
    assert broker.chat(payload)["done"]
    with pytest.raises(BrokerDenied, match="hourly request limit"): broker.chat(payload)
    broker2 = ClaudeBroker(_config(tmp_path / "b", daily_budget_usd=0.05), opener=lambda *_a, **_k: _success())
    with pytest.raises(BrokerDenied, match="daily cost budget"):
        broker2.chat({"messages": [{"role": "user", "content": "á" * 20_000}]})


def test_unix_endpoint_denies_missing_wrong_capability_and_instance(tmp_path: Path) -> None:
    config = _config(tmp_path); server, thread = _start(config)
    body = b'{"messages":[{"role":"user","content":"hola"}]}'
    try:
        for headers in ("", "Authorization: Bearer wrong\r\nX-SEAL-Instance: JARVIS-u116\r\n", f"Authorization: Bearer {'A'*48}\r\nX-SEAL-Instance: JARVIS-u999\r\n"):
            response = _raw(config.socket_path, (f"POST /api/chat HTTP/1.1\r\nHost: x\r\n{headers}Content-Length: {len(body)}\r\n\r\n").encode() + body)
            assert b" 401 " in response
        assert _model_backend_health(f"unix://{config.socket_path}", MODEL, "A" * 48) == ("claude-broker-unix", True)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_auth_happens_before_body_and_slow_client_cannot_block_health(tmp_path: Path) -> None:
    config = _config(tmp_path); server, thread = _start(config)
    cap = config.client_capability_file.read_text().strip()
    try:
        start = time.monotonic()
        response = _raw(config.socket_path, b"POST /api/chat HTTP/1.1\r\nHost:x\r\nContent-Length:100\r\n\r\nx")
        assert b" 401 " in response and time.monotonic() - start < 1.0

        slow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); slow.settimeout(4)
        slow.connect(str(config.socket_path))
        slow.sendall((f"POST /api/chat HTTP/1.1\r\nHost:x\r\nAuthorization: Bearer {cap}\r\nX-SEAL-Instance: JARVIS-u116\r\nContent-Length:100\r\n\r\nx").encode())
        start = time.monotonic()
        assert _model_backend_health(f"unix://{config.socket_path}", MODEL, cap)[1] is True
        assert time.monotonic() - start < 1.0
        assert b" 408 " in slow.recv(65536)
        slow.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_disconnect_does_not_escape_as_server_error(tmp_path: Path) -> None:
    config = _config(tmp_path); server, thread = _start(config)
    errors = []
    server.handle_error = lambda *_args: errors.append("unhandled")
    cap = config.client_capability_file.read_text().strip()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); sock.connect(str(config.socket_path))
    body = b'{"messages":[{"role":"user","content":"hola"}]}'
    sock.sendall((f"POST /api/chat HTTP/1.1\r\nHost:x\r\nAuthorization: Bearer {cap}\r\nX-SEAL-Instance: JARVIS-u116\r\nContent-Length:{len(body)}\r\n\r\n").encode() + body)
    sock.close(); time.sleep(0.2)
    server.shutdown(); server.server_close(); thread.join(timeout=2)
    assert errors == []


def test_worker_e2e_uses_capability_but_never_provider_key(tmp_path: Path) -> None:
    config = _config(tmp_path); captured = {}
    def opener(req, timeout):
        headers = dict(req.header_items())
        captured["xapikey"] = headers.get("X-api-key")
        captured["has_auth"] = "Authorization" in headers
        return _success("Hola aislado")
    server, thread = _start(config, opener)
    cap = config.client_capability_file.read_text().strip()
    try:
        answer = _ollama_reply(MODEL, f"unix://{config.socket_path}", "JARVIS", "profesor", [{"role":"user","content":"Hola"}], "", None, cap)
        assert answer == "Hola aislado"
        with pytest.raises(RuntimeError, match="capability is missing"):
            _model_backend_health(f"unix://{config.socket_path}", MODEL)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    assert captured["xapikey"].startswith("sk-ant-")
    assert captured["has_auth"] is False
    assert stat.S_IMODE(config.key_file.stat().st_mode) == 0o600
