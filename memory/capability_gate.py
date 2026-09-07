"""Capability Gate — security checkpoint before high-risk agent operations.

Checks soul_v3.capability_scope before allowing execution.
High-risk ops (shell code_edit, file_write_prod, network, browser, camera)
send a confirmation request via webchat and record the attempt in capability_audit.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Any

import asyncpg

from soul_event_recorders import record_denial
from seal_secrets import pg_dsn

# Cura seguridad (NEXUS 13-jun, audit FABLE M13): god-cred NO hardcoded en código (principio G3
# — el sandbox probó que el código se lee). Igual que tool_broker.py: del env/secret store.
_DB_URL = os.environ.get("SEAL_DB_URL") or pg_dsn(required=True)
_WEBCHAT_URL = "http://localhost:8765/api/agents/send"

HIGH_RISK_OPS = {"code_edit", "file_write_prod", "network", "browser", "camera"}


async def _get_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(_DB_URL, min_size=1, max_size=3)


async def check_capability(
    agent: str,
    capability: str,
    operation: str | None = None,
    context: str = "",
) -> dict[str, Any]:
    """Check if agent is allowed to use a capability/operation.

    Returns:
        {"allowed": bool, "reason": str, "requires_confirmation": bool}
    """
    conn = await asyncpg.connect(_DB_URL)
    try:
        row = await conn.fetchrow(
            "SELECT allowed, constraints FROM soul_v3.capability_scope "
            "WHERE agent=$1 AND capability=$2",
            agent,
            capability,
        )

        if row is None:
            return {
                "allowed": False,
                "reason": f"No capability_scope entry for {agent}/{capability} — deny by default",
                "requires_confirmation": False,
            }

        if not row["allowed"]:
            return {
                "allowed": False,
                "reason": f"{agent}/{capability} explicitly denied in capability_scope",
                "requires_confirmation": False,
            }

        raw = row["constraints"] or {}
        constraints = json.loads(raw) if isinstance(raw, str) else raw
        denied_ops = constraints.get("denied_ops", [])

        if operation and operation in denied_ops:
            return {
                "allowed": False,
                "reason": f"{agent}/{capability}/{operation} is in denied_ops: {denied_ops}",
                "requires_confirmation": False,
            }

        # High-risk ops require confirmation
        requires_confirmation = operation in HIGH_RISK_OPS if operation else False

        return {
            "allowed": True,
            "reason": "allowed by capability_scope",
            "requires_confirmation": requires_confirmation,
            "constraints": constraints,
        }
    finally:
        await conn.close()


async def request_confirmation(
    agent: str,
    capability: str,
    operation: str,
    context: str,
) -> None:
    """Send confirmation request to William via webchat for high-risk operations."""
    msg = {
        "from": "NEXUS",
        "to": "William",
        "type": "conversation",
        "channel": "web_chat",
        "message": (
            f"SECURITY GATE — confirmación requerida:\n"
            f"Agente: {agent}\n"
            f"Capacidad: {capability}/{operation}\n"
            f"Contexto: {context}\n"
            f"¿Autorizar? Responde 'sí autorizo {agent} {operation}' para confirmar."
        ),
    }
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _WEBCHAT_URL,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        # FAIL-LOUD (cura NEXUS 13-jun, audit FABLE M13): NO tragar el error en silencio.
        # Si el prompt de confirmacion no llega al webchat, debe quedar VISIBLE en el log
        # (el humano nunca veria el pedido de autorizacion = degradacion silenciosa).
        print(f"[capability_gate] request_confirmation POST fallo: {e}", file=sys.stderr)


async def audit_capability_use(
    agent: str,
    capability: str,
    operation: str | None,
    action: str,
    authorized_by: str,
    change_reason: str,
    old_value: dict | None = None,
    new_value: dict | None = None,
) -> int:
    """Record capability use/change in capability_audit. Returns audit id."""
    conn = await asyncpg.connect(_DB_URL)
    try:
        row = await conn.fetchrow(
            """
            INSERT INTO soul_v3.capability_audit
                (agent, capability, action, old_value, new_value, authorized_by, change_reason)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            agent,
            capability,
            action,
            json.dumps(old_value) if old_value else None,
            json.dumps(new_value or {"operation": operation}),
            authorized_by,
            change_reason,
        )
        return row["id"]
    finally:
        await conn.close()


async def gate(
    agent: str,
    capability: str,
    operation: str | None = None,
    context: str = "",
    authorized_by: str = "system",
) -> dict[str, Any]:
    """Full gate check: verify scope, send confirmation if high-risk, audit the attempt.

    Usage:
        result = await gate("JARVIS", "shell", "query", context="SELECT COUNT(*) FROM memories")
        if not result["allowed"]:
            raise PermissionError(result["reason"])
    """
    result = await check_capability(agent, capability, operation, context)

    # Audit every gate check
    await audit_capability_use(
        agent=agent,
        capability=capability,
        operation=operation,
        action="grant" if result["allowed"] else "deny",
        authorized_by=authorized_by,
        change_reason=f"gate check: {context[:100]}",
        new_value={"allowed": result["allowed"], "operation": operation, "context": context[:200]},
    )

    if not result["allowed"]:
        conn = await asyncpg.connect(_DB_URL)
        try:
            await record_denial(
                conn,
                agent=agent,
                denied_action=f"{capability}/{operation or capability}",
                denial_reason=result.get("reason", ""),
                source="capability_gate",
                context={"context": context[:200], "capability": capability, "operation": operation},
            )
        finally:
            await conn.close()

    if result["allowed"] and result.get("requires_confirmation"):
        await request_confirmation(agent, capability, operation or capability, context)

    return result
