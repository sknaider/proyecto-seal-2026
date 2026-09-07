#!/usr/bin/env python3
"""Shared types for the SEAL continuous-awareness shadow loop.

These objects are intentionally side-effect free. Fase 1 is a shadow-mode
contract: normalize events, decide attention, and record evidence without
posting, editing files, restarting services, or waking a model.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


TRUSTED_HUMANS = {"william", "henry", "kinger"}
TEAM_AGENTS = {"ada", "jarvis", "alice", "nexus", "dum"}
ALLOWED_ACTIONS = {
    "ignore",
    "store_only",
    "reflex_action",
    "local_reflect",
    "ask_william",
    "wake_codex",
    "wake_claude",
    "wake_nexus",
    "emergency_stop",
}
ALLOWED_BUDGET_CLASSES = {"zero", "cheap", "normal", "deep", "code_heavy", "emergency"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def agent_key(value: str) -> str:
    return value.strip().lower()


def mention_pattern(agent: str) -> re.Pattern[str]:
    escaped = re.escape(agent.strip())
    return re.compile(rf"(?<![a-z0-9_])@?{escaped}(?![a-z0-9_])", re.IGNORECASE)


def mentions_agent(content: str, agent: str) -> bool:
    return bool(mention_pattern(agent).search(content or ""))


def is_dm_channel(channel: str) -> bool:
    return channel.strip().lower().startswith("dm:")


def allowed_dm_channel(agent: str) -> str:
    return f"dm:{agent_key(agent)}:william"


def is_allowed_dm(agent: str, channel: str) -> bool:
    return channel.strip().lower() == allowed_dm_channel(agent)


def is_public_webchat(channel: str) -> bool:
    return channel.strip().lower().startswith("web_chat")


def contains_destructive_command(content: str) -> bool:
    text = content or ""
    patterns = (
        re.compile(r"\brm\s+-[^\n;]*r[^\n;]*f\b", re.IGNORECASE),
        re.compile(r"\bdrop\s+(database|schema|table)\b", re.IGNORECASE),
        re.compile(r"\btruncate\s+table\b", re.IGNORECASE),
        re.compile(r"\bdelete\s+from\b(?![^;]*\bwhere\b)", re.IGNORECASE | re.DOTALL),
        re.compile(r"\bupdate\s+\S+\s+set\b(?![^;]*\bwhere\b)", re.IGNORECASE | re.DOTALL),
    )
    return any(pattern.search(text) for pattern in patterns)


@dataclass(frozen=True)
class AwarenessEvent:
    event_id: str
    agent: str
    source: str
    channel: str
    kind: str
    sender: str = ""
    target: str = ""
    content: str = ""
    priority: str = "normal"
    requires_response: bool = False
    payload_ref: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    observed_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AttentionDecision:
    event_id: str
    agent: str
    action: str
    budget_class: str
    target_runtime: str | None
    target_agent: str | None
    should_respond: bool
    response_channel: str | None
    reason: str
    risk_level: str = "low"
    blocked: bool = False
    requires_confirmation: bool = False
    actions: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.action not in ALLOWED_ACTIONS:
            raise ValueError(f"Unknown awareness action: {self.action}")
        if self.budget_class not in ALLOWED_BUDGET_CLASSES:
            raise ValueError(f"Unknown budget class: {self.budget_class}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
