from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.nerves_a3_reversible_canary import (
    A3CanaryError,
    AGENTS,
    _atomic_write,
    run_canary,
    verify_latest,
)


def test_all_agents_mutate_and_restore_absent_state(tmp_path: Path) -> None:
    rows = [run_canary(agent, root=tmp_path) for agent in AGENTS]
    assert [row["agent"] for row in rows] == list(AGENTS)
    assert all(row["mutation_verified"] for row in rows)
    assert all(row["rollback_verified"] for row in rows)
    assert all(not (tmp_path / agent / "state.json").exists() for agent in AGENTS)
    assert len(verify_latest(root=tmp_path)) == len(AGENTS)


def test_existing_state_is_restored_byte_exact(tmp_path: Path) -> None:
    target = tmp_path / "ADA" / "state.json"
    original = b'{"keep":"byte-exact"}\\n'
    _atomic_write(target, original)
    receipt = run_canary("ADA", root=tmp_path)
    assert target.read_bytes() == original
    assert receipt["before"] == receipt["after"]
    assert receipt["before"]["sha256"] != receipt["mutation"]["sha256"]


def test_receipt_and_runtime_paths_are_private(tmp_path: Path) -> None:
    receipt = run_canary("ADA", root=tmp_path)
    receipt_path = Path(receipt["receipt_path"])
    assert receipt_path.stat().st_mode & 0o777 == 0o600
    assert receipt_path.parent.stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "ADA" / ".lock").stat().st_mode & 0o777 == 0o600


def test_symlink_target_fails_closed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("untouched", encoding="utf-8")
    agent_dir = tmp_path / "ADA"
    agent_dir.mkdir(parents=True)
    (agent_dir / "state.json").symlink_to(outside)
    with pytest.raises(A3CanaryError, match="unsafe_state_file"):
        run_canary("ADA", root=tmp_path)
    assert outside.read_text(encoding="utf-8") == "untouched"


def test_unknown_agent_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(A3CanaryError, match="unknown_agent"):
        run_canary("TEAM", root=tmp_path)


def test_verify_fails_loud_when_receipt_is_missing(tmp_path: Path) -> None:
    with pytest.raises(A3CanaryError, match="missing_receipt"):
        verify_latest(("ADA",), root=tmp_path)


def test_verify_rejects_tampered_contract(tmp_path: Path) -> None:
    receipt = run_canary("ADA", root=tmp_path)
    path = Path(receipt["receipt_path"])
    value = json.loads(path.read_text(encoding="utf-8"))
    value["rollback_verified"] = False
    _atomic_write(
        path,
        (
            json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode(),
    )
    with pytest.raises(A3CanaryError, match="receipt_contract_mismatch"):
        verify_latest(("ADA",), root=tmp_path)


def test_verify_rejects_post_state_drift(tmp_path: Path) -> None:
    run_canary("ADA", root=tmp_path)
    _atomic_write(tmp_path / "ADA" / "state.json", b"drift\n")
    with pytest.raises(A3CanaryError, match="rollback_state_mismatch"):
        verify_latest(("ADA",), root=tmp_path)
