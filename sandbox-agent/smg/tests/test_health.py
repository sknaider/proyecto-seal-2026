"""Unit tests for health monitor circuit breaker."""
import pytest

from smg.health import HealthMonitor


def test_circuit_opens_after_threshold():
    h = HealthMonitor({"a": "http://127.0.0.1:1/health"}, failure_threshold=3)
    assert h.is_healthy("a") is True
    h.mark_failure("a")
    h.mark_failure("a")
    assert h.is_healthy("a") is True
    h.mark_failure("a")
    assert h.is_healthy("a") is False


def test_circuit_closes_on_success():
    h = HealthMonitor({"a": "http://127.0.0.1:1/health"}, failure_threshold=2)
    h.mark_failure("a")
    h.mark_failure("a")
    assert h.is_healthy("a") is False
    h.mark_success("a")
    assert h.is_healthy("a") is True


def test_snapshot_shape():
    h = HealthMonitor({"a": "http://127.0.0.1:1/health"})
    snap = h.snapshot()
    assert "a" in snap
    assert "healthy" in snap["a"]
    assert "consecutive_failures" in snap["a"]


def test_is_healthy_unknown_backend():
    h = HealthMonitor({})
    assert h.is_healthy("missing") is False
