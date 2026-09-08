#!/usr/bin/env python3
"""MCP PostgreSQL nativo de SOUL: observabilidad tipada, sin SQL arbitrario.

El servidor autentica directamente como ``mcp_observer`` y falla cerrado si la
credencial deriva a otro rol. Solo consulta las cuatro vistas operativas que el
rol tiene permitidas; no existe una herramienta ``query`` ni una ruta para
inyectar identificadores o sentencias SQL.
"""

from __future__ import annotations

import os
import pathlib
import stat
import hashlib
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import asyncpg
from mcp.server.fastmcp import FastMCP


MCP_NAME = "soul-postgres-native"
EXPECTED_ROLE = "mcp_observer"
MAX_ROWS = 200

EXPECTED_VIEWS = {
    "agent_work_status_v": {
        "columns": ("agent", "status", "item_count", "last_updated_at"),
        "sha256": "0ad8847949ba6f92e6d427e452119790f68967b77770559aa896621425c1a322",
    },
    "tool_latency_health": {
        "columns": ("agent", "tool_name", "n", "median_ms", "p95_ms", "avg_ms"),
        "sha256": "1c4f015487bd91ca8608551cff56405c751a7302277eef995499ad93cc3773a0",
    },
    "v_agent_tools_boot": {
        "columns": (
            "tool_key", "display_name", "category", "purpose", "endpoint", "host",
            "port", "protocol", "owner_agent", "status", "usage_hint",
            "last_checked_at", "last_used_at",
        ),
        "sha256": "6ec017400340777e9fad7e2e19e5a4ef00a9ea131c5b4d47bdf54161ef000369",
    },
    "v_tool_broker_observe_rollup": {
        "columns": (
            "actor", "action", "capability", "tool_class", "decision",
            "would_decision", "calls", "first_seen", "last_seen", "claimed_agent",
        ),
        "sha256": "9cc5d67ea988cefe0c458b9d379dae9ac9187258ab49569577f06a0a6bcb11fe",
    },
}

mcp = FastMCP(MCP_NAME)


def _limit(value: int) -> int:
    return max(1, min(int(value), MAX_ROWS))


def _json_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


def _records(rows: list[asyncpg.Record]) -> list[dict[str, Any]]:
    return [
        {key: _json_value(value) for key, value in dict(row).items()}
        for row in rows
    ]


# Credencial del observer en un ARCHIVO, no sólo en el entorno.
#
# POR QUE (7-sep-2026 19:25): el DSN vivía únicamente en `POSTGRES_MCP_DSN`, heredado
# por el proceso al arrancar. Cuando repuse la credencial del observer -el archivo se
# había perdido con el home- roté el rol, y los dos servidores MCP que corrían desde el
# 2-sep se quedaron con la clave vieja EN MEMORIA: no hay forma de que un proceso
# releea una variable de entorno. Una credencial que sólo vive en el entorno no se
# puede rotar sin reiniciar a todos los que la heredaron.
#
# El archivo -modo 600, el mismo que exige el stability guard- sí se relee.
# La lectura vive en `seal_observer_credencial`, sin dependencias, para que la
# comparta el exporter de métricas (su unidad corre con /usr/bin/python3, donde
# el paquete `mcp` no existe y cargar ESTE módulo falla). Una sola copia.
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "seal_observer_credencial",
    pathlib.Path(__file__).resolve().parent / "seal_observer_credencial.py",
)
_cred = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_cred)

_OBSERVER_ENV = _cred.RUTA_POR_DEFECTO
_CLAVES_DSN = _cred.CLAVES_DSN


def _dsn_del_archivo(ruta: pathlib.Path | None = None) -> str:
    """Envoltorio: resuelve `_OBSERVER_ENV` EN CADA LLAMADA.

    El módulo compartido tiene la lógica y los comentarios que explican por qué
    no puede ser un default de firma. Acá se conserva el nombre `_OBSERVER_ENV`
    como atributo de ESTE módulo porque los 13 brazos de
    `tests/test_soul_postgres_mcp_dsn_v1.py` lo monkeypatchean: si la función
    leyera el global del otro módulo, esos tests pasarían sin probar nada.
    """
    return _cred.leer_dsn_del_archivo(_OBSERVER_ENV if ruta is None else ruta)


def _dsn() -> str:
    value = os.environ.get("POSTGRES_MCP_DSN", "").strip()
    if value:
        return value
    value = _dsn_del_archivo()
    if not value:
        raise RuntimeError(
            "POSTGRES_MCP_DSN no está configurado y no pude leer un DSN de "
            f"{_OBSERVER_ENV}"
        )
    return value


async def _connect() -> asyncpg.Connection:
    try:
        conn = await asyncpg.connect(_dsn(), command_timeout=15)
    except asyncpg.InvalidPasswordError:
        # ADA, 19:29: una credencial VIEJA en el entorno seguia ganando, que es EXACTAMENTE
        # lo que rompio el MCP a las 19:08 -dos procesos del 2-sep con la clave anterior en
        # memoria-. El reintento va acotado: SOLO ante fallo de autenticacion, UNA vez, y
        # solo si el archivo ofrece un DSN distinto del que acaba de fallar. Cualquier otro
        # error se propaga: un reintento amplio esconderia una caida real de la base.
        del_archivo = _dsn_del_archivo()
        if not del_archivo or del_archivo == os.environ.get("POSTGRES_MCP_DSN", "").strip():
            raise
        conn = await asyncpg.connect(del_archivo, command_timeout=15)
    identity = await conn.fetchrow(
        """
        SELECT current_user::text AS current_user,
               session_user::text AS session_user,
               current_setting('transaction_read_only') AS transaction_read_only,
               r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolcanlogin,
               r.rolreplication, r.rolbypassrls
        FROM pg_catalog.pg_roles r
        WHERE r.rolname = current_user
        """
    )
    if (
        identity["current_user"] != EXPECTED_ROLE
        or identity["session_user"] != EXPECTED_ROLE
        or identity["transaction_read_only"] != "on"
        or identity["rolsuper"]
        or identity["rolcreaterole"]
        or identity["rolcreatedb"]
        or not identity["rolcanlogin"]
        or identity["rolreplication"]
        or identity["rolbypassrls"]
    ):
        await conn.close()
        raise PermissionError(
            "frontera PostgreSQL inválida: el MCP no autenticó como el rol observador"
        )
    try:
        await _verify_view_contract(conn)
    except Exception:
        await conn.close()
        raise
    return conn


async def _verify_view_contract(conn: asyncpg.Connection) -> None:
    """Fail closed if an approved view changes shape or meaning silently."""
    for view_name, expected in EXPECTED_VIEWS.items():
        column_rows = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'soul_v3' AND table_name = $1
            ORDER BY ordinal_position
            """,
            view_name,
        )
        columns = tuple(row["column_name"] for row in column_rows)
        definition = await conn.fetchval(
            "SELECT pg_get_viewdef(('soul_v3.' || $1)::regclass, true)",
            view_name,
        )
        digest = hashlib.sha256(definition.encode("utf-8")).hexdigest()
        if columns != expected["columns"] or digest != expected["sha256"]:
            raise PermissionError(
                f"contrato de vista PostgreSQL cambió sin revisión: soul_v3.{view_name}"
            )


async def _fetch(sql: str, *args: Any) -> list[dict[str, Any]]:
    conn = await _connect()
    try:
        async with conn.transaction(readonly=True):
            rows = await conn.fetch(sql, *args)
        return _records(rows)
    finally:
        await conn.close()


@mcp.tool()
async def soul_postgres_readiness() -> dict[str, Any]:
    """Verifica identidad, modo read-only y disponibilidad de las vistas aprobadas."""
    conn = await _connect()
    try:
        row = await conn.fetchrow(
            """
            SELECT current_user::text AS current_user,
                   session_user::text AS session_user,
                   current_setting('transaction_read_only') AS transaction_read_only,
                   has_table_privilege(current_user, 'soul_v3.agent_work_status_v', 'SELECT') AS work_view,
                   has_table_privilege(current_user, 'soul_v3.tool_latency_health', 'SELECT') AS latency_view,
                   has_table_privilege(current_user, 'soul_v3.v_agent_tools_boot', 'SELECT') AS tools_view,
                   has_table_privilege(current_user, 'soul_v3.v_tool_broker_observe_rollup', 'SELECT') AS broker_view,
                   has_table_privilege(current_user, 'soul_v3.chat_messages', 'SELECT') AS private_chat
            """
        )
        result = {key: _json_value(value) for key, value in dict(row).items()}
        result["ok"] = bool(
            result["current_user"] == EXPECTED_ROLE
            and result["session_user"] == EXPECTED_ROLE
            and result["transaction_read_only"] == "on"
            and result["work_view"]
            and result["latency_view"]
            and result["tools_view"]
            and result["broker_view"]
            and not result["private_chat"]
        )
        result["arbitrary_sql"] = False
        return result
    finally:
        await conn.close()


@mcp.tool()
async def soul_agent_work_status(
    agent: str = "",
    status: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """Resume trabajo durable por agente/estado desde la vista aprobada."""
    rows = await _fetch(
        """
        SELECT agent, status, item_count, last_updated_at
        FROM soul_v3.agent_work_status_v
        WHERE ($1 = '' OR agent = upper($1))
          AND ($2 = '' OR status = lower($2))
        ORDER BY last_updated_at DESC NULLS LAST, agent, status
        LIMIT $3
        """,
        agent.strip(),
        status.strip(),
        _limit(limit),
    )
    return {"ok": True, "count": len(rows), "rows": rows}


@mcp.tool()
async def soul_tool_latency(
    agent: str = "",
    tool_name: str = "",
    min_calls: int = 1,
    limit: int = 100,
) -> dict[str, Any]:
    """Lee latencia agregada de herramientas, excluyendo long-polls bloqueantes."""
    rows = await _fetch(
        """
        SELECT agent, tool_name, n, median_ms, p95_ms, avg_ms
        FROM soul_v3.tool_latency_health
        WHERE ($1 = '' OR agent = upper($1))
          AND ($2 = '' OR tool_name = $2)
          AND n >= $3
        ORDER BY p95_ms DESC NULLS LAST, n DESC
        LIMIT $4
        """,
        agent.strip(),
        tool_name.strip(),
        max(0, int(min_calls)),
        _limit(limit),
    )
    return {"ok": True, "count": len(rows), "rows": rows}


@mcp.tool()
async def soul_tools_inventory(
    status: str = "",
    category: str = "",
    owner_agent: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    """Lista herramientas/servicios SOUL registrados y su último estado conocido."""
    rows = await _fetch(
        """
        SELECT tool_key, display_name, category, purpose, endpoint, host, port,
               protocol, owner_agent, status, usage_hint, last_checked_at, last_used_at
        FROM soul_v3.v_agent_tools_boot
        WHERE ($1 = '' OR status = lower($1))
          AND ($2 = '' OR category = $2)
          AND ($3 = '' OR owner_agent = upper($3))
        ORDER BY status, category, tool_key
        LIMIT $4
        """,
        status.strip(),
        category.strip(),
        owner_agent.strip(),
        _limit(limit),
    )
    return {"ok": True, "count": len(rows), "rows": rows}


@mcp.tool()
async def soul_broker_observability(
    actor: str = "",
    decision: str = "",
    capability: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    """Consulta el rollup seguro del broker de capacidades sin exponer payloads."""
    rows = await _fetch(
        """
        SELECT actor, action, capability, tool_class, decision, would_decision,
               calls, first_seen, last_seen, claimed_agent
        FROM soul_v3.v_tool_broker_observe_rollup
        WHERE ($1 = '' OR actor = upper($1))
          AND ($2 = '' OR decision = lower($2))
          AND ($3 = '' OR capability = $3)
        ORDER BY last_seen DESC NULLS LAST, calls DESC
        LIMIT $4
        """,
        actor.strip(),
        decision.strip(),
        capability.strip(),
        _limit(limit),
    )
    return {"ok": True, "count": len(rows), "rows": rows}


if __name__ == "__main__":
    mcp.run()
