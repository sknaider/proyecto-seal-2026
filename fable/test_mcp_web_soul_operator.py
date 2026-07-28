from __future__ import annotations

import pytest

from mcp_web_soul_control import BrowserControlPlane, ControlDenied
from mcp_web_soul_operator import OperatorCommand, apply_command, parse_command


def test_operator_command_parser_is_exact():
    approval = "123e4567-e89b-42d3-a456-426614174000"
    assert parse_command(f"OK BROWSER APPROVE {approval}") == OperatorCommand("approve", approval)
    assert parse_command(f"OK GITHUB APPROVE {approval}") == OperatorCommand("approve", approval)
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
