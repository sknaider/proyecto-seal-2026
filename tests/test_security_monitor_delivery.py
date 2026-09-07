from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "sandbox-agent"
    / "seal_security_monitor.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "seal_security_monitor_delivery_test", MODULE_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_failed_delivery_does_not_consume_cooldown(monkeypatch):
    monitor = _load_module()
    monitor.log = lambda _message: None

    def fail(*_args, **_kwargs):
        raise RuntimeError("synthetic delivery failure")

    monkeypatch.setattr(monitor, "send_agent_message_sync", fail)
    assert monitor.alert("HIGH", "retryable", "synthetic") is False
    assert "retryable" not in monitor._alert_cooldowns


def test_confirmed_delivery_starts_cooldown(monkeypatch):
    monitor = _load_module()
    monitor.log = lambda _message: None
    monkeypatch.setattr(
        monitor,
        "send_agent_message_sync",
        lambda *_args, **_kwargs: {"ok": True},
    )
    assert monitor.alert("HIGH", "confirmed", "synthetic") is True
    assert monitor._alert_cooldowns["confirmed"] > 0
    assert monitor.alert("HIGH", "confirmed", "synthetic") is False
