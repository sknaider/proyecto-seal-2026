from __future__ import annotations

from types import SimpleNamespace

import pytest

import mcp_web_soul_reconcile as reconcile


class RecordingAudit:
    instances: list["RecordingAudit"] = []

    def __init__(self, path, **kwargs) -> None:
        self.path = path
        self.kwargs = dict(kwargs)
        self.calls: list[dict] = []
        type(self).instances.append(self)

    def append(self, **payload) -> None:
        self.calls.append(dict(payload))


class RecordingControl:
    instances: list["RecordingControl"] = []
    new_effect = True

    def __init__(self, path, *, shared_group) -> None:
        self.path = path
        self.shared_group = shared_group
        self.calls: list[dict] = []
        type(self).instances.append(self)

    def reconcile_remote_effect(self, operation_id, *, effect_occurred, operator):
        self.calls.append(
            {
                "operation_id": operation_id,
                "effect_occurred": effect_occurred,
                "operator": operator,
            }
        )
        return {"new_effect": type(self).new_effect}


@pytest.fixture(autouse=True)
def reset_recorders():
    RecordingAudit.instances.clear()
    RecordingControl.instances.clear()
    RecordingControl.new_effect = True


def _allow_dedicated_operator(monkeypatch) -> None:
    monkeypatch.setattr(reconcile.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=771))
    monkeypatch.setattr(reconcile.os, "geteuid", lambda: 771)


def test_reconcile_refuses_any_uid_other_than_dedicated_operator(monkeypatch):
    monkeypatch.setattr(reconcile.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_uid=771))
    monkeypatch.setattr(reconcile.os, "geteuid", lambda: 1000)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("UID denial must happen before audit/control construction")

    monkeypatch.setattr(reconcile, "AuditTrail", forbidden)
    monkeypatch.setattr(reconcile, "BrowserControlPlane", forbidden)

    with pytest.raises(SystemExit, match="root -> seal-mcp-web-operator"):
        reconcile.main(["--operation-id", "op-1", "--effect-occurred"])


@pytest.mark.parametrize(
    ("resolution_flag", "effect_occurred"),
    [("--effect-occurred", True), ("--no-effect", False)],
)
def test_reconcile_effect_and_no_effect_emit_witnessed_intent_and_completion(
    monkeypatch, capsys, resolution_flag, effect_occurred
):
    _allow_dedicated_operator(monkeypatch)
    monkeypatch.setattr(reconcile, "AuditTrail", RecordingAudit)
    monkeypatch.setattr(reconcile, "BrowserControlPlane", RecordingControl)

    assert reconcile.main(
        ["--operation-id", "op-42", resolution_flag, "--operator", "William"]
    ) == 0

    assert capsys.readouterr().out == "reconciled\n"
    assert len(RecordingControl.instances) == 1
    control = RecordingControl.instances[0]
    assert control.path == reconcile.CONTROL_PATH
    assert control.shared_group == "seal-mcp-web-control"
    assert control.calls == [
        {
            "operation_id": "op-42",
            "effect_occurred": effect_occurred,
            "operator": "William",
        }
    ]

    assert len(RecordingAudit.instances) == 1
    audit = RecordingAudit.instances[0]
    assert audit.path == reconcile.STATE_ROOT / "audit" / "operator.jsonl"
    assert audit.kwargs == {
        "agent": "OPERATOR",
        "witness_socket": "/run/seal-audit-witness/witness.sock",
        "witness_required": True,
    }
    expected_arguments = {
        "operation_id": "op-42",
        "effect_occurred": effect_occurred,
        "operator": "William",
    }
    assert audit.calls == [
        {
            "tool": "remote_effect_reconcile.intent",
            "arguments": expected_arguments,
            "ok": True,
            "action_class": "OPERATOR",
            "required": True,
        },
        {
            "tool": "remote_effect_reconcile",
            "arguments": expected_arguments,
            "ok": True,
            "action_class": "OPERATOR",
            "required": True,
        },
    ]


def test_reconcile_is_idempotent_and_reports_no_new_effect(monkeypatch, capsys):
    _allow_dedicated_operator(monkeypatch)
    RecordingControl.new_effect = False
    monkeypatch.setattr(reconcile, "AuditTrail", RecordingAudit)
    monkeypatch.setattr(reconcile, "BrowserControlPlane", RecordingControl)

    assert reconcile.main(
        ["--operation-id", "op-already", "--effect-occurred"]
    ) == 0

    assert capsys.readouterr().out == "already-reconciled\n"
    assert len(RecordingControl.instances[0].calls) == 1
    assert [call["tool"] for call in RecordingAudit.instances[0].calls] == [
        "remote_effect_reconcile.intent",
        "remote_effect_reconcile",
    ]


def test_failed_reconciliation_never_emits_false_completion(monkeypatch):
    class FailingControl(RecordingControl):
        def reconcile_remote_effect(self, *_args, **_kwargs):
            raise RuntimeError("simulated reconciliation failure")

    _allow_dedicated_operator(monkeypatch)
    monkeypatch.setattr(reconcile, "AuditTrail", RecordingAudit)
    monkeypatch.setattr(reconcile, "BrowserControlPlane", FailingControl)

    with pytest.raises(RuntimeError, match="simulated reconciliation failure"):
        reconcile.main(["--operation-id", "op-fail", "--no-effect"])

    assert [call["tool"] for call in RecordingAudit.instances[0].calls] == [
        "remote_effect_reconcile.intent"
    ]
