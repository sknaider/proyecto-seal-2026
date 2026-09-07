from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "dependency_inventory_under_test",
    ROOT / "tools" / "dependency_inventory.py",
)
assert SPEC and SPEC.loader
INVENTORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INVENTORY)


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def test_listener_pid_returns_concrete_owner(monkeypatch):
    monkeypatch.setattr(
        INVENTORY.subprocess,
        "run",
        lambda *_args, **_kwargs: _completed(
            'LISTEN 0 4096 127.0.0.1:8765 0.0.0.0:* users:(("python",pid=4242,fd=9))\n'
        ),
    )

    assert INVENTORY._listener_pid(8765, set()) == 4242


def test_listener_pid_distinguishes_unobservable_owner(monkeypatch):
    results = iter(
        [
            _completed(returncode=1, stderr="sudo unavailable"),
            _completed("LISTEN 0 4096 127.0.0.1:8765 0.0.0.0:*\n"),
        ]
    )
    monkeypatch.setattr(
        INVENTORY.subprocess, "run", lambda *_args, **_kwargs: next(results)
    )

    assert (
        INVENTORY._listener_pid(8765, set())
        == INVENTORY._NO_OBSERVABLE
    )


def test_listener_pid_returns_none_when_nothing_listens(monkeypatch):
    results = iter(
        [
            _completed(returncode=1, stderr="sudo unavailable"),
            _completed(),
        ]
    )
    monkeypatch.setattr(
        INVENTORY.subprocess, "run", lambda *_args, **_kwargs: next(results)
    )

    assert INVENTORY._listener_pid(8765, set()) is None
