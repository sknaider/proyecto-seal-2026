"""Tests for ADA executor — whitelist ampliada + audit log."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

_KERNEL = Path(__file__).resolve().parent.parent / "kernel"
sys.path.insert(0, str(_KERNEL))

import executor  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "AUDIT_LOG", tmp_path / "audit.jsonl")
    yield tmp_path


# ── validate ────────────────────────────────────────────────────────────────

def test_validate_git_log_allowed():
    assert executor.validate("git log --oneline -5") == "git log --oneline -5"


def test_validate_git_commit_allowed_for_ada():
    """ADA can commit (different from NEXUS read-only git)."""
    assert "git commit" in executor.validate("git commit -m 'test'")


def test_validate_git_unknown_op_blocked():
    with pytest.raises(executor.ExecutorError):
        executor.validate("git rebase -i HEAD~3")


def test_validate_pip_allowed():
    assert "pip" in executor.validate("pip install httpx")


def test_validate_pytest_allowed():
    assert "pytest" in executor.validate("pytest tests/")


def test_validate_nvidia_smi_allowed():
    assert executor.validate("nvidia-smi") == "nvidia-smi"


def test_validate_rm_rf_blocked():
    with pytest.raises(executor.ExecutorError):
        executor.validate("rm -rf /tmp/foo")


def test_validate_chmod_777_blocked():
    with pytest.raises(executor.ExecutorError):
        executor.validate("chmod 777 file.py")


def test_validate_drop_table_blocked():
    with pytest.raises(executor.ExecutorError):
        executor.validate("python3 scripts/run.py 'DROP TABLE users'")


def test_validate_etc_path_blocked():
    with pytest.raises(executor.ExecutorError):
        executor.validate("cat /etc/passwd")


def test_validate_claude_dir_blocked():
    with pytest.raises(executor.ExecutorError):
        executor.validate("ls /home/dadito/.claude/foo")


def test_validate_python_train_script_allowed():
    assert "train_" in executor.validate("python3 train_model.py --epochs 5")


def test_validate_python_random_script_blocked():
    with pytest.raises(executor.ExecutorError):
        executor.validate("python3 some_random_script.py")


def test_validate_unknown_command_blocked():
    with pytest.raises(executor.ExecutorError):
        executor.validate("nmap -sV localhost")


# ── propose mode (default) ──────────────────────────────────────────────────

def test_propose_mode_writes_audit(isolated_audit, monkeypatch):
    monkeypatch.delenv("ADA_EXECUTE_MODE", raising=False)
    ex = executor.Executor()
    ex.mode = "propose"
    result = asyncio.run(ex.handle("git log --oneline -3"))
    assert "[ADA propone]" in result
    assert executor.AUDIT_LOG.exists()
    entry = json.loads(executor.AUDIT_LOG.read_text().splitlines()[0])
    assert entry["mode"] == "propose"
    assert entry["proposed"] is True


def test_blocked_command_logged(isolated_audit):
    ex = executor.Executor()
    result = asyncio.run(ex.handle("rm -rf /tmp/something"))
    assert "[BLOCKED]" in result
    entry = json.loads(executor.AUDIT_LOG.read_text().splitlines()[0])
    assert entry["blocked"] is True


# ── execute mode ────────────────────────────────────────────────────────────

def test_execute_mode_runs_command(isolated_audit, monkeypatch):
    monkeypatch.setenv("ADA_EXECUTE_MODE", "execute")
    ex = executor.Executor()
    ex.reload_mode()
    assert ex.mode == "execute"
    result = asyncio.run(ex.handle("date"))
    assert "[OK]" in result or "[FAIL]" in result
    entry = json.loads(executor.AUDIT_LOG.read_text().splitlines()[0])
    assert entry["mode"] == "execute"
    assert "duration_ms" in entry


# ── whitelist comparison vs NEXUS ───────────────────────────────────────────

def test_whitelist_ampliada_includes_engineer_tools():
    """ADA whitelist includes pip, pytest, nvidia-smi, ssh, scp, rsync, etc."""
    for cmd in ("pip", "pip3", "pytest", "nvidia-smi", "watch", "cp", "mv", "mkdir",
                "chmod", "tar", "rsync", "ssh", "scp"):
        assert cmd in executor.ALLOWED_BASH_COMMANDS, f"{cmd} should be in ADA whitelist"


def test_git_write_ops_allowed():
    """ADA can do git write ops (NEXUS cannot)."""
    for op in ("add", "commit", "push", "checkout", "stash"):
        assert op in executor.GIT_ALLOWED_OPS
