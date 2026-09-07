#!/usr/bin/env python3
"""Deterministic SEAL reflex layer.

This module handles small, high-confidence events without an LLM. It does not
execute side effects; it returns the safe action plan that a daemon/bridge can
apply or escalate.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any


TRUSTED_HUMANS = {"william", "henry", "kinger"}
TEAM_AGENTS = {"ada", "jarvis", "alice", "nexus", "dum"}
ADA_PATTERN = re.compile(r"(?<![a-z0-9_])@?ada(?![a-z0-9_])", re.IGNORECASE)
DESTRUCTIVE_PATTERNS = [
    re.compile(r"\brm\s+-[^\n;]*r[^\n;]*f\b", re.IGNORECASE),
    re.compile(r"\bdrop\s+(database|schema|table)\b", re.IGNORECASE),
    re.compile(r"\btruncate\s+table\b", re.IGNORECASE),
    re.compile(r"\bdelete\s+from\b(?![^;]*\bwhere\b)", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bupdate\s+\S+\s+set\b(?![^;]*\bwhere\b)", re.IGNORECASE | re.DOTALL),
]


@dataclass(frozen=True)
class ReflexEvent:
    event_type: str
    content: str = ""
    sender: str = ""
    channel: str = "web_chat"
    target: str = "ADA"
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReflexDecision:
    event_type: str
    should_wake: bool
    should_respond: bool
    response_channel: str | None
    actions: list[str]
    risk_level: str
    reason: str
    requires_confirmation: bool = False
    blocked: bool = False


def mentions_ada(content: str) -> bool:
    return bool(ADA_PATTERN.search(content))


def is_destructive_command(content: str) -> bool:
    return any(pattern.search(content) for pattern in DESTRUCTIVE_PATTERNS)


def _sender_key(sender: str) -> str:
    return sender.strip().lower()


def evaluate_event(event: ReflexEvent) -> ReflexDecision:
    event_type = event.event_type.strip().lower()
    channel = event.channel.strip().lower()
    sender = _sender_key(event.sender)
    content = event.content or ""
    metadata = event.metadata or {}

    if event_type == "privacy_boundary" or (channel.startswith("dm:") and channel != "dm:ada:william"):
        return ReflexDecision(
            event_type=event_type,
            should_wake=False,
            should_respond=False,
            response_channel=None,
            actions=["deny_access", "log_privacy_boundary"],
            risk_level="high",
            reason="ADA must not read or answer other agents' DMs",
            blocked=True,
        )

    if event_type in {"command", "shell_command"} and is_destructive_command(content):
        return ReflexDecision(
            event_type=event_type,
            should_wake=True,
            should_respond=True,
            response_channel="dm:ada:william" if sender == "william" else channel,
            actions=["block_execution", "show_count_scope", "request_explicit_william_confirmation"],
            risk_level="critical",
            reason="destructive command requires COUNT + scope + explicit William approval",
            requires_confirmation=True,
            blocked=True,
        )

    if channel == "dm:ada:william":
        return ReflexDecision(
            event_type=event_type,
            should_wake=True,
            should_respond=True,
            response_channel="dm:ada:william",
            actions=["ack_william", "answer_private"],
            risk_level="low",
            reason="private William DM always wakes ADA",
        )

    if event_type == "web_chat":
        explicit_ada = mentions_ada(content)
        trusted_sender = sender in TRUSTED_HUMANS
        should_respond = explicit_ada and trusted_sender
        should_wake = explicit_ada or channel.startswith("dm:")
        return ReflexDecision(
            event_type=event_type,
            should_wake=should_wake,
            should_respond=should_respond,
            response_channel="web_chat" if should_respond else None,
            actions=["ack_william"] if should_respond and sender == "william" else ["silent"],
            risk_level="low",
            reason="public web_chat requires explicit ADA mention from William/Henry",
        )

    if event_type == "test_failure":
        return ReflexDecision(
            event_type=event_type,
            should_wake=True,
            should_respond=False,
            response_channel=None,
            actions=["capture_failure", "rerun_targeted_test", "update_working_state_blocked"],
            risk_level="medium",
            reason="failed tests block victory claims until fixed",
        )

    if event_type == "service_status" and metadata.get("state") not in {None, "active", "ok", "healthy"}:
        service = str(metadata.get("service", "unknown"))
        return ReflexDecision(
            event_type=event_type,
            should_wake=True,
            should_respond=False,
            response_channel=None,
            actions=["capture_service_state", "run_health_check", "alert_dum", f"service:{service}"],
            risk_level="high",
            reason="service is not healthy",
        )

    if event_type == "memory_contradiction":
        return ReflexDecision(
            event_type=event_type,
            should_wake=True,
            should_respond=False,
            response_channel=None,
            actions=["active_recall", "mark_conflict", "request_nexus_review"],
            risk_level="medium",
            reason="contradictory memory needs evidence before overwrite",
        )

    return ReflexDecision(
        event_type=event_type,
        should_wake=False,
        should_respond=False,
        response_channel=None,
        actions=["no_op"],
        risk_level="low",
        reason="no deterministic reflex matched",
    )


def evaluate_payload(payload: dict[str, Any]) -> ReflexDecision:
    return evaluate_event(
        ReflexEvent(
            event_type=str(payload.get("event_type", "unknown")),
            content=str(payload.get("content", "")),
            sender=str(payload.get("sender", "")),
            channel=str(payload.get("channel", "web_chat")),
            target=str(payload.get("target", "ADA")),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL deterministic reflex layer")
    sub = parser.add_subparsers(dest="command", required=True)
    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--json", required=True, help="Event payload as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "evaluate":
        decision = evaluate_payload(json.loads(args.json))
        print(json.dumps(asdict(decision), indent=2, ensure_ascii=False))
        return 0 if not decision.blocked else 2
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
