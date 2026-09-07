from __future__ import annotations

import asyncio
import grp
import os
import stat
from typing import Any

import pytest

import mcp_web_soul_operator as operator_module
from mcp_web_soul_control import BrowserControlPlane, ControlDenied
from mcp_web_soul_operator import OperatorCommand, apply_command, parse_command, process_record


class RecordingAudit:
    def __init__(self, *, fail_completion_once: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_completion_once = fail_completion_once

    def append(self, **payload: Any) -> None:
        self.calls.append(dict(payload))
        if (
            self.fail_completion_once
            and payload["action_class"] == "OPERATOR"
        ):
            self.fail_completion_once = False
            raise RuntimeError("witness unavailable")


class FaultOnce:
    def __init__(self, point: str) -> None:
        self.point = point
        self.fired = False

    def __call__(self, point: str) -> None:
        if point == self.point and not self.fired:
            self.fired = True
            raise RuntimeError(f"fault:{point}")


def _pending_approval(control: BrowserControlPlane, *, pid: int = 50) -> str:
    session = control.ensure_session(agent="ADA", pid=pid)
    request = control.request_approval(
        session_id=session,
        tool="click",
        arguments={"selector": f"button[type=submit][data-test='{pid}']"},
    )
    return str(request["approval_id"])


def _record(message_id: int, approval_id: str) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "content": f"OK BROWSER APPROVE {approval_id}",
        "authenticated_operator": "William",
    }


def test_operator_command_parser_is_exact():
    approval = "123e4567-e89b-42d3-a456-426614174000"
    assert parse_command(f"OK BROWSER APPROVE {approval}") == OperatorCommand("approve", approval)
    # The SQL boundary only emits BROWSER commands; claiming GITHUB support in
    # the parser made that path unreachable and unauditable.
    assert parse_command(f"OK GITHUB APPROVE {approval}") is None
    assert parse_command(f"por favor OK BROWSER APPROVE {approval}") is None
    assert parse_command("OK BROWSER APPROVE nope") is None


def test_operator_path_can_approve_but_agent_path_cannot(tmp_path):
    control = BrowserControlPlane(tmp_path / "control.sqlite3")
    session = control.ensure_session(agent="ADA", pid=1)
    args = {"selector": "button[type=submit]"}
    request = control.request_approval(session_id=session, tool="click", arguments=args)

    with pytest.raises(ControlDenied):
        control.authorize(
            session_id=session,
            tool="click",
            arguments=args,
            approval_id=request["approval_id"],
        )

    apply_command(control, OperatorCommand("approve", request["approval_id"]), operator="William")
    permit = control.authorize(
        session_id=session,
        tool="click",
        arguments=args,
        approval_id=request["approval_id"],
    )
    assert permit.approval_id == request["approval_id"]


def test_operator_takeover_and_release(tmp_path):
    control = BrowserControlPlane(tmp_path / "control.sqlite3")
    session = control.ensure_session(agent="ADA", pid=2)
    control.request_takeover(session)
    apply_command(control, OperatorCommand("takeover", session), operator="William")
    assert control.session_status(session)["state"] == "human_active"
    apply_command(control, OperatorCommand("release", session), operator="William")
    assert control.session_status(session)["state"] == "ready"


def test_runtime_dsn_prefers_systemd_credential(monkeypatch, tmp_path):
    credential_dir = tmp_path / "credentials"
    credential_dir.mkdir()
    (credential_dir / "operator.dsn").write_text("postgresql://operator@example/db\n")
    monkeypatch.delenv("MCP_WEB_SOUL_OPERATOR_DSN", raising=False)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))

    assert operator_module._runtime_dsn() == "postgresql://operator@example/db"


def test_runtime_dsn_never_falls_back_to_shared_uid_legacy_file(monkeypatch, tmp_path):
    legacy = tmp_path / ".config" / "seal"
    legacy.mkdir(parents=True)
    (legacy / "mcp_web_soul_operator.env").write_text(
        "MCP_WEB_SOUL_OPERATOR_DSN=postgresql://legacy/unsafe\n"
    )
    monkeypatch.delenv("MCP_WEB_SOUL_OPERATOR_DSN", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    with pytest.raises(RuntimeError, match="systemd credential"):
        operator_module._runtime_dsn()


def test_cursor_missing_is_first_boot_but_malformed_is_fatal(monkeypatch, tmp_path):
    cursor = tmp_path / "operator.cursor"
    monkeypatch.setattr(operator_module, "CURSOR_PATH", cursor)
    assert operator_module._read_cursor() is None

    cursor.write_text("not-an-integer\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="cursor is corrupt"):
        operator_module._read_cursor()


def test_cursor_write_uses_unique_nofollow_candidate_and_fsyncs_directory(
    monkeypatch, tmp_path
):
    cursor = tmp_path / "state" / "operator.cursor"
    cursor.parent.mkdir(mode=0o700)
    victim = tmp_path / "victim"
    victim.write_text("SAFE\n", encoding="utf-8")
    collision = cursor.parent / ".operator.cursor.collision.tmp"
    collision.symlink_to(victim)
    tokens = iter(("collision", "fresh"))
    monkeypatch.setattr(operator_module, "CURSOR_PATH", cursor)
    monkeypatch.setattr(operator_module.secrets, "token_hex", lambda _n: next(tokens))

    fsync_kinds: list[int] = []
    real_fsync = os.fsync
    real_open = os.open
    open_flags: list[int] = []

    def recording_open(path, flags: int, *args):
        open_flags.append(flags)
        return real_open(path, flags, *args)

    def recording_fsync(fd: int) -> None:
        fsync_kinds.append(os.fstat(fd).st_mode)
        real_fsync(fd)

    monkeypatch.setattr(operator_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(operator_module.os, "open", recording_open)
    operator_module._write_cursor(771)

    assert victim.read_text(encoding="utf-8") == "SAFE\n"
    assert collision.is_symlink()
    assert cursor.read_text(encoding="utf-8") == "771\n"
    assert stat.S_IMODE(cursor.stat().st_mode) == 0o600
    assert any(stat.S_ISREG(mode) for mode in fsync_kinds)
    assert any(stat.S_ISDIR(mode) for mode in fsync_kinds)
    candidate_flags = open_flags[0]
    assert candidate_flags & os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        assert candidate_flags & os.O_NOFOLLOW
    assert not (cursor.parent / ".operator.cursor.fresh.tmp").exists()


def test_productive_operator_refuses_missing_cursor_before_watermark(monkeypatch, tmp_path):
    class Connection:
        def __init__(self) -> None:
            self.fetchval_called = False
            self.closed = False

        async def fetchval(self, *_args):
            self.fetchval_called = True
            return 999

        async def close(self):
            self.closed = True

    connection = Connection()

    async def connect(_dsn):
        return connection

    monkeypatch.setattr(operator_module, "CURSOR_PATH", tmp_path / "missing.cursor")
    monkeypatch.setattr(operator_module, "CONTROL_PATH", tmp_path / "control.sqlite3")
    monkeypatch.setenv("MCP_WEB_SOUL_OPERATOR_REQUIRE_CURSOR", "1")
    monkeypatch.setenv("MCP_WEB_SOUL_OPERATOR_DSN", "postgresql://operator/test")
    monkeypatch.setattr(operator_module.asyncpg, "connect", connect)

    with pytest.raises(RuntimeError, match="cursor is required in productive mode"):
        asyncio.run(operator_module.run_once())

    assert connection.fetchval_called is False
    assert connection.closed is True


def test_control_plane_shared_group_contract(tmp_path):
    group = grp.getgrgid(os.getgid()).gr_name
    control = BrowserControlPlane(tmp_path / "control.sqlite3", shared_group=group)

    assert control.db_path.stat().st_gid == os.getgid()
    assert control.key_path.stat().st_gid == os.getgid()
    assert control.db_path.stat().st_mode & 0o777 == 0o660
    assert control.key_path.stat().st_mode & 0o777 == 0o660


def test_operator_message_id_cannot_change_payload(tmp_path):
    control = BrowserControlPlane(tmp_path / "control.sqlite3")
    first = _pending_approval(control, pid=61)
    second = _pending_approval(control, pid=62)
    control.reserve_operator_operation(
        message_id=700,
        action="approve",
        target=first,
        operator="William",
    )

    with pytest.raises(ControlDenied) as caught:
        control.reserve_operator_operation(
            message_id=700,
            action="approve",
            target=second,
            operator="William",
        )

    assert caught.value.code == "operator_message_conflict"
    row = control.operator_operation(700)
    assert row is not None
    assert row["status"] == "intent"
    assert first in row["command_json"]
    assert second not in row["command_json"]


@pytest.mark.parametrize(
    ("fault_point", "status_after_fault", "cursor_after_fault"),
    [
        ("before_intent", None, []),
        ("after_intent", "intent", []),
        ("before_effect", "intent", []),
        ("after_effect", "effect", []),
        ("before_completion", "effect", []),
        ("after_completion", "completion", []),
        ("before_cursor", "completion", []),
        ("after_cursor", "completion", [808]),
    ],
)
def test_fault_cuts_apply_effect_exactly_once(
    tmp_path,
    monkeypatch,
    fault_point,
    status_after_fault,
    cursor_after_fault,
):
    control = BrowserControlPlane(tmp_path / f"{fault_point}.sqlite3")
    approval_id = _pending_approval(control)
    audit = RecordingAudit()
    cursor: list[int] = []
    mutation_calls = 0
    original = control._apply_operator_action_conn

    def counted_mutation(*args, **kwargs):
        nonlocal mutation_calls
        mutation_calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(control, "_apply_operator_action_conn", counted_mutation)
    with pytest.raises(RuntimeError, match=f"fault:{fault_point}"):
        process_record(
            control,
            _record(808, approval_id),
            audit=audit,
            cursor_writer=cursor.append,
            fault_hook=FaultOnce(fault_point),
        )

    operation = control.operator_operation(808)
    assert (operation["status"] if operation else None) == status_after_fault
    assert cursor == cursor_after_fault

    result = process_record(
        control,
        _record(808, approval_id),
        audit=audit,
        cursor_writer=cursor.append,
    )

    assert control.operator_operation(808)["status"] == "completion"
    assert control.approval_status(approval_id)["status"] == "approved"
    assert cursor[-1] == 808
    assert mutation_calls == 1
    if status_after_fault in {"effect", "completion"}:
        assert result["replayed"] == 1


def test_witness_completion_retry_uses_outbox_without_reapplying(tmp_path, monkeypatch):
    control = BrowserControlPlane(tmp_path / "control.sqlite3")
    approval_id = _pending_approval(control)
    audit = RecordingAudit(fail_completion_once=True)
    cursor: list[int] = []
    mutation_calls = 0
    original = control._apply_operator_action_conn

    def counted_mutation(*args, **kwargs):
        nonlocal mutation_calls
        mutation_calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(control, "_apply_operator_action_conn", counted_mutation)
    with pytest.raises(RuntimeError, match="witness unavailable"):
        process_record(
            control,
            _record(909, approval_id),
            audit=audit,
            cursor_writer=cursor.append,
        )

    pending = control.operator_operation(909)
    assert pending["status"] == "effect"
    assert cursor == []
    assert mutation_calls == 1

    replay = process_record(
        control,
        _record(909, approval_id),
        audit=audit,
        cursor_writer=cursor.append,
    )

    assert replay == {"applied": 0, "rejected": 0, "replayed": 1, "cursor": 909}
    assert control.operator_operation(909)["status"] == "completion"
    assert cursor == [909]
    assert mutation_calls == 1
    completion_attempts = [
        call for call in audit.calls if call["action_class"] == "OPERATOR"
    ]
    assert len(completion_attempts) == 2
