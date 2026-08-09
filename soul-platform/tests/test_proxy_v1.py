from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx
import pytest
from soul_framework import Soul

from soul_platform.proxy import ProxySettings, create_app


def _settings(tmp_path: Path, model: str = "brain-a") -> ProxySettings:
    token = tmp_path / "proxy.token"
    token.write_text("t" * 40)
    token.chmod(0o600)
    return ProxySettings(
        soul_name="MachineSoul",
        soul_db=tmp_path / "MachineSoul.db",
        machine_soul_id=str(uuid.uuid4()),
        host="127.0.0.1",
        port=11435,
        require_auth=True,
        token_file=token,
        upstream_kind="openai-compatible",
        upstream_base_url="http://127.0.0.1:11434/v1",
        upstream_model=model,
    )


def _transport(captured: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={"data": [
                    {"id": name}
                    for name in ("brain-a", "gemma-test", "qwen-test", "brain")
                ]},
            )
        body = json.loads(request.content)
        captured.append(body)
        system = body["messages"][0]["content"]
        answer = "ORQUIDEA-127387" if "ORQUIDEA-127387" in system else "NO_MEMORY"
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "model": body["model"],
                "choices": [{"message": {"role": "assistant", "content": answer}}],
            },
        )

    return httpx.MockTransport(handler)


async def _seed(settings: ProxySettings):
    async with Soul.create(
        settings.soul_name, backend="sqlite", backend_url=str(settings.soul_db)
    ) as soul:
        await soul.memory.store("La clave de continuidad es ORQUIDEA-127387.", importance=10)


async def _request(app, method: str, path: str, **kwargs):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            return await client.request(method, path, **kwargs)


async def test_auth_health_ready_and_no_secret_leak(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings, upstream_transport=_transport([]))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            denied = await client.get("/v1/models")
            health = await client.get("/health")
            ready = await client.get("/ready")
    assert denied.status_code == 401
    assert health.status_code == 200
    assert health.json()["machine_soul_id"] == settings.machine_soul_id
    assert settings.upstream_base_url not in health.text
    assert settings.read_token() not in health.text
    assert ready.status_code == 200 and ready.json()["ready"] is True


async def test_ready_rejects_reachable_upstream_without_configured_model(tmp_path):
    settings = _settings(tmp_path, model="missing-model")
    app = create_app(settings, upstream_transport=_transport([]))
    response = await _request(app, "GET", "/ready")
    assert response.status_code == 503
    assert response.json() == {
        "ready": False,
        "soul_loaded": True,
        "brain_reachable": False,
    }


async def test_same_soul_survives_model_switch_and_process_restart(tmp_path):
    first = _settings(tmp_path, "gemma-test")
    await _seed(first)
    captured_a: list[dict] = []
    app_a = create_app(first, upstream_transport=_transport(captured_a))
    payload = {"messages": [{"role": "user", "content": "clave de continuidad"}]}
    response_a = await _request(
        app_a,
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {first.read_token()}"},
        json=payload,
    )
    second = ProxySettings(**{**first.__dict__, "upstream_model": "qwen-test"})
    captured_b: list[dict] = []
    app_b = create_app(second, upstream_transport=_transport(captured_b))
    response_b = await _request(
        app_b,
        "POST",
        "/v1/chat/completions",
        headers={"X-Soul-Token": second.read_token()},
        json=payload,
    )
    assert response_a.json()["choices"][0]["message"]["content"] == "ORQUIDEA-127387"
    assert response_b.json()["choices"][0]["message"]["content"] == "ORQUIDEA-127387"
    assert captured_a[0]["model"] == "gemma-test"
    assert captured_b[0]["model"] == "qwen-test"
    assert response_a.headers["X-Soul-Id"] == response_b.headers["X-Soul-Id"]
    assert response_a.headers["X-Soul-Baseline"] == response_b.headers["X-Soul-Baseline"]
    assert int(response_a.headers["X-Soul-Memories"]) >= 1
    assert response_a.headers["X-Soul-Memory-Ids"] == response_b.headers["X-Soul-Memory-Ids"]
    assert response_a.headers["X-Soul-Memory-SHA256"] == response_b.headers["X-Soul-Memory-SHA256"]


async def test_different_soul_is_negative_control(tmp_path):
    settings = _settings(tmp_path)
    captured: list[dict] = []
    response = await _request(
        create_app(settings, upstream_transport=_transport(captured)),
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.read_token()}"},
        json={"messages": [{"role": "user", "content": "clave de continuidad"}]},
    )
    assert response.json()["choices"][0]["message"]["content"] == "NO_MEMORY"
    assert int(response.headers["X-Soul-Memories"]) == 0


async def test_streaming_and_oversized_requests_fail_closed(tmp_path):
    settings = _settings(tmp_path)
    app = create_app(settings, upstream_transport=_transport([]))
    headers = {"Authorization": f"Bearer {settings.read_token()}"}
    streamed = await _request(
        app,
        "POST",
        "/v1/chat/completions",
        headers=headers,
        json={"stream": True, "messages": [{"role": "user", "content": "hi"}]},
    )
    tiny = ProxySettings(**{**settings.__dict__, "max_request_bytes": 4096})
    oversized = await _request(
        create_app(tiny, upstream_transport=_transport([])),
        "POST",
        "/v1/chat/completions",
        headers=headers,
        json={"messages": [{"role": "user", "content": "x" * 5000}]},
    )
    assert streamed.status_code == 422
    assert oversized.status_code == 413


async def test_upstream_response_limit_and_authenticated_shutdown(tmp_path):
    settings = ProxySettings(**{**_settings(tmp_path).__dict__, "max_response_bytes": 4096})

    def oversized(_request):
        return httpx.Response(200, content=b"x" * 5000)

    app = create_app(settings, upstream_transport=httpx.MockTransport(oversized))
    called = []
    app.state.request_shutdown = lambda: called.append(True)
    headers = {"Authorization": f"Bearer {settings.read_token()}"}
    response = await _request(
        app,
        "POST",
        "/v1/chat/completions",
        headers=headers,
        json={"messages": [{"role": "user", "content": "hi"}]},
    )
    stopped = await _request(app, "POST", "/admin/shutdown", headers=headers)
    assert response.status_code == 502 and response.json()["error"] == "upstream response too large"
    assert stopped.status_code == 200 and called == [True]


def test_settings_reject_public_bind_remote_without_opt_in_and_weak_token(tmp_path):
    settings = _settings(tmp_path)
    with pytest.raises(ValueError, match="loopback"):
        ProxySettings(**{**settings.__dict__, "host": "0.0.0.0"}).validate()
    with pytest.raises(ValueError, match="disabled"):
        ProxySettings(
            **{**settings.__dict__, "upstream_base_url": "https://example.com/v1"}
        ).validate()
    settings.token_file.write_text("weak")
    with pytest.raises(ValueError, match="32 bytes"):
        settings.validate()
    settings.token_file.write_text("t" * 40)
    with pytest.raises(ValueError, match="api_key_env"):
        ProxySettings(**{**settings.__dict__, "upstream_api_key_env": "AWS_SECRET_ACCESS_KEY"}).validate()


def test_config_and_soul_paths_must_be_private_and_canonical(tmp_path):
    settings = _settings(tmp_path)
    config = tmp_path / "proxy.toml"
    config.write_text(
        "[soul]\n"
        f'name="MachineSoul"\ndb="{settings.soul_db}"\n'
        f'machine_soul_id="{settings.machine_soul_id}"\n'
        "[proxy]\n"
        f'host="127.0.0.1"\nport=11435\nrequire_auth=true\ntoken_file="{settings.token_file}"\n'
        '[upstream]\nkind="ollama"\nbase_url="http://127.0.0.1:11434/v1"\nmodel="brain"\n'
    )
    config.chmod(0o666)
    with pytest.raises(ValueError, match="group/other"):
        ProxySettings.from_toml(config)
    config.chmod(0o600)
    outside = tmp_path.parent / "outside.db"
    config.write_text(config.read_text().replace(str(settings.soul_db), str(outside)))
    with pytest.raises(ValueError, match="canonical SOUL root"):
        ProxySettings.from_toml(config)
