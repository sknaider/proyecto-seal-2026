from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "messages" / "laptop_activity_api.py"
TEST_TOKEN = "test-only-laptop-token-not-a-live-secret"


def _load_module(monkeypatch, *, token: str | None = TEST_TOKEN, token_file: Path | None = None):
    monkeypatch.setenv(
        "SEAL_LAPTOP_ACTIVITY_PG_DSN",
        "postgresql://svc_seal_laptop_activity:test@localhost:5433/seal_memory",
    )
    monkeypatch.setenv("SEAL_REQUIRE_DEDICATED_OPERATIONAL_DSN", "1")
    monkeypatch.delenv("LAPTOP_API_TOKEN", raising=False)
    monkeypatch.delenv("LAPTOP_API_TOKEN_FILE", raising=False)
    if token is not None:
        monkeypatch.setenv("LAPTOP_API_TOKEN", token)
    if token_file is not None:
        monkeypatch.setenv("LAPTOP_API_TOKEN_FILE", str(token_file))

    spec = importlib.util.spec_from_file_location("laptop_activity_api_test", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_startup_fails_closed_without_token(monkeypatch):
    with pytest.raises(RuntimeError, match="missing required LAPTOP_API_TOKEN"):
        _load_module(monkeypatch, token=None)


def test_private_token_file_contract(monkeypatch, tmp_path):
    token_file = tmp_path / "laptop.token"
    token_file.write_text(TEST_TOKEN + "\n", encoding="utf-8")
    token_file.chmod(0o600)

    module = _load_module(monkeypatch, token=None, token_file=token_file)
    assert module.TOKEN == TEST_TOKEN

    token_file.chmod(0o640)
    with pytest.raises(RuntimeError, match="mode 0600 or stricter"):
        module._read_private_token_file(str(token_file))

    token_file.chmod(0o600)
    real_uid = os.geteuid()
    with monkeypatch.context() as owner_patch:
        owner_patch.setattr(module.os, "geteuid", lambda: real_uid + 1)
        with pytest.raises(RuntimeError, match="owned by the service uid"):
            module._read_private_token_file(str(token_file))

    symlink = tmp_path / "laptop-link.token"
    symlink.symlink_to(token_file)
    with pytest.raises(RuntimeError, match="regular non-symlink"):
        module._read_private_token_file(str(symlink))


def test_isolated_api_requires_correct_token(monkeypatch):
    module = _load_module(monkeypatch)

    class FakeConnection:
        async def fetchval(self, _query):
            return 2

    class FakeAcquire:
        async def __aenter__(self):
            return FakeConnection()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def acquire(self):
            return FakeAcquire()

    async def fake_pool():
        return FakePool()

    monkeypatch.setattr(module, "pool", fake_pool)
    with TestClient(module.app) as client:
        assert client.get("/health").status_code == 401
        assert client.get("/recent").status_code == 401
        assert client.post("/save", json={"content": "test"}).status_code == 401
        assert client.get("/health", headers={"X-Token": "incorrect"}).status_code == 401
        assert client.get("/recent", headers={"X-Token": "incorrect"}).status_code == 401
        assert client.post(
            "/save",
            headers={"X-Token": "incorrect"},
            json={"content": "test"},
        ).status_code == 401
        response = client.get("/health", headers={"X-Token": TEST_TOKEN})
        assert response.status_code == 200
        assert response.json() == {"ok": True, "rows": 2}
