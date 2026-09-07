#!/usr/bin/env python3
"""Select the visible primary Codex TUI rollout, never a subagent/app-server.

Codex stores the primary thread and forked subagents below the same sessions
tree. Selecting by mtime alone lets a fresh subagent steal the terminal relay.
The first ``session_meta`` record is authoritative for the file's own origin;
later copied metadata from the parent must not override it.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path


def rollout_event_payload(record: dict) -> dict:
    """Normalize legacy/new TUI message events, never tool output or reasoning."""
    payload = record.get("payload")
    if record.get("type") != "event_msg" or not isinstance(payload, dict):
        return {}
    if payload.get("type") != "item_completed":
        return payload
    item = payload.get("item")
    if not isinstance(item, dict):
        return {}
    kind = {"UserMessage": "user_message", "AgentMessage": "agent_message"}.get(item.get("type"))
    if not kind:
        return {}
    content = item.get("content")
    if not isinstance(content, list):
        return {}
    text = "\n".join(part["text"] for part in content if isinstance(part, dict)
                     and part.get("type") in {"text", "Text"} and isinstance(part.get("text"), str))
    return {"type": kind, "message": text, "phase": item.get("phase"), "turn_id": payload.get("turn_id")}


def first_session_meta(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(4):
                line = handle.readline()
                if not line:
                    break
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if record.get("type") == "session_meta":
                    payload = record.get("payload")
                    return payload if isinstance(payload, dict) else {}
    except OSError:
        return {}
    return {}


def is_primary_tui_session(path: Path) -> bool:
    meta = first_session_meta(path)
    return _is_primary_meta(meta)


def _is_primary_meta(meta: dict) -> bool:
    source = meta.get("source")
    return (
        meta.get("originator") == "codex-tui"
        and meta.get("thread_source") == "user"
        and source == "cli"
        and not meta.get("forked_from_id")
        and not meta.get("agent_path")
    )


def _validate_live_handle(handle) -> None:
    """Bind an open inode to one explicitly configured, still-live ADA TUI.

    A restored home can lose the rollout pathname while Codex keeps its inode
    open. Never select a Desktop thread to compensate, or trust a reused PID/fd.
    The opened handle is validated before reading any conversation events.
    """
    source = os.environ.get("ADA_CODEX_LIVE_TUI_ROLLOUT", "")
    match = re.fullmatch(r"/proc/([1-9][0-9]*)/fd/([0-9]+)", source)
    if not match:
        raise OSError("invalid_live_tui_binding")
    process = Path("/proc") / match[1]
    fields = (process / "stat").read_text().rsplit(") ", 1)[1].split()
    info = os.fstat(handle.fileno())
    if (
        fields[19] != os.environ.get("ADA_CODEX_LIVE_TUI_START_TICKS")
        or str(info.st_ino) != os.environ.get("ADA_CODEX_LIVE_TUI_INODE")
        or info.st_uid != os.getuid()
        or (process / "comm").read_text().strip() != "codex"
    ):
        raise OSError("live_tui_process_changed")
    offset = handle.tell()
    try:
        handle.seek(0)
        record = json.loads(handle.readline())
        if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
            raise OSError("invalid_live_tui_metadata")
        meta = record["payload"]
        if (record.get("type") != "session_meta" or not _is_primary_meta(meta)
            or meta.get("id") != os.environ.get("ADA_CODEX_LIVE_TUI_THREAD_ID")):
            raise OSError("live_tui_identity_changed")
    finally:
        handle.seek(offset)


@contextmanager
def open_rollout(path: Path, mode="r", **kwargs):
    """Validate the actual opened fd; pathname checks alone have a reuse race."""
    with path.open(mode, **kwargs) as handle:
        if str(path).startswith("/proc/"):
            if str(path) != os.environ.get("ADA_CODEX_LIVE_TUI_ROLLOUT"):
                raise OSError("unbound_proc_rollout")
            try:
                _validate_live_handle(handle)
            except (ValueError, IndexError, KeyError) as exc:
                raise OSError("invalid_live_tui_metadata") from exc
        yield handle


def pinned_tui_rollout() -> Path | None:
    source = os.environ.get("ADA_CODEX_LIVE_TUI_ROLLOUT")
    if not source:
        return None
    if not re.fullmatch(r"/proc/[1-9][0-9]*/fd/[0-9]+", source):
        return None
    path = Path(source)
    try:
        with open_rollout(path, "rb"):
            return path
    except OSError:
        return None


def newest_primary_tui_session(sessions_dir: Path) -> Path | None:
    if os.environ.get("ADA_CODEX_LIVE_TUI_ROLLOUT"):
        # A lost pinned TUI must not transfer its DM to an unrelated session.
        return pinned_tui_rollout()
    candidates = [
        path for path in sessions_dir.rglob("*.jsonl")
        if is_primary_tui_session(path)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)
