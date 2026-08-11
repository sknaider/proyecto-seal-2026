from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

from messages.ada_user_clone_worker import _fetch_inbox, _post_chat
from messages.u116_chat_relay import ChatRelay, RelayDenied, _Server, make_handler


TOKEN = "T" * 48


class Response:
    def __init__(self, payload: dict, status=200):
        self.raw = json.dumps(payload).encode(); self.status = status
        self.headers = {"Content-Type": "application/json"}
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, limit): assert limit > len(self.raw); return self.raw


def _token(tmp_path: Path) -> Path:
    path = tmp_path / "token"; path.write_text(TOKEN); path.chmod(0o600); return path


def test_relay_forwards_only_exact_inbox_and_send_contract(tmp_path: Path) -> None:
    seen = []
    def opener(req, timeout):
        seen.append((req.method, req.full_url, dict(req.header_items()), req.data))
        if req.method == "GET":
            return Response({"ok": True, "instance_id": "JARVIS-u116", "messages": [], "next_cursor": 7})
        return Response({"ok": True, "id": "db_1"})
    relay = ChatRelay(_token(tmp_path), opener=opener)
    assert relay.forward("GET", "/api/user-clones/inbox?cursor=0&limit=20")[0] == 200
    payload = {
        "from":"JARVIS", "to":"william2", "type":"conversation",
        "channel":"dm:jarvis:william2", "message":"hola", "session_key":TOKEN,
        "in_reply_to":"1", "instance_id":"JARVIS-u116", "idempotency_key":"x",
    }
    assert relay.forward("POST", "/api/agents/send", json.dumps(payload).encode())[0] == 200
    assert all(url.startswith("http://127.0.0.1:8765/") for _, url, _, _ in seen)
    assert all(headers.get("Authorization") == "Bearer " + TOKEN for _, _, headers, _ in seen)
    with pytest.raises(RelayDenied, match="route denied"):
        relay.forward("GET", "/api/admin/secrets")
    payload["instance_id"] = "JARVIS-u999"
    with pytest.raises(RelayDenied, match="identity denied"):
        relay.forward("POST", "/api/agents/send", json.dumps(payload).encode())


def test_worker_chat_roundtrip_over_uds_and_auth_denials(tmp_path: Path) -> None:
    calls = []
    def opener(req, timeout):
        calls.append(req.full_url)
        if req.method == "GET":
            return Response({"ok":True,"instance_id":"JARVIS-u116","messages":[],"next_cursor":9})
        return Response({"ok":True,"id":"db_9"})
    relay = ChatRelay(_token(tmp_path), opener=opener)
    sock = tmp_path / "relay.sock"
    server = _Server(str(sock), make_handler(relay))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f"unix://{sock}"
    try:
        assert _fetch_inbox(token=TOKEN, inbox_url=url, cursor=0)["next_cursor"] == 9
        assert _post_chat(
            token=TOKEN, chat_url=url, agent="JARVIS", username="william2",
            channel="dm:jarvis:william2", message="hola", source_id=8,
            instance_key="JARVIS-u116", kind="final",
        )["id"] == "db_9"
        with pytest.raises(RuntimeError, match="HTTP 401"):
            _fetch_inbox(token="X" * 48, inbox_url=url, cursor=0)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    assert calls == [
        "http://127.0.0.1:8765/api/user-clones/inbox?cursor=0&limit=20",
        "http://127.0.0.1:8765/api/agents/send",
    ]


def test_relay_source_and_unit_have_no_provider_key_and_local_only_egress() -> None:
    root = Path(__file__).parents[2]
    source = (root / "messages/u116_chat_relay.py").read_text()
    unit = (root / "systemd/seal-u116-chat-relay.service").read_text()
    assert "api.anthropic.com" not in source and "sk-ant-" not in source
    assert "User=seal-u116-chat-relay" in unit
    assert "Group=seal-u116-chat-client" in unit
    assert "Group=seal-claude-u116-client" not in unit
    assert "seal-chat.service" not in unit
    assert "IPAddressDeny=any" in unit and "IPAddressAllow=localhost" in unit
    assert "ProtectHome=true" in unit


def test_relay_thread_count_is_bounded_under_slow_clients(tmp_path: Path) -> None:
    relay = ChatRelay(_token(tmp_path), opener=lambda *_a, **_k: Response({"ok": True}))
    sock_path = tmp_path / "bounded.sock"
    server = _Server(str(sock_path), make_handler(relay), max_workers=2)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    slow_clients = []
    request_head = (
        "POST /api/agents/send HTTP/1.1\r\nHost:x\r\n"
        f"Authorization: Bearer {TOKEN}\r\nContent-Length:100\r\n\r\nx"
    ).encode()
    try:
        for _ in range(2):
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(str(sock_path)); client.sendall(request_head)
            slow_clients.append(client)
        time.sleep(0.1)
        denied = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        denied.settimeout(0.5); denied.connect(str(sock_path))
        denied.sendall(
            ("GET /health HTTP/1.1\r\nHost:x\r\n"
             f"Authorization: Bearer {TOKEN}\r\n\r\n").encode()
        )
        try:
            assert denied.recv(1024) == b""
        except ConnectionResetError:
            pass
        denied.close()
        assert server._slots._value == 0
    finally:
        for client in slow_clients:
            client.close()
        server.shutdown(); server.server_close(); thread.join(timeout=3)
