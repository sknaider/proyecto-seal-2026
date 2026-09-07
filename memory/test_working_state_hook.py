from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest

import working_state_hook
from working_state_hook import event_from_hook_payload, hook_output


def test_bash_post_tool_success_becomes_tool_result() -> None:
    decision = event_from_hook_payload(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -q memory/test_working_state_hook.py"},
            "tool_response": {"exit_code": 0, "stdout": "4 passed"},
        },
        temporary=True,
    )

    assert decision.should_record is True
    assert decision.event is not None
    assert decision.event.event_type == "tool_result"
    assert decision.event.status == "success"
    assert decision.event.evidence["command"] == "pytest -q memory/test_working_state_hook.py"
    assert decision.event.evidence["output"] == "4 passed"
    assert decision.event.temporary is True


def test_bash_post_tool_failure_becomes_failed_path() -> None:
    decision = event_from_hook_payload(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "pytest -q"},
            "tool_response": {"exit_code": 1, "stderr": "failed"},
        }
    )

    assert decision.event is not None
    assert decision.event.status == "failed"
    assert decision.event.result == "failed"


def test_edit_post_tool_records_file_artifact() -> None:
    decision = event_from_hook_payload(
        {
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": "memory/working_state_hook.py"},
            "tool_response": {"success": True, "output": "updated"},
        }
    )

    assert decision.event is not None
    assert decision.event.action == "Edit memory/working_state_hook.py"
    assert decision.event.evidence["artifact"] == "memory/working_state_hook.py"


def test_task_completed_payload_becomes_verification() -> None:
    decision = event_from_hook_payload(
        {
            "hook_event_name": "TaskCompleted",
            "task_id": "s4",
            "task_subject": "Sprint 4 hook ingestion",
            "result": "tests passed",
        }
    )

    assert decision.event is not None
    assert decision.event.event_type == "verification"
    assert decision.event.status == "success"
    assert decision.event.evidence["task_id"] == "s4"


def test_unsupported_or_missing_payload_is_silent() -> None:
    assert event_from_hook_payload({}).should_record is False
    assert event_from_hook_payload({"tool_name": "UnknownTool"}).should_record is False


def test_hook_output_is_compact_context() -> None:
    output = hook_output(
        {
            "recorded": True,
            "event_id": 10,
            "projection": {"score": 100, "next_step": "continue_with_evidence"},
        }
    )

    context = output["hookSpecificOutput"]["additionalContext"]
    assert "event_id=10" in context
    assert "score=100" in context


@pytest.mark.asyncio
async def test_main_is_silent_when_flag_disabled(monkeypatch, capsys) -> None:
    monkeypatch.delenv("SEAL_WORKING_STATE_HOOK", raising=False)

    code = await working_state_hook.main_async(
        SimpleNamespace(agent="ADA", temporary=False, apply=False, force=False, cleanup_temp_agent="")
    )

    assert code == 0
    assert capsys.readouterr().out.strip() == "{}"


@pytest.mark.asyncio
async def test_main_is_non_blocking_on_ingest_error(monkeypatch, capsys) -> None:
    async def fail_ingest(*args, **kwargs):
        raise RuntimeError("db down")

    monkeypatch.setenv("SEAL_WORKING_STATE_HOOK", "1")
    monkeypatch.setattr(working_state_hook, "ingest_hook_payload", fail_ingest)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(read=lambda: '{"tool_name":"Bash"}'))

    code = await working_state_hook.main_async(
        SimpleNamespace(agent="ADA", temporary=False, apply=False, force=False, cleanup_temp_agent="")
    )

    assert code == 0
    assert capsys.readouterr().out.strip() == "{}"
