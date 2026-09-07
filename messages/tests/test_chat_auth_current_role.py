from __future__ import annotations

from pathlib import Path
import sys

import pytest
from starlette.requests import Request

MESSAGES_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MESSAGES_DIR))

import chat_auth  # noqa: E402


def _request(token: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/probe",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "query_string": b"",
        }
    )


class CurrentSessionDB:
    def __init__(self, role: str, user_id: int = 1, username: str = "William") -> None:
        self.role = role
        self.user_id = user_id
        self.username = username

    async def validate_session(self, _token_hash: str) -> dict:
        return {
            "session_id": 99,
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.username,
            "role": self.role,
            "avatar_url": None,
        }


@pytest.mark.asyncio
async def test_live_database_role_overrides_stale_privileged_jwt(monkeypatch) -> None:
    token = chat_auth.create_token(1, "William", "superuser")
    monkeypatch.setattr(chat_auth, "_auth_db", CurrentSessionDB("basic"))
    user = await chat_auth.require_auth(_request(token))
    assert user["role"] == "basic"
    assert user["sub"] == "1"
    assert user["_session_token_hash"] == chat_auth.hash_token(token)


@pytest.mark.asyncio
async def test_live_database_promotion_is_visible_without_relogin(monkeypatch) -> None:
    token = chat_auth.create_token(103, "katy", "basic")
    monkeypatch.setattr(chat_auth, "_auth_db", CurrentSessionDB("admin", 103, "katy"))
    user = await chat_auth.require_auth(_request(token))
    assert user["role"] == "admin"
    assert user["username"] == "katy"


def test_fresh_session_identity_preserves_non_authority_jwt_metadata() -> None:
    identity = chat_auth._fresh_session_identity(
        {"sub": "1", "username": "old", "role": "superuser", "iat": 10},
        {
            "user_id": 103,
            "username": "katy",
            "display_name": "Katy",
            "role": "basic",
            "avatar_url": "/katy.png",
        },
    )
    assert identity == {
        "sub": "103",
        "username": "katy",
        "display_name": "Katy",
        "role": "basic",
        "avatar_url": "/katy.png",
        "iat": 10,
    }
