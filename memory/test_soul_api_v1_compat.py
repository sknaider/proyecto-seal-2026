from __future__ import annotations

import importlib
import os
import pathlib
import subprocess
import sys

import pytest
from fastapi import HTTPException


HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))


def _compat_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SOUL_API_V1_DATABASE_URL", "postgresql://compat@127.0.0.1:5435/soul_api_db")
    monkeypatch.setenv("SOUL_API_V1_SECRET_KEY", "t" * 64)
    from soul_api_v1_compat import load_module

    return load_module()


def test_compat_fails_closed_without_database_or_secret() -> None:
    env = os.environ.copy()
    for key in ("DATABASE_URL", "SECRET_KEY", "SOUL_API_V1_DATABASE_URL", "SOUL_API_V1_SECRET_KEY"):
        env.pop(key, None)
    script = (
        "import importlib.util; "
        f"p={str(ROOT / 'seal-agent' / 'api' / 'main.py')!r}; "
        "s=importlib.util.spec_from_file_location('compat_fail_closed',p); "
        "m=importlib.util.module_from_spec(s); s.loader.exec_module(m)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], env=env, text=True, capture_output=True, timeout=10, check=False
    )
    assert completed.returncode != 0
    assert "SOUL_API_V1_DATABASE_URL is required" in completed.stderr


def test_native_jwt_roundtrip_and_tamper_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _compat_module(monkeypatch)
    token = module._make_jwt("agent-1")
    assert module._decode_jwt(token)["sub"] == "agent-1"
    with pytest.raises(module.JWTError):
        module._decode_jwt(token[:-1] + ("A" if token[-1] != "A" else "B"))


@pytest.mark.asyncio
async def test_widget_ui_rejects_query_keys_and_uses_text_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _compat_module(monkeypatch)
    agent_id = "00000000-0000-0000-0000-000000000001"
    with pytest.raises(HTTPException) as error:
        await module.chat_ui(agent_id, key="sk-seal-secret")
    assert error.value.status_code == 400
    response = await module.chat_ui(agent_id)
    assert "innerHTML" not in response
    assert "document.createTextNode" in response
    assert "sk-seal-secret" not in response


def test_absorbed_app_keeps_contract_and_ocean_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _compat_module(monkeypatch)
    paths = {route.path for route in module.app.routes}
    assert "/v1/auth/token" in paths
    assert "/v1/soul/{agent_id}" in paths
    assert "/v1/soul/{agent_id}/sync" in paths
    assert "/v1/widget/{agent_id}/chat" in paths
    assert "/install.ps1" in paths
    source = (ROOT / "seal-agent" / "api" / "main.py").read_text(encoding="utf-8")
    assert '"O": "ocean_o"' in source
    assert "invalid_ocean_dimension" in source


def test_distributed_sdk_uses_pgvector_only_and_canonical_local_mcp_port() -> None:
    sdk = ROOT / "seal-agent" / "api" / "sdk"
    installer = (sdk / "install.ps1").read_text(encoding="utf-8")
    compose = (sdk / "docker-compose.yml").read_text(encoding="utf-8")
    client_mcp = (sdk / "agent" / "mcp_server_client.py").read_text(encoding="utf-8")
    combined = "\n".join((installer, compose, client_mcp)).lower()
    assert "qdrant" not in combined
    assert "soul-vectors" not in combined
    assert "6335" not in combined
    assert "localhost:8766" not in combined
    assert "127.0.0.1:8766" not in combined
    assert "http://localhost:8771/sse" in installer
    assert '"127.0.0.1:8771:8771"' in compose
    assert "pgvector/pgvector:pg16" in compose
