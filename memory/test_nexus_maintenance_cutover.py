from __future__ import annotations

import types

from memory import nexus_maintenance as nm


class _Run:
    def __init__(self, state: str):
        self.stdout = state + "\n"
        self.stderr = ""


def _issue(service: str):
    return types.SimpleNamespace(
        key=f"svc_down:{service}",
        data={"service": service},
    )


def test_v2_intent_excludes_both_v1_watch_units(monkeypatch, tmp_path):
    marker = tmp_path / "alice_cuerpo_activo"
    marker.write_text("ALICE-V2\n", encoding="utf-8")
    monkeypatch.setattr(nm, "_ALICE_ACTIVE_BODY_PATH", marker)

    watched = nm._watch_services()

    assert "seal-alice-dm-poller.service" not in watched
    assert "seal-bridge-alice.service" not in watched
    assert "seal-nexus-dm-poller.service" in watched


def test_absent_marker_preserves_v1_watch_contract(monkeypatch, tmp_path):
    monkeypatch.setattr(nm, "_ALICE_ACTIVE_BODY_PATH", tmp_path / "missing")

    watched = nm._watch_services()

    assert "seal-alice-dm-poller.service" in watched
    assert "seal-bridge-alice.service" in watched


def test_unknown_marker_preserves_v1_watch_contract(monkeypatch, tmp_path):
    marker = tmp_path / "alice_cuerpo_activo"
    marker.write_text("VALOR-DESCONOCIDO\n", encoding="utf-8")
    monkeypatch.setattr(nm, "_ALICE_ACTIVE_BODY_PATH", marker)

    watched = nm._watch_services()

    assert "seal-alice-dm-poller.service" in watched
    assert "seal-bridge-alice.service" in watched


def test_detector_does_not_emit_v1_issues_during_v2(monkeypatch, tmp_path):
    marker = tmp_path / "alice_cuerpo_activo"
    marker.write_text("ALICE-V2", encoding="utf-8")
    monkeypatch.setattr(nm, "_ALICE_ACTIVE_BODY_PATH", marker)
    monkeypatch.setattr(nm, "_systemctl_user", lambda args, timeout=8: _Run("inactive"))

    issues = nm.detect_services()
    services = {issue.data["service"] for issue in issues}

    assert "seal-alice-dm-poller.service" not in services
    assert "seal-bridge-alice.service" not in services
    assert "seal-nexus-dm-poller.service" in services


def test_fixer_rechecks_cutover_intent_before_restart(monkeypatch, tmp_path):
    marker = tmp_path / "alice_cuerpo_activo"
    marker.write_text("ALICE-V2", encoding="utf-8")
    monkeypatch.setattr(nm, "_ALICE_ACTIVE_BODY_PATH", marker)
    monkeypatch.setattr(
        nm.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("restart must not run")),
    )

    result = nm.fix_restart(_issue("seal-alice-dm-poller.service"))

    assert result.applied is False
    assert "ALICE-V2 cutover intent" in result.detail
