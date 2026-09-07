import json
from pathlib import Path

import asyncpg
import pytest

from seal_secrets import pg_dsn
from skill_use_hook import (
    event_key,
    extract_invocations,
    record_coverage,
    record_payload,
    resolve_agent,
    resolve_runtime,
)


ROOT = Path(__file__).resolve().parents[1]


def _hook_commands(config: dict, event: str) -> list[str]:
    return [
        hook.get("command", "")
        for group in config.get("hooks", {}).get(event, [])
        for hook in group.get("hooks", [])
    ]


def test_runtime_configs_wire_the_recorder_to_real_skill_paths():
    codex = json.loads((ROOT / ".codex/hooks.json").read_text(encoding="utf-8"))
    claude = json.loads((ROOT / ".claude/settings.json").read_text(encoding="utf-8"))

    assert any("skill_use_hook.py" in command for command in _hook_commands(codex, "SessionStart"))
    assert any("SEAL_SKILL_RUNTIME=codex" in command for command in _hook_commands(codex, "SessionStart"))
    assert any("skill_use_hook.py" in command for command in _hook_commands(claude, "SessionStart"))
    assert any("SEAL_SKILL_RUNTIME=claude_code" in command for command in _hook_commands(claude, "SessionStart"))
    assert any("skill_use_hook.py" in command for command in _hook_commands(codex, "PostToolUse"))
    assert "PostToolUseFailure" not in codex.get("hooks", {})  # unsupported by Codex 0.146
    assert any("skill_use_hook.py" in command for command in _hook_commands(claude, "PostToolUse"))
    assert any(
        "skill_use_hook.py" in command
        for command in _hook_commands(claude, "PostToolUseFailure")
    )
    assert any(
        "skill_use_hook.py" in command
        for command in _hook_commands(claude, "UserPromptExpansion")
    )
    assert any("skill_use_hook.py" in command for command in _hook_commands(claude, "Stop"))


def test_extracts_native_skill_tool():
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Skill",
        "tool_input": {"skill": "soul-gstack", "args": "investigate"},
    }
    assert extract_invocations(payload) == [
        {"name": "soul-gstack", "path": None, "source": "native_skill_tool"}
    ]


def test_extracts_direct_slash_skill_expansion():
    payload = {
        "hook_event_name": "UserPromptExpansion",
        "expansion_type": "slash_command",
        "command_name": "seal-audit",
        "command_args": "memory/skill_use_hook.py",
        "prompt": "/seal-audit memory/skill_use_hook.py",
    }
    assert extract_invocations(payload) == [
        {"name": "seal-audit", "path": None, "source": "user_prompt_expansion"}
    ]


def test_extracts_prevalidation_failure_from_transcript(tmp_path):
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "assistant",
                        "timestamp": "2026-08-29T00:00:00.000Z",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "tool-fail",
                                    "name": "Skill",
                                    "input": {"skill": "soul-gstack"},
                                }
                            ]
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "timestamp": "2026-08-29T00:00:00.017Z",
                        "message": {
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "tool-fail",
                                    "is_error": True,
                                    "content": "<tool_use_error>controlled</tool_use_error>",
                                }
                            ]
                        },
                    }
                ),
            ]
        )
        + "\n"
    )
    rows = extract_invocations(
        {"hook_event_name": "Stop", "transcript_path": str(transcript)}
    )
    assert rows == [
        {
            "name": "soul-gstack",
            "path": None,
            "source": "transcript_skill_failure",
            "success": False,
            "error": "<tool_use_error>controlled</tool_use_error>",
            "duration_ms": 17,
            "tool_use_id": "tool-fail",
        }
    ]


def test_ignores_mcp_prompt_expansion():
    payload = {
        "hook_event_name": "UserPromptExpansion",
        "expansion_type": "mcp_prompt",
        "command_name": "postgres-query",
    }
    assert extract_invocations(payload) == []


def test_extracts_codex_skill_file_read(tmp_path):
    skill = tmp_path / "skills" / "seal-audit" / "SKILL.md"
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "exec_command",
        "cwd": str(tmp_path),
        "tool_input": {"cmd": "sed -n '1,260p' skills/seal-audit/SKILL.md"},
    }
    invocation = extract_invocations(payload)[0]
    assert invocation["name"] == "seal-audit"
    assert invocation["path"] == skill.resolve()
    assert invocation["source"] == "skill_file_read"


@pytest.mark.parametrize(
    "command",
    [
        "rg -n SKILL.md .",
        "find . -name SKILL.md",
        "pytest -q tests/test_SKILL.md.py",
        "echo skills/seal-audit/SKILL.md",
    ],
)
def test_does_not_count_mentions_as_invocations(command):
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    assert extract_invocations(payload) == []


def test_event_key_is_stable_and_event_sensitive():
    payload = {
        "hook_event_name": "PostToolUse",
        "session_id": "session-a",
        "tool_use_id": "tool-a",
    }
    key = event_key(payload, "soul-gstack", None)
    assert key == event_key(dict(payload), "soul-gstack", None)
    payload["hook_event_name"] = "PostToolUseFailure"
    assert key != event_key(payload, "soul-gstack", None)


def test_resolve_agent_from_environment(monkeypatch):
    monkeypatch.setenv("SEAL_AGENT", "ada")
    assert resolve_agent({}) == "ADA"


def test_resolve_runtime_is_explicit_and_bounded(monkeypatch):
    monkeypatch.setenv("SEAL_SKILL_RUNTIME", "claude_code")
    assert resolve_runtime({}) == "claude_code"
    monkeypatch.setenv("SEAL_SKILL_RUNTIME", "browser")
    assert resolve_runtime({}) is None


@pytest.mark.asyncio
async def test_session_start_records_coverage_and_requires_session_id():
    conn = await asyncpg.connect(pg_dsn(required=True))
    tr = conn.transaction()
    await tr.start()
    try:
        row = await record_coverage(
            conn,
            {
                "hook_event_name": "SessionStart",
                "session_id": "pytest-coverage-nexus",
                "source": "startup",
            },
            agent="NEXUS",
            runtime="claude_code",
        )
        assert row["agent"] == "NEXUS"
        assert row["runtime"] == "claude_code"
        status = await conn.fetchval(
            """
            SELECT coverage_status
            FROM soul_v3.skill_telemetry_agent_coverage
            WHERE agent='NEXUS' AND runtime='claude_code'
            """
        )
        assert status == "measured_since_coverage_start"
        with pytest.raises(ValueError, match="non-empty session_id"):
            await record_coverage(
                conn,
                {"hook_event_name": "SessionStart"},
                agent="NEXUS",
                runtime="claude_code",
            )
    finally:
        await tr.rollback()
        await conn.close()


@pytest.mark.asyncio
async def test_records_real_skill_and_deduplicates_in_transaction():
    conn = await asyncpg.connect(pg_dsn(required=True))
    tr = conn.transaction()
    await tr.start()
    try:
        before = await conn.fetchrow(
            "SELECT id, success_count, failure_count FROM soul_v3.skills WHERE name='soul-gstack' AND invalid_at IS NULL"
        )
        assert before is not None
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "soul-gstack"},
            "session_id": "pytest-skill-hook",
            "tool_use_id": "tool-success",
            "duration_ms": 17,
        }
        first = await record_payload(conn, payload, agent="ADA")
        second = await record_payload(conn, payload, agent="ADA")
        after = await conn.fetchrow(
            "SELECT success_count, failure_count FROM soul_v3.skills WHERE id=$1", before["id"]
        )
        assert first[0]["deduplicated"] is False
        assert second[0]["deduplicated"] is True
        assert after["success_count"] == before["success_count"] + 1
        assert after["failure_count"] == before["failure_count"]
    finally:
        await tr.rollback()
        await conn.close()


@pytest.mark.asyncio
async def test_records_failure_separately_from_success():
    conn = await asyncpg.connect(pg_dsn(required=True))
    tr = conn.transaction()
    await tr.start()
    try:
        before = await conn.fetchrow(
            "SELECT id, success_count, failure_count FROM soul_v3.skills WHERE name='soul-gstack' AND invalid_at IS NULL"
        )
        payload = {
            "hook_event_name": "PostToolUseFailure",
            "tool_name": "Skill",
            "tool_input": {"skill": "soul-gstack"},
            "session_id": "pytest-skill-hook",
            "tool_use_id": "tool-failure",
            "duration_ms": 9,
            "error": "controlled failure",
        }
        rows = await record_payload(conn, payload, agent="ADA")
        after = await conn.fetchrow(
            "SELECT success_count, failure_count FROM soul_v3.skills WHERE id=$1", before["id"]
        )
        assert rows[0]["success"] is False
        assert rows[0]["error"] == "controlled failure"
        assert after["success_count"] == before["success_count"]
        assert after["failure_count"] == before["failure_count"] + 1
    finally:
        await tr.rollback()
        await conn.close()
