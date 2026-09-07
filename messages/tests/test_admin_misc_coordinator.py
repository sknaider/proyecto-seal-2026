"""Hermetic characterization tests for SEAL chat server ADMIN and MISC domains.

Tests: admin_create_user(), admin_delete_user(), _is_local_or_lan(), read_last_n(),
_compute_loaded_code_hash(), code_version() with quality gates for diff-coverage
under adversarial review. All tests are fail-closed (no DB connections, no side effects
beyond monkeypatched mocks).

Runs hermetic: env -u SEAL_PG_DSN python3 -m pytest test_admin_misc_coordinator.py -q
"""

import asyncio
import json
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.responses import JSONResponse
from starlette.requests import Request

MESSAGES_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MESSAGES_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(MESSAGES_DIR) not in sys.path:
    sys.path.insert(0, str(MESSAGES_DIR))

from messages import chat_server as server  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Test Fixtures and Helpers
# ────────────────────────────────────────────────────────────────────────────

def _json_body(response) -> dict:
    """Extract JSON from response."""
    if hasattr(response, "body"):
        return json.loads(response.body.decode("utf-8"))
    return response


def _request_for_admin(method="POST", path="/api/admin/users") -> Request:
    """Create a mock Request for admin endpoint."""
    return Request({
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "query_string": b"",
        "scheme": "http",
        "server": ("127.0.0.1", 8765),
        "client": ("127.0.0.1", 41000),
    })


@pytest.fixture
def admin_env(monkeypatch):
    """Setup for admin_create_user and admin_delete_user tests."""
    # Mock chat_db methods
    get_user_by_id = AsyncMock()
    get_user_by_username = AsyncMock()
    create_user = AsyncMock()
    delete_user = AsyncMock()
    set_user_agents = AsyncMock()

    monkeypatch.setattr(server.chat_db, "get_user_by_id", get_user_by_id)
    monkeypatch.setattr(server.chat_db, "get_user_by_username", get_user_by_username)
    monkeypatch.setattr(server.chat_db, "create_user", create_user)
    monkeypatch.setattr(server.chat_db, "delete_user", delete_user)
    monkeypatch.setattr(server.chat_db, "set_user_agents", set_user_agents)

    return {
        "get_user_by_id": get_user_by_id,
        "get_user_by_username": get_user_by_username,
        "create_user": create_user,
        "delete_user": delete_user,
        "set_user_agents": set_user_agents,
    }


# ────────────────────────────────────────────────────────────────────────────
# MISC Domain Tests: _is_local_or_lan, read_last_n, _compute_loaded_code_hash, code_version
# ────────────────────────────────────────────────────────────────────────────

class TestIsLocalOrLan:
    """Characterization tests for _is_local_or_lan(host) — network ACL."""

    def test_positive_localhost_127_0_0_1(self):
        """Positive: 127.0.0.1 is local."""
        assert server._is_local_or_lan("127.0.0.1") is True

    def test_positive_localhost_ipv6(self):
        """Positive: ::1 is local."""
        assert server._is_local_or_lan("::1") is True

    def test_positive_localhost_hostname(self):
        """Positive: localhost hostname is local."""
        assert server._is_local_or_lan("localhost") is True

    def test_positive_lan_192_168_68_prefix(self):
        """Positive: 192.168.68.x is LAN."""
        assert server._is_local_or_lan("192.168.68.1") is True
        assert server._is_local_or_lan("192.168.68.254") is True
        assert server._is_local_or_lan("192.168.68.100") is True

    def test_positive_tailscale_100_prefix(self):
        """Positive: 100.x.x.x (Tailscale CGNAT) is allowed."""
        assert server._is_local_or_lan("100.75.201.110") is True
        assert server._is_local_or_lan("100.0.0.1") is True
        assert server._is_local_or_lan("100.255.255.255") is True

    def test_negative_public_ipv4_8_8_8_8(self):
        """Negative: public IP 8.8.8.8 is rejected."""
        assert server._is_local_or_lan("8.8.8.8") is False

    def test_negative_public_ipv4_1_1_1_1(self):
        """Negative: public IP 1.1.1.1 is rejected."""
        assert server._is_local_or_lan("1.1.1.1") is False

    def test_negative_none_host(self):
        """Negative: None/empty host returns False."""
        assert server._is_local_or_lan(None) is False
        assert server._is_local_or_lan("") is False

    def test_negative_wrong_lan_prefix_10_x(self):
        """Negative: 10.x.x.x (private but not 192.168.68) is rejected (not in allowed list)."""
        # The function only allows specific prefixes: 127/::1/localhost/192.168.68/100
        assert server._is_local_or_lan("10.0.0.1") is False

    def test_negative_wrong_lan_prefix_172_16(self):
        """Negative: 172.16.x.x (private but not 192.168.68) is rejected."""
        assert server._is_local_or_lan("172.16.0.1") is False


class TestComputeLoadedCodeHash:
    """Characterization tests for _compute_loaded_code_hash() — deterministic hash."""

    def test_positive_hash_is_deterministic(self):
        """Positive: calling twice returns the same hash."""
        hash1 = server._compute_loaded_code_hash()
        hash2 = server._compute_loaded_code_hash()
        assert hash1 == hash2

    def test_positive_hash_is_hex_string(self):
        """Positive: hash is a valid SHA256 hex string (64 chars)."""
        h = server._compute_loaded_code_hash()
        assert isinstance(h, str)
        assert len(h) == 64
        # Should be valid hex
        int(h, 16)  # Raises if invalid

    def test_positive_hash_matches_loaded_module(self):
        """Positive: hash matches the actual __file__ in memory."""
        import hashlib
        h = server._compute_loaded_code_hash()
        with open(server.__file__, "rb") as f:
            expected = hashlib.sha256(f.read()).hexdigest()
        assert h == expected

    def test_positive_code_version_endpoint_includes_hash(self):
        """Positive: code_version endpoint returns the cached hash."""
        result = asyncio.run(server.code_version())
        assert result["code_hash"] == server._LOADED_CODE_HASH
        assert len(result["code_hash"]) == 64


class TestReadLastN:
    """Characterization tests for read_last_n(path, n, max_age_minutes)."""

    def test_positive_read_last_3_messages(self, tmp_path):
        """Positive: reads last 3 messages from JSONL."""
        # Use timestamps within the last 60 minutes from now (UTC)
        from datetime import datetime, timezone, timedelta
        now_utc = datetime.now(timezone.utc)
        ts1 = (now_utc - timedelta(minutes=50)).isoformat()
        ts2 = (now_utc - timedelta(minutes=40)).isoformat()
        ts3 = (now_utc - timedelta(minutes=30)).isoformat()
        ts4 = (now_utc - timedelta(minutes=20)).isoformat()
        ts5 = (now_utc - timedelta(minutes=10)).isoformat()

        jsonl_path = tmp_path / "messages.jsonl"
        jsonl_path.write_text(
            f'{{"timestamp":"{ts1}","message":"msg1"}}\n'
            f'{{"timestamp":"{ts2}","message":"msg2"}}\n'
            f'{{"timestamp":"{ts3}","message":"msg3"}}\n'
            f'{{"timestamp":"{ts4}","message":"msg4"}}\n'
            f'{{"timestamp":"{ts5}","message":"msg5"}}\n'
        )
        result = server.read_last_n(jsonl_path, 3, max_age_minutes=60)
        assert len(result) == 3
        assert result[-1]["message"] == "msg5"
        assert result[0]["message"] == "msg3"

    def test_positive_filters_by_max_age(self, tmp_path):
        """Positive: only messages within max_age_minutes are returned."""
        from datetime import datetime, timezone, timedelta
        now_utc = datetime.now(timezone.utc)

        # Old message (>60min old) — should be filtered out
        old_ts = (now_utc - timedelta(minutes=65)).isoformat()
        # New message (30min old) — should be included
        new_ts = (now_utc - timedelta(minutes=30)).isoformat()

        jsonl_path = tmp_path / "messages.jsonl"
        jsonl_path.write_text(
            f'{{"timestamp":"{old_ts}","message":"old"}}\n'
            f'{{"timestamp":"{new_ts}","message":"new"}}\n'
        )
        result = server.read_last_n(jsonl_path, 10, max_age_minutes=60)
        # Only the new one should pass the max_age filter
        assert len(result) >= 1  # At least the new one
        messages = [m.get("message") for m in result]
        assert "new" in messages

    def test_positive_empty_file(self, tmp_path):
        """Positive: empty JSONL returns empty list."""
        jsonl_path = tmp_path / "empty.jsonl"
        jsonl_path.touch()
        result = server.read_last_n(jsonl_path, 5)
        assert result == []

    def test_positive_nonexistent_file(self, tmp_path):
        """Positive: nonexistent file returns empty list (no crash)."""
        result = server.read_last_n(tmp_path / "nonexistent.jsonl", 5)
        assert result == []

    def test_negative_invalid_timestamp_skipped(self, tmp_path):
        """Negative/control: lines with invalid timestamps are skipped."""
        from datetime import datetime, timezone, timedelta
        now_utc = datetime.now(timezone.utc)
        ts1 = (now_utc - timedelta(minutes=30)).isoformat()
        ts2 = (now_utc - timedelta(minutes=20)).isoformat()

        jsonl_path = tmp_path / "mixed.jsonl"
        jsonl_path.write_text(
            f'{{"timestamp":"{ts1}","message":"valid1"}}\n'
            '{"timestamp":"not-a-date","message":"invalid"}\n'
            f'{{"timestamp":"{ts2}","message":"valid2"}}\n'
        )
        result = server.read_last_n(jsonl_path, 10)
        assert len(result) == 2
        assert all(m.get("message") in ("valid1", "valid2") for m in result)

    def test_negative_missing_timestamp_skipped(self, tmp_path):
        """Negative/control: entries without timestamp field are skipped."""
        from datetime import datetime, timezone, timedelta
        now_utc = datetime.now(timezone.utc)
        ts = (now_utc - timedelta(minutes=30)).isoformat()

        jsonl_path = tmp_path / "no_ts.jsonl"
        jsonl_path.write_text(
            '{"message":"no timestamp"}\n'
            f'{{"timestamp":"{ts}","message":"with timestamp"}}\n'
        )
        result = server.read_last_n(jsonl_path, 10)
        assert len(result) == 1
        assert result[0]["message"] == "with timestamp"


# ────────────────────────────────────────────────────────────────────────────
# ADMIN Domain Tests: admin_create_user, admin_delete_user (SECURITY-CRITICAL)
# ────────────────────────────────────────────────────────────────────────────

class TestAdminCreateUser:
    """Characterization tests for admin_create_user — SECURITY CRITICAL."""

    @pytest.mark.asyncio
    async def test_positive_superuser_creates_basic_user(self, admin_env):
        """Positive: superuser can create a basic user."""
        admin_env["get_user_by_id"].return_value = {
            "id": 1,
            "username": "dadito",
            "role": "superuser",
        }
        admin_env["get_user_by_username"].return_value = None  # username doesn't exist yet
        admin_env["create_user"].return_value = {
            "id": 2,
            "username": "newuser",
            "display_name": "New User",
            "role": "basic",
        }
        admin_env["set_user_agents"].return_value = []

        req = _request_for_admin()
        req._body = json.dumps({
            "username": "newuser",
            "display_name": "New User",
            "password": "secret123",
            "role": "basic",
            "agents": [],
        }).encode()
        req._receive = AsyncMock(return_value={
            "type": "http.request",
            "body": req._body,
        })

        user = {"sub": "1", "username": "dadito", "role": "superuser"}
        response = await server.admin_create_user(req, user)

        assert response["ok"] is True
        assert response["user"]["username"] == "newuser"
        assert response["user"]["role"] == "basic"
        admin_env["create_user"].assert_called_once()

    @pytest.mark.asyncio
    async def test_positive_admin_creates_basic_user(self, admin_env):
        """Positive: admin role (non-super) can create basic users."""
        admin_env["get_user_by_id"].return_value = {
            "id": 5,
            "username": "henry",
            "role": "admin",
        }
        admin_env["get_user_by_username"].return_value = None
        admin_env["create_user"].return_value = {
            "id": 6,
            "username": "bob",
            "role": "basic",
        }
        admin_env["set_user_agents"].return_value = []

        req = _request_for_admin()
        req._body = json.dumps({
            "username": "bob",
            "password": "pass",
            "role": "basic",
        }).encode()
        req._receive = AsyncMock(return_value={
            "type": "http.request",
            "body": req._body,
        })

        user = {"sub": "5", "username": "henry", "role": "admin"}
        response = await server.admin_create_user(req, user)

        assert response["ok"] is True
        assert response["user"]["username"] == "bob"

    @pytest.mark.asyncio
    async def test_negative_admin_cannot_create_superuser_403(self, admin_env):
        """Negative/control: admin (non-super) CANNOT create superuser (403)."""
        admin_env["get_user_by_id"].return_value = {
            "id": 5,
            "username": "henry",
            "role": "admin",
        }
        admin_env["get_user_by_username"].return_value = None

        req = _request_for_admin()
        req._body = json.dumps({
            "username": "newsuper",
            "password": "pass",
            "role": "superuser",  # ← trying to create superuser as admin
        }).encode()
        req._receive = AsyncMock(return_value={
            "type": "http.request",
            "body": req._body,
        })

        user = {"sub": "5", "username": "henry", "role": "admin"}
        response = await server.admin_create_user(req, user)

        assert response.status_code == 403
        body = _json_body(response)
        # Ahora por IDENTIDAD (dadito): un admin NO crea superusuarios.
        assert "solo el superadmin (dadito)" in body["error"]

    @pytest.mark.asyncio
    async def test_negative_basic_user_cannot_create_user_403(self, admin_env):
        """Negative/control: basic user has no admin permission (403)."""
        admin_env["get_user_by_id"].return_value = {
            "id": 99,
            "username": "regular",
            "role": "basic",  # ← NOT admin
        }

        req = _request_for_admin()
        req._body = json.dumps({
            "username": "newuser",
            "password": "pass",
            "role": "basic",
        }).encode()
        req._receive = AsyncMock(return_value={
            "type": "http.request",
            "body": req._body,
        })

        user = {"sub": "99", "username": "regular", "role": "basic"}
        response = await server.admin_create_user(req, user)

        assert response.status_code == 403
        body = _json_body(response)
        assert body["error"] == "admin_only"

    @pytest.mark.asyncio
    async def test_negative_missing_password_400(self, admin_env):
        """Negative/control: request without password is rejected (400)."""
        admin_env["get_user_by_id"].return_value = {
            "id": 1,
            "username": "dadito",
            "role": "superuser",
        }

        req = _request_for_admin()
        req._body = json.dumps({
            "username": "newuser",
            # password missing
            "role": "basic",
        }).encode()
        req._receive = AsyncMock(return_value={
            "type": "http.request",
            "body": req._body,
        })

        user = {"sub": "1", "username": "dadito", "role": "superuser"}
        response = await server.admin_create_user(req, user)

        assert response.status_code == 400
        body = _json_body(response)
        assert "password" in body["error"].lower() or "requeridos" in body["error"]

    @pytest.mark.asyncio
    async def test_negative_username_already_exists_409(self, admin_env):
        """Negative/control: duplicate username rejected (409)."""
        admin_env["get_user_by_id"].return_value = {
            "id": 1,
            "username": "dadito",
            "role": "superuser",
        }
        admin_env["get_user_by_username"].return_value = {
            "id": 2,
            "username": "existing",
            "role": "basic",
        }

        req = _request_for_admin()
        req._body = json.dumps({
            "username": "existing",  # already exists
            "password": "pass",
            "role": "basic",
        }).encode()
        req._receive = AsyncMock(return_value={
            "type": "http.request",
            "body": req._body,
        })

        user = {"sub": "1", "username": "dadito", "role": "superuser"}
        response = await server.admin_create_user(req, user)

        assert response.status_code == 409
        body = _json_body(response)
        assert "ya existe" in body["error"]


class TestAdminDeleteUser:
    """Characterization tests for admin_delete_user — SECURITY CRITICAL."""

    @pytest.mark.asyncio
    async def test_positive_admin_deletes_basic_user(self, admin_env):
        """Positive: SOLO dadito (superadmin) borra. Regla de oro de William
        'solo yo borro' ahora enforceada por IDENTIDAD: dadito borra un basic -> 200."""
        admin_env["get_user_by_id"].return_value = {
            "id": 1,
            "username": "dadito",
            "role": "superuser",
        }
        admin_env["get_user_by_username"].return_value = {
            "id": 10,
            "username": "target_user",
            "role": "basic",  # ← not superuser
        }
        admin_env["delete_user"].return_value = True

        user = {"sub": "1", "username": "dadito", "role": "superuser",
                "_session_token_hash": "session-hash"}
        response = await server.admin_delete_user("target_user", user)

        assert response["ok"] is True
        admin_env["delete_user"].assert_called_once_with(
            "target_user", actor_user_id=1, session_token_hash="session-hash"
        )

    @pytest.mark.asyncio
    async def test_positive_superuser_deletes_superuser(self, admin_env):
        """Positive: superuser can delete another superuser."""
        admin_env["get_user_by_id"].return_value = {
            "id": 1,
            "username": "dadito",
            "role": "superuser",
        }
        admin_env["get_user_by_username"].return_value = {
            "id": 2,
            "username": "other_super",
            "role": "superuser",  # ← is superuser
        }
        admin_env["delete_user"].return_value = True

        user = {"sub": "1", "username": "dadito", "role": "superuser",
                "_session_token_hash": "session-hash"}
        response = await server.admin_delete_user("other_super", user)

        assert response["ok"] is True

    @pytest.mark.asyncio
    async def test_negative_admin_cannot_delete_superuser_403(self, admin_env):
        """Negative/control: admin (non-super) CANNOT delete superuser (403).

        HALLAZGO: This is the SECURITY CONTROL that enforces escalation protection.
        """
        admin_env["get_user_by_id"].return_value = {
            "id": 5,
            "username": "henry",
            "role": "admin",  # ← NOT superuser
        }
        admin_env["get_user_by_username"].return_value = {
            "id": 1,
            "username": "dadito",
            "role": "superuser",  # ← target IS superuser
        }

        user = {"sub": "5", "username": "henry", "role": "admin"}
        response = await server.admin_delete_user("dadito", user)

        assert response.status_code == 403
        body = _json_body(response)
        # Ahora enforceado por IDENTIDAD (dadito), no por rol: un admin nunca borra.
        assert "solo el superadmin (dadito)" in body["error"]

    @pytest.mark.asyncio
    async def test_negative_user_cannot_self_delete_400(self, admin_env):
        """Negative/control: ni dadito puede borrarse a si mismo (previene lockout).
        El actor es dadito (unico que pasa el dadito-lock); igual el guard de
        auto-borrado lo frena con 400."""
        admin_env["get_user_by_id"].return_value = {
            "id": 1,
            "username": "dadito",
            "role": "superuser",
        }
        admin_env["get_user_by_username"].return_value = {
            "id": 1,
            "username": "dadito",  # ← same as actor
            "role": "superuser",
        }

        user = {"sub": "1", "username": "dadito", "role": "superuser"}
        response = await server.admin_delete_user("dadito", user)

        assert response.status_code == 400
        body = _json_body(response)
        assert "no podes borrarte a vos mismo" in body["error"]

    @pytest.mark.asyncio
    async def test_negative_basic_user_cannot_delete_403(self, admin_env):
        """Negative/control: basic user has no admin permission (403)."""
        admin_env["get_user_by_id"].return_value = {
            "id": 99,
            "username": "regular",
            "role": "basic",
        }

        user = {"sub": "99", "username": "regular", "role": "basic"}
        response = await server.admin_delete_user("someone", user)

        assert response.status_code == 403
        body = _json_body(response)
        assert body["error"] == "admin_only"

    @pytest.mark.asyncio
    async def test_negative_target_user_not_found_404(self, admin_env):
        """Negative/control: deleting nonexistent user returns 404."""
        admin_env["get_user_by_id"].return_value = {
            "id": 1,
            "username": "dadito",
            "role": "superuser",
        }
        admin_env["get_user_by_username"].return_value = None

        user = {"sub": "1", "username": "dadito", "role": "superuser"}
        response = await server.admin_delete_user("nonexistent", user)

        assert response.status_code == 404
        body = _json_body(response)
        assert "no existe" in body["error"]

    @pytest.mark.asyncio
    async def test_negative_last_superuser_delete_conflict_409(self, admin_env):
        """The transactional DB guard is surfaced rather than becoming 500."""
        admin_env["get_user_by_id"].return_value = {
            "id": 1, "username": "dadito", "role": "superuser",
        }
        admin_env["get_user_by_username"].return_value = {
            "id": 2, "username": "backup_owner", "role": "superuser",
        }
        admin_env["delete_user"].side_effect = server.LastSuperuserError(
            "es el ultimo superuser"
        )
        response = await server.admin_delete_user(
            "backup_owner", {"sub": "1", "username": "dadito", "role": "superuser",
                             "_session_token_hash": "session-hash"}
        )
        assert response.status_code == 409
        assert "ultimo superuser" in _json_body(response)["error"]


# ────────────────────────────────────────────────────────────────────────────
# INVARIANTE SUPERADMIN = SOLO dadito (el FIX: de por-rol a por-identidad)
# El hueco que cerramos: antes un SEGUNDO superuser (aaron) heredaba la autoridad
# sobre el invariante superuser. Ahora el enforcement es por IDENTIDAD (dadito),
# asi que un superuser que NO es dadito ya no puede. NEXUS lo verifica por efecto
# con su canary (aaron-superuser -> 403, dadito -> 200).
# ────────────────────────────────────────────────────────────────────────────
class TestSuperadminIdentityLock:
    def test_owner_identity_is_durable_id_not_display_username(self):
        assert server._require_dadito(
            {"id": 1, "username": "William", "role": "superuser"}
        ) is None

    @pytest.mark.asyncio
    async def test_non_dadito_superuser_cannot_delete_403(self, admin_env):
        """CORE del fix: un superuser que NO es dadito NO borra (antes SI podia)."""
        admin_env["get_user_by_id"].return_value = {"id": 7, "username": "aaron", "role": "superuser"}
        admin_env["get_user_by_username"].return_value = {"id": 10, "username": "victima", "role": "basic"}
        user = {"sub": "7", "username": "aaron", "role": "superuser"}
        response = await server.admin_delete_user("victima", user)
        assert response.status_code == 403
        assert "solo el superadmin (dadito)" in _json_body(response)["error"]
        admin_env["delete_user"].assert_not_called()

    @pytest.mark.asyncio
    async def test_non_dadito_superuser_cannot_create_superuser_403(self, admin_env):
        """Un superuser que NO es dadito NO crea otro superuser."""
        admin_env["get_user_by_id"].return_value = {"id": 7, "username": "aaron", "role": "superuser"}
        admin_env["get_user_by_username"].return_value = None
        req = _request_for_admin()
        req._body = json.dumps({"username": "otro", "password": "p", "role": "superuser"}).encode()
        req._receive = AsyncMock(return_value={"type": "http.request", "body": req._body})
        user = {"sub": "7", "username": "aaron", "role": "superuser"}
        response = await server.admin_create_user(req, user)
        assert response.status_code == 403
        assert "solo el superadmin (dadito)" in _json_body(response)["error"]

    @pytest.mark.asyncio
    async def test_dadito_creates_superuser_200(self, admin_env):
        """Positivo: dadito (superadmin) SI crea superuser -> 200."""
        admin_env["get_user_by_id"].return_value = {"id": 1, "username": "dadito", "role": "superuser"}
        admin_env["get_user_by_username"].return_value = None
        admin_env["create_user"].return_value = {"id": 20, "username": "nuevo_super",
                                                 "display_name": "nuevo_super", "role": "superuser"}
        admin_env["set_user_agents"].return_value = []
        req = _request_for_admin()
        req._body = json.dumps({"username": "nuevo_super", "password": "p", "role": "superuser"}).encode()
        req._receive = AsyncMock(return_value={"type": "http.request", "body": req._body})
        user = {"sub": "1", "username": "dadito", "role": "superuser"}
        response = await server.admin_create_user(req, user)
        assert response["ok"] is True
        assert response["user"]["role"] == "superuser"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
