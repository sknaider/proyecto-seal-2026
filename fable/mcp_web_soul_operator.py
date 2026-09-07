#!/usr/bin/env python3
"""Operador autenticado de approvals/takeover para ``mcp-web-soul``.

Solo consume comandos exactos escritos por William en ``web_chat`` o en
``dm:ada:william``. El agente solicita; esta ruta externa decide y muta el control
plane. No lee DMs de otros agentes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

import asyncpg

from mcp_web_soul_control import BrowserControlPlane, ControlDenied
from mcp_web_soul_security import AuditTrail


ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = Path(
    os.environ.get("MCP_WEB_SOUL_OPERATOR_STATE_ROOT", ROOT / "var" / "mcp-web-soul")
).expanduser().resolve()
CURSOR_PATH = STATE_ROOT / "operator.cursor"
CONTROL_PATH = Path(
    os.environ.get("MCP_WEB_SOUL_CONTROL_PATH", ROOT / "var" / "mcp-web-soul" / "control.sqlite3")
).expanduser().resolve()
CONTROL_GROUP = os.environ.get("MCP_WEB_SOUL_CONTROL_GROUP", "").strip() or None
AUDIT = AuditTrail(
    STATE_ROOT / "audit" / "operator.jsonl",
    agent="OPERATOR",
    witness_socket="/run/seal-audit-witness/witness.sock",
    witness_required=True,
)
COMMAND_RE = re.compile(
    r"^\s*OK\s+BROWSER\s+(?P<action>APPROVE|TAKEOVER|RELEASE|RENEW)\s+"
    r"(?P<target>[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class OperatorCommand:
    action: str
    target: str


def parse_command(content: str) -> OperatorCommand | None:
    match = COMMAND_RE.match(str(content or ""))
    if not match:
        return None
    return OperatorCommand(match.group("action").lower(), match.group("target").lower())


def _runtime_dsn() -> str:
    direct = os.environ.get("MCP_WEB_SOUL_OPERATOR_DSN", "").strip()
    if direct:
        return direct
    credentials_directory = os.environ.get("CREDENTIALS_DIRECTORY", "").strip()
    if credentials_directory:
        credential = Path(credentials_directory) / "operator.dsn"
        try:
            dsn = credential.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            dsn = ""
        if dsn:
            return dsn
    raise RuntimeError("DSN del operador no disponible en systemd credential")


def _read_cursor() -> int | None:
    try:
        return int(CURSOR_PATH.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        return None
    except ValueError as exc:
        raise RuntimeError("operator cursor is corrupt") from exc


def _write_cursor(message_id: int) -> None:
    value = int(message_id)
    if value < 0:
        raise ValueError("operator cursor cannot be negative")
    parent = CURSOR_PATH.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    # Never reuse a predictable .tmp name.  O_EXCL prevents an attacker or a
    # crashed previous writer from redirecting/truncating the candidate, while
    # O_NOFOLLOW makes the no-symlink contract explicit at the syscall boundary.
    temp: Path | None = None
    fd: int | None = None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for _attempt in range(8):
        candidate = parent / f".{CURSOR_PATH.name}.{secrets.token_hex(16)}.tmp"
        try:
            fd = os.open(candidate, flags, 0o600)
            temp = candidate
            break
        except FileExistsError:
            continue
    if fd is None or temp is None:
        raise RuntimeError("cannot allocate private operator cursor candidate")

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            fd = None
            os.fchmod(handle.fileno(), 0o600)
            handle.write(f"{value}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, CURSOR_PATH)
        temp = None
        directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if fd is not None:
            os.close(fd)
        if temp is not None:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass


def _cursor_required() -> bool:
    return os.environ.get("MCP_WEB_SOUL_OPERATOR_REQUIRE_CURSOR", "0").strip() == "1"


def apply_command(control: BrowserControlPlane, command: OperatorCommand, *, operator: str) -> None:
    if command.action == "approve":
        control.approve(command.target, operator=operator)
    elif command.action == "takeover":
        control.activate_takeover(command.target, operator=operator)
    elif command.action == "release":
        control.release_takeover(command.target, operator=operator)
    elif command.action == "renew":
        control.renew_takeover(command.target, operator=operator)
    else:  # pragma: no cover
        raise ValueError("acción desconocida")


FaultHook = Callable[[str], None]
CursorWriter = Callable[[int], None]


def _inject_fault(fault_hook: FaultHook | None, point: str) -> None:
    if fault_hook is not None:
        fault_hook(point)


def process_record(
    control: BrowserControlPlane,
    record: Mapping[str, Any],
    *,
    audit: AuditTrail = AUDIT,
    cursor_writer: CursorWriter = _write_cursor,
    fault_hook: FaultHook | None = None,
) -> dict[str, int]:
    """Process one immutable chat row with a durable exactly-once effect journal.

    SQLite owns the control mutation and the effect outbox in one
    ``BEGIN IMMEDIATE`` transaction.  The external witness is deliberately
    outside that transaction: if delivery fails, ``status='effect'`` remains
    pending and a retry re-emits completion without applying the mutation.  The
    control effect is exactly-once; witness delivery is intentionally
    at-least-once because a crash after witness append and before local
    completion can repeat the same idempotent audit record.
    """

    row = dict(record)
    message_id = int(row["message_id"])
    command = parse_command(str(row.get("content") or ""))
    operator = str(row.get("authenticated_operator") or "").strip()
    if command is None or not operator:
        audit.append(
            tool="operator.reject",
            arguments={"message_id": message_id, "target": ""},
            ok=False,
            action_class="OPERATOR",
            error="invalid_command_or_operator",
            required=True,
        )
        _inject_fault(fault_hook, "before_cursor")
        cursor_writer(message_id)
        _inject_fault(fault_hook, "after_cursor")
        return {"applied": 0, "rejected": 1, "replayed": 0, "cursor": message_id}

    _inject_fault(fault_hook, "before_intent")
    operation = control.reserve_operator_operation(
        message_id=message_id,
        action=command.action,
        target=command.target,
        operator=operator,
    )
    if operation["status"] == "intent":
        audit.append(
            tool=f"operator.{command.action}.intent",
            arguments={"message_id": message_id, "target": command.target},
            ok=True,
            action_class="OPERATOR_INTENT",
            required=True,
        )
    _inject_fault(fault_hook, "after_intent")

    _inject_fault(fault_hook, "before_effect")
    effect = control.apply_operator_operation(
        message_id=message_id,
        action=command.action,
        target=command.target,
        operator=operator,
    )
    _inject_fault(fault_hook, "after_effect")

    if effect["status"] != "completion":
        _inject_fault(fault_hook, "before_completion")
        audit.append(
            tool=f"operator.{command.action}",
            arguments={"message_id": message_id, "target": command.target},
            ok=bool(effect["ok"]),
            action_class="OPERATOR",
            error=effect.get("error"),
            required=True,
        )
        control.complete_operator_operation(
            message_id=message_id,
            action=command.action,
            target=command.target,
            operator=operator,
        )
        _inject_fault(fault_hook, "after_completion")

    _inject_fault(fault_hook, "before_cursor")
    cursor_writer(message_id)
    _inject_fault(fault_hook, "after_cursor")
    return {
        "applied": int(bool(effect["ok"]) and bool(effect["new_effect"])),
        "rejected": int(not bool(effect["ok"])),
        "replayed": int(not bool(effect["new_effect"])),
        "cursor": message_id,
    }


async def run_once(*, initialize_only: bool = False) -> dict[str, int]:
    control = BrowserControlPlane(CONTROL_PATH, shared_group=CONTROL_GROUP)
    conn = await asyncpg.connect(_runtime_dsn())
    try:
        cursor = _read_cursor()
        if cursor is None:
            if _cursor_required():
                raise RuntimeError("operator cursor is required in productive mode")
            cursor = int(await conn.fetchval("SELECT soul_v3.mcp_web_soul_operator_watermark()"))
            _write_cursor(cursor)
            return {"seen": 0, "applied": 0, "rejected": 0, "cursor": cursor}
        if initialize_only:
            return {"seen": 0, "applied": 0, "rejected": 0, "cursor": cursor}
        rows = await conn.fetch("SELECT * FROM soul_v3.mcp_web_soul_operator_commands($1)", cursor)
        stats = {"seen": len(rows), "applied": 0, "rejected": 0, "replayed": 0, "cursor": cursor}
        for record in rows:
            result = process_record(control, dict(record))
            stats["applied"] += result["applied"]
            stats["rejected"] += result["rejected"]
            stats["replayed"] += result["replayed"]
            stats["cursor"] = result["cursor"]
        return stats
    finally:
        await conn.close()


async def serve(interval: float = 1.0) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    await run_once(initialize_only=True)
    while not stop.is_set():
        try:
            await run_once()
        except Exception as exc:
            AUDIT.append(
                tool="operator.poll",
                arguments={},
                ok=False,
                action_class="OPERATOR",
                error=f"{type(exc).__name__}:{exc}",
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.2, interval))
        except TimeoutError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(run_once()) if args.once else asyncio.run(serve())
    if isinstance(result, dict):
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
