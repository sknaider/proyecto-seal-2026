#!/usr/bin/env python3
"""Shared write-path guard for new SOUL memory importance.

This module is intentionally small and dependency-light so non-daemon writers
can share the same admission-time importance rules before the MCP is migrated
in a controlled restart window.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agent_rubric import load_agent_rubric
from memory_admission import memory_auto_event_skip_reason


CHAT_EXCERPT_RE = re.compile(r"^\[[A-ZÁÉÍÓÚÑ]+\]:")
TOKEN_RE = re.compile(r"[a-zA-ZáéíóúÁÉÍÓÚñÑ0-9]+")
WILLIAM_DIRECTIVE_RE = re.compile(
    r"\bWilliam\s+(autoriz[oó]|orden[oó]|dijo|firm[oó]|confirma|confirm[oó])\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ImportanceGuardResult:
    category: str
    content: str
    importance: int
    metadata_patch: dict[str, Any] | None = None


def _token_count(text: str) -> int:
    return len(TOKEN_RE.findall(text or ""))


def _bounded_importance(value: object, default: int = 5) -> int:
    try:
        return max(1, min(10, int(value)))
    except Exception:
        return default


def normalize_memory_importance_for_write(
    *,
    agent: str,
    category: str,
    content: str,
    requested_importance: int,
    source: str = "conversation",
    metadata: dict[str, Any] | None = None,
    memory_type: str | None = None,
) -> ImportanceGuardResult:
    """Normalize importance for a memory before inserting it.

    This is an admission-time guard. It does not use recall_count because new
    memories have not yet proven usage. Post-hoc recalibration remains a
    separate audited operation.
    """

    category = str(category or "fact")
    content = str(content or "").strip()
    source = str(source or "conversation")
    original_importance = _bounded_importance(requested_importance)
    importance = original_importance
    metadata = dict(metadata or {})
    reasons: list[str] = []

    rubric = load_agent_rubric(agent)
    william_directive = bool(WILLIAM_DIRECTIVE_RE.search(content))
    forced = metadata.get("force_memory") is True

    if (
        CHAT_EXCERPT_RE.match(content)
        and category not in rubric.chat_excerpt_override_categories
        and not william_directive
        and not forced
    ):
        capped = min(importance, rubric.chat_excerpt_importance_cap)
        if capped != importance:
            reasons.append("chat_excerpt_cap")
        importance = capped

    if (
        importance >= 9
        and len(content) < rubric.minimum_chars_for_importance_9
        and category not in rubric.high_importance_categories
        and not william_directive
        and not forced
    ):
        importance = min(importance, 6)
        reasons.append("short_noncritical_high_importance")

    if (
        importance >= 9
        and _token_count(content) < rubric.short_memory_token_threshold
        and category not in rubric.chat_excerpt_override_categories
        and not william_directive
        and not forced
    ):
        importance = min(importance, 6)
        reasons.append("too_few_tokens_for_high_importance")

    auto_skip_reason = memory_auto_event_skip_reason(
        agent=agent,
        category=category,
        content=content,
        source=source,
        importance=importance,
        metadata=metadata,
    )
    if auto_skip_reason and not forced and not william_directive:
        capped = min(importance, 4)
        if capped != importance:
            reasons.append(f"auto_noise_cap:{auto_skip_reason}")
        importance = capped

    if importance == original_importance and not reasons:
        return ImportanceGuardResult(category=category, content=content, importance=importance)

    patch = {
        "importance_guard": {
            "version": "2026-06-12",
            "agent": str(agent or "").upper(),
            "category": category,
            "memory_type": memory_type,
            "source": source,
            "original_importance": original_importance,
            "normalized_importance": importance,
            "reasons": sorted(set(reasons)),
        }
    }
    return ImportanceGuardResult(
        category=category,
        content=content,
        importance=importance,
        metadata_patch=patch,
    )
