"""Central guardrail for destructive SEAL operations.

This module does not execute destructive actions. It validates that a caller
has already produced the mandatory preview evidence before execution:

- exact affected COUNT
- explicit scope
- explicit William confirmation

Use it from scripts, daemons, or manual runbooks before DELETE/DROP/TRUNCATE,
file removal, service stop/restart/kill, or memory invalidation batches.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


AUDIT_LOG = Path("/tmp/seal_destructive_guard_audit.jsonl")


class DestructiveKind(str, Enum):
    SQL_DELETE = "sql_delete"
    SQL_DROP = "sql_drop"
    SQL_TRUNCATE = "sql_truncate"
    FILE_REMOVE = "file_remove"
    PROCESS_KILL = "process_kill"
    SERVICE_CONTROL = "service_control"
    MEMORY_INVALIDATION = "memory_invalidation"
    OTHER = "other"


@dataclass(frozen=True)
class DestructiveRequest:
    agent: str
    kind: DestructiveKind | str
    target: str
    scope: str
    affected_count: int | None
    preview: str = ""
    confirmed_by: str | None = None
    confirmation_text: str | None = None


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str
    operation_id: str
    required_confirmation: str
    evidence: dict[str, Any]


def operation_id(req: DestructiveRequest) -> str:
    """Stable short id for the operation evidence William is confirming."""
    payload = {
        "agent": req.agent.strip().upper(),
        "kind": str(req.kind),
        "target": req.target.strip(),
        "scope": req.scope.strip(),
        "affected_count": req.affected_count,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:12]


def required_confirmation(req: DestructiveRequest) -> str:
    return f"OK {req.agent.strip().upper()} {operation_id(req)}"


def evaluate(req: DestructiveRequest, *, audit: bool = True) -> GuardDecision:
    """Return ALLOW only when COUNT, scope and William confirmation are exact."""
    op_id = operation_id(req)
    required = required_confirmation(req)

    if not req.agent.strip():
        decision = _deny(req, op_id, required, "agent required")
    elif not req.target.strip():
        decision = _deny(req, op_id, required, "target required")
    elif not req.scope.strip():
        decision = _deny(req, op_id, required, "explicit scope required")
    elif req.affected_count is None:
        decision = _deny(req, op_id, required, "exact affected_count required")
    elif not isinstance(req.affected_count, int) or req.affected_count < 0:
        decision = _deny(req, op_id, required, "affected_count must be a non-negative integer")
    elif (req.confirmed_by or "").strip().lower() != "william":
        decision = _deny(req, op_id, required, "William confirmation required")
    elif (req.confirmation_text or "").strip() != required:
        decision = _deny(req, op_id, required, f"confirmation text must equal: {required}")
    else:
        decision = GuardDecision(
            allowed=True,
            reason="confirmed by William with exact count and scope",
            operation_id=op_id,
            required_confirmation=required,
            evidence=_evidence(req),
        )

    if audit:
        _audit(req, decision)
    return decision


def require_approval(req: DestructiveRequest) -> None:
    """Raise PermissionError unless evaluate(req) allows execution."""
    decision = evaluate(req)
    if not decision.allowed:
        raise PermissionError(decision.reason)


def classify_command(command: str) -> DestructiveKind | None:
    """Best-effort classifier for common destructive shell/SQL commands."""
    c = command.strip()
    checks: list[tuple[DestructiveKind, str]] = [
        (DestructiveKind.SQL_DROP, r"\bDROP\s+(DATABASE|TABLE|SCHEMA)\b"),
        (DestructiveKind.SQL_TRUNCATE, r"\bTRUNCATE\s+(TABLE\s+)?\S+"),
        (DestructiveKind.SQL_DELETE, r"\bDELETE\s+FROM\b"),
        (DestructiveKind.FILE_REMOVE, r"(^|[;&|]\s*)rm\s+(-[A-Za-z]*[rf][A-Za-z]*\s+)+"),
        (DestructiveKind.PROCESS_KILL, r"\b(pkill|killall|kill\s+-9)\b"),
        (DestructiveKind.SERVICE_CONTROL, r"\bsystemctl\b.*\b(stop|restart|kill)\b"),
    ]
    for kind, pattern in checks:
        if re.search(pattern, c, re.IGNORECASE):
            return kind
    return None


def sql_count_query(sql: str) -> str | None:
    """Convert simple DELETE/TRUNCATE statements to preview COUNT queries."""
    text = sql.strip().rstrip(";")
    delete = re.match(r"DELETE\s+FROM\s+([A-Za-z_][\w.]*)(\s+WHERE\s+.+)?$", text, re.IGNORECASE | re.DOTALL)
    if delete:
        table = delete.group(1)
        where = (delete.group(2) or "").strip()
        return f"SELECT COUNT(*) FROM {table} {where}".strip()

    truncate = re.match(r"TRUNCATE\s+(?:TABLE\s+)?([A-Za-z_][\w.]*)$", text, re.IGNORECASE)
    if truncate:
        return f"SELECT COUNT(*) FROM {truncate.group(1)}"

    return None


def _deny(req: DestructiveRequest, op_id: str, required: str, reason: str) -> GuardDecision:
    return GuardDecision(
        allowed=False,
        reason=reason,
        operation_id=op_id,
        required_confirmation=required,
        evidence=_evidence(req),
    )


def _evidence(req: DestructiveRequest) -> dict[str, Any]:
    data = asdict(req)
    data["kind"] = str(req.kind)
    return data


def _audit(req: DestructiveRequest, decision: GuardDecision) -> None:
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "request": _evidence(req),
        "decision": asdict(decision),
    }
    try:
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except OSError:
        # Guard decision must not depend on audit-file availability.
        pass


def _run_tests() -> None:
    base = DestructiveRequest(
        agent="ADA",
        kind=DestructiveKind.SQL_DELETE,
        target="soul_v3.memories",
        scope="agent='TEST' AND invalid_at IS NULL",
        affected_count=3,
        preview="SELECT COUNT(*) FROM soul_v3.memories WHERE agent='TEST' AND invalid_at IS NULL",
    )

    denied = evaluate(base, audit=False)
    assert not denied.allowed
    assert "William confirmation required" in denied.reason

    wrong_text = evaluate(
        DestructiveRequest(**{**asdict(base), "confirmed_by": "William", "confirmation_text": "OK ADA wrong"}),
        audit=False,
    )
    assert not wrong_text.allowed

    ok = evaluate(
        DestructiveRequest(
            **{
                **asdict(base),
                "confirmed_by": "William",
                "confirmation_text": required_confirmation(base),
            }
        ),
        audit=False,
    )
    assert ok.allowed

    assert classify_command("psql -c 'DELETE FROM memories WHERE agent = ''TEST'''") == DestructiveKind.SQL_DELETE
    assert classify_command("rm -rf /tmp/seal-test") == DestructiveKind.FILE_REMOVE
    assert classify_command("systemctl --user restart seal-mcp-server.service") == DestructiveKind.SERVICE_CONTROL
    assert sql_count_query("DELETE FROM soul_v3.memories WHERE agent = 'TEST'") == (
        "SELECT COUNT(*) FROM soul_v3.memories WHERE agent = 'TEST'"
    )
    assert sql_count_query("TRUNCATE TABLE soul_v3.tmp") == "SELECT COUNT(*) FROM soul_v3.tmp"
    print("destructive_guard tests passed")


if __name__ == "__main__":
    _run_tests()
