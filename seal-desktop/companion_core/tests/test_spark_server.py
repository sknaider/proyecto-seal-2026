"""
Spec-required tests for companion_core Spark server-side auth (D2-D5).

Tests:
  test_enroll_creates_valid_key
  test_replay_attack_blocked
  test_timestamp_out_of_window
  test_signature_constant_time
  test_rate_limit_enforced
  test_audit_log_recorded
  test_revoke_client
"""
import hmac
import inspect
import secrets
import time
import pytest
from httpx import AsyncClient, ASGITransport

from companion_core.main import app
from companion_core.db import get_db
from companion_core.spark_auth import solve_pow, _sign
import companion_core.spark_auth as _sa


@pytest.fixture(autouse=True)
def reset_server_state():
    """Clear in-memory nonce/rate/PoW/enroll-IP stores between tests."""
    _sa._nonce_store.clear()
    _sa._rate_store.clear()
    _sa._pow_challenges.clear()
    _sa._enroll_ip_store.clear()
    yield
    _sa._nonce_store.clear()
    _sa._rate_store.clear()
    _sa._pow_challenges.clear()
    _sa._enroll_ip_store.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _auth_headers(api_key: str, client_id: str, method: str, path: str, body: bytes = b"") -> dict:
    """Build HMAC-signed request headers."""
    ts = int(time.time())
    nonce = secrets.token_hex(16)
    sig = _sign(api_key, method, path, ts, nonce, body)
    return {
        "X-Client-Id": client_id,
        "X-SEAL-Timestamp": str(ts),
        "X-SEAL-Nonce": nonce,
        "X-SEAL-Signature": sig,
    }


async def _enroll_client(ac: AsyncClient, device_fp: str | None = None) -> dict:
    """Full enrollment flow; returns {client_id, api_key, expires_at}."""
    r = await ac.get("/api/spark/server/pow-challenge")
    assert r.status_code == 200
    challenge = r.json()["challenge"]
    difficulty = r.json()["difficulty"]
    nonce = solve_pow(challenge, difficulty)

    pub_pem = (
        "-----BEGIN PUBLIC KEY-----\n"
        "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEtest==\n"
        "-----END PUBLIC KEY-----\n"
    )
    r2 = await ac.post("/api/spark/server/enroll", json={
        "pub_pem": pub_pem,
        "device_fp": device_fp or secrets.token_hex(32),
        "app_version": "test",
        "pow_challenge": challenge,
        "pow_nonce": nonce,
    })
    assert r2.status_code == 200, f"enroll failed: {r2.text}"
    return r2.json()


# ── Test 1: enrollment creates a valid key ────────────────────────────────────

@pytest.mark.anyio
async def test_enroll_creates_valid_key(monkeypatch):
    """POST /api/spark/server/enroll must return a 64-hex api_key and UUID client_id."""
    monkeypatch.setenv("SEAL_MASTER_KEY", secrets.token_bytes(32).hex())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        result = await _enroll_client(ac)

    assert "client_id" in result
    assert "api_key" in result
    assert "expires_at" in result
    assert len(result["api_key"]) == 64
    assert all(c in "0123456789abcdef" for c in result["api_key"])
    # client_id must be a UUID-ish string
    assert len(result["client_id"]) == 36 and result["client_id"].count("-") == 4


# ── Test 2: replay attack blocked (nonce reuse → 401) ────────────────────────

@pytest.mark.anyio
async def test_replay_attack_blocked(monkeypatch):
    """Same nonce used twice must return 401 on the second request."""
    monkeypatch.setenv("SEAL_MASTER_KEY", secrets.token_bytes(32).hex())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        creds = await _enroll_client(ac)
        client_id = creds["client_id"]
        api_key   = creds["api_key"]

        # Build headers once — fixed nonce
        ts    = int(time.time())
        nonce = secrets.token_hex(16)
        sig   = _sign(api_key, "GET", "/api/spark/server/access-log", ts, nonce, b"")
        hdrs  = {
            "X-Client-Id": client_id,
            "X-SEAL-Timestamp": str(ts),
            "X-SEAL-Nonce": nonce,
            "X-SEAL-Signature": sig,
        }

        # First request — must succeed
        r1 = await ac.get("/api/spark/server/access-log", headers=hdrs)
        assert r1.status_code == 200, f"first request failed: {r1.text}"

        # Second request with IDENTICAL nonce — must be rejected
        r2 = await ac.get("/api/spark/server/access-log", headers=hdrs)
        assert r2.status_code == 401
        assert "nonce_replay" in r2.text


# ── Test 3: stale timestamp window → 401 ─────────────────────────────────────

@pytest.mark.anyio
async def test_timestamp_out_of_window(monkeypatch):
    """Timestamp older than 300s must be rejected with 401."""
    monkeypatch.setenv("SEAL_MASTER_KEY", secrets.token_bytes(32).hex())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        creds = await _enroll_client(ac)
        client_id = creds["client_id"]
        api_key   = creds["api_key"]

        stale_ts = int(time.time()) - 400   # 400s in the past
        nonce = secrets.token_hex(16)
        sig   = _sign(api_key, "GET", "/api/spark/server/access-log", stale_ts, nonce, b"")
        hdrs  = {
            "X-Client-Id": client_id,
            "X-SEAL-Timestamp": str(stale_ts),
            "X-SEAL-Nonce": nonce,
            "X-SEAL-Signature": sig,
        }

        r = await ac.get("/api/spark/server/access-log", headers=hdrs)
        assert r.status_code == 401
        # verify_spark_signature rejects stale ts before nonce check
        assert r.status_code == 401


# ── Test 4: HMAC verify uses constant-time compare ───────────────────────────

def test_signature_constant_time():
    """
    verify_spark_signature must use hmac.compare_digest (constant-time).
    Inspect the source to guarantee it — no timing oracle.
    """
    from companion_core import spark_auth
    src = inspect.getsource(spark_auth.verify_spark_signature)
    assert "compare_digest" in src, (
        "verify_spark_signature must use hmac.compare_digest for constant-time comparison"
    )
    # Also verify the function rejects a wrong signature without exposing timing
    key = "a" * 64
    body = b'{"x":1}'
    ts = int(time.time())
    assert not spark_auth.verify_spark_signature(key, body, "GET", "/test", str(ts), "nonce", "wrong" * 12)


# ── Test 5: rate limit enforced → 429 ────────────────────────────────────────

@pytest.mark.anyio
async def test_rate_limit_enforced(monkeypatch):
    """After exceeding RATE_LIMIT requests in RATE_WINDOW seconds, server must return 429."""
    from companion_core import spark_auth
    # Patch limits so the test is fast (3 req/window)
    monkeypatch.setattr(spark_auth, "RATE_LIMIT", 3)
    monkeypatch.setattr(spark_auth, "RATE_WINDOW", 60.0)
    monkeypatch.setenv("SEAL_MASTER_KEY", secrets.token_bytes(32).hex())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        creds = await _enroll_client(ac)
        client_id = creds["client_id"]
        api_key   = creds["api_key"]

        statuses = []
        for _ in range(5):   # 3 allowed + 2 over limit
            h = _auth_headers(api_key, client_id, "GET", "/api/spark/server/access-log")
            r = await ac.get("/api/spark/server/access-log", headers=h)
            statuses.append(r.status_code)

        assert 429 in statuses, f"expected 429 after rate limit; got: {statuses}"
        # The first RATE_LIMIT requests must succeed
        assert statuses[:3] == [200, 200, 200], f"expected first 3 to be 200; got: {statuses}"


# ── Test 6: audit log recorded after authenticated request ────────────────────

@pytest.mark.anyio
async def test_audit_log_recorded(monkeypatch):
    """Each authenticated request must write a row to api_access_log."""
    monkeypatch.setenv("SEAL_MASTER_KEY", secrets.token_bytes(32).hex())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        creds = await _enroll_client(ac)
        client_id = creds["client_id"]
        api_key   = creds["api_key"]

        h = _auth_headers(api_key, client_id, "GET", "/api/spark/server/access-log")
        r = await ac.get("/api/spark/server/access-log", headers=h)
        assert r.status_code == 200

        # Check the DB directly — filter for the GET /access-log row specifically
        db = get_db()
        rows = await db.execute_fetchall(
            "SELECT client_id, method, path, status FROM api_access_log"
            " WHERE client_id=? AND method='GET'",
            (client_id,),
        )
        assert len(rows) >= 1, "api_access_log must record the GET /access-log request"
        assert rows[0][0] == client_id
        assert rows[0][1] == "GET"
        assert rows[0][3] == 200


# ── Test 7: revoked client gets 403 ──────────────────────────────────────────

@pytest.mark.anyio
async def test_revoke_client(monkeypatch):
    """After a client is revoked (revoked_at set), authenticated requests must return 403."""
    monkeypatch.setenv("SEAL_MASTER_KEY", secrets.token_bytes(32).hex())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        creds = await _enroll_client(ac)
        client_id = creds["client_id"]
        api_key   = creds["api_key"]

        # Confirm client works before revocation
        h = _auth_headers(api_key, client_id, "GET", "/api/spark/server/access-log")
        r = await ac.get("/api/spark/server/access-log", headers=h)
        assert r.status_code == 200, f"pre-revoke request failed: {r.text}"

        # Revoke directly in DB (avoids needing a second admin client)
        db = get_db()
        await db.execute(
            "UPDATE spark_clients SET revoked_at=datetime('now') WHERE client_id=?",
            (client_id,),
        )
        await db.commit()

        # Subsequent request must return 403
        h2 = _auth_headers(api_key, client_id, "GET", "/api/spark/server/access-log")
        r2 = await ac.get("/api/spark/server/access-log", headers=h2)
        assert r2.status_code == 403
        assert "revoked" in r2.text


# ── Bug-fix tests (NEXUS audit) ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_revoke_privilege_check(monkeypatch):
    """Client A must NOT be able to revoke client B (privilege escalation fix)."""
    monkeypatch.setenv("SEAL_MASTER_KEY", secrets.token_bytes(32).hex())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        creds_a = await _enroll_client(ac)
        creds_b = await _enroll_client(ac)   # different device_fp → unique
        client_id_a = creds_a["client_id"]
        api_key_a   = creds_a["api_key"]
        client_id_b = creds_b["client_id"]

        # Client A tries to revoke client B — must get 403
        h = _auth_headers(api_key_a, client_id_a, "DELETE",
                          f"/api/spark/server/clients/{client_id_b}")
        r = await ac.delete(f"/api/spark/server/clients/{client_id_b}", headers=h)
        assert r.status_code == 403, f"expected 403 for cross-client revoke; got {r.status_code}: {r.text}"
        assert "can_only_revoke_self" in r.text

        # Client A can still revoke itself
        h2 = _auth_headers(api_key_a, client_id_a, "DELETE",
                           f"/api/spark/server/clients/{client_id_a}")
        r2 = await ac.delete(f"/api/spark/server/clients/{client_id_a}", headers=h2)
        assert r2.status_code == 200


@pytest.mark.anyio
async def test_enroll_ip_rate_limit(monkeypatch):
    """Enrollment must reject with 429 after ENROLL_IP_LIMIT attempts from same IP."""
    monkeypatch.setenv("SEAL_MASTER_KEY", secrets.token_bytes(32).hex())
    monkeypatch.setattr(_sa, "ENROLL_IP_LIMIT", 2)
    monkeypatch.setattr(_sa, "ENROLL_IP_WINDOW", 3600.0)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        statuses = []
        for _ in range(4):
            r = await ac.get("/api/spark/server/pow-challenge")
            challenge = r.json()["challenge"]
            nonce = solve_pow(challenge, r.json()["difficulty"])
            r2 = await ac.post("/api/spark/server/enroll", json={
                "pub_pem": "-----BEGIN PUBLIC KEY-----\ntest\n-----END PUBLIC KEY-----\n",
                "device_fp": secrets.token_hex(32),
                "app_version": "test",
                "pow_challenge": challenge,
                "pow_nonce": nonce,
            })
            statuses.append(r2.status_code)

        assert 429 in statuses, f"expected 429 after IP rate limit; got: {statuses}"
        assert statuses[:2] == [200, 200], f"first 2 should succeed; got: {statuses}"


@pytest.mark.anyio
async def test_nonce_recorded_only_after_valid_signature(monkeypatch):
    """A request with wrong signature must NOT consume the nonce."""
    monkeypatch.setenv("SEAL_MASTER_KEY", secrets.token_bytes(32).hex())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        creds = await _enroll_client(ac)
        client_id = creds["client_id"]
        api_key   = creds["api_key"]

        ts    = int(time.time())
        nonce = secrets.token_hex(16)
        # Wrong signature — different key
        bad_sig = _sign("b" * 64, "GET", "/api/spark/server/access-log", ts, nonce, b"")
        hdrs_bad = {
            "X-Client-Id": client_id,
            "X-SEAL-Timestamp": str(ts),
            "X-SEAL-Nonce": nonce,
            "X-SEAL-Signature": bad_sig,
        }
        r_bad = await ac.get("/api/spark/server/access-log", headers=hdrs_bad)
        assert r_bad.status_code == 401
        assert "invalid_signature" in r_bad.text

        # Same nonce with correct signature must still work (nonce was NOT consumed)
        good_sig = _sign(api_key, "GET", "/api/spark/server/access-log", ts, nonce, b"")
        hdrs_good = {
            "X-Client-Id": client_id,
            "X-SEAL-Timestamp": str(ts),
            "X-SEAL-Nonce": nonce,
            "X-SEAL-Signature": good_sig,
        }
        r_good = await ac.get("/api/spark/server/access-log", headers=hdrs_good)
        assert r_good.status_code == 200, (
            f"nonce was incorrectly consumed by the failed request; got {r_good.status_code}: {r_good.text}"
        )
