#!/usr/bin/env python3
"""SEAL Capa 4 — Turn Extract Runner (called by turn_extract_stop_hook.py).

Reads SEAL_AGENT and SEAL_TRANSCRIPT from env.
Parses the last assistant turn from the transcript JSONL.
Calls turn_extractor.extract_and_store() with ADD-only fact extraction.

Designed to run as a detached subprocess — non-blocking from Stop hook.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

AGENT = os.environ.get("SEAL_AGENT", "")
TRANSCRIPT = os.environ.get("SEAL_TRANSCRIPT", "")
MAX_CONTENT_CHARS = 3000


def read_last_assistant_turn(jsonl_path: str) -> tuple[str, list[str]]:
    """Extract last assistant turn content + tool names from transcript JSONL.

    Claude Code JSONL format per entry:
      {type: "assistant"|"user"|..., message: {role: ..., content: [...]}, ...}
    Content blocks: {type: "text"|"tool_use"|"thinking", text: ..., name: ...}
    """
    try:
        lines = Path(jsonl_path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return "", []

    text_parts: list[str] = []
    tool_names: list[str] = []
    found_assistant = False

    for line in reversed(lines[-300:]):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue

        entry_type = d.get("type", "")
        msg = d.get("message", {})
        if not isinstance(msg, dict):
            continue
        content = msg.get("content", [])

        if entry_type == "assistant":
            found_assistant = True
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type", "")
                    if btype == "text":
                        text = block.get("text", "")
                        if text:
                            text_parts.append(text)
                    elif btype == "tool_use":
                        name = block.get("name", "")
                        if name:
                            tool_names.append(name)
            elif isinstance(content, str) and content:
                text_parts.append(content)

        elif entry_type == "user" and found_assistant:
            break

    combined = " ".join(reversed(text_parts))[:MAX_CONTENT_CHARS]
    return combined, list(reversed(tool_names))[:15]


async def run() -> None:
    if not AGENT or not TRANSCRIPT:
        return

    turn_content, tool_names = read_last_assistant_turn(TRANSCRIPT)
    if not turn_content:
        return

    from turn_extractor import extract_and_store

    # session_id real de la sesion que disparo el hook (cura H7, 3-sep); fallback al hash del transcript
    session_id = os.environ.get("SEAL_SESSION_ID_HOOK", "").strip() or f"stop-hook-{abs(hash(TRANSCRIPT)) % 999999}"
    await extract_and_store(
        agent=AGENT,
        session_id=session_id,
        turn_index=0,
        turn_content=turn_content,
        tool_names=tool_names,
    )


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception:
        pass
