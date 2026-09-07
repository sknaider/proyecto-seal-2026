#!/usr/bin/env python3
"""Select the visible primary Codex TUI rollout, never a subagent/app-server.

Codex stores the primary thread and forked subagents below the same sessions
tree. Selecting by mtime alone lets a fresh subagent steal the terminal relay.
The first ``session_meta`` record is authoritative for the file's own origin;
later copied metadata from the parent must not override it.
"""

from __future__ import annotations

import json
from pathlib import Path


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
    source = meta.get("source")
    return (
        meta.get("originator") == "codex-tui"
        and meta.get("thread_source") == "user"
        and source == "cli"
        and not meta.get("forked_from_id")
        and not meta.get("agent_path")
    )


def newest_primary_tui_session(sessions_dir: Path) -> Path | None:
    candidates = [
        path for path in sessions_dir.rglob("*.jsonl")
        if is_primary_tui_session(path)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)

