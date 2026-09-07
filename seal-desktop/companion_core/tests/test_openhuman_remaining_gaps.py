import pytest
from httpx import AsyncClient, ASGITransport

from companion_core.main import app


@pytest.mark.asyncio
async def test_health_exposes_operational_details():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert "database" in data
    assert "size_bytes" in data["database"]
    assert "system" in data
    assert "disk_free_mb" in data["system"]
    assert "services" in data
    assert "ollama" in data["services"]


@pytest.mark.asyncio
async def test_oauth_start_requires_real_provider_credentials():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/connections/oauth/start", json={"connector_id": "gmail"})
    assert r.status_code == 409
    data = r.json()
    assert data["ok"] is False
    assert data["provider"] == "google"
    assert data["configured"] is False
    assert "CLIENT_ID" in data["setup_hint"]


@pytest.mark.asyncio
async def test_oauth_start_builds_authorization_url(monkeypatch):
    monkeypatch.setenv("SEAL_GOOGLE_CLIENT_ID", "google-client")
    monkeypatch.setenv("SEAL_GOOGLE_CLIENT_SECRET", "google-secret")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/connections/oauth/start", json={"connector_id": "gmail"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["provider"] == "google"
    assert "accounts.google.com" in data["auth_url"]
    assert "client_id=google-client" in data["auth_url"]
    assert "state=" in data["auth_url"]


@pytest.mark.asyncio
async def test_oauth_callback_stores_connection_without_exposing_token(monkeypatch):
    import companion_core.main as main_mod

    monkeypatch.setenv("SEAL_GITHUB_CLIENT_ID", "gh-client")
    monkeypatch.setenv("SEAL_GITHUB_CLIENT_SECRET", "gh-secret")

    async def fake_exchange(meta, code, redirect_uri):
        assert meta["provider"] == "github"
        assert code == "abc123"
        return {"access_token": "secret-token", "token_type": "bearer"}

    monkeypatch.setattr(main_mod, "_oauth_exchange_token", fake_exchange)
    monkeypatch.setattr(main_mod, "_encrypt_oauth_token", lambda payload: (b"ciphertext", b"nonce12345678"))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        start = await client.post("/api/connections/oauth/start", json={"connector_id": "github"})
        state = start.json()["state"]
        cb = await client.get(f"/api/connections/oauth/callback?code=abc123&state={state}")
        listed = await client.get("/api/connections")

    assert cb.status_code == 200
    assert cb.json()["connected"] is True
    github = next(c for c in listed.json()["connectors"] if c["id"] == "github")
    assert github["connected"] is True
    assert "secret-token" not in str(listed.json())


@pytest.mark.asyncio
async def test_billing_plans_and_local_subscription():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        plans = await client.get("/api/billing/plans")
        sub = await client.get("/api/billing/subscription")
        patch = await client.patch("/api/billing/subscription", json={"plan_id": "plus", "status": "local"})
        sub2 = await client.get("/api/billing/subscription")

    assert plans.status_code == 200
    assert {p["id"] for p in plans.json()["plans"]} == {"free", "plus", "pro"}
    assert sub.json()["plan_id"] == "free"
    assert patch.status_code == 200
    assert sub2.json()["plan_id"] == "plus"
