from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "jarvis_nerves_watch_under_test",
    ROOT / "tools" / "jarvis_nerves_watch.py",
)
assert SPEC and SPEC.loader
WATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WATCH)


def _completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def test_parse_failed_units_filters_noise_but_keeps_both_prefixes():
    output = "\n".join(
        [
            "seal-instinct-cron.service loaded failed failed SEAL maintenance",
            "soul-export.timer loaded failed failed SOUL export",
            "update-notifier-crash.service loaded failed failed OS noise",
        ]
    )

    assert WATCH._parse_failed_units(output) == [
        "seal-instinct-cron.service",
        "soul-export.timer",
    ]


def test_parse_failed_units_rejects_unreadable_nonempty_output():
    with pytest.raises(ValueError, match="unexpected systemctl row"):
        WATCH._parse_failed_units("systemd output format unexpectedly changed")


def test_failed_units_reads_user_and_system_scopes(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        unit = "seal-user.service" if "--user" in command else "soul-system.service"
        return _completed(f"{unit} loaded failed failed canary\n")

    monkeypatch.setattr(WATCH.subprocess, "run", fake_run)

    assert WATCH._failed_units("user") == ["seal-user.service"]
    assert WATCH._failed_units("system") == ["soul-system.service"]
    assert calls[0][:2] == ["systemctl", "--user"]
    assert calls[1][0] == "systemctl" and "--user" not in calls[1]


def test_failed_units_command_failure_is_not_zero(monkeypatch):
    monkeypatch.setattr(
        WATCH.subprocess,
        "run",
        lambda *args, **kwargs: _completed(returncode=1, stderr="bus unavailable"),
    )

    with pytest.raises(RuntimeError, match="failed-unit sweep"):
        WATCH._failed_units("user")


def test_check_finds_uncatalogued_failed_unit_with_all_inventory_axes_green(monkeypatch):
    inventory_results = iter(
        [
            _completed("diff green\n"),
            _completed("identity green\n"),
            _completed(json.dumps([{"name": "catalogued", "lifecycle": "active", "healthy": True}])),
        ]
    )
    monkeypatch.setattr(WATCH, "_run", lambda args: next(inventory_results))
    monkeypatch.setattr(
        WATCH,
        "_failed_units",
        lambda scope: ["seal-uncatalogued.service"] if scope == "user" else [],
    )

    state, findings, _detail = WATCH.check()

    assert state == "FINDING"
    assert findings == [
        "SYSTEMD user: unidad(es) SOUL/SEAL fallida(s): seal-uncatalogued.service"
    ]


def test_failed_unit_cannot_be_silenced_by_known_baseline(monkeypatch, tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "jarvis_nerves_known_baseline.py").write_text(
        "def filter_findings(findings):\n"
        "    return [], [{'finding': item} for item in findings]\n",
        encoding="utf-8",
    )
    inventory_results = iter(
        [
            _completed("diff green\n"),
            _completed("identity green\n"),
            _completed("[]"),
        ]
    )
    monkeypatch.setattr(WATCH, "ROOT", tmp_path)
    monkeypatch.setattr(WATCH, "_run", lambda args: next(inventory_results))
    monkeypatch.setattr(
        WATCH,
        "_failed_units",
        lambda scope: ["soul-never-silent.service"] if scope == "system" else [],
    )

    state, findings, _detail = WATCH.check()

    assert state == "FINDING"
    assert findings == [
        "SYSTEMD system: unidad(es) SOUL/SEAL fallida(s): soul-never-silent.service"
    ]


def test_check_fails_closed_when_one_systemd_scope_is_unreadable(monkeypatch):
    inventory_results = iter(
        [
            _completed("diff green\n"),
            _completed("identity green\n"),
            _completed("[]"),
        ]
    )
    monkeypatch.setattr(WATCH, "_run", lambda args: next(inventory_results))

    def fake_failed_units(scope):
        if scope == "system":
            raise RuntimeError("permission denied")
        return []

    monkeypatch.setattr(WATCH, "_failed_units", fake_failed_units)

    state, findings, detail = WATCH.check()

    assert state == "BROKEN"
    assert findings == ["systemd failed-unit sweep unavailable"]
    assert "permission denied" in detail
