from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import seal_agent_stability_guard as guard


NOW = 1_800_000_000.0


def write_state(path, *, status: str, checked_at: int = int(NOW), failures: int = 0) -> None:
    path.write_text(
        json.dumps(
            {
                "status": status,
                "raw_status": status,
                "checked_at": checked_at,
                "consecutive_failures": failures,
            }
        ),
        encoding="utf-8",
    )


def test_auth_guard_healthy_is_part_of_system_green(tmp_path) -> None:
    path = tmp_path / "state.json"
    write_state(path, status="healthy")
    result = guard.check_auth_guard_status(path, now=NOW)
    assert result["ok"] is True
    assert result["status"] == "healthy"


def test_auth_guard_login_failure_breaks_system_green(tmp_path) -> None:
    path = tmp_path / "state.json"
    write_state(path, status="needs_login", failures=2)
    result = guard.check_auth_guard_status(path, now=NOW)
    assert result["ok"] is False
    assert "requiere login" in result["issues"][0]


def test_stale_auth_guard_breaks_system_green(tmp_path) -> None:
    path = tmp_path / "state.json"
    write_state(path, status="healthy", checked_at=int(NOW - guard.AUTH_GUARD_MAX_AGE_SECONDS - 1))
    result = guard.check_auth_guard_status(path, now=NOW)
    assert result["ok"] is False
    assert "stale" in result["issues"][0]


def test_stability_sender_uses_authenticated_status_route(monkeypatch) -> None:
    observed = {}

    def fake_run(argv, timeout=10):
        observed["argv"] = argv
        return SimpleNamespace(returncode=0, stdout='{"ok":true}', stderr="")

    monkeypatch.setattr(guard, "run", fake_run)
    assert guard.post_webchat("safe status", "stable-key") is True
    argv = observed["argv"]
    assert argv[argv.index("--type") + 1] == "status"
    assert argv[argv.index("--idempotency-key") + 1] == "stable-key"
