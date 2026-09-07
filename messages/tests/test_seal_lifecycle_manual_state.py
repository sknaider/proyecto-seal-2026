"""Estado `manual` del reconciliador seal-lifecycle (ADA, 3-sep-2026).

Contexto: agent_lifecycle.ADA = stopped desde el 12-jul. Al reparar el DSN del
controlador, este revivio y mato con SIGKILL la sesion ADA Claude que William
habia lanzado (PID 914726, 15:02:01). William: "solo te usare de momentos y no
quiero 2 adas". `manual` = el controlador ni mata ni relanza a ese agente.

Los tests llaman a la funcion real reconcile_agent con kill/launch/log_event
sustituidos por espias; no tocan la DB ni procesos.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "messages"))
os.environ.setdefault("SEAL_PG_DSN", "postgresql://test:test@localhost:1/test")

import seal_lifecycle_controller as ctl  # noqa: E402


def _row(desired: str) -> dict:
    return {"agent_name": "ADA", "desired_state": desired, "changed_by": "t", "changed_at": None, "reason": ""}


@pytest.fixture
def spy(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(ctl, "kill_agent", lambda a, p, dry_run=False: (calls.append(("KILL", a, p)) or (True, "spy")))
    monkeypatch.setattr(ctl, "launch_agent", lambda a, dry_run=False: (calls.append(("LAUNCH", a)) or (True, "spy")))

    async def _log(pool, agent, event, desired, actual, detail, dry_run=False):
        calls.append(("LOG", event))

    async def _age(pool, agent, events):
        return None  # sin cooldown: si toca matar, mata

    monkeypatch.setattr(ctl, "log_event", _log)
    monkeypatch.setattr(ctl, "get_last_action_age", _age)
    return calls


def _run(row):
    return asyncio.run(ctl.reconcile_agent(None, row))


def test_manual_alive_is_noop(spy, monkeypatch):
    """qa_positive: ADA Claude viva + manual -> no la mata."""
    monkeypatch.setattr(ctl, "is_process_alive", lambda a: 4242)
    _run(_row("manual"))
    assert spy == []


def test_manual_dead_is_noop(spy, monkeypatch):
    """qa_negative: ADA Claude apagada + manual -> NO la relanza (no hay 2 ADAs)."""
    monkeypatch.setattr(ctl, "is_process_alive", lambda a: None)
    _run(_row("manual"))
    assert spy == []


def test_control_stopped_alive_kills(spy, monkeypatch):
    """qa_control: el mismo arnes con stopped+viva SI mata: el test mide algo."""
    monkeypatch.setattr(ctl, "is_process_alive", lambda a: 4242)
    _run(_row("stopped"))
    assert ("KILL", "ADA", 4242) in spy and ("LOG", "auto_killed") in spy


def test_control_running_dead_launches(spy, monkeypatch):
    """qa_control: running+apagada relanza — por eso running NO sirve para ADA."""
    monkeypatch.setattr(ctl, "is_process_alive", lambda a: None)
    _run(_row("running"))
    assert ("LAUNCH", "ADA") in spy


def test_dsn_no_literal_password():
    """unit: el archivo no vuelve a llevar una contrasena literal del rol seal."""
    src = (ROOT / "messages" / "seal_lifecycle_controller.py").read_text(encoding="utf-8")
    assert "postgresql://seal:" not in src
    assert "from seal_secrets import pg_dsn" in src
