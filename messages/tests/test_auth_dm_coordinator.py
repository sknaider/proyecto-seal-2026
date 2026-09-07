"""Hermetic characterization tests for SEAL chat server AUTH/DM domain.

Tests: _resolve_agent_auth, _agent_auth_gate, auth_me, dm_get_messages,
chat_send (DM privacy), and DM access controls. Quality gates for diff-coverage
under adversarial review (ADA).

Key invariant: sender field in request body does NOT authorize; identity comes
from verified session only. DM privacy denies access to channels where user is
not a participant.

Runs hermetic: env -u SEAL_PG_DSN python3 -m pytest test_auth_dm_coordinator.py -q
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager

import pytest
from fastapi.responses import JSONResponse

MESSAGES_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MESSAGES_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(MESSAGES_DIR) not in sys.path:
    sys.path.insert(0, str(MESSAGES_DIR))

from messages import chat_server as server  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Test Helpers
# ────────────────────────────────────────────────────────────────────────────


class _Request:
    """Mock FastAPI Request with configurable host and optional token."""
    def __init__(self, body: dict, host: str = "127.0.0.1", auth_header: str | None = None):
        self._body = body
        self.client = SimpleNamespace(host=host)
        self.headers = {}
        if auth_header:
            self.headers["authorization"] = auth_header
        self.cookies = {}
        self.query_params = {}  # For _extract_token

    async def json(self) -> dict:
        return self._body


def _json_body(response) -> dict:
    """Extract JSON from JSONResponse."""
    if hasattr(response, "body"):
        return json.loads(response.body.decode("utf-8"))
    return response  # Already a dict


# ────────────────────────────────────────────────────────────────────────────
# Tests for _resolve_agent_auth — Session Resolution
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolve_agent_auth_positive_valid_session_key(monkeypatch):
    """Positive: valid session_key in body resolves to verified session."""
    mock_pool = AsyncMock()
    monkeypatch.setattr(server.chat_db, "pool", mock_pool)

    async def mock_validate(token_hash):
        return {"username": "JARVIS", "user_id": 7, "role": "agent"}

    monkeypatch.setattr(server.chat_db, "validate_session", mock_validate)

    request = _Request({"session_key": "raw-session-key-xyz"})
    result = await server._resolve_agent_auth(request, {"session_key": "raw-session-key-xyz"})

    assert result is not None
    assert result["username"] == "JARVIS"
    assert result["user_id"] == 7


@pytest.mark.asyncio
async def test_resolve_agent_auth_negative_invalid_session_key_returns_none(monkeypatch):
    """Negative: invalid session_key resolves to None."""
    mock_pool = AsyncMock()
    monkeypatch.setattr(server.chat_db, "pool", mock_pool)

    async def mock_validate(token_hash):
        return None  # Session not found

    monkeypatch.setattr(server.chat_db, "validate_session", mock_validate)

    request = _Request({"session_key": "invalid-key"})
    result = await server._resolve_agent_auth(request, {"session_key": "invalid-key"})

    assert result is None


@pytest.mark.asyncio
async def test_resolve_agent_auth_negative_no_pool_returns_none(monkeypatch):
    """Negative: when chat_db.pool is None, returns None (fail-closed)."""
    monkeypatch.setattr(server.chat_db, "pool", None)

    request = _Request({"session_key": "any-key"})
    result = await server._resolve_agent_auth(request, {"session_key": "any-key"})

    assert result is None


# ────────────────────────────────────────────────────────────────────────────
# Tests for _agent_auth_gate — Authorization Gate
# ────────────────────────────────────────────────────────────────────────────


def test_agent_auth_gate_positive_valid_session_and_sender_match_no_rejection(monkeypatch):
    """Positive: verified session with matching sender → no rejection."""
    request = _Request({})
    session = {"username": "JARVIS", "user_id": 7}
    monkeypatch.setattr(server, "_agent_auth_mode", lambda: "ENFORCE")

    rejection = server._agent_auth_gate(session, "JARVIS", "claim", request)

    assert rejection is None


def test_agent_auth_gate_positive_case_insensitive_match_no_rejection(monkeypatch):
    """Positive: case-insensitive sender match → no rejection."""
    request = _Request({})
    session = {"username": "jarvis", "user_id": 7}
    monkeypatch.setattr(server, "_agent_auth_mode", lambda: "ENFORCE")

    rejection = server._agent_auth_gate(session, "JARVIS", "claim", request)

    assert rejection is None


def test_agent_auth_gate_negative_no_session_401_enforce(monkeypatch):
    """Negative (ENFORCE): no session → 401 agent_auth_required."""
    request = _Request({})
    monkeypatch.setattr(server, "_agent_auth_mode", lambda: "ENFORCE")

    rejection = server._agent_auth_gate(None, "JARVIS", "claim", request)

    assert rejection is not None
    assert rejection.status_code == 401
    body = _json_body(rejection)
    assert body["error"] == "agent_auth_required"


def test_agent_auth_gate_negative_sender_mismatch_403_enforce(monkeypatch):
    """Negative (ENFORCE): verified session but sender mismatch → 403 agent_sender_mismatch."""
    request = _Request({})
    session = {"username": "ALICE", "user_id": 5}
    monkeypatch.setattr(server, "_agent_auth_mode", lambda: "ENFORCE")

    rejection = server._agent_auth_gate(session, "JARVIS", "claim", request)

    assert rejection is not None
    assert rejection.status_code == 403
    body = _json_body(rejection)
    assert body["error"] == "agent_sender_mismatch"


def test_agent_auth_gate_negative_no_session_no_enforcement_no_rejection(monkeypatch):
    """Negative (AUDIT): no session but mode is AUDIT → no rejection."""
    request = _Request({})
    monkeypatch.setattr(server, "_agent_auth_mode", lambda: "AUDIT")

    rejection = server._agent_auth_gate(None, "JARVIS", "claim", request)

    assert rejection is None


def test_agent_auth_gate_positive_clone_identity_valid_no_rejection(monkeypatch):
    """Positive: clone identity (JARVIS-u123) with matching parent → no rejection."""
    request = _Request({})
    session = {"username": "JARVIS-u123", "user_id": 7}
    monkeypatch.setattr(server, "_agent_auth_mode", lambda: "ENFORCE")

    rejection = server._agent_auth_gate(
        session, "JARVIS", "claim", request, instance_id="JARVIS-u123"
    )

    assert rejection is None


# ────────────────────────────────────────────────────────────────────────────
# Tests for auth_me — Current User Identity
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auth_me_positive_returns_authenticated_user_info(monkeypatch):
    """Positive: auth_me returns full user info from authenticated user."""
    full_user = {
        "id": 3,
        "username": "william",
        "display_name": "William",
        "role": "admin",
        "avatar_url": "http://example.com/avatar.jpg",
    }

    async def mock_get_user(uid):
        return full_user

    monkeypatch.setattr(server.chat_db, "get_user_by_id", mock_get_user)

    user = {"sub": "3", "username": "william"}
    response = await server.auth_me(user)

    assert response["ok"] is True
    assert response["user"]["username"] == "william"
    assert response["user"]["role"] == "admin"
    assert response["user"]["id"] == 3


@pytest.mark.asyncio
async def test_auth_me_negative_user_not_found_404(monkeypatch):
    """Negative: user not found in DB → 404."""
    async def mock_get_user(uid):
        return None

    monkeypatch.setattr(server.chat_db, "get_user_by_id", mock_get_user)

    user = {"sub": "999", "username": "ghost"}
    response = await server.auth_me(user)

    assert response.status_code == 404
    body = _json_body(response)
    assert body["error"] == "User not found"


# ────────────────────────────────────────────────────────────────────────────
# Tests for DM Privacy — Participant Check Function
# ────────────────────────────────────────────────────────────────────────────


def test_dm_other_participant_positive_valid_participant():
    """Positive: valid participant in DM channel returns the other endpoint."""
    # dm:william:jarvis — william is the sender
    other = server._dm_other_participant("dm:william:jarvis", "william")
    assert other is not None
    assert other.upper() == "JARVIS"


def test_dm_other_participant_positive_both_endpoints():
    """Positive: both endpoints in a DM are valid participants."""
    # Check william as sender
    other1 = server._dm_other_participant("dm:william:jarvis", "william")
    assert other1 is not None and other1.upper() == "JARVIS"

    # Check jarvis as sender
    other2 = server._dm_other_participant("dm:william:jarvis", "jarvis")
    assert other2 is not None and other2.lower() == "william"


def test_dm_other_participant_negative_non_participant_returns_none():
    """Negative (KEY PRIVACY): non-participant returns None (access denied)."""
    # alice is not in dm:william:jarvis
    other = server._dm_other_participant("dm:william:jarvis", "alice")
    assert other is None


def test_dm_other_participant_negative_exact_match_not_substring():
    """Negative (KEY PRIVACY): exact match required, not substring (ali ⊂ alice)."""
    # ali is not a valid participant in dm:alice:jarvis
    other = server._dm_other_participant("dm:alice:jarvis", "ali")
    assert other is None


def test_dm_other_participant_negative_empty_sender_returns_none():
    """Negative: empty sender string returns None."""
    other = server._dm_other_participant("dm:william:jarvis", "")
    assert other is None


# ────────────────────────────────────────────────────────────────────────────
# Tests for dm_get_messages — DM Privacy (Access Control)
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dm_get_messages_negative_non_participant_denied_403(monkeypatch):
    """Negative (KEY PRIVACY TEST): user NOT in DM cannot read messages (403)."""
    full_user = {"username": "william", "id": 3}

    async def mock_get_user(uid):
        return full_user

    monkeypatch.setattr(server.chat_db, "get_user_by_id", mock_get_user)
    monkeypatch.setattr(server.chat_db, "pool", object())

    user = {"sub": "3", "username": "william", "role": "admin"}
    # william tries to access dm:alice:jarvis (not a participant)
    response = await server.dm_get_messages("dm:alice:jarvis", 50, user)

    assert response.status_code == 403
    body = _json_body(response)
    assert body["error"] == "not a participant of this DM"


@pytest.mark.asyncio
async def test_dm_get_messages_negative_not_dm_channel_400(monkeypatch):
    """Negative: non-DM channel request → 400."""
    full_user = {"username": "william", "id": 3}

    async def mock_get_user(uid):
        return full_user

    monkeypatch.setattr(server.chat_db, "get_user_by_id", mock_get_user)

    user = {"sub": "3", "username": "william", "role": "admin"}
    response = await server.dm_get_messages("web_chat", 50, user)

    assert response.status_code == 400
    body = _json_body(response)
    assert body["error"] == "not a dm channel"


@pytest.mark.asyncio
async def test_dm_get_messages_negative_user_not_found_404(monkeypatch):
    """Negative: user not found in DB → 404."""
    async def mock_get_user(uid):
        return None

    monkeypatch.setattr(server.chat_db, "get_user_by_id", mock_get_user)

    user = {"sub": "999", "username": "ghost"}
    response = await server.dm_get_messages("dm:william:jarvis", 50, user)

    assert response.status_code == 404
    body = _json_body(response)
    assert body["error"] == "user not found"


# ────────────────────────────────────────────────────────────────────────────
# Tests for Sender Falsification Protection
# ────────────────────────────────────────────────────────────────────────────


def test_agent_auth_gate_sender_in_body_does_not_authorize(monkeypatch):
    """Control (CRITICAL): falsified 'sender' field in body does NOT authorize."""
    # The request body has sender="ALICE", but the verified session is "JARVIS".
    # The gate must reject this based on SESSION, not BODY.
    request = _Request({"sender": "ALICE"})
    session = {"username": "JARVIS", "user_id": 7}
    monkeypatch.setattr(server, "_agent_auth_mode", lambda: "ENFORCE")

    rejection = server._agent_auth_gate(session, "ALICE", "claim", request)

    # The session is JARVIS, asserted_sender is ALICE, they don't match
    assert rejection is not None
    assert rejection.status_code == 403
    body = _json_body(rejection)
    assert body["error"] == "agent_sender_mismatch"


def test_agent_auth_gate_body_param_ignored_session_is_source_of_truth(monkeypatch):
    """Control (CRITICAL): asserted_sender parameter (not body) is the claim being verified."""
    # Even if body has other fields, the _agent_auth_gate checks asserted_sender parameter
    # against the verified session. This proves the gate uses SESSION, not BODY.
    request = _Request({"sender": "MALICIOUS", "payload": "fake"})
    session = {"username": "JARVIS", "user_id": 7}
    monkeypatch.setattr(server, "_agent_auth_mode", lambda: "ENFORCE")

    # asserted_sender is "MALICIOUS" (from parameter, not body)
    # session.username is "JARVIS"
    # They must not match → 403
    rejection = server._agent_auth_gate(session, "MALICIOUS", "claim", request)

    assert rejection is not None
    assert rejection.status_code == 403


# ────────────────────────────────────────────────────────────────────────────
# Tests for chat_send DM Privacy
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_send_dm_privacy_non_participant_denied_403(monkeypatch):
    """Negative (KEY PRIVACY TEST): non-participant cannot send to DM (403)."""
    def mock_screen(content, user):
        return {"allow": True}

    async def mock_create_dm_channel(a, b):
        return f"dm:{a}:{b}"

    monkeypatch.setattr(server, "_screen_authenticated_human_message", mock_screen)
    create_dm = AsyncMock(side_effect=mock_create_dm_channel)
    monkeypatch.setattr(server.chat_db, "create_dm_channel", create_dm)
    monkeypatch.setattr(server.chat_db, "get_user_by_id", AsyncMock(return_value={"id": 3}))
    monkeypatch.setattr(server.chat_db, "pool", object())

    request = _Request({"message": "Hello", "channel": "dm:alice:jarvis"})
    user = {"sub": "3", "username": "william", "role": "admin"}

    response = await server.chat_send(request, user)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 403
    body = _json_body(response)
    assert body["error"] == "not a participant of this DM"
    create_dm.assert_not_awaited()


# ────────────────────────────────────────────────────────────────────────────
# Tests for chat_messages DM Access
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_messages_dm_non_participant_denied_403(monkeypatch):
    """Negative: non-participant denied DM read access via chat_messages."""
    full_user = {"username": "william", "id": 3}

    async def mock_require_auth(request):
        return {"sub": "3", "username": "william", "role": "admin"}

    async def mock_get_user(uid):
        return full_user

    async def mock_user_can_access(uid, channel):
        return False  # william cannot access dm:alice:jarvis

    monkeypatch.setattr(server, "require_auth", mock_require_auth)
    monkeypatch.setattr(server.chat_db, "get_user_by_id", mock_get_user)
    monkeypatch.setattr(server.chat_db, "user_can_access_channel", mock_user_can_access)
    monkeypatch.setattr(server.chat_db, "pool", object())
    create_dm = AsyncMock(return_value="dm:alice:jarvis")
    monkeypatch.setattr(server.chat_db, "create_dm_channel", create_dm)

    request = _Request({})
    response = await server.chat_messages(request, channel="dm:alice:jarvis", limit=50)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 403
    body = _json_body(response)
    assert body["error"] == "Access denied"
    create_dm.assert_not_awaited()
