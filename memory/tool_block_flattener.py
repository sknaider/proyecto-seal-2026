"""SEAL Tool Block Flattener — Spec v3 §5.4

Converts tool_use/tool_result structured blocks to plain text before LLM aux compression.
Injects synthetic tool_result for orphaned tool_use blocks to prevent API rejection.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FlatTurn:
    role: str
    content: str
    turn_index: float
    original_id: int | None = None
    is_synthetic: bool = False


def _input_summary(input_data: Any, max_chars: int = 150) -> str:
    if not input_data:
        return ""
    try:
        text = json.dumps(input_data, ensure_ascii=False)
    except Exception:
        text = str(input_data)
    if len(text) > max_chars:
        return text[:max_chars] + "…"
    return text


def _content_summary(content: Any, max_chars: int = 300) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    parts.append(f"[tool_result: {_content_summary(block.get('content', ''), 200)}]")
                else:
                    parts.append(str(block.get("text") or block.get("content") or ""))
            else:
                parts.append(str(block))
        text = " ".join(p for p in parts if p)
    elif isinstance(content, dict):
        text = str(content.get("text") or content.get("content") or content)
    else:
        text = str(content)
    text = text.strip()
    if len(text) > max_chars:
        return text[:max_chars] + "…"
    return text


def flatten_tool_blocks(turns: list[dict]) -> list[FlatTurn]:
    """
    Convert session_turns rows (dicts with role/content/turn_index/id) to FlatTurn list.
    tool_use → '[tool_call: name(input)]'
    tool_result → '[tool_result: name → output]'
    Orphaned tool_use → synthetic pairing appended.
    """
    flat: list[FlatTurn] = []
    pending_tool_uses: dict[str, tuple[str, float, int | None]] = {}

    for turn in turns:
        role = turn.get("role", "")
        content = turn.get("content", "")
        idx = float(turn.get("turn_index", 0))
        tid = turn.get("id")

        if role == "assistant":
            tool_calls = _extract_tool_uses(content)
            if tool_calls:
                for tc in tool_calls:
                    use_id = tc.get("id", f"orphan_{idx}_{tc['name']}")
                    summary = f"[tool_call: {tc['name']}({_input_summary(tc.get('input'))})]"
                    flat.append(FlatTurn(role="assistant", content=summary, turn_index=idx, original_id=tid))
                    pending_tool_uses[use_id] = (tc["name"], idx, tid)
            else:
                text = _content_summary(content, max_chars=500)
                flat.append(FlatTurn(role="assistant", content=text, turn_index=idx, original_id=tid))

        elif role == "tool":
            use_id = _extract_tool_use_id(content)
            tool_name = _extract_tool_name(content) or "tool"
            pending_tool_uses.pop(use_id, None)
            result_text = f"[tool_result: {tool_name} → {_content_summary(content, 300)}]"
            flat.append(FlatTurn(role="tool_result", content=result_text, turn_index=idx + 0.1, original_id=tid))

        else:
            text = _content_summary(content, max_chars=500)
            flat.append(FlatTurn(role=role, content=text, turn_index=idx, original_id=tid))

    for use_id, (tool_name, orig_idx, orig_tid) in pending_tool_uses.items():
        flat.append(FlatTurn(
            role="tool_result",
            content=f"[tool_result: synthetic — {tool_name} output truncated]",
            turn_index=orig_idx + 0.5,
            original_id=orig_tid,
            is_synthetic=True,
        ))

    flat.sort(key=lambda t: t.turn_index)
    return flat


def _extract_tool_uses(content: Any) -> list[dict]:
    if isinstance(content, list):
        return [
            {"id": b.get("id", ""), "name": b.get("name", "tool"), "input": b.get("input")}
            for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                return _extract_tool_uses(parsed)
        except Exception:
            pass
    return []


def _extract_tool_use_id(content: Any) -> str:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except Exception:
            return ""
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                return b.get("tool_use_id", "")
    if isinstance(content, dict):
        return content.get("tool_use_id", "")
    return ""


def _extract_tool_name(content: Any) -> str | None:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except Exception:
            return None
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                return b.get("tool_name") or b.get("name")
    if isinstance(content, dict):
        return content.get("tool_name") or content.get("name")
    return None


def flat_turns_to_text(flat_turns: list[FlatTurn]) -> str:
    lines = []
    for t in flat_turns:
        prefix = t.role.upper()
        lines.append(f"[{prefix} turn {t.turn_index:.0f}] {t.content}")
    return "\n".join(lines)
