from __future__ import annotations

import concurrent.futures

import pytest

from mcp_web_soul_control import (
    ACCOUNT,
    DESTRUCTIVE,
    FINANCIAL,
    REMOTE_COMMIT,
    BrowserControlPlane,
    ControlDenied,
    classify_action,
    discover_orphan_browsers,
)


@pytest.fixture()
def control(tmp_path):
    return BrowserControlPlane(tmp_path / "control.sqlite3")


def test_server_side_classification_catches_sensitive_clicks():
    assert classify_action("click", {"selector": "button.checkout"}) == FINANCIAL
    assert classify_action("click", {"selector": "#delete-account"}) == DESTRUCTIVE
    assert classify_action("click", {"selector": "button[type=submit]"}) == REMOTE_COMMIT
    assert classify_action("type_text", {"selector": "#password", "value": "x"}) == ACCOUNT


def test_sensitive_action_fails_closed_without_approval(control):
    session = control.ensure_session(agent="ADA", pid=101)
    with pytest.raises(ControlDenied, match="approval requerido") as exc:
        control.authorize(session_id=session, tool="set_cookie", arguments={"name": "sid", "value": "secret", "url": "https://x"})
    assert exc.value.code == "approval_required"


def test_approval_is_exact_bound_and_single_use(control):
    session = control.ensure_session(agent="ADA", pid=102)
    args = {"selector": "button[type=submit]"}
    approval = control.request_approval(session_id=session, tool="click", arguments=args)
    control.approve(approval["approval_id"], operator="William")

    with pytest.raises(ControlDenied) as mismatch:
        control.authorize(
            session_id=session,
            tool="click",
            arguments={"selector": "button.delete"},
            approval_id=approval["approval_id"],
        )
    assert mismatch.value.code == "approval_mismatch"

    permit = control.authorize(
        session_id=session,
        tool="click",
        arguments=args,
        approval_id=approval["approval_id"],
    )
    control.finish(permit, ok=True)
    assert control.approval_status(approval["approval_id"])["status"] == "executed"
    with pytest.raises(ControlDenied) as replay:
        control.authorize(
            session_id=session,
            tool="click",
            arguments=args,
            approval_id=approval["approval_id"],
        )
    assert replay.value.code == "approval_state"


def test_concurrent_replay_has_exactly_one_winner(control):
    session = control.ensure_session(agent="ADA", pid=103)
    args = {"selector": "#confirm-submit"}
    approval = control.request_approval(session_id=session, tool="click", arguments=args)
    control.approve(approval["approval_id"], operator="William")

    def attempt():
        try:
            return control.authorize(
                session_id=session,
                tool="click",
                arguments=args,
                approval_id=approval["approval_id"],
            )
        except ControlDenied:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(result is not None for result in results) == 1


def test_takeover_blocks_agent_until_external_release(control):
    session = control.ensure_session(agent="ADA", pid=104)
    control.request_takeover(session)
    with pytest.raises(ControlDenied) as locked:
        control.authorize(session_id=session, tool="browse", arguments={"url": "https://example.test"})
    assert locked.value.code == "takeover_locked"

    control.activate_takeover(session, operator="William")
    with pytest.raises(ControlDenied) as human_active:
        control.authorize(session_id=session, tool="click", arguments={"selector": "#safe"})
    assert human_active.value.code == "takeover_locked"

    status_permit = control.authorize(
        session_id=session,
        tool="browser_takeover",
        arguments={"operation": "status"},
    )
    assert status_permit.action_class == "READ"

    control.release_takeover(session, operator="William")
    permit = control.authorize(session_id=session, tool="browse", arguments={"url": "https://example.test"})
    assert permit.approval_id is None


def test_orphan_scan_distinguishes_supervised_browser(tmp_path):
    def process(pid, parent, args, *, name="test"):
        proc = tmp_path / str(pid)
        proc.mkdir()
        (proc / "cmdline").write_bytes(b"\0".join(arg.encode() for arg in args) + b"\0")
        (proc / "status").write_text(f"Name:\t{name}\nPPid:\t{parent}\n", encoding="utf-8")

    process(1, 0, ["/sbin/init"])
    process(50, 1, ["python3", "/repo/fable/seal_cdp_mcp.py"])
    process(100, 50, ["brave-browser", "--remote-debugging-port=9222", "--user-data-dir=/tmp/managed"])
    process(200, 1, ["brave-browser", "--remote-debugging-port=9333", "--user-data-dir=/tmp/orphan"])

    assert discover_orphan_browsers(tmp_path) == [
        {"pid": 200, "port": 9333, "profile": "/tmp/orphan"}
    ]


def test_orphan_scan_detects_browser_child_after_wrapper_dies(tmp_path):
    profile = tmp_path / "var" / "mcp-web-soul" / "runtime-profiles" / "ALICE" / "profile-dead"

    init = tmp_path / "1"
    init.mkdir()
    (init / "cmdline").write_bytes(b"/sbin/init\0")
    (init / "status").write_text("Name:\tinit\nPPid:\t0\n", encoding="utf-8")

    proc = tmp_path / "300"
    (proc / "fd").mkdir(parents=True)
    (proc / "net").mkdir()
    (proc / "cmdline").write_bytes(str(profile).encode() + b"\0--no-first-run\0")
    (proc / "status").write_text("Name:\tbrave\nPPid:\t1\n", encoding="utf-8")
    (proc / "fd" / "87").symlink_to("socket:[4567]")
    (proc / "net" / "tcp").write_text(
        "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"
        "   0: 0100007F:C505 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000 0 4567\n",
        encoding="utf-8",
    )

    assert discover_orphan_browsers(tmp_path) == [
        {"pid": 300, "port": 50437, "profile": str(profile)}
    ]
