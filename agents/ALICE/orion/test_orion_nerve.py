from __future__ import annotations

import asyncio
import json

import orion_nerve as nerve


class Completed:
    def __init__(self, returncode: int, stdout: str = ""):
        self.returncode = returncode
        self.stdout = stdout


def test_alert_cooldown_requires_confirmed_delivery(monkeypatch):
    state = {}
    monkeypatch.setattr(
        nerve.subprocess,
        "run",
        lambda *args, **kwargs: Completed(1, '{"ok":false}'),
    )
    assert nerve._alert_william(state, "critical", "message") is False
    assert state.get("alerts") is None

    monkeypatch.setattr(
        nerve.subprocess,
        "run",
        lambda *args, **kwargs: Completed(0, '{"ok":true}'),
    )
    assert nerve._alert_william(state, "critical", "message") is True
    assert state["alerts"]["critical"] > 0


def test_nerve_backup_never_prunes(monkeypatch):
    commands = []

    def run(cmd, **kwargs):
        commands.append(cmd)
        return Completed(0)

    monkeypatch.setattr(nerve.subprocess, "run", run)
    assert nerve._run_backup() is True
    assert commands == [
        ["python3", str(nerve.BACKUP_SCRIPT), "backup", "--no-prune"]
    ]


def test_restart_is_green_only_after_full_probe(monkeypatch):
    before = {
        "fails": [
            "servicio orion-exam no activo (inactive)",
            "no pude verificar identidad viva: RuntimeError",
        ],
        "active": "inactive",
        "login_http": 0,
        "backup_age_min": 2.0,
        "db_user": "unknown",
        "counts": {},
        "baseline": {},
    }
    after = {
        "fails": [],
        "active": "active",
        "login_http": 200,
        "backup_age_min": 2.1,
        "db_user": "svc_orion_exam",
        "counts": {"users": 3},
        "baseline": {"users": 2},
    }
    probes = iter([before, after])

    async def probe():
        return next(probes)

    artifacts = []
    monkeypatch.setattr(nerve, "_probe", probe)
    monkeypatch.setattr(nerve, "_restart_service", lambda: None)
    monkeypatch.setattr(nerve, "_service_healthy", lambda: True)
    monkeypatch.setattr(nerve.time, "sleep", lambda _: None)
    monkeypatch.setattr(nerve, "_load_state", lambda: {})
    monkeypatch.setattr(
        nerve, "_write_private_json", lambda path, payload: None
    )
    monkeypatch.setattr(
        nerve,
        "_append_private",
        lambda path, payload: artifacts.append((path, payload)),
    )
    monkeypatch.setattr(nerve, "_alert_william", lambda *args: True)

    assert asyncio.run(nerve.run()) == 1
    status = artifacts[-1][1]
    assert status["status"] == "REMEDIATED"
    assert status["db_user"] == "svc_orion_exam"
    assert status["fails"] == []


def test_unverifiable_without_action_fails_closed(monkeypatch):
    snapshot = {
        "fails": ["no pude verificar identidad viva: PermissionError"],
        "active": "active",
        "login_http": 200,
        "backup_age_min": 2.0,
        "db_user": "unknown",
        "counts": {},
        "baseline": {},
    }

    async def probe():
        return snapshot

    artifacts = []
    monkeypatch.setattr(nerve, "_probe", probe)
    monkeypatch.setattr(nerve, "_load_state", lambda: {})
    monkeypatch.setattr(
        nerve, "_write_private_json", lambda path, payload: None
    )
    monkeypatch.setattr(
        nerve,
        "_append_private",
        lambda path, payload: artifacts.append((path, payload)),
    )
    monkeypatch.setattr(nerve, "_alert_william", lambda *args: True)

    assert asyncio.run(nerve.run()) == 2
    status = artifacts[-1][1]
    assert status["status"] == "CRITICAL"
    assert status["actions"] == []
    assert status["escalations"] == snapshot["fails"]


def test_critical_identity_blocks_restart(monkeypatch):
    snapshot = {
        "fails": [
            "/login inaccesible: URLError",
            "identidad ORION inválida: proceso=seal, config=svc_orion_exam",
        ],
        "active": "active",
        "login_http": 0,
        "backup_age_min": 2.0,
        "db_user": "seal",
        "counts": {},
        "baseline": {},
    }

    async def probe():
        return snapshot

    actions = []
    artifacts = []
    monkeypatch.setattr(nerve, "_probe", probe)
    monkeypatch.setattr(
        nerve, "_restart_service", lambda: actions.append("restart")
    )
    monkeypatch.setattr(nerve, "_load_state", lambda: {})
    monkeypatch.setattr(
        nerve, "_write_private_json", lambda path, payload: None
    )
    monkeypatch.setattr(
        nerve,
        "_append_private",
        lambda path, payload: artifacts.append((path, payload)),
    )
    monkeypatch.setattr(nerve, "_alert_william", lambda *args: True)

    assert asyncio.run(nerve.run()) == 2
    assert actions == []
    assert artifacts[-1][1]["actions"] == []


def test_critical_data_loss_blocks_backup(monkeypatch):
    snapshot = {
        "fails": [
            "backup viejo (99 min > 45)",
            "orion_exam.users cayó a 0 (baseline > 0)",
        ],
        "active": "active",
        "login_http": 200,
        "backup_age_min": 99.0,
        "db_user": "svc_orion_exam",
        "counts": {"users": 0},
        "baseline": {"users": 2},
    }

    async def probe():
        return snapshot

    actions = []
    artifacts = []
    monkeypatch.setattr(nerve, "_probe", probe)
    monkeypatch.setattr(
        nerve, "_run_backup", lambda: actions.append("backup") or True
    )
    monkeypatch.setattr(nerve, "_load_state", lambda: {})
    monkeypatch.setattr(
        nerve, "_write_private_json", lambda path, payload: None
    )
    monkeypatch.setattr(
        nerve,
        "_append_private",
        lambda path, payload: artifacts.append((path, payload)),
    )
    monkeypatch.setattr(nerve, "_alert_william", lambda *args: True)

    assert asyncio.run(nerve.run()) == 2
    assert actions == []
    assert artifacts[-1][1]["actions"] == []


def test_restart_attempt_is_persisted_before_side_effect(monkeypatch):
    before = {
        "fails": ["/login inaccesible: URLError"],
        "active": "active",
        "login_http": 0,
        "backup_age_min": 2.0,
        "db_user": "svc_orion_exam",
        "counts": {"users": 3},
        "baseline": {"users": 2},
    }
    after = dict(before)
    probes = iter([before, after])

    async def probe():
        return next(probes)

    events = []
    monkeypatch.setattr(nerve, "_probe", probe)
    monkeypatch.setattr(nerve, "_load_state", lambda: {})
    monkeypatch.setattr(
        nerve,
        "_write_private_json",
        lambda path, payload: events.append(("persist", dict(payload))),
    )
    monkeypatch.setattr(
        nerve, "_restart_service", lambda: events.append(("restart", None))
    )
    monkeypatch.setattr(nerve, "_service_healthy", lambda: False)
    monkeypatch.setattr(nerve.time, "sleep", lambda _: None)
    monkeypatch.setattr(nerve, "_append_private", lambda *args: None)
    monkeypatch.setattr(nerve, "_alert_william", lambda *args: True)

    assert asyncio.run(nerve.run()) == 2
    assert events[0][0] == "persist"
    assert len(events[0][1]["restarts"]) == 1
    assert events[1][0] == "restart"


def test_backup_guard_blocks_third_attempt(monkeypatch):
    now = nerve.time.time()
    state = {"backup_attempts": [now, now]}
    snapshot = {
        "fails": ["backup viejo (99 min > 45)"],
        "active": "active",
        "login_http": 200,
        "backup_age_min": 99.0,
        "db_user": "svc_orion_exam",
        "counts": {"users": 3},
        "baseline": {"users": 2},
    }

    async def probe():
        return snapshot

    actions = []
    monkeypatch.setattr(nerve, "_probe", probe)
    monkeypatch.setattr(nerve, "_load_state", lambda: state)
    monkeypatch.setattr(
        nerve, "_run_backup", lambda: actions.append("backup") or True
    )
    monkeypatch.setattr(nerve, "_write_private_json", lambda *args: None)
    monkeypatch.setattr(nerve, "_append_private", lambda *args: None)
    monkeypatch.setattr(nerve, "_alert_william", lambda *args: True)

    assert asyncio.run(nerve.run()) == 2
    assert actions == []


def test_missing_baseline_is_critical_and_never_auto_created(monkeypatch):
    assert nerve._is_critical_fail(
        "baseline de conteos ausente (requiere bootstrap explícito)"
    )


def test_baseline_bootstrap_requires_explicit_approval(monkeypatch):
    monkeypatch.delenv("SEAL_ORION_BASELINE_APPROVED", raising=False)
    assert asyncio.run(nerve.bootstrap_baseline()) == 2
