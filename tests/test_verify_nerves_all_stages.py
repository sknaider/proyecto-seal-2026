from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_nerves_all_stages", ROOT / "tools/verify_nerves_all_stages.py"
)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def test_test_count_parses_pytest_summary():
    assert gate._test_count("...\n1054 passed in 1.2s\n") == 1054


def test_test_count_fails_closed_without_summary():
    assert gate._test_count("collection failed") == 0


def test_runtime_report_covers_every_live_agent_and_fable(monkeypatch, tmp_path):
    contract = gate._read_json(gate.CONTRACT)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "_show", lambda _unit, prop: {
        "ActiveState": "active",
        "UnitFileState": "enabled",
        "LastTriggerUSec": "Wed 2026-07-22 08:00:00 -05",
        "Result": "success",
        "ExecMainStatus": "0",
    }[prop])
    paths = []
    for cfg in contract["shared_runtime"]["maintenance"].values():
        paths.append(tmp_path / cfg["artifact"])
    paths.append(tmp_path / contract["fable_sidecar"]["artifact"])
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
        path.chmod(0o600)
    report = gate.runtime_report(contract)
    assert report["ok"] is True
    assert {row["agent"] for row in report["agents"]} == {
        "ADA", "ALICE", "DUM", "JARVIS", "NEXUS", "FABLE"
    }


def test_runtime_row_rejects_insecure_artifact(monkeypatch, tmp_path):
    artifact = tmp_path / "artifact.json"
    artifact.write_text("{}", encoding="utf-8")
    artifact.chmod(0o644)
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "_show", lambda _unit, prop: {
        "ActiveState": "active",
        "UnitFileState": "enabled",
        "LastTriggerUSec": "now",
        "Result": "success",
        "ExecMainStatus": "0",
    }[prop])
    assert gate._runtime_row("ADA", "a.service", "a.timer", artifact)["ok"] is False
