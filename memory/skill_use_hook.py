#!/usr/bin/env python3
"""Record real native skill invocations in ``soul_v3.skill_use_log``.

Claude Code exposes skill execution as the built-in ``Skill`` tool.  Codex
loads filesystem-backed skills by reading ``SKILL.md``.  This hook accepts both
signals, records only skills that already belong to the agent/TEAM registry,
and deduplicates hook retries by session/tool-use id.

The hook is observational and fail-open: telemetry must never interrupt an
agent turn.  Failures and unresolved skills remain visible in a restricted
JSONL diagnostic log rather than being silently discarded.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parent))

from seal_secrets import pg_dsn
from soul_event_recorders import record_skill_use


KNOWN_AGENTS = frozenset({"ADA", "ALICE", "JARVIS", "NEXUS", "DUM", "FABLE"})
HOOK_VERSION = "20260828.2"
SESSION_EVENTS = frozenset({"SessionStart"})
SUCCESS_EVENTS = frozenset({"PostToolUse", "PostTool"})
FAILURE_EVENTS = frozenset({"PostToolUseFailure", "PostToolFailure"})
EXPANSION_EVENTS = frozenset({"UserPromptExpansion"})
TRANSCRIPT_EVENTS = frozenset({"Stop", "SessionEnd"})
READ_TOOLS = frozenset({"Read", "read_file", "view_file"})
BASH_TOOLS = frozenset({"Bash", "exec_command", "functions.exec_command"})
READ_COMMANDS = frozenset({"cat", "sed", "head", "tail", "bat"})
ERROR_LOG = Path(
    os.environ.get(
        "SEAL_SKILL_USE_HOOK_ERROR_LOG",
        str(Path.home() / ".local/state/seal/skill_use_hook_errors.jsonl"),
    )
)


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _event(payload: dict[str, Any]) -> str:
    return _text(
        payload.get("hook_event_name")
        or payload.get("hookEventName")
        or payload.get("event")
    )


def _tool(payload: dict[str, Any]) -> str:
    return _text(payload.get("tool_name") or payload.get("toolName") or payload.get("tool"))


def _tool_input(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("tool_input") or payload.get("toolInput") or payload.get("input") or {}
    return value if isinstance(value, dict) else {}


def resolve_agent(payload: dict[str, Any]) -> str | None:
    candidates: Iterable[Any] = (
        os.environ.get("SEAL_AGENT"),
        os.environ.get("CLAUDE_AGENT"),
        payload.get("agent"),
        payload.get("agent_name"),
    )
    for candidate in candidates:
        name = _text(candidate).strip().upper()
        if name in KNOWN_AGENTS:
            return name

    transcript = _text(payload.get("transcript_path") or payload.get("transcriptPath")).lower()
    for name in KNOWN_AGENTS:
        if re.search(rf"(?:^|[-_/]){name.lower()}(?:[-_/]|$)", transcript):
            return name
    return None


def resolve_runtime(payload: dict[str, Any]) -> str | None:
    runtime = _text(
        os.environ.get("SEAL_SKILL_RUNTIME")
        or payload.get("skill_runtime")
        or payload.get("runtime")
    ).strip().lower()
    return runtime if runtime in {"claude_code", "codex", "local_gemma"} else None


def _skill_path(value: Any, cwd: str | None = None) -> Path | None:
    raw = _text(value).strip().strip("'\"")
    if not raw or Path(raw).name != "SKILL.md":
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute() and cwd:
        path = Path(cwd) / path
    try:
        return path.resolve(strict=False)
    except OSError:
        return path.absolute()


def _bash_skill_paths(command: str, cwd: str | None) -> list[Path]:
    """Extract only SKILL.md paths read by known read-only commands.

    Merely mentioning a path in ``rg``, ``find`` or a test command is not an
    invocation and must not increment usage.
    """

    paths: list[Path] = []
    for segment in re.split(r"\s*(?:&&|\|\||;|\n)\s*", command):
        if not segment.strip():
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            continue
        while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0]):
            tokens.pop(0)
        if not tokens or Path(tokens[0]).name not in READ_COMMANDS:
            continue
        for token in tokens[1:]:
            path = _skill_path(token, cwd)
            if path is not None:
                paths.append(path)
    return paths


def _transcript_skill_failures(payload: dict[str, Any]) -> list[dict[str, Any]]:
    transcript = Path(_text(payload.get("transcript_path") or payload.get("transcriptPath")))
    if not transcript.is_file():
        return []
    uses: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    try:
        lines = transcript.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except (TypeError, json.JSONDecodeError):
            continue
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use" and item.get("name") == "Skill":
                tool_input = item.get("input") if isinstance(item.get("input"), dict) else {}
                name = _text(tool_input.get("skill")).strip().lstrip("/")
                if name and _text(item.get("id")):
                    uses[_text(item["id"])] = {
                        "name": name,
                        "timestamp": _text(row.get("timestamp")),
                    }
            if item.get("type") != "tool_result" or not item.get("is_error"):
                continue
            tool_use_id = _text(item.get("tool_use_id"))
            use = uses.get(tool_use_id)
            if use is None:
                continue
            duration_ms = None
            try:
                started = datetime.fromisoformat(use["timestamp"].replace("Z", "+00:00"))
                ended = datetime.fromisoformat(_text(row.get("timestamp")).replace("Z", "+00:00"))
                duration_ms = max(0, round((ended - started).total_seconds() * 1000))
            except (TypeError, ValueError):
                pass
            failures.append(
                {
                    "name": use["name"],
                    "path": None,
                    "source": "transcript_skill_failure",
                    "success": False,
                    "error": _text(item.get("content"))[:2000] or "skill load failed",
                    "duration_ms": duration_ms,
                    "tool_use_id": tool_use_id,
                }
            )
    return failures


def extract_invocations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if _event(payload) in TRANSCRIPT_EVENTS:
        return _transcript_skill_failures(payload)
    if _event(payload) in EXPANSION_EVENTS:
        if _text(payload.get("expansion_type")) != "slash_command":
            return []
        name = _text(payload.get("command_name")).strip().lstrip("/")
        if not name:
            return []
        return [{"name": name, "path": None, "source": "user_prompt_expansion"}]

    tool = _tool(payload)
    tool_input = _tool_input(payload)
    cwd = _text(payload.get("cwd")) or None

    if tool == "Skill":
        name = _text(
            tool_input.get("skill")
            or tool_input.get("skill_name")
            or tool_input.get("name")
            or tool_input.get("command")
        ).strip().lstrip("/")
        if not name:
            return []
        return [{"name": name.split()[0], "path": None, "source": "native_skill_tool"}]

    if tool in READ_TOOLS:
        path = _skill_path(tool_input.get("file_path") or tool_input.get("path"), cwd)
        if path is None:
            return []
        return [{"name": path.parent.name, "path": path, "source": "skill_file_read"}]

    if tool in BASH_TOOLS:
        command = _text(tool_input.get("command") or tool_input.get("cmd"))
        return [
            {"name": path.parent.name, "path": path, "source": "skill_file_read"}
            for path in _bash_skill_paths(command, cwd)
        ]
    return []


def event_key(payload: dict[str, Any], skill_name: str, path: Path | None) -> str:
    tool_use_id = _text(payload.get("tool_use_id") or payload.get("toolUseId"))
    expansion_position = ""
    if not tool_use_id and _event(payload) in EXPANSION_EVENTS:
        transcript = Path(_text(payload.get("transcript_path") or payload.get("transcriptPath")))
        try:
            expansion_position = f"{transcript}:{transcript.stat().st_size}"
        except OSError:
            expansion_position = _text(payload.get("prompt"))
    stable = "|".join(
        (
            _text(payload.get("session_id") or payload.get("sessionId")),
            tool_use_id or expansion_position,
            _event(payload),
            skill_name,
            str(path or ""),
        )
    )
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def _name_candidates(name: str) -> list[str]:
    candidates = [name]
    if ":" in name:
        candidates.append(name.rsplit(":", 1)[-1])
    return list(dict.fromkeys(candidate.strip().lstrip("/") for candidate in candidates if candidate.strip()))


async def _resolve_skill_id(
    conn: asyncpg.Connection,
    *,
    agent: str,
    name: str,
    path: Path | None,
) -> int | None:
    paths: list[str] = []
    if path is not None:
        paths.append(str(path))
        try:
            paths.append(str(path.resolve(strict=True)))
        except OSError:
            pass
    return await conn.fetchval(
        """
        SELECT id
        FROM soul_v3.skills
        WHERE invalid_at IS NULL
          AND (agent = $1 OR agent = 'TEAM')
          AND (name = ANY($2::text[]) OR skill_path = ANY($3::text[]))
        ORDER BY
          CASE WHEN skill_path = ANY($3::text[]) THEN 0 ELSE 1 END,
          CASE WHEN agent = $1 THEN 0 ELSE 1 END,
          id DESC
        LIMIT 1
        """,
        agent,
        _name_candidates(name),
        list(dict.fromkeys(paths)),
    )


async def record_payload(
    conn: asyncpg.Connection,
    payload: dict[str, Any],
    *,
    agent: str,
) -> list[dict[str, Any]]:
    event = _event(payload)
    if event not in SUCCESS_EVENTS | FAILURE_EVENTS | EXPANSION_EVENTS | TRANSCRIPT_EVENTS:
        return []
    recorded: list[dict[str, Any]] = []
    for invocation in extract_invocations(payload):
        path = invocation["path"]
        invocation_payload = dict(payload)
        if invocation.get("tool_use_id"):
            invocation_payload["tool_use_id"] = invocation["tool_use_id"]
        key = event_key(invocation_payload, invocation["name"], path)
        success = bool(
            invocation.get("success", event in SUCCESS_EVENTS | EXPANSION_EVENTS)
        )
        async with conn.transaction():
            # Hook replays/resumes must not double-count the same tool use.
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", key)
            existing = await conn.fetchrow(
                """
                SELECT id, skill_id, agent, success, error, duration_ms, created_at
                FROM soul_v3.skill_use_log
                WHERE params->>'event_key' = $1
                LIMIT 1
                """,
                key,
            )
            if existing is not None:
                recorded.append({**dict(existing), "deduplicated": True})
                continue

            skill_id = await _resolve_skill_id(
                conn,
                agent=agent,
                name=invocation["name"],
                path=path,
            )
            if skill_id is None:
                recorded.append(
                    {
                        "unresolved": True,
                        "agent": agent,
                        "skill_name": invocation["name"],
                        "skill_path": str(path or ""),
                        "event_key": key,
                    }
                )
                continue

            params = {
                "event_key": key,
                "session_id": _text(payload.get("session_id") or payload.get("sessionId")),
                "tool_use_id": _text(
                    invocation.get("tool_use_id")
                    or payload.get("tool_use_id")
                    or payload.get("toolUseId")
                ),
                "telemetry_source": invocation["source"],
                "telemetry_runtime": resolve_runtime(payload) or "unknown",
                "skill_path": str(path or ""),
                "semantics": "skill_invocation",
                "outcome_scope": "activation_or_load",
            }
            row = await record_skill_use(
                conn,
                agent=agent,
                skill_id=skill_id,
                params=params,
                success=success,
                error=(
                    None
                    if success
                    else _text(invocation.get("error") or payload.get("error"))[:2000]
                    or "skill tool failed"
                ),
                duration_ms=(
                    int(invocation.get("duration_ms", payload.get("duration_ms")))
                    if isinstance(
                        invocation.get("duration_ms", payload.get("duration_ms")),
                        (int, float),
                    )
                    else None
                ),
            )
            recorded.append({**row, "deduplicated": False})
    return recorded


async def record_coverage(
    conn: asyncpg.Connection,
    payload: dict[str, Any],
    *,
    agent: str,
    runtime: str,
) -> dict[str, Any]:
    """Mark the first session that can actually emit skill telemetry.

    A missing usage row is only a measured zero after this marker exists.
    SessionStart is used because inferring coverage from the first skill call
    would leave the pre-first-call interval ambiguous.
    """
    session_id = _text(payload.get("session_id") or payload.get("sessionId")).strip()
    if not session_id:
        raise ValueError("SessionStart coverage requires a non-empty session_id")
    row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.skill_telemetry_coverage
            (agent, runtime, session_id, hook_version, evidence_source, metadata)
        VALUES ($1, $2, $3, $4, 'session_start_hook', $5::jsonb)
        ON CONFLICT (agent, runtime, session_id) DO UPDATE SET
            last_seen_at = NOW(),
            hook_version = EXCLUDED.hook_version,
            metadata = soul_v3.skill_telemetry_coverage.metadata || EXCLUDED.metadata
        RETURNING agent, runtime, session_id, coverage_started_at, last_seen_at
        """,
        agent,
        runtime,
        session_id,
        HOOK_VERSION,
        json.dumps(
            {
                "hook_event": _event(payload),
                "source": _text(payload.get("source")),
            },
            sort_keys=True,
        ),
    )
    return dict(row)


def _diagnostic(kind: str, payload: dict[str, Any], detail: Any) -> None:
    try:
        ERROR_LOG.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "event": _event(payload),
            "tool": _tool(payload),
            "session_id": _text(payload.get("session_id") or payload.get("sessionId")),
            "detail": detail,
        }
        with ERROR_LOG.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        ERROR_LOG.chmod(0o600)
    except Exception:
        pass


async def main_async(payload: dict[str, Any]) -> None:
    if _event(payload) in SESSION_EVENTS:
        agent = resolve_agent(payload)
        runtime = resolve_runtime(payload)
        if agent is None or runtime is None:
            _diagnostic(
                "unresolved_coverage_identity",
                payload,
                {"agent": agent, "runtime": runtime},
            )
            return
        conn = await asyncpg.connect(pg_dsn(required=True))
        try:
            await record_coverage(conn, payload, agent=agent, runtime=runtime)
        finally:
            await conn.close()
        return

    invocations = extract_invocations(payload)
    if not invocations:
        return
    agent = resolve_agent(payload)
    if agent is None:
        _diagnostic("unresolved_agent", payload, {"skills": [item["name"] for item in invocations]})
        return
    conn = await asyncpg.connect(pg_dsn(required=True))
    try:
        rows = await record_payload(conn, payload, agent=agent)
        unresolved = [row for row in rows if row.get("unresolved")]
        if unresolved:
            _diagnostic("unresolved_skill", payload, unresolved)
    finally:
        await conn.close()


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if isinstance(payload, dict):
            asyncio.run(main_async(payload))
    except Exception as exc:
        _diagnostic("hook_failure", {}, f"{type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
