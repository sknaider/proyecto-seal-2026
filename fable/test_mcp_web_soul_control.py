from __future__ import annotations

import concurrent.futures
import json
import sqlite3
from pathlib import Path

import mcp_web_soul_control as control_module

import pytest

from mcp_web_soul_control import (
    ACCOUNT,
    AUTH_SESSION,
    DESTRUCTIVE,
    FINANCIAL,
    REMOTE_COMMIT,
    UI_MUTATION,
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


def test_structural_dom_risk_catches_password_and_opaque_form_submit():
    assert classify_action(
        "type_text", {"selector": "#x7", "value": "secret"},
        risk_context={"type": "password"},
    ) == ACCOUNT
    assert classify_action(
        "browser_action", {"action": "type_text", "target": "#x7", "value": "secret"},
        risk_context={"autocomplete": "current-password"},
    ) == ACCOUNT
    assert classify_action(
        "click", {"selector": "#x7"}, risk_context={"submitsForm": True},
    ) == REMOTE_COMMIT
    assert classify_action(
        "type_text", {"selector": "#notes", "value": "harmless"},
        risk_context={"type": "text", "autocomplete": "off"},
    ) == UI_MUTATION
    assert classify_action(
        "click", {"selector": "#x7"},
        risk_context={
            "found": True, "tag": "button", "type": "button",
            "text": "Abrir detalles", "submitsForm": False,
        },
    ) == UI_MUTATION


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Eliminar cuenta", DESTRUCTIVE),
        ("Pagar ahora", FINANCIAL),
        ("Enviar formulario", REMOTE_COMMIT),
        ("Guardar cambios", REMOTE_COMMIT),
        ("Cambiar contraseña", ACCOUNT),
    ],
)
def test_spanish_sensitive_clicks_never_degrade_to_free_ui_mutation(label, expected):
    assert classify_action("click", {"selector": label}) == expected


def test_press_key_classifies_the_exact_effective_key():
    assert classify_action(
        "browser_action", {"action": "press_key", "target": "Enter", "value": ""},
    ) == REMOTE_COMMIT
    assert classify_action(
        "browser_action", {"action": "press_key", "target": "ignored", "value": "NumpadEnter"},
    ) == REMOTE_COMMIT
    # Execution prefers a non-empty value over target, so this really presses Tab.
    assert classify_action(
        "browser_action", {"action": "press_key", "target": "Enter", "value": "Tab"},
    ) == UI_MUTATION


@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        ("browse", {"url": "https://example.test/pay-now"}, REMOTE_COMMIT),
        ("browse", {"url": "https://example.test/eliminar-cuenta"}, REMOTE_COMMIT),
        (
            "save_url",
            {"url": "https://example.test/confirm-payment", "path": "receipt.bin"},
            REMOTE_COMMIT,
        ),
        ("navigate", {"url": "https://example.test/account/security"}, REMOTE_COMMIT),
        ("navigate", {"url": "https://example.test/publicar"}, REMOTE_COMMIT),
        ("open_tab", {"url": "https://example.test/cerrar-sesi%C3%B3n"}, REMOTE_COMMIT),
        (
            "browser_action",
            {"action": "navigate", "url": "https://example.test/confirm-payment"},
            REMOTE_COMMIT,
        ),
        (
            "browser_action",
            {"action": "open_tab", "url": "https://example.test/delete-account"},
            REMOTE_COMMIT,
        ),
    ],
)
def test_sensitive_navigation_urls_require_approval_in_english_and_spanish(
    tool, arguments, expected,
):
    assert classify_action(tool, arguments) == expected


def test_back_is_a_browser_state_mutation_direct_and_grouped():
    assert classify_action("back", {}) == UI_MUTATION
    assert classify_action("browser_action", {"action": "back"}) == UI_MUTATION
    assert classify_action("browser_action", {"action": "dialog_dismiss"}) == UI_MUTATION
    assert classify_action("browser_action", {"action": "switch_tab"}) == UI_MUTATION
    assert classify_action("close_browser", {}) == UI_MUTATION


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"action": " click ", "target": "#delete-account"}, DESTRUCTIVE),
        ({"action": " press_key ", "target": "Enter"}, REMOTE_COMMIT),
        ({"action": " set_cookie ", "target": "sid", "value": "x"}, AUTH_SESSION),
        (
            {"action": " navigate ", "url": "https://example.test/pay-now"},
            REMOTE_COMMIT,
        ),
        ({"action": " upload_file ", "target": "#file", "value": "x"}, REMOTE_COMMIT),
    ],
)
def test_grouped_action_normalization_cannot_bypass_sensitive_classification(
    arguments, expected,
):
    assert classify_action("browser_action", arguments) == expected


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


def _approved_remote_permit(control, *, pid=120):
    session = control.ensure_session(agent="ADA", pid=pid)
    args = {"selector": "button[type=submit]"}
    approval = control.request_approval(session_id=session, tool="click", arguments=args)
    control.approve(approval["approval_id"], operator="William")
    permit = control.authorize(
        session_id=session,
        tool="click",
        arguments=args,
        approval_id=approval["approval_id"],
    )
    return args, approval, permit


def test_remote_effect_is_armed_before_execution_and_crash_is_non_retryable(control):
    args, approval, permit = _approved_remote_permit(control)
    operation_id = control.arm_remote_effect(permit, tool="click", arguments=args)

    row = control.remote_effect_operation(operation_id)
    assert row["status"] == "armed"
    control.mark_remote_effect_indeterminate(
        permit, operation_id, error_code="simulated_crash_after_possible_effect"
    )
    assert control.remote_effect_operation(operation_id)["status"] == "indeterminate"
    assert control.approval_status(approval["approval_id"])["status"] == "failed"
    with pytest.raises(ControlDenied) as replay:
        control.authorize(
            session_id=permit.session_id,
            tool="click",
            arguments=args,
            approval_id=approval["approval_id"],
        )
    assert replay.value.code == "approval_state"
    with pytest.raises(ControlDenied) as logical_retry:
        control.request_approval(
            session_id=permit.session_id,
            tool="click",
            arguments=args,
        )
    assert logical_retry.value.code == "remote_effect_reconciliation_required"


def test_remote_effect_completion_and_approval_finalize_atomically(control):
    args, approval, permit = _approved_remote_permit(control, pid=121)
    operation_id = control.arm_remote_effect(permit, tool="click", arguments=args)
    control.observe_remote_effect(operation_id, ok=True)
    control.finalize_remote_effect(permit, operation_id, ok=True)

    assert control.remote_effect_operation(operation_id)["status"] == "completed"
    assert control.approval_status(approval["approval_id"])["status"] == "executed"


def test_remote_effect_cannot_be_armed_twice(control):
    args, _approval, permit = _approved_remote_permit(control, pid=122)
    operation_id = control.arm_remote_effect(permit, tool="click", arguments=args)
    assert operation_id
    with pytest.raises(ControlDenied) as replay:
        control.arm_remote_effect(permit, tool="click", arguments=args)
    assert replay.value.code == "remote_effect_indeterminate"


def test_remote_effect_fence_is_cross_session_and_canonical(control):
    session_one = control.ensure_session(agent="ADA", pid=123)
    raw = {
        "action": " navigate ",
        "url": "HTTPS://Example.COM:443/delete-account#fragment-one",
    }
    approval = control.request_approval(
        session_id=session_one, tool="browser_action", arguments=raw,
    )
    control.approve(approval["approval_id"], operator="William")
    permit = control.authorize(
        session_id=session_one,
        tool="browser_action",
        arguments=raw,
        approval_id=approval["approval_id"],
    )
    operation_id = control.arm_remote_effect(
        permit, tool="browser_action", arguments=raw,
    )
    control.mark_remote_effect_indeterminate(
        permit, operation_id, error_code="simulated_crash",
    )

    session_two = control.ensure_session(agent="ADA", pid=124)
    equivalent = {
        "action": "navigate",
        "url": "https://example.com/delete-account#other-fragment",
    }
    assert control.remote_effect_key("browser_action", raw) == control.remote_effect_key(
        "browser_action", equivalent
    )
    with pytest.raises(ControlDenied) as blocked:
        control.request_approval(
            session_id=session_two,
            tool="browser_action",
            arguments=equivalent,
        )
    assert blocked.value.code == "remote_effect_reconciliation_required"

    control.reconcile_remote_effect(
        operation_id, effect_occurred=False, operator="William",
    )
    replacement = control.request_approval(
        session_id=session_two,
        tool="browser_action",
        arguments=equivalent,
    )
    assert replacement["status"] == "requested"


def test_effect_key_ignores_non_remote_navigation_and_download_arguments(control):
    assert control.remote_effect_key(
        "browse", {"url": "https://example.test/a#one", "wait_until": "load"},
    ) == control.remote_effect_key(
        "browse", {"url": "https://example.test/a#two", "wait_until": "domcontentloaded"},
    )
    assert control.remote_effect_key(
        "save_url", {"url": "https://example.test/file", "path": "a.bin"},
    ) == control.remote_effect_key(
        "save_url", {"url": "https://example.test/file", "path": "b.bin"},
    )
    assert control.remote_effect_key(
        "browser_action", {"action": " navigate ", "url": "https://example.test/x", "timeout": 1},
    ) == control.remote_effect_key(
        "browser_action", {"action": "navigate", "url": "https://example.test/x", "timeout": 99},
    )


def test_expired_unexecuted_approval_does_not_deadlock_effect(monkeypatch, control):
    session = control.ensure_session(agent="ADA", pid=125)
    args = {"selector": "button[type=submit]"}
    now = control_module.time.time()
    first = control.request_approval(
        session_id=session, tool="click", arguments=args, ttl_seconds=1,
    )
    monkeypatch.setattr(control_module.time, "time", lambda: now + 2)
    second = control.request_approval(
        session_id=session, tool="click", arguments=args, ttl_seconds=1,
    )
    assert control.approval_status(first["approval_id"])["status"] == "expired"
    assert second["status"] == "requested"


def test_any_unresolved_effect_fences_other_actions_for_same_agent(control):
    args, _approval, permit = _approved_remote_permit(control, pid=126)
    operation_id = control.arm_remote_effect(permit, tool="click", arguments=args)
    control.mark_remote_effect_indeterminate(permit, operation_id, error_code="crash")
    other_session = control.ensure_session(agent="ADA", pid=127)
    with pytest.raises(ControlDenied) as blocked:
        control.request_approval(
            session_id=other_session,
            tool="set_cookie",
            arguments={"name": "sid", "value": "x", "url": "https://example.test"},
        )
    assert blocked.value.code == "remote_effect_reconciliation_required"


@pytest.mark.parametrize(
    ("tool", "arguments", "secret", "risk_context"),
    [
        (
            "set_cookie",
            {"name": "session", "value": "COOKIE-SECRET-XYZ", "url": "https://example.test"},
            "COOKIE-SECRET-XYZ",
            None,
        ),
        (
            "type_text",
            {"selector": "#opaque", "value": "PASSWORD-SECRET-XYZ"},
            "PASSWORD-SECRET-XYZ",
            {"type": "password"},
        ),
    ],
)
def test_remote_effect_journal_never_persists_secret_plaintext(
    control, tool, arguments, secret, risk_context,
):
    session = control.ensure_session(agent="ADA", pid=130 + len(secret))
    request = control.request_approval(
        session_id=session,
        tool=tool,
        arguments=arguments,
        risk_context=risk_context,
    )
    control.approve(request["approval_id"], operator="William")
    permit = control.authorize(
        session_id=session,
        tool=tool,
        arguments=arguments,
        approval_id=request["approval_id"],
        risk_context=risk_context,
        arm_remote_effect=True,
    )
    row = control.remote_effect_operation(permit.approval_id)
    assert row["status"] == "armed"
    encoded = "\n".join(str(value) for value in row.values())
    assert secret not in encoded
    assert "redacted" in row["arguments_json"]
    raw_db = control.db_path.read_bytes()
    assert secret.encode() not in raw_db


def test_legacy_remote_effect_migration_physically_scrubs_secrets(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    original = BrowserControlPlane(path)
    session = original.ensure_session(agent="ADA", pid=991)
    secret = "LEGACY-COOKIE-SECRET-MUST-DISAPPEAR"
    raw = json.dumps({
        "tool": "set_cookie",
        "arguments": {
            "name": "sid",
            "value": secret,
            "url": "https://example.test",
        },
    })
    with sqlite3.connect(path) as conn:
        for suffix, status in (("a", "indeterminate"), ("b", "completed")):
            conn.execute(
                "INSERT INTO remote_effect_operations("
                "operation_id,session_id,action_hash,tool,arguments_json,status,armed_at"
                ") VALUES(?,?,?,?,?,?,?)",
                (f"legacy-{suffix}", session, "a" * 64, "set_cookie", raw, status, 1.0),
            )
        conn.commit()

    migrated = BrowserControlPlane(path)
    with migrated._connect() as conn:
        rows = conn.execute(
            "SELECT arguments_json,effect_scope,effect_key FROM remote_effect_operations"
        ).fetchall()
    assert len(rows) == 2
    assert all("redacted" in row["arguments_json"] for row in rows)
    assert all(secret not in row["arguments_json"] for row in rows)
    assert all(row["effect_scope"] == "ADA" for row in rows)
    assert all(len(row["effect_key"]) == 64 for row in rows)
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        if candidate.exists():
            assert secret.encode() not in candidate.read_bytes()


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
    permit = control.authorize(session_id=session, tool="get_text", arguments={})
    assert permit.approval_id is None


def test_pid_reuse_never_inherits_a_stale_session_or_approval(control):
    first = control.ensure_session(agent="ADA", pid=777)
    request = control.request_approval(
        session_id=first,
        tool="click",
        arguments={"selector": "button[type=submit]"},
    )
    second = control.ensure_session(agent="ADA", pid=777)

    assert second != first
    assert control.session_status(first)["state"] == "closed"
    assert control.approval_status(request["approval_id"])["status"] == "cancelled"


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
