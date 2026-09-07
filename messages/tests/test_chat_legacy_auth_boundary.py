"""Regression guards for legacy chat surfaces that expose private SEAL data."""

from pathlib import Path
import sys

import pytest
from fastapi import HTTPException
from starlette.requests import Request

MESSAGES_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MESSAGES_DIR))

import chat_auth  # noqa: E402
import chat_server  # noqa: E402


PRIVATE_GET_PATHS = {
    "/api/files/read",
    "/api/files/tree",
    "/api/soul/ocean",
    "/api/soul/ocean/all",
    "/api/soul/memories",
    "/api/soul/instincts",
    "/api/soul/working-state",
    "/api/soul/beliefs",
    "/api/soul/events",
    "/api/soul/thoughts",
    "/api/soul/brain-stats",
    "/uploads/{filename}",
}


def _request(token: str) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/probe",
        "headers": [(b"authorization", f"Bearer {token}".encode())],
        "query_string": b"",
    })


def test_private_legacy_routes_require_auth_dependency() -> None:
    protected = set()
    for route in chat_server.app.routes:
        if getattr(route, "path", None) not in PRIVATE_GET_PATHS:
            continue
        if any(dep.call is chat_auth.require_auth for dep in route.dependant.dependencies):
            protected.add(route.path)
    assert protected == PRIVATE_GET_PATHS

    upload = next(route for route in chat_server.app.routes if getattr(route, "path", None) == "/api/upload")
    assert any(dep.call is chat_auth.require_auth for dep in upload.dependant.dependencies)


def test_filesystem_and_soul_scope_fail_closed() -> None:
    with pytest.raises(HTTPException) as denied_file:
        chat_server._require_superuser({"username": "Henry", "role": "admin"})
    assert denied_file.value.status_code == 403

    chat_server._require_superuser({"username": "William", "role": "superuser"})
    chat_server._require_soul_scope({"username": "ADA", "role": "basic"}, "ADA")
    with pytest.raises(HTTPException) as denied_soul:
        chat_server._require_soul_scope({"username": "ADA", "role": "basic"}, "ALICE")
    assert denied_soul.value.status_code == 403


@pytest.mark.asyncio
async def test_session_database_error_never_falls_back_to_jwt(monkeypatch) -> None:
    class BrokenSessionDB:
        async def validate_session(self, _token_hash):
            raise RuntimeError("database unavailable")

    token = chat_auth.create_token(1, "William", "superuser")
    monkeypatch.setattr(chat_auth, "_auth_db", BrokenSessionDB())
    with pytest.raises(HTTPException) as exc:
        await chat_auth.require_auth(_request(token))
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_missing_session_store_never_falls_back_to_jwt(monkeypatch) -> None:
    token = chat_auth.create_token(1, "William", "superuser")
    monkeypatch.setattr(chat_auth, "_auth_db", None)
    with pytest.raises(HTTPException) as exc:
        await chat_auth.require_auth(_request(token))
    assert exc.value.status_code == 503


def test_websocket_identity_has_no_lan_username_fallback() -> None:
    source = Path(chat_server.__file__).read_text(encoding="utf-8")
    ws_block = source.split('@app.websocket("/ws")', 1)[1].split('@app.websocket("/ws/agents")', 1)[0]
    assert 'ws.query_params.get("user")' not in ws_block
    assert 'code=4401' in ws_block
    agent_block = source.split('@app.websocket("/ws/agents")', 1)[1].split('@app.post("/internal/stream")', 1)[0]
    assert "sender = agent_name" in agent_block
    assert 'msg.get("from", agent_name)' not in agent_block


def test_typing_identity_is_authenticated_and_server_owned() -> None:
    source = Path(chat_server.__file__).read_text(encoding="utf-8")
    block = source.split('@app.post("/api/chat/typing")', 1)[1].split("# ---- Anti-flood", 1)[0]
    assert "Depends(require_auth)" in block
    assert 'body.get("from"' not in block


def test_ack_is_localhost_only() -> None:
    source = Path(chat_server.__file__).read_text(encoding="utf-8")
    block = source.split('@app.post("/api/agents/ack")', 1)[1].split('@app.post("/api/agents/status")', 1)[0]
    assert '("127.0.0.1", "::1", "localhost")' in block
    assert "_is_local_or_lan" not in block
