#!/usr/bin/env python3
"""Adaptador MCP Prometheus nativo de SOUL con cuotas y salida curada."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP


MCP_NAME = "soul-prometheus-native"
PROMETHEUS_URL = "http://127.0.0.1:9090"
MAX_QUERY_CHARS = 4096
MAX_RANGE_SECONDS = 7 * 24 * 3600
MIN_STEP_SECONDS = 15.0
MAX_POINTS_PER_SERIES = 10_000
MAX_SERIES = 500
MAX_RESPONSE_BYTES = 1_000_000
_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h|d|w)$")
_LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

mcp = FastMCP(MCP_NAME)


def _limit(value: int | None, default: int = 200) -> int:
    if value is None:
        value = default
    return max(1, min(int(value), MAX_SERIES))


def _query(value: str) -> str:
    value = str(value).strip()
    if not value or len(value) > MAX_QUERY_CHARS or "\x00" in value:
        raise ValueError(f"query debe tener 1..{MAX_QUERY_CHARS} caracteres")
    return value


def _timestamp(value: str) -> float:
    raw = str(value).strip()
    try:
        return float(raw)
    except ValueError:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()


def _duration_seconds(value: str) -> float:
    match = _DURATION_RE.fullmatch(str(value).strip())
    if not match:
        raise ValueError("step inválido; use ms|s|m|h|d|w")
    amount = float(match.group(1))
    factors = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return amount * factors[match.group(2)]


def _validate_range(start: str, end: str, step: str) -> None:
    start_ts = _timestamp(start)
    end_ts = _timestamp(end)
    step_seconds = _duration_seconds(step)
    span = end_ts - start_ts
    if span <= 0 or span > MAX_RANGE_SECONDS:
        raise ValueError("range debe ser positivo y no superar 7 días")
    if step_seconds < MIN_STEP_SECONDS:
        raise ValueError("step mínimo: 15s")
    if math.ceil(span / step_seconds) + 1 > MAX_POINTS_PER_SERIES:
        raise ValueError("range excede el presupuesto de muestras")


def _safe_includes(includes: list[str] | None) -> list[str]:
    if not includes:
        return []
    values = [str(item) for item in includes]
    if len(values) > 32 or any(not _LABEL_RE.fullmatch(item) for item in values):
        raise ValueError("includes contiene labels inválidos")
    return values


async def _request(path: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(
            base_url=PROMETHEUS_URL,
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
        ) as client:
            response = await client.get(path, params=params)
            response.raise_for_status()
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise ValueError("respuesta Prometheus excede 1 MiB")
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError("Prometheus no respondió dentro del contrato seguro") from exc
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise RuntimeError("Prometheus devolvió una respuesta inválida")
    return payload


def _project_metric(metric: dict[str, Any], includes: list[str]) -> dict[str, Any]:
    if not includes:
        return dict(metric)
    allowed = {"__name__", *includes}
    return {key: value for key, value in metric.items() if key in allowed}


def _bounded_query_payload(
    payload: dict[str, Any], includes: list[str], limit: int
) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Prometheus devolvió data inválida")
    result = data.get("result")
    if not isinstance(result, list):
        return payload
    selected = result[:limit]
    samples = 0
    for item in selected:
        if not isinstance(item, dict):
            continue
        metric = item.get("metric")
        if isinstance(metric, dict):
            item["metric"] = _project_metric(metric, includes)
        values = item.get("values")
        if isinstance(values, list):
            samples += len(values)
        elif "value" in item:
            samples += 1
    if samples > MAX_POINTS_PER_SERIES * max(1, len(selected)):
        raise RuntimeError("Prometheus excedió el presupuesto de muestras")
    data["result"] = selected
    payload["truncated"] = len(result) > len(selected)
    payload["series_returned"] = len(selected)
    return payload


@mcp.tool()
async def prom_query(
    query: str,
    time: str = "",
    includes: list[str] | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Ejecuta PromQL instantáneo con endpoint fijo, cuotas y labels opcionales."""
    params: dict[str, Any] = {"query": _query(query)}
    if time:
        _timestamp(time)
        params["time"] = time
    safe_includes = _safe_includes(includes)
    payload = await _request("/api/v1/query", params, timeout=10)
    return _bounded_query_payload(payload, safe_includes, _limit(limit))


@mcp.tool()
async def prom_range(
    query: str,
    start: str,
    end: str,
    step: str,
    includes: list[str] | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Ejecuta PromQL por rango: máximo 7 días, step mínimo 15s y salida acotada."""
    _validate_range(start, end, step)
    safe_includes = _safe_includes(includes)
    payload = await _request(
        "/api/v1/query_range",
        {"query": _query(query), "start": start, "end": end, "step": step},
        timeout=30,
    )
    return _bounded_query_payload(payload, safe_includes, _limit(limit))


@mcp.tool()
async def prom_discover(prefix: str = "", limit: int = 500) -> dict[str, Any]:
    """Descubre nombres de métricas con filtro/paginado duro."""
    if len(prefix) > 200:
        raise ValueError("prefix demasiado largo")
    payload = await _request("/api/v1/label/__name__/values", {}, timeout=10)
    names = payload.get("data")
    if not isinstance(names, list):
        raise RuntimeError("Prometheus devolvió métricas inválidas")
    filtered = [str(name) for name in names if str(name).startswith(prefix)]
    selected = filtered[: _limit(limit, MAX_SERIES)]
    return {
        "status": "success",
        "data": selected,
        "count": len(selected),
        "truncated": len(filtered) > len(selected),
    }


@mcp.tool()
async def prom_metadata(metric: str = "", limit: int = 500) -> dict[str, Any]:
    """Obtiene metadata de métricas con límite de nombres devueltos."""
    if metric and not _LABEL_RE.fullmatch(metric):
        raise ValueError("metric inválida")
    payload = await _request(
        "/api/v1/metadata", {"metric": metric} if metric else {}, timeout=10
    )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Prometheus devolvió metadata inválida")
    keys = sorted(data)[: _limit(limit, MAX_SERIES)]
    return {
        "status": "success",
        "data": {key: data[key] for key in keys},
        "count": len(keys),
        "truncated": len(data) > len(keys),
    }


def _safe_target(target: dict[str, Any]) -> dict[str, Any]:
    labels = target.get("labels") if isinstance(target.get("labels"), dict) else {}
    return {
        "labels": {str(k): str(v) for k, v in labels.items()},
        "scrapePool": str(target.get("scrapePool", "")),
        "health": str(target.get("health", "unknown")),
        "lastScrape": str(target.get("lastScrape", "")),
        "lastScrapeDuration": target.get("lastScrapeDuration"),
        "lastError": str(target.get("lastError", ""))[:300],
        "scrapeInterval": str(target.get("scrapeInterval", "")),
        "scrapeTimeout": str(target.get("scrapeTimeout", "")),
    }


@mcp.tool()
async def prom_targets(state: str = "active") -> dict[str, Any]:
    """Devuelve salud de targets sin URLs globales ni discoveredLabels internos."""
    if state not in {"active", "dropped", "any"}:
        raise ValueError("state debe ser active|dropped|any")
    payload = await _request("/api/v1/targets", {"state": state}, timeout=10)
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("Prometheus devolvió targets inválidos")
    active = data.get("activeTargets") if isinstance(data.get("activeTargets"), list) else []
    dropped = data.get("droppedTargets") if isinstance(data.get("droppedTargets"), list) else []
    return {
        "status": "success",
        "data": {
            "activeTargets": [_safe_target(item) for item in active[:MAX_SERIES]],
            "droppedTargets": [_safe_target(item) for item in dropped[:MAX_SERIES]],
        },
        "truncated": len(active) > MAX_SERIES or len(dropped) > MAX_SERIES,
    }


if __name__ == "__main__":
    mcp.run()
