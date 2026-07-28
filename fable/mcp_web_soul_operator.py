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
import signal
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from mcp_web_soul_control import BrowserControlPlane, ControlDenied
from mcp_web_soul_security import AuditTrail


ROOT = Path(__file__).resolve().parents[1]
STATE_ROOT = ROOT / "var" / "mcp-web-soul"
CURSOR_PATH = STATE_ROOT / "operator.cursor"
CONTROL_PATH = STATE_ROOT / "control.sqlite3"
AUDIT = AuditTrail(STATE_ROOT / "audit" / "operator.jsonl", agent="OPERATOR")
COMMAND_RE = re.compile(
    r"^\s*OK\s+(?:BROWSER|GITHUB)\s+(?P<action>APPROVE|TAKEOVER|RELEASE|RENEW)\s+"
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
    env_path = Path.home() / ".config" / "seal" / "mcp_web_soul_operator.env"
    values: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        if "=" not in raw or raw.lstrip().startswith("#"):
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    dsn = values.get("MCP_WEB_SOUL_OPERATOR_DSN")
    if not dsn:
        raise RuntimeError("DSN del operador no disponible")
    return dsn


def _read_cursor() -> int | None:
    try:
        return int(CURSOR_PATH.read_text(encoding="utf-8").strip())
    except (FileNotFoundError, ValueError):
        return None


def _write_cursor(message_id: int) -> None:
    CURSOR_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = CURSOR_PATH.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"{int(message_id)}\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, CURSOR_PATH)
    os.chmod(CURSOR_PATH, 0o600)


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


async def run_once(*, initialize_only: bool = False) -> dict[str, int]:
    control = BrowserControlPlane(CONTROL_PATH)
    conn = await asyncpg.connect(_runtime_dsn())
    try:
        cursor = _read_cursor()
        if cursor is None:
            cursor = int(await conn.fetchval("SELECT soul_v3.mcp_web_soul_operator_watermark()"))
            _write_cursor(cursor)
            return {"seen": 0, "applied": 0, "rejected": 0, "cursor": cursor}
        if initialize_only:
            return {"seen": 0, "applied": 0, "rejected": 0, "cursor": cursor}
        rows = await conn.fetch("SELECT * FROM soul_v3.mcp_web_soul_operator_commands($1)", cursor)
        stats = {"seen": len(rows), "applied": 0, "rejected": 0, "cursor": cursor}
        for record in rows:
            row = dict(record)
            message_id = int(row["message_id"])
            command = parse_command(row["content"])
            operator = str(row.get("authenticated_operator") or "") or None
            ok = False
            error = None
            if command and operator:
                try:
                    apply_command(control, command, operator=operator)
                    ok = True
                    stats["applied"] += 1
                except (ControlDenied, ValueError) as exc:
                    error = f"{type(exc).__name__}:{exc}"
            if not ok:
                stats["rejected"] += 1
                error = error or "invalid_command_or_operator"
            AUDIT.append(
                tool=f"operator.{command.action if command else 'reject'}",
                arguments={"message_id": message_id, "target": command.target if command else ""},
                ok=ok,
                action_class="OPERATOR",
                error=error,
                required=True,
            )
            _write_cursor(message_id)
            stats["cursor"] = message_id
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
