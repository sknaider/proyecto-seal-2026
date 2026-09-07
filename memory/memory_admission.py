"""Shared admission helpers for SOUL memory writes."""
from __future__ import annotations

import re
from typing import Any

_WILLIAM_DIRECTIVE_RE = re.compile(
    r"\bWilliam\s+(autoriz[oó]|orden[oó]|dijo|firm[oó]|confirma|confirm[oó])\b",
    re.IGNORECASE,
)

_AUTO_EVENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "heartbeat_status",
        re.compile(
            r"(\bheartbeat\b|\[HB\]|\balive\s*[—-]\s*runtime\b|"
            r"\balive\s*=\s*(true|false)\b|\bbeat written\b|\bsigo vivo\b|"
            r"\bGPU\s+\d+\s*C\b|"
            r"\b(system_alive|auto_restart)\b|"
            r"\b(ADA|JARVIS|ALICE|NEXUS|DUM)\s+(true|false)\s*[—-]\s*runtime\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "local_dependency_path",
        re.compile(
            r"(/\.venv/|/venv/|/site-packages/|/__pycache__/|/node_modules/|"
            r"\.venv[/\\]|site-packages[/\\]|__pycache__[/\\]|node_modules[/\\])",
            re.IGNORECASE,
        ),
    ),
    (
        "technical_mode_timer",
        re.compile(
            r"\b\d+\s*min\s+in\s+technical-pure\s+mode\b",
            re.IGNORECASE,
        ),
    ),
    (
        "shell_command_echo",
        re.compile(
            r"^\s*(bash|systemctl|curl|cat|ps\s+aux)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "monitor_checkpoint_noise",
        re.compile(
            r"(Checkpoint\s+.*\bguardado\b|Monitor started|ws_listener|monitor_connect|"
            r"\b(ada|alice|jarvis|nexus|dum)_heartbeat\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "short_ack",
        re.compile(
            r"^\s*(ok|ack|recibido|confirmado|entendido|listo|done)\s*[.!]*\s*$",
            re.IGNORECASE,
        ),
    ),
)


def memory_auto_event_skip_reason(
    *,
    agent: str,
    category: str,
    content: str,
    source: str = "conversation",
    importance: int = 5,
    metadata: dict[str, Any] | None = None,
) -> str | None:
    """Return reason if a low-value auto-event should not enter memories.

    This is intentionally conservative:
    - William directives are never skipped by this filter.
    - High-value semantic categories are preserved.
    - The filter targets recurring liveness/status/path noise that belongs in
      event_log or files, not long-term memory.
    """
    content = content or ""
    category = (category or "").lower()
    source = (source or "").lower()
    importance = max(1, min(10, int(importance or 5)))
    metadata = metadata or {}

    if _WILLIAM_DIRECTIVE_RE.search(content):
        return None
    if category in {"decision", "correction", "trust", "core", "milestone"} and importance >= 8:
        return None
    if metadata.get("force_memory") is True:
        return None

    source_is_auto = source in {
        "heartbeat",
        "system_alive",
        "auto_restart",
        "auto_llm_extract",
        "session_capture",
        "transcript_streamer",
        "auto_stop_hook",
        "conversation",
    }
    for reason, pattern in _AUTO_EVENT_PATTERNS:
        if pattern.search(content):
            if source_is_auto or category in {"fact", "dynamic", "conversation_turn", "status", "insight"}:
                return reason
    return None


def memory_skip_audit_record(
    *,
    category: str,
    content: str,
    source: str,
    importance: int,
    reason: str,
) -> tuple[str, str]:
    """Build the standard event_log content + metadata for skipped memories."""
    event_content = f"memory_store skipped auto-event: {reason}"
    metadata = {
        "action": "skip",
        "reason": reason,
        "category": category,
        "source": source,
        "importance": max(1, min(10, int(importance or 5))),
        "content_preview": (content or "")[:100],
    }
    import json

    return event_content, json.dumps(metadata, ensure_ascii=False)


async def audit_memory_skip_event(
    conn: Any,
    *,
    agent: str,
    category: str,
    content: str,
    source: str,
    importance: int,
    reason: str,
) -> None:
    """Write a memory_skip event_log row for a skipped automatic memory."""
    event_content, metadata_json = memory_skip_audit_record(
        category=category,
        content=content,
        source=source,
        importance=importance,
        reason=reason,
    )
    await conn.execute(
        """
        INSERT INTO soul_v3.event_log (agent, event_type, content, metadata)
        VALUES ($1, 'memory_skip', $2, $3::jsonb)
        """,
        agent,
        event_content,
        metadata_json,
    )
