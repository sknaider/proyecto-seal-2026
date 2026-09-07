from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import tools.nerves_a3_reversible_canary as canary
from tools.nerves_a3_reversible_canary import A3CanaryError, AGENTS


@pytest.fixture
def fixed_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "fixed-root"
    monkeypatch.setattr(canary, "DEFAULT_ROOT", root)
    return root


def test_all_agents_mutate_and_restore_absent_state(fixed_root: Path) -> None:
    rows = [canary.run_canary(agent) for agent in AGENTS]
    assert [row["agent"] for row in rows] == list(AGENTS)
    assert all(row["mutation_verified"] for row in rows)
    assert all(row["rollback_verified"] for row in rows)
    assert all(not (fixed_root / agent / "state.json").exists() for agent in AGENTS)
    assert len(canary.verify_latest()) == len(AGENTS)


def test_existing_state_is_restored_byte_exact(fixed_root: Path) -> None:
    canary.run_canary("ADA")
    target = fixed_root / "ADA" / "state.json"
    original = b'{"keep":"byte-exact"}\\n'
    target.write_bytes(original)
    target.chmod(0o600)
    receipt = canary.run_canary("ADA")
    assert target.read_bytes() == original
    assert receipt["before"] == receipt["after"]
    assert receipt["before"]["sha256"] != receipt["mutation"]["sha256"]


def test_receipt_and_runtime_paths_are_private(fixed_root: Path) -> None:
    receipt = canary.run_canary("ADA")
    receipt_path = Path(receipt["receipt_path"])
    assert receipt_path.stat().st_mode & 0o777 == 0o600
    assert receipt_path.parent.stat().st_mode & 0o777 == 0o700
    assert (fixed_root / "ADA" / ".lock").stat().st_mode & 0o777 == 0o600


def test_symlink_target_fails_closed(fixed_root: Path) -> None:
    outside = fixed_root.parent / "outside"
    outside.write_text("untouched", encoding="utf-8")
    agent_dir = fixed_root / "ADA"
    agent_dir.mkdir(parents=True)
    (agent_dir / "state.json").symlink_to(outside)
    with pytest.raises(A3CanaryError, match="unsafe_file"):
        canary.run_canary("ADA")
    assert outside.read_text(encoding="utf-8") == "untouched"


def test_symlink_lock_fails_closed(fixed_root: Path) -> None:
    outside = fixed_root.parent / "outside-lock"
    outside.write_text("untouched", encoding="utf-8")
    agent_dir = fixed_root / "ADA"
    agent_dir.mkdir(parents=True)
    (agent_dir / ".lock").symlink_to(outside)
    with pytest.raises(A3CanaryError, match="unsafe_lock"):
        canary.run_canary("ADA")
    assert outside.read_text(encoding="utf-8") == "untouched"


def test_symlink_root_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    monkeypatch.setattr(canary, "DEFAULT_ROOT", linked)
    with pytest.raises(A3CanaryError, match="unsafe_root_symlink"):
        canary.run_canary("ADA")


@pytest.mark.parametrize("had_state", [False, True])
def test_exception_after_mutation_still_rolls_back(
    fixed_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    had_state: bool,
) -> None:
    original = b'{"original":true}\n'
    if had_state:
        canary.run_canary("ADA")
        target = fixed_root / "ADA" / "state.json"
        target.write_bytes(original)
        target.chmod(0o600)
    else:
        target = fixed_root / "ADA" / "state.json"

    def fail_after_write(agent_fd: int, expected: bytes) -> dict[str, object]:
        assert canary._read_at(agent_fd, "state.json") == expected
        raise RuntimeError("injected_after_mutation")

    monkeypatch.setattr(canary, "_verify_mutation", fail_after_write)
    with pytest.raises(RuntimeError, match="injected_after_mutation"):
        canary.run_canary("ADA")
    if had_state:
        assert target.read_bytes() == original
    else:
        assert not target.exists()


def test_unknown_agent_is_rejected(fixed_root: Path) -> None:
    with pytest.raises(A3CanaryError, match="unknown_agent"):
        canary.run_canary("TEAM")


def test_verify_fails_loud_when_receipt_is_missing(fixed_root: Path) -> None:
    with pytest.raises(A3CanaryError, match="missing_receipt"):
        canary.verify_latest(("ADA",))


def _rewrite_receipt(path: Path, value: dict[str, object]) -> None:
    path.write_bytes(canary._canonical(value))
    path.chmod(0o600)


def test_verify_rejects_tampered_contract(fixed_root: Path) -> None:
    receipt = canary.run_canary("ADA")
    path = Path(receipt["receipt_path"])
    value = json.loads(path.read_text(encoding="utf-8"))
    value["rollback_verified"] = False
    _rewrite_receipt(path, value)
    with pytest.raises(A3CanaryError, match="receipt_contract_mismatch"):
        canary.verify_latest(("ADA",))


def test_verify_rejects_traversal_target(fixed_root: Path) -> None:
    receipt = canary.run_canary("ADA")
    path = Path(receipt["receipt_path"])
    value = json.loads(path.read_text(encoding="utf-8"))
    value["target"] = "../../outside"
    _rewrite_receipt(path, value)
    with pytest.raises(
        A3CanaryError, match="receipt_contract_mismatch:ADA:target"
    ):
        canary.verify_latest(("ADA",))


def test_verify_rejects_noncanonical_receipt(fixed_root: Path) -> None:
    receipt = canary.run_canary("ADA")
    path = Path(receipt["receipt_path"])
    value = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(A3CanaryError, match="noncanonical_receipt"):
        canary.verify_latest(("ADA",))


def test_verify_rejects_post_state_drift(fixed_root: Path) -> None:
    canary.run_canary("ADA")
    target = fixed_root / "ADA" / "state.json"
    target.write_bytes(b"drift\n")
    target.chmod(0o600)
    with pytest.raises(A3CanaryError, match="rollback_state_mismatch"):
        canary.verify_latest(("ADA",))


def test_cli_has_no_arbitrary_root_override() -> None:
    actions = {option for action in canary._parser()._actions for option in action.option_strings}
    assert "--root" not in actions


def test_directory_leaf_cannot_be_replaced_by_symlink(
    fixed_root: Path,
) -> None:
    fixed_root.mkdir(parents=True)
    actual = fixed_root.parent / "actual-agent"
    actual.mkdir()
    (fixed_root / "ADA").symlink_to(actual, target_is_directory=True)
    with pytest.raises(A3CanaryError, match="unsafe_directory"):
        canary.run_canary("ADA")
    assert os.listdir(actual) == []
