import pytest
from httpx import AsyncClient, ASGITransport
from companion_core.main import app


@pytest.mark.asyncio
async def test_get_config_mode_is_user_product():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/config")
        assert r.status_code == 200
        data = r.json()
        assert data["mode"] == "user-product"
        assert "version" in data


@pytest.mark.asyncio
async def test_first_run_creates_config(tmp_path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/api/companion/first-run", json={
            "name": "William",
            "primary_agent": "USER",
            "ocean": {"O": 0.7, "C": 0.8, "E": 0.6, "A": 0.9, "N": 0.3}
        })
        assert r.status_code == 200
        data = r.json()
        assert data.get("ok") is True


@pytest.mark.asyncio
async def test_first_run_idempotent():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"name": "William", "primary_agent": "USER"}
        r1 = await client.post("/api/companion/first-run", json=payload)
        r2 = await client.post("/api/companion/first-run", json=payload)
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r2.json().get("ok") is True


@pytest.mark.asyncio
async def test_get_companion_config_returns_dict():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/companion/config")
        assert r.status_code == 200
        assert isinstance(r.json(), dict)


@pytest.mark.asyncio
async def test_patch_companion_config_persists():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.patch("/api/companion/config", json={"name": "TestUser"})
        assert r.status_code == 200
        assert r.json().get("ok") is True
        r2 = await client.get("/api/companion/config")
        assert r2.json().get("name") == "TestUser"
