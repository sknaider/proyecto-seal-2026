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
