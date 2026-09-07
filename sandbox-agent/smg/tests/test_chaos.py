"""Chaos tests — verify SMG behaves correctly when backends fail.

These are integration tests that need:
  * SMG running on :8770
  * seal-mcp-sse upstream on :8766
Skipped automatically if either is not reachable.
"""
from __future__ import annotations

import subprocess
import time

import httpx
import pytest

SMG_URL = "http://127.0.0.1:8770"
UPSTREAM_SERVICE = "seal-mcp-sse.service"


def _curl_ok(url: str) -> bool:
    try:
        r = httpx.get(url, timeout=2.0)
        return r.status_code < 500
    except Exception:
        return False


smg_live = _curl_ok(f"{SMG_URL}/health")

pytestmark = pytest.mark.skipif(not smg_live, reason="SMG not running on :8770")


def _systemctl(action: str, service: str) -> int:
    return subprocess.call(
        ["systemctl", "--user", action, service],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def test_health_endpoint_reports_primary_backend():
    r = httpx.get(f"{SMG_URL}/health", timeout=3.0)
    assert r.status_code == 200
    data = r.json()
    assert "status" in data
    assert "backends" in data
    assert "primary_sse" in data["backends"]


def test_message_passthrough_succeeds_when_backend_up():
    r = httpx.post(
        f"{SMG_URL}/message",
        headers={"X-SEAL-Agent": "NEXUS_CHAOS"},
        json={"jsonrpc": "2.0", "method": "ping", "id": 1},
        timeout=5.0,
    )
    # Upstream may return 200, 404, or 400 — we just need SMG didn't 500
    assert r.status_code < 500, f"SMG facade failed with {r.status_code}"


def test_rate_limit_activates_on_burst():
    # use a dedicated synthetic agent to avoid polluting real rate buckets
    agent = "CHAOS_BURST"
    statuses = []
    for _ in range(15):
        r = httpx.post(
            f"{SMG_URL}/message",
            headers={"X-SEAL-Agent": agent},
            json={"jsonrpc": "2.0", "method": "ping", "id": 1},
            timeout=2.0,
        )
        statuses.append(r.status_code)
    # burst is 10 — later requests must hit 429
    rate_limited = [s for s in statuses if s == 429]
    assert len(rate_limited) >= 3, f"expected rate-limit 429s, got {statuses}"


@pytest.mark.slow
def test_chaos_upstream_down_circuit_opens_then_recovers():
    """Stop upstream, wait for circuit to open, restart, verify recovery."""
    # Pre-condition: upstream alive
    assert _curl_ok(f"{SMG_URL}/health"), "SMG must be up"

    # Stop upstream
    rc = _systemctl("stop", UPSTREAM_SERVICE)
    assert rc == 0, "failed to stop upstream"

    try:
        # Give health monitor time (interval=30s) to detect multiple failures
        # For a fast test we also actively POST to trigger failures.
        time.sleep(2)
        for _ in range(3):
            try:
                httpx.post(f"{SMG_URL}/message", json={}, timeout=2.0)
            except httpx.HTTPError:
                pass
            time.sleep(0.5)

        # Now wait up to ~95s for circuit to open (monitor interval 30s * 3 failures)
        deadline = time.time() + 100
        opened = False
        while time.time() < deadline:
            data = httpx.get(f"{SMG_URL}/health", timeout=3.0).json()
            if not data["backends"]["primary_sse"]["healthy"]:
                opened = True
                break
            time.sleep(5)

        assert opened, "circuit never opened after backend down for 100s"
    finally:
        _systemctl("start", UPSTREAM_SERVICE)

    # Give recovery time (recovery_check_s=60)
    deadline = time.time() + 90
    recovered = False
    while time.time() < deadline:
        data = httpx.get(f"{SMG_URL}/health", timeout=3.0).json()
        if data["backends"]["primary_sse"]["healthy"]:
            recovered = True
            break
        time.sleep(5)
    assert recovered, "circuit never closed after backend recovered"
