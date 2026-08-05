"""Focused contract tests for the agent-authenticated upload endpoint."""

import io
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from starlette.datastructures import Headers, UploadFile
from starlette.requests import Request


MESSAGES_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MESSAGES_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(MESSAGES_DIR) not in sys.path:
    sys.path.insert(0, str(MESSAGES_DIR))

from messages import chat_server as server  # noqa: E402


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000d4944415478da6364f80f0000010001005c9d8db6"
    "0000000049454e44ae426082"
)


class _Pool:
    async def fetchval(self, _query, _channel):
        return False


def _request() -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "path": "/api/agents/upload",
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("127.0.0.1", 8765),
        "client": ("127.0.0.1", 41000),
    })


def _file() -> UploadFile:
    return UploadFile(
        io.BytesIO(PNG_1X1),
        filename="probe.png",
        headers=Headers({"content-type": "image/png"}),
    )


def _body(response):
    return json.loads(response.body.decode("utf-8"))


@pytest.fixture
def upload_env(monkeypatch, tmp_path):
    create_message = AsyncMock(return_value={"id": 125529})
    broadcast = AsyncMock()
    enqueue = AsyncMock()

    async def stamp(entry, **_kwargs):
        return entry

    monkeypatch.setattr(server, "UPLOADS_DIR", tmp_path)
    monkeypatch.setattr(server, "LOG_WILLIAM", tmp_path / "william.jsonl")
    monkeypatch.setattr(server.chat_db, "pool", _Pool())
    monkeypatch.setattr(server.chat_db, "create_message", create_message)
    monkeypatch.setattr(server, "_agent_auth_mode", lambda: "ENFORCE")
    monkeypatch.setattr(server, "_stamp_coordination", stamp)
    monkeypatch.setattr(server, "broadcast", broadcast)
    monkeypatch.setattr(server, "enqueue", enqueue)
    return create_message, broadcast, enqueue


@pytest.mark.asyncio
async def test_authenticated_agent_upload_persists_verified_identity(monkeypatch, upload_env, tmp_path):
    create_message, broadcast, enqueue = upload_env

    async def resolve(_request, _body):
        return {"username": "JARVIS", "user_id": 7, "role": "agent"}

    monkeypatch.setattr(server, "_resolve_agent_auth", resolve)
    response = await server.agents_upload(
        _request(), _file(), "brochure", "web_chat", "JARVIS", "valid-session", ""
    )

    assert response.status_code == 200
    assert _body(response)["file_url"].startswith("/uploads/image_")
    assert create_message.await_args.kwargs["sender_name"] == "JARVIS"
    assert create_message.await_args.kwargs["sender_type"] == "user"
    assert len(list(tmp_path.glob("image_*.png"))) == 1
    broadcast.assert_awaited_once()
    enqueue.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("session", "sender", "instance_id", "channel", "status", "error"),
    [
        (None, "JARVIS", "", "web_chat", 401, "agent_auth_required"),
        ({"username": "JARVIS", "user_id": 7, "role": "agent"}, "NEXUS", "", "web_chat", 403, "agent_sender_mismatch"),
        ({"username": "JARVIS", "user_id": 7, "role": "user"}, "JARVIS", "", "web_chat", 403, "agent_role_required"),
        ({"username": "MALLORY", "user_id": 8, "role": "agent"}, "MALLORY", "", "web_chat", 403, "agent_identity_required"),
        ({"username": "JARVIS", "user_id": 7, "role": "agent"}, "JARVIS", "JARVIS-u103", "web_chat", 403, "agent_upload_instance_unsupported"),
        ({"username": "JARVIS", "user_id": 7, "role": "agent"}, "JARVIS", "", "ghost-channel", 422, "unknown_channel"),
        ({"username": "JARVIS", "user_id": 7, "role": "agent"}, "JARVIS", "", "dm:JARVIS:ghost", 422, "unknown_channel"),
    ],
)
async def test_agent_upload_rejects_before_writing(
    monkeypatch, upload_env, tmp_path, session, sender, instance_id, channel, status, error
):
    create_message, broadcast, enqueue = upload_env

    async def resolve(_request, _body):
        return session

    monkeypatch.setattr(server, "_resolve_agent_auth", resolve)
    response = await server.agents_upload(
        _request(), _file(), "probe", channel, sender, "session", instance_id
    )

    assert response.status_code == status
    assert _body(response)["error"] == error
    assert not list(tmp_path.glob("*.png"))
    create_message.assert_not_awaited()
    broadcast.assert_not_awaited()
    enqueue.assert_not_awaited()


def test_human_upload_route_still_requires_auth_dependency():
    route = next(route for route in server.app.routes if route.path == "/api/upload")
    dependency_names = {
        dependency.call.__name__
        for dependency in route.dependant.dependencies
        if dependency.call is not None
    }
    assert "require_auth" in dependency_names
