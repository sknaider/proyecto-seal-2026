#!/usr/bin/env python3
"""SOUL ToolBroker — observe-first capability broker.

Fase 1 starts in observe mode: compute the policy decision, write a canonical
audit row, but do not block production. Enforcement is a later mode flip after
the registry is seeded from real traffic.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from db import DB_URL

ALLOW = "allow"
DENY = "deny"
CONFIRM_REQUIRED = "confirm_required"
OBSERVE = "observe"
ERROR = "error"

READ = "read"
COMPUTE = "compute"
WRITE = "write"
EXEC = "exec"
COMM = "comm"
DESTRUCTIVE = "destructive"

VALID_MODES = {"observe", "migrate", "enforce"}
DEFAULT_MODE = "observe"
_DESTRUCTIVE_RE = re.compile(
    r"\b(rm\s+-|rm\s+.*\s-r|delete|drop|truncate|destroy|wipe|purge|"
    r"borrar|eliminar|format|mkfs|reset\s+--hard)\b",
    re.IGNORECASE,
)

_WRITE_RE = re.compile(
    r"\b(write|edit|patch|apply_patch|insert|update|create|save|store|emit|send|post|"
    r"restart|start|stop|systemctl|deploy|upload)\b",
    re.IGNORECASE,
)

_READ_RE = re.compile(
    r"\b(read|list|search|find|get|fetch|status|health|query|select|open|cat|tail|head)\b",
    re.IGNORECASE,
)

_HARD_DISABLED_TOOLS: dict[str, str] = {}


@dataclass(frozen=True)
class BrokerDecision:
    allow: bool
    needs_confirmation: bool
    decision: str
    mode: str
    agent: str
    tool: str
    tool_class: str
    capability: str
    resource: str
    reason: str
    rule_id: str
    audit_id: int | None = None
    would_allow: bool | None = None
    would_decision: str | None = None


_SENSITIVE_KEYS = {
    "token",
    "session_token",
    "authorization",
    "api_key",
    "password",
    "secret",
}


def redact_args(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_args(item)
        return redacted
    if isinstance(value, list):
        return [redact_args(item) for item in value]
    return value


def normalize_mode(mode: str | None = None) -> str:
    value = (mode or os.environ.get("SEAL_TOOL_BROKER_MODE") or DEFAULT_MODE).strip().lower()
    return value if value in VALID_MODES else DEFAULT_MODE


def stable_hash(value: Any) -> str:
    encoded = json.dumps(redact_args(value), sort_keys=True, default=str, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def infer_tool_class(tool: str, args: dict[str, Any] | None = None) -> str:
    args = args or {}
    haystack = f"{tool} {args.get('command', '')} {args.get('description', '')} {args.get('action', '')}"
    lower = tool.lower()
    if "memory_invalidate" in lower or "memory_delete" in lower:
        return DESTRUCTIVE
    if "memory_store" in lower or "memory_write" in lower:
        return WRITE
    if "webchat_send" in lower or "agents/send" in lower:
        return COMM
    if _DESTRUCTIVE_RE.search(haystack):
        return DESTRUCTIVE
    if lower == "bash" or "systemctl" in haystack.lower():
        return EXEC
    if "send" in lower or "webchat" in lower or "message" in lower or "post" in lower:
        return COMM
    if _WRITE_RE.search(haystack):
        return WRITE
    if _READ_RE.search(haystack):
        return READ
    return COMPUTE


def infer_capability(tool: str, tool_class: str) -> str:
    lower = tool.lower()
    if lower == "bash" or tool_class == EXEC:
        return "shell"
    if "file" in lower or lower in {"read", "write", "edit"}:
        return "filesystem"
    if "memory" in lower:
        return "memory"
    if "web_search" in lower:
        return "network"
    if "webchat" in lower or "send" in lower or tool_class == COMM:
        return "communication"
    if "browser" in lower or "http" in lower or "fetch" in lower:
        return "network"
    return lower.replace("mcp__seal-memory__", "").replace("__", ".")


def resource_for(tool: str, args: dict[str, Any] | None = None) -> str:
    args = args or {}
    if tool == "Bash":
        return str(args.get("command", ""))[:180] or "bash"
    for key in ("path", "file_path", "url", "channel", "resource", "tool"):
        if args.get(key):
            return str(args[key])[:180]
    return tool


def validate_scope_constraints(
    tool: str,
    args: dict[str, Any],
    constraints: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Apply capability constraints which are security boundaries, not metadata.

    ``allowed_roots`` is evaluated after strict realpath resolution so ``..`` and
    symlink escapes cannot turn an allowed repository root into arbitrary host
    filesystem access.
    """
    constraints = constraints or {}
    roots = constraints.get("allowed_roots")
    if not roots:
        return True, "no path constraint"
    if not isinstance(roots, list) or not all(isinstance(root, str) and root for root in roots):
        return False, f"{tool}: invalid allowed_roots constraint"

    raw_path = args.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip() or "\x00" in raw_path:
        return False, f"{tool}: path required by allowed_roots"
    try:
        candidate = Path(raw_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return False, f"{tool}: path does not resolve to an existing target"

    for raw_root in roots:
        try:
            root = Path(raw_root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        try:
            candidate.relative_to(root)
            return True, f"{tool}: path is inside an allowed root"
        except ValueError:
            continue
    return False, f"{tool}: resolved path is outside allowed_roots"


def policy_decision(
    *,
    agent: str,
    tool: str,
    args: dict[str, Any] | None = None,
    taint: bool = False,
    scope_allowed: bool | None = None,
    scope_reason: str | None = None,
) -> BrokerDecision:
    args = args or {}
    mode = normalize_mode()
    tool_class = infer_tool_class(tool, args)
    capability = infer_capability(tool, tool_class)
    resource = resource_for(tool, args)

    hard_reason = _HARD_DISABLED_TOOLS.get(tool)
    if hard_reason:
        return BrokerDecision(
            allow=False,
            needs_confirmation=False,
            decision=DENY,
            mode=mode,
            agent=agent,
            tool=tool,
            tool_class=tool_class,
            capability=capability,
            resource=resource,
            reason=hard_reason,
            rule_id="HARD_DISABLED_UNSAFE_IMPLEMENTATION",
            would_allow=False,
            would_decision=DENY,
        )

    if taint and tool_class in {WRITE, EXEC, COMM, DESTRUCTIVE}:
        would_decision = CONFIRM_REQUIRED
        reason = f"taint session requires confirmation for {tool_class}"
        rule_id = "TAINT_CONFIRM"
    elif tool_class == DESTRUCTIVE:
        would_decision = CONFIRM_REQUIRED
        reason = "destructive class requires explicit confirmation"
        rule_id = "DESTRUCTIVE_CONFIRM"
    elif scope_allowed is False:
        would_decision = DENY
        reason = scope_reason or "capability denied by scope"
        rule_id = "CAPABILITY_DENY"
    elif scope_allowed is True:
        would_decision = ALLOW
        reason = scope_reason or "allowed by capability scope"
        rule_id = "CAPABILITY_ALLOW"
    else:
        would_decision = DENY
        reason = scope_reason or "no capability grant/scope found; deny by default"
        rule_id = "DENY_BY_DEFAULT"

    if mode == "observe":
        return BrokerDecision(
            allow=True,
            needs_confirmation=False,
            decision=OBSERVE,
            mode=mode,
            agent=agent,
            tool=tool,
            tool_class=tool_class,
            capability=capability,
            resource=resource,
            reason=f"observe only: would_decision={would_decision}; {reason}",
            rule_id=f"OBSERVE_{rule_id}",
            would_allow=would_decision == ALLOW,
            would_decision=would_decision,
        )

    if would_decision == ALLOW:
        return BrokerDecision(
            allow=True,
            needs_confirmation=False,
            decision=ALLOW,
            mode=mode,
            agent=agent,
            tool=tool,
            tool_class=tool_class,
            capability=capability,
            resource=resource,
            reason=reason,
            rule_id=rule_id,
            would_allow=True,
            would_decision=would_decision,
        )

    return BrokerDecision(
        allow=False,
        needs_confirmation=would_decision == CONFIRM_REQUIRED,
        decision=would_decision,
        mode=mode,
        agent=agent,
        tool=tool,
        tool_class=tool_class,
        capability=capability,
        resource=resource,
        reason=reason,
        rule_id=rule_id,
        would_allow=False,
        would_decision=would_decision,
    )


async def _scope_check(
    conn: Any,
    agent: str,
    capability: str,
    tool: str,
    args: dict[str, Any] | None = None,
) -> tuple[bool | None, str]:
    row = await conn.fetchrow(
        """
        SELECT allowed, constraints
        FROM soul_v3.capability_scope
        WHERE agent=$1 AND capability=$2
        """,
        agent,
        capability,
    )
    if row is not None:
        if not row["allowed"]:
            return False, f"{agent}/{capability} explicitly denied in capability_scope"
        constraints = row["constraints"] or {}
        if isinstance(constraints, str):
            constraints = json.loads(constraints)
        denied_ops = constraints.get("denied_ops", []) if isinstance(constraints, dict) else []
        if tool in denied_ops:
            return False, f"{agent}/{capability}/{tool} denied by capability_scope.denied_ops"
        constraints_ok, constraints_reason = validate_scope_constraints(tool, args or {}, constraints)
        if not constraints_ok:
            return False, constraints_reason
        return True, "allowed by capability_scope"

    grant = await conn.fetchrow(
        """
        SELECT id
        FROM soul_v3.capability_grants
        WHERE caller_id=$1
          AND active IS TRUE
          AND (expires_at IS NULL OR expires_at > NOW())
          AND (tool=$2 OR tool='*')
        LIMIT 1
        """,
        agent,
        tool,
    )
    if grant:
        return True, "allowed by active capability_grants"
    return None, "no capability_scope/capability_grants entry"


async def _record_audit(
    conn: Any,
    decision: BrokerDecision,
    args: dict[str, Any],
    session_id: str | None,
    audit_metadata: dict[str, Any] | None = None,
) -> int:
    redacted = redact_args(args)
    metadata = {
        "broker": "tool_broker",
        "mode": decision.mode,
        "tool_class": decision.tool_class,
        "capability": decision.capability,
        "rule_id": decision.rule_id,
        "would_allow": decision.would_allow,
        "would_decision": decision.would_decision,
        "claimed_agent": str(args.get("agent", ""))[:80] if args.get("agent") else None,
        "arg_keys": sorted(redacted.keys()),
    }
    if audit_metadata:
        metadata.update(redact_args(audit_metadata))
    row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.audit_log
            (actor, session_id, action, resource, params_hash, taint, decision, reason, metadata)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
        RETURNING id
        """,
        decision.agent,
        session_id,
        decision.tool,
        decision.resource,
        stable_hash(redacted),
        False,
        decision.decision,
        decision.reason,
        json.dumps(metadata),
    )
    return int(row["id"])


async def check(
    agent: str,
    session_id: str | None,
    tool: str,
    args: dict[str, Any] | None = None,
    *,
    taint: bool = False,
    conn: Any | None = None,
    audit: bool = True,
    audit_metadata: dict[str, Any] | None = None,
) -> BrokerDecision:
    """Check a tool call. In observe mode this never blocks production."""
    args = args or {}
    owns_conn = conn is None
    transaction = None
    if conn is None:
        import asyncpg
        try:
            conn = await asyncpg.connect(DB_URL)
        except Exception as exc:
            if normalize_mode() == "observe":
                return BrokerDecision(
                    allow=True,
                    needs_confirmation=False,
                    decision=OBSERVE,
                    mode="observe",
                    agent=agent,
                    tool=tool,
                    tool_class=infer_tool_class(tool, args),
                    capability=infer_capability(tool, infer_tool_class(tool, args)),
                    resource=resource_for(tool, args),
                    reason=f"observe fail-open: db connect failed: {exc}",
                    rule_id="OBSERVE_BROKER_DB_ERROR",
                    would_allow=None,
                    would_decision=ERROR,
                )
            raise
    try:
        if owns_conn:
            transaction = conn.transaction()
            await transaction.start()
            tenant_id = os.environ.get(
                "SEAL_INTERNAL_TENANT_ID",
                "00000000-0000-0000-0000-000000000000",
            )
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            await conn.execute("SELECT set_config('app.agent', $1, true)", agent)
        capability = infer_capability(tool, infer_tool_class(tool, args))
        scope_allowed, scope_reason = await _scope_check(conn, agent, capability, tool, args)
        decision = policy_decision(
            agent=agent,
            tool=tool,
            args=args,
            taint=taint,
            scope_allowed=scope_allowed,
            scope_reason=scope_reason,
        )
        if audit:
            try:
                audit_id = await _record_audit(conn, decision, args, session_id, audit_metadata)
                decision = BrokerDecision(**{**asdict(decision), "audit_id": audit_id})
            except Exception:
                if decision.mode != "observe":
                    raise
        if transaction is not None:
            await transaction.commit()
            transaction = None
        return decision
    except BaseException:
        if transaction is not None:
            await transaction.rollback()
            transaction = None
        raise
    finally:
        if owns_conn:
            await conn.close()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def _cli() -> int:
    parser = argparse.ArgumentParser(description="SOUL ToolBroker observe checker")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--tool", required=True)
    parser.add_argument("--session-id")
    parser.add_argument("--args-json", default="{}")
    parser.add_argument("--no-audit", action="store_true")
    ns = parser.parse_args()
    args = json.loads(ns.args_json)
    decision = await check(ns.agent, ns.session_id, ns.tool, args, audit=not ns.no_audit)
    print(json.dumps(asdict(decision), indent=2, default=_json_default))
    return 0 if decision.allow else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_cli()))
