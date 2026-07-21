from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import seal_agent_stability_guard as guard


def write_valid_files(root) -> None:
    (root / "scripts").mkdir()
    (root / "CLAUDE.md").write_text(
        "\n".join(guard.AUTONOMY_REQUIRED_CLAUSES), encoding="utf-8"
    )
    (root / "scripts/seal_send.py").write_text(
        "AUTONOMY BLOCKED approval_gate", encoding="utf-8"
    )
    (root / "scripts/seal_autonomy_guard.py").write_text(
        "def autonomy_warning(): pass", encoding="utf-8"
    )


def test_autonomy_files_green_when_contract_and_blocker_are_present(tmp_path) -> None:
    write_valid_files(tmp_path)
    result = guard.check_autonomy_files(tmp_path)
    assert result == {"ok": True, "status": "healthy", "issues": []}


def test_autonomy_files_fail_on_legacy_rule(tmp_path) -> None:
    write_valid_files(tmp_path)
    contract = tmp_path / "CLAUDE.md"
    contract.write_text(
        contract.read_text(encoding="utf-8") + "\n" + guard.AUTONOMY_LEGACY_PHRASES[0],
        encoding="utf-8",
    )
    result = guard.check_autonomy_files(tmp_path)
    assert result["ok"] is False
    assert result["status"] == "drift"
    assert any("legacy" in issue for issue in result["issues"])


def test_autonomy_contract_combines_file_and_database_drift(tmp_path, monkeypatch) -> None:
    write_valid_files(tmp_path)

    async def fake_governance():
        return {"ok": False, "status": "drift", "issues": ["db drift"]}

    monkeypatch.setattr(guard, "check_autonomy_governance", fake_governance)
    result = asyncio.run(guard.check_autonomy_contract(tmp_path))
    assert result["ok"] is False
    assert result["status"] == "drift"
    assert result["issues"] == ["db drift"]


def test_stable_hash_changes_when_autonomy_drifts() -> None:
    base = {
        "status": "GREEN",
        "issues": [],
        "fixes": [],
        "ws": [],
        "auth_guard": {"status": "healthy", "issues": []},
        "autonomy_contract": {"status": "healthy", "issues": []},
    }
    healthy = guard.stable_hash(base)
    base["autonomy_contract"] = {"status": "drift", "issues": ["lost clause"]}
    assert guard.stable_hash(base) != healthy
