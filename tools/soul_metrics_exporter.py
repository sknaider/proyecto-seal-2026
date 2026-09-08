#!/usr/bin/env python3
"""Exporter Prometheus mínimo de SOUL, sin contenidos de memoria ni chat."""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from typing import Any

import asyncpg
import uvicorn
from fastapi import FastAPI, Response


EXPECTED_ROLE = "mcp_observer"
SOUL_UNIT_PREFIXES = (
    "seal-",
    "ada-codex-",
    "orion-exam.",
    "dum-",
    "claude-proxy.",
    "fable-spectre-",
    "gtl-editor.",
    "spectre-",
)
app = FastAPI(title="SOUL Metrics Exporter", docs_url=None, redoc_url=None)


def _dsn() -> str:
    """DSN del observador, con respaldo en el archivo de credencial.

    Por qué el respaldo: la unidad se reconstruyó desde el journal el 7-sep y el
    journal preserva ExecStart pero NO EnvironmentFile, así que la variable sólo
    sobrevivía en el entorno del proceso vivo. Cuando el 7-sep 19:08 se rotó
    `mcp_observer`, ese valor congelado quedó inválido: la unidad siguió
    `active` y falló 943 veces seguidas contra la base. Un proceso de larga vida
    no vuelve a leer su entorno; el archivo sí se relee en cada conexión.

    El entorno mantiene la precedencia para no romper a quien lo defina bien.
    """
    value = os.environ.get("POSTGRES_MCP_DSN", "").strip()
    if value:
        return value
    # `_dsn_del_archivo` devuelve CADENA VACÍA cuando no puede leer el archivo;
    # sólo levanta si existe con permisos flojos. Un `except` no alcanza: sin
    # este chequeo explícito, `_dsn()` devolvía "" en silencio y el fallo
    # aparecía después, disfrazado de error de conexión. Lo encontró el control
    # negativo (sin variable Y sin archivo), no la prueba que confirmaba.
    # Anclado a __file__, NO al cwd: la unidad arranca desde otro directorio.
    # Y se carga `seal_observer_credencial`, que no importa nada fuera de la
    # biblioteca estándar: cargar `soul_postgres_mcp` para reusar la función
    # fallaba con ModuleNotFoundError porque su FastMCP necesita el paquete
    # `mcp`, ausente en el /usr/bin/python3 con el que corre este servicio.
    try:
        import importlib.util
        import pathlib as _pl
        _origen = _pl.Path(__file__).resolve().parent / "seal_observer_credencial.py"
        _spec = importlib.util.spec_from_file_location("seal_observer_credencial", _origen)
        if _spec is None or _spec.loader is None:
            raise ImportError(f"no pude cargar {_origen}")
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        del_archivo = _mod.leer_dsn_del_archivo()
    except Exception as exc:
        raise RuntimeError(
            "POSTGRES_MCP_DSN no está configurado y el archivo de credencial "
            f"no es utilizable ({type(exc).__name__})"
        ) from exc
    if not del_archivo:
        raise RuntimeError(
            "POSTGRES_MCP_DSN no está configurado y el archivo de credencial "
            "no existe o no contiene un DSN"
        )
    return del_archivo


def _label(value: Any) -> str:
    return (
        str(value if value is not None else "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )


def _number(value: Any) -> str:
    if value is None:
        return "NaN"
    if isinstance(value, Decimal):
        value = float(value)
    return str(value)


async def _snapshot() -> dict[str, list[dict[str, Any]]]:
    conn = await asyncpg.connect(_dsn(), command_timeout=10)
    try:
        identity = await conn.fetchrow(
            "SELECT current_user::text AS current_user, session_user::text AS session_user"
        )
        if (
            identity["current_user"] != EXPECTED_ROLE
            or identity["session_user"] != EXPECTED_ROLE
        ):
            raise PermissionError("exporter SOUL no autenticó como mcp_observer")
        async with conn.transaction(readonly=True):
            work = await conn.fetch(
                "SELECT agent, status, item_count FROM soul_v3.agent_work_status_v"
            )
            latency = await conn.fetch(
                """
                SELECT agent, tool_name, n, median_ms, p95_ms, avg_ms
                FROM soul_v3.tool_latency_health
                """
            )
            tools = await conn.fetch(
                """
                SELECT status, category, owner_agent, count(*)::bigint AS tools
                FROM soul_v3.v_agent_tools_boot
                GROUP BY status, category, owner_agent
                """
            )
        snapshot = {
            "work": [dict(row) for row in work],
            "latency": [dict(row) for row in latency],
            "tools": [dict(row) for row in tools],
        }
    finally:
        await conn.close()
    snapshot["units"] = await _systemd_snapshot()
    return snapshot


async def _systemd_snapshot() -> list[dict[str, str]]:
    """Return aggregate-safe health for loaded SOUL user units.

    `systemctl list-units` is the authority for active runtime state. We keep
    descriptions and command lines out of metrics to avoid leaking arguments.
    """
    process = await asyncio.create_subprocess_exec(
        "systemctl",
        "--user",
        "list-units",
        "--all",
        "--type=service",
        "--type=timer",
        "--plain",
        "--no-legend",
        "--no-pager",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5.0)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError("systemd user-unit snapshot timed out")
    if process.returncode != 0:
        raise RuntimeError(f"systemctl list-units failed rc={process.returncode}")

    units: list[dict[str, str]] = []
    for raw_line in stdout.decode("utf-8", errors="replace").splitlines():
        fields = raw_line.split(maxsplit=4)
        if len(fields) < 4:
            continue
        unit, load, active, sub = fields[:4]
        if load != "loaded" or not unit.startswith(SOUL_UNIT_PREFIXES):
            continue
        units.append(
            {
                "unit": unit,
                "type": unit.rsplit(".", 1)[-1],
                "active": active,
                "sub": sub,
            }
        )
    return units


def render_metrics(snapshot: dict[str, list[dict[str, Any]]]) -> str:
    lines = [
        "# HELP soul_exporter_up Whether the SOUL metrics snapshot completed.",
        "# TYPE soul_exporter_up gauge",
        "soul_exporter_up 1",
        "# HELP soul_agent_work_items Durable work items grouped by agent and status.",
        "# TYPE soul_agent_work_items gauge",
    ]
    for row in snapshot["work"]:
        lines.append(
            'soul_agent_work_items{agent="%s",status="%s"} %s'
            % (_label(row["agent"]), _label(row["status"]), _number(row["item_count"]))
        )
    lines.extend(
        [
            "# HELP soul_tool_calls_total Observed calls represented by the latency health view.",
            "# TYPE soul_tool_calls_total gauge",
            "# HELP soul_tool_latency_milliseconds Aggregated tool latency in milliseconds.",
            "# TYPE soul_tool_latency_milliseconds gauge",
        ]
    )
    for row in snapshot["latency"]:
        labels = 'agent="%s",tool="%s"' % (
            _label(row["agent"]),
            _label(row["tool_name"]),
        )
        lines.append(f"soul_tool_calls_total{{{labels}}} {_number(row['n'])}")
        for statistic, key in (("median", "median_ms"), ("p95", "p95_ms"), ("average", "avg_ms")):
            lines.append(
                f'soul_tool_latency_milliseconds{{{labels},statistic="{statistic}"}} '
                f"{_number(row[key])}"
            )
    lines.extend(
        [
            "# HELP soul_registered_tools Registered SOUL tools grouped by operational state.",
            "# TYPE soul_registered_tools gauge",
        ]
    )
    for row in snapshot["tools"]:
        lines.append(
            'soul_registered_tools{status="%s",category="%s",owner="%s"} %s'
            % (
                _label(row["status"]),
                _label(row["category"]),
                _label(row["owner_agent"]),
                _number(row["tools"]),
            )
        )
    lines.extend(
        [
            "# HELP soul_user_unit_state Current state of a loaded SOUL systemd user unit.",
            "# TYPE soul_user_unit_state gauge",
            "# HELP soul_user_units Loaded SOUL systemd user units grouped by state.",
            "# TYPE soul_user_units gauge",
        ]
    )
    unit_counts: dict[tuple[str, str, str], int] = {}
    for row in snapshot.get("units", []):
        labels = 'unit="%s",type="%s",active="%s",sub="%s"' % (
            _label(row["unit"]),
            _label(row["type"]),
            _label(row["active"]),
            _label(row["sub"]),
        )
        lines.append(f"soul_user_unit_state{{{labels}}} 1")
        key = (str(row["type"]), str(row["active"]), str(row["sub"]))
        unit_counts[key] = unit_counts.get(key, 0) + 1
    for (unit_type, active, sub), count in sorted(unit_counts.items()):
        lines.append(
            'soul_user_units{type="%s",active="%s",sub="%s"} %s'
            % (_label(unit_type), _label(active), _label(sub), count)
        )
    return "\n".join(lines) + "\n"


@app.get("/health")
async def health() -> dict[str, Any]:
    snapshot = await _snapshot()
    return {
        "ok": True,
        "role": EXPECTED_ROLE,
        "work_series": len(snapshot["work"]),
        "latency_series": len(snapshot["latency"]),
        "tool_series": len(snapshot["tools"]),
        "unit_series": len(snapshot["units"]),
    }


@app.get("/metrics")
async def metrics() -> Response:
    return Response(
        content=render_metrics(await _snapshot()),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9108, log_level="warning")
