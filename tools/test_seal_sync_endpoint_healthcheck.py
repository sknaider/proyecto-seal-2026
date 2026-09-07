from __future__ import annotations

import io
import json

import seal_sync_endpoint_healthcheck as health


class _Response:
    status = 200

    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self._body).encode("utf-8")


def test_probe_requires_exact_health_contract(monkeypatch):
    monkeypatch.setattr(
        health.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response({
            "status": "up",
            "service": "seal_sync_endpoint",
        }),
    )
    assert health.probe("http://sync/health") is True

    monkeypatch.setattr(
        health.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _Response({"status": "up"}),
    )
    assert health.probe("http://sync/health") is False


def test_wait_ready_retries_until_socket_is_ready(monkeypatch):
    attempts = iter([False, False, True])
    monkeypatch.setattr(health, "probe", lambda _url: next(attempts))
    monkeypatch.setattr(health.time, "sleep", lambda _seconds: None)
    ticks = iter([0.0, 0.1, 0.2, 0.3, 0.4])
    monkeypatch.setattr(health.time, "monotonic", lambda: next(ticks))
    assert health.wait_ready("100.64.0.1", 8778, 1.0) is True


def test_missing_tailnet_bind_fails_closed():
    assert health.wait_ready("", 8778, 1.0) is False
