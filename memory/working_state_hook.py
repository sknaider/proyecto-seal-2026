#!/usr/bin/env python3
"""Hook adapter for the Working State Journal.

The journal is the durable storage/projection layer. This file is only the
adapter from hook payloads to journal events, so it can be tested before any
daemon or global hook wiring is enabled.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hook_utils import detect_agent
from working_state_journal import (
    WorkingStateEvent,
    apply_projection,
    cleanup_temporary_events,
    connect_db,
    project_agent,
    record_event,
)


MAX_FIELD_CHARS = 500
SUPPORTED_TOOLS = {"Bash", "Edit", "MultiEdit", "Write", "Read", "Task"}


@dataclass(frozen=True)
class HookJournalDecision:
    should_record: bool
    reason: str
    event: WorkingStateEvent | None = None


def _clip(value: Any, limit: int = MAX_FIELD_CHARS) -> str:
    text = str(value or "")
    return text[:limit]


def _hook_name(payload: dict[str, Any]) -> str:
    return str(payload.get("hook_event_name") or payload.get("hookEventName") or payload.get("event") or "")


def _tool_name(payload: dict[str, Any]) -> str:
    return str(payload.get("tool_name") or payload.get("toolName") or "")


def _tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("tool_input") or payload.get("toolInput") or {}
    return dict(value) if isinstance(value, dict) else {}


def _tool_response(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("tool_response") or payload.get("toolResponse") or payload.get("response") or {}
    return dict(value) if isinstance(value, dict) else {}


def _response_failed(response: dict[str, Any]) -> bool:
    if response.get("is_error") is True or response.get("error"):
        return True
    for key in ("exit_code", "returncode", "status_code"):
        if key in response:
            try:
                return int(response[key]) != 0
            except (TypeError, ValueError):
                return True
    if "success" in response:
        return response.get("success") is False
    return False


def _action_for_tool(tool: str, tool_input: dict[str, Any]) -> str:
    if tool == "Bash":
        return _clip(tool_input.get("command") or "bash")
    if tool in {"Edit", "Write", "Read"}:
        file_path = tool_input.get("file_path") or tool_input.get("path") or "unknown"
        return _clip(f"{tool} {file_path}")
    if tool == "MultiEdit":
        file_path = tool_input.get("file_path") or "unknown"
        edits = tool_input.get("edits") or []
        edit_count = len(edits) if isinstance(edits, list) else 0
        return _clip(f"MultiEdit {file_path} edits={edit_count}")
    if tool == "Task":
        return _clip(tool_input.get("description") or tool_input.get("prompt") or "Task")
    return _clip(tool or "unknown")


def _artifact_for_tool(tool: str, tool_input: dict[str, Any]) -> str:
    if tool in {"Edit", "Write", "Read", "MultiEdit"}:
        return _clip(tool_input.get("file_path") or tool_input.get("path") or "")
    return ""


def _output_for_response(response: dict[str, Any]) -> str:
    for key in ("stdout", "output", "result", "content", "stderr", "error"):
        if response.get(key):
            return _clip(response[key])
    return "hook payload recorded"


def event_from_hook_payload(payload: dict[str, Any], *, temporary: bool = False) -> HookJournalDecision:
    hook_name = _hook_name(payload)
    tool = _tool_name(payload)
    tool_input = _tool_input(payload)
    response = _tool_response(payload)

    if hook_name in {"TaskCompleted", "TaskComplete"} or payload.get("task_id"):
        action = _clip(payload.get("task_subject") or payload.get("title") or "task completed")
        result = _clip(payload.get("result") or payload.get("summary") or "task completed")
        event = WorkingStateEvent(
            event_type="verification",
            action=action,
            result=result,
            status="success",
            evidence={
                "artifact": "task_completed_hook",
                "output": result,
                "task_id": _clip(payload.get("task_id")),
            },
            temporary=temporary,
        )
        return HookJournalDecision(True, "task_completed_payload", event)

    if not tool:
        return HookJournalDecision(False, "missing_tool_name")
    if tool not in SUPPORTED_TOOLS:
        return HookJournalDecision(False, f"unsupported_tool:{tool}")

    if hook_name in {"PreToolUse", "PreTool"}:
        event_type = "tool_call"
        status = "running"
        result = "tool call started"
    else:
        event_type = "tool_result"
        status = "failed" if _response_failed(response) else "success"
        result = _output_for_response(response)

    evidence = {
        "tool": tool,
        "hook": hook_name or "unknown",
    }
    if tool == "Bash" and tool_input.get("command"):
        evidence["command"] = _clip(tool_input["command"])
    artifact = _artifact_for_tool(tool, tool_input)
    if artifact:
        evidence["artifact"] = artifact
    if response:
        evidence["output"] = _output_for_response(response)

    event = WorkingStateEvent(
        event_type=event_type,
        action=_action_for_tool(tool, tool_input),
        result=result,
        status=status,
        evidence=evidence,
        temporary=temporary,
    )
    return HookJournalDecision(True, "recordable_tool_payload", event)


async def ingest_hook_payload(
    payload: dict[str, Any],
    *,
    agent: str,
    temporary: bool = False,
    apply: bool = False,
) -> dict[str, Any]:
    decision = event_from_hook_payload(payload, temporary=temporary)
    if not decision.should_record or decision.event is None:
        return {"recorded": False, "reason": decision.reason}

    conn = await connect_db()
    try:
        event_id = await record_event(conn, agent, decision.event)
        work_ledger_row = None
        agent_task_row = None
        if decision.reason == "task_completed_payload" and not temporary:
            from agent_tasks_registry import AgentTaskRecord, upsert_agent_task
            from agent_work_ledger import WorkItem, ensure_schema, upsert_work_item

            task_id_raw = payload.get("task_id")
            source_task_id = None
            try:
                source_task_id = int(task_id_raw) if task_id_raw not in (None, "") else None
            except (TypeError, ValueError):
                source_task_id = None
            title = _clip(payload.get("task_subject") or payload.get("title") or "task completed")
            result = _clip(payload.get("result") or payload.get("summary") or "")
            agent_task_row = await upsert_agent_task(
                conn,
                AgentTaskRecord(
                    agent=agent,
                    title=title,
                    description=_clip(payload.get("summary") or payload.get("result") or ""),
                    status="completed",
                    source="working_state_hook",
                    source_ref=f"task_id:{task_id_raw}" if task_id_raw else None,
                    evidence={
                        "working_state_event_id": event_id,
                        "hook_reason": decision.reason,
                        "result": result,
                    },
                ),
            )
            await ensure_schema(conn)
            work_ledger_row = await upsert_work_item(
                conn,
                WorkItem(
                    agent=agent,
                    title=title,
                    description=result,
                    status="completed",
                    source="working_state_hook",
                    source_ref=f"task_id:{task_id_raw}" if task_id_raw else None,
                    source_task_id=source_task_id,
                    evidence={
                        "working_state_event_id": event_id,
                        "hook_reason": decision.reason,
                        "result": result,
                    },
                ),
            )
        projection = await project_agent(conn, agent, limit=20)
        if apply:
            await apply_projection(conn, projection)
        return {
            "recorded": True,
            "reason": decision.reason,
            "event_id": event_id,
            "agent_task_id": agent_task_row["id"] if agent_task_row else None,
            "work_ledger_id": work_ledger_row["id"] if work_ledger_row else None,
            "projection": asdict(projection),
        }
    finally:
        await conn.close()


def hook_output(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("recorded"):
        return {}
    projection = result.get("projection") or {}
    return {
        "hookSpecificOutput": {
            # Claude Code validates this discriminator against the event that
            # invoked the hook. "WorkingStateJournal" is our internal journal
            # name, not a valid hook event, and caused a fleet-wide
            # `PostToolUse:Bash ... Invalid input` after every recorded event.
            "hookEventName": "PostToolUse",
            "additionalContext": (
                "[SEAL Working State] "
                f"event_id={result.get('event_id')} "
                f"score={projection.get('score')} "
                f"next_step={projection.get('next_step')}"
            ),
        }
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest hook payload into working_state_journal")
    parser.add_argument("--agent", default="")
    parser.add_argument("--temporary", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true", help="Run even when SEAL_WORKING_STATE_HOOK is not enabled")
    parser.add_argument("--cleanup-temp-agent", default="")
    return parser


async def main_async(args: argparse.Namespace) -> int:
    if args.cleanup_temp_agent:
        conn = await connect_db()
        try:
            deleted = await cleanup_temporary_events(conn, args.cleanup_temp_agent)
            print(json.dumps({"cleanup_deleted": deleted}, sort_keys=True))
        finally:
            await conn.close()
        return 0

    enabled = os.environ.get("SEAL_WORKING_STATE_HOOK", "").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled and not args.force:
        print(json.dumps({}))
        return 0

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        print(json.dumps({}))
        return 0

    agent = args.agent or detect_agent()
    if not agent:
        # Identidad declarada y fuera del roster: NO escribir a nombre de otro.
        # stdout queda vacio (hook_output vacio) para no alterar el contrato del
        # hook; el motivo va a stderr, que es donde se lee sin ensuciar la sesion.
        print(json.dumps({}))
        print(
            f"working_state: SEAL_AGENT={os.environ.get('SEAL_AGENT','')!r} "
            "fuera del roster — no escribo journal (fail-closed)",
            file=sys.stderr,
        )
        return 0
    agent = agent.upper()
    try:
        result = await ingest_hook_payload(payload, agent=agent, temporary=args.temporary, apply=args.apply)
    except Exception:
        print(json.dumps({}))
        return 0
    print(json.dumps(hook_output(result), sort_keys=True))
    return 0


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    sys.exit(main())
