from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tools import soul_prometheus_mcp as native


def test_query_and_label_limits() -> None:
    assert native._query("up") == "up"
    with pytest.raises(ValueError):
        native._query("x" * (native.MAX_QUERY_CHARS + 1))
    with pytest.raises(ValueError):
        native._safe_includes(["bad-label!"])


def test_range_contract() -> None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=1)
    native._validate_range(start.isoformat(), end.isoformat(), "30s")
    with pytest.raises(ValueError):
        native._validate_range(start.isoformat(), end.isoformat(), "1s")
    with pytest.raises(ValueError):
        native._validate_range(
            (end - timedelta(days=8)).isoformat(), end.isoformat(), "1m"
        )


def test_query_projection_and_truncation() -> None:
    payload = {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [
                {"metric": {"__name__": "up", "job": "soul", "secret": "x"}, "value": [1, "1"]},
                {"metric": {"__name__": "up", "job": "other"}, "value": [1, "0"]},
            ],
        },
    }
    result = native._bounded_query_payload(payload, ["job"], 1)
    assert result["data"]["result"][0]["metric"] == {"__name__": "up", "job": "soul"}
    assert result["truncated"] is True


def test_targets_remove_internal_urls_and_discovered_labels() -> None:
    target = {
        "labels": {"job": "seal"},
        "globalUrl": "http://internal:9090/metrics",
        "scrapeUrl": "http://secret-host/metrics",
        "discoveredLabels": {"token": "secret"},
        "health": "up",
    }
    result = native._safe_target(target)
    assert result["health"] == "up"
    assert "globalUrl" not in result
    assert "scrapeUrl" not in result
    assert "discoveredLabels" not in result


@pytest.mark.asyncio
async def test_query_uses_fixed_endpoint_path(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def fake_request(path, params, timeout):
        captured.update(path=path, params=params, timeout=timeout)
        return {"status": "success", "data": {"resultType": "vector", "result": []}}

    monkeypatch.setattr(native, "_request", fake_request)
    result = await native.prom_query("up", limit=10)
    assert result["status"] == "success"
    assert captured["path"] == "/api/v1/query"
    assert captured["params"] == {"query": "up"}
