#!/usr/bin/env python3
"""Deterministic attention governor for continuous-awareness Fase 1.

This governor only returns decisions. It does not publish chat messages, start
models, restart services, or run shell commands.
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

from awareness_collector import normalize_payload, shadow_fixture_events
from awareness_types import (
    AttentionDecision,
    AwarenessEvent,
    TRUSTED_HUMANS,
    agent_key,
    allowed_dm_channel,
    contains_destructive_command,
    is_public_webchat,
)


TECHNICAL_INTENT = re.compile(
    r"\b(implementa|arregla|corrige|revisa|test|pytest|build|commit|spec|archivo|daemon|servicio|bridge|runtime|"
    r"collector|governor|dashboard|modelo|gemma|codex|python|typescript|api|db|sql)\b",
    re.IGNORECASE,
)


def has_technical_intent(content: str) -> bool:
    return bool(TECHNICAL_INTENT.search(content or ""))


def decide_attention(event: AwarenessEvent) -> AttentionDecision:
    sender_key = agent_key(event.sender)
    trusted_sender = sender_key in TRUSTED_HUMANS
    allowed_dm = event.channel == allowed_dm_channel(event.agent)
    public = is_public_webchat(event.channel)
    explicit = bool(event.metadata.get("explicit_agent_mention"))
    technical = has_technical_intent(event.content)
    destructive = bool(event.metadata.get("destructive_command")) or contains_destructive_command(event.content)

    if event.kind == "privacy_boundary" or event.metadata.get("privacy_boundary"):
        return AttentionDecision(
            event_id=event.event_id,
            agent=event.agent,
            action="store_only",
            budget_class="zero",
            target_runtime=None,
            target_agent=event.agent,
            should_respond=False,
            response_channel=None,
            reason="cross-agent DM is redacted and stored only as a privacy boundary",
            risk_level="high",
            blocked=True,
            actions=["log_privacy_boundary"],
            evidence={"redacted": event.metadata.get("redacted") is True},
        )

    if destructive:
        return AttentionDecision(
            event_id=event.event_id,
            agent=event.agent,
            action="reflex_action",
            budget_class="emergency",
            target_runtime="python",
            target_agent=event.agent,
            should_respond=True,
            response_channel=allowed_dm_channel(event.agent) if sender_key == "william" else event.channel,
            reason="destructive operation requires COUNT, scope and explicit William confirmation",
            risk_level="critical",
            blocked=True,
            requires_confirmation=True,
            actions=["block_execution", "show_count_scope", "request_explicit_william_confirmation"],
            evidence={"destructive_command": True},
        )

    if event.kind == "chat_message" and allowed_dm:
        if technical:
            return AttentionDecision(
                event_id=event.event_id,
                agent=event.agent,
                action="wake_codex",
                budget_class="code_heavy",
                target_runtime="codex",
                target_agent=event.agent,
                should_respond=True,
                response_channel=allowed_dm_channel(event.agent),
                reason="William DM with technical intent wakes ADA Codex",
                actions=["ack_william", "active_recall", "start_coding_turn"],
                evidence={"technical_intent": True},
            )
        return AttentionDecision(
            event_id=event.event_id,
            agent=event.agent,
            action="local_reflect",
            budget_class="cheap",
            target_runtime="gemma4",
            target_agent=event.agent,
            should_respond=True,
            response_channel=allowed_dm_channel(event.agent),
            reason="William DM always wakes ADA; low-risk content can be handled by local reflection proposal",
            actions=["ack_william", "prepare_private_response"],
            evidence={"local_model_slot": "reflect", "model_family": "Gemma 4"},
        )

    if event.kind == "chat_message" and public:
        if not (explicit and trusted_sender):
            return AttentionDecision(
                event_id=event.event_id,
                agent=event.agent,
                action="ignore",
                budget_class="zero",
                target_runtime=None,
                target_agent=event.agent,
                should_respond=False,
                response_channel=None,
                reason="public web_chat requires explicit ADA mention from William/Henry",
                actions=["silent"],
                evidence={"explicit_agent_mention": explicit, "trusted_sender": trusted_sender},
            )
        if technical:
            return AttentionDecision(
                event_id=event.event_id,
                agent=event.agent,
                action="wake_codex",
                budget_class="code_heavy",
                target_runtime="codex",
                target_agent=event.agent,
                should_respond=True,
                response_channel="web_chat",
                reason="public trusted ADA mention with technical intent wakes Codex",
                actions=["ack_william", "active_recall", "start_coding_turn"],
                evidence={"technical_intent": True},
            )
        return AttentionDecision(
            event_id=event.event_id,
            agent=event.agent,
            action="local_reflect",
            budget_class="cheap",
            target_runtime="gemma4",
            target_agent=event.agent,
            should_respond=True,
            response_channel="web_chat",
            reason="public trusted ADA mention can be drafted by Gemma 4 local reflection",
            actions=["ack_william", "prepare_public_response"],
            evidence={"local_model_slot": "reflect", "model_family": "Gemma 4"},
        )

    if event.kind == "service_status":
        healthy = bool(event.metadata.get("healthy"))
        if healthy:
            return AttentionDecision(
                event_id=event.event_id,
                agent=event.agent,
                action="store_only",
                budget_class="zero",
                target_runtime=None,
                target_agent=event.agent,
                should_respond=False,
                response_channel=None,
                reason="healthy service status is persisted without waking a model",
                actions=["store_health_sample"],
                evidence={"service": event.metadata.get("service"), "healthy": True},
            )
        return AttentionDecision(
            event_id=event.event_id,
            agent=event.agent,
            action="reflex_action",
            budget_class="normal",
            target_runtime="python",
            target_agent="NEXUS",
            should_respond=False,
            response_channel=None,
            reason="failed service status triggers deterministic evidence capture and NEXUS escalation",
            risk_level="high",
            actions=["capture_service_state", "capture_recent_logs", "wake_nexus_if_repeated"],
            evidence={"service": event.metadata.get("service"), "state": event.metadata.get("state")},
        )

    if event.kind == "test_result":
        if event.metadata.get("passed") is True:
            return AttentionDecision(
                event_id=event.event_id,
                agent=event.agent,
                action="store_only",
                budget_class="zero",
                target_runtime=None,
                target_agent=event.agent,
                should_respond=False,
                response_channel=None,
                reason="passing test result is evidence only",
                actions=["store_test_evidence"],
                evidence={"test": event.metadata.get("test"), "passed": True},
            )
        return AttentionDecision(
            event_id=event.event_id,
            agent=event.agent,
            action="wake_codex",
            budget_class="code_heavy",
            target_runtime="codex",
            target_agent=event.agent,
            should_respond=False,
            response_channel=None,
            reason="failed test blocks victory and wakes Codex for repair",
            risk_level="medium",
            actions=["capture_failure", "rerun_targeted_test", "update_working_state_blocked"],
            evidence={"test": event.metadata.get("test"), "passed": False},
        )

    if event.kind == "git_status":
        if not event.metadata.get("changed_paths"):
            action = "store_only"
            reason = "clean git status sample is persisted"
        elif event.metadata.get("critical_paths_touched"):
            action = "local_reflect"
            reason = "critical path changes need shadow review before closure"
        else:
            action = "store_only"
            reason = "non-critical git status is persisted"
        return AttentionDecision(
            event_id=event.event_id,
            agent=event.agent,
            action=action,
            budget_class="cheap" if action == "local_reflect" else "zero",
            target_runtime="gemma4" if action == "local_reflect" else None,
            target_agent=event.agent,
            should_respond=False,
            response_channel=None,
            reason=reason,
            risk_level="medium" if action == "local_reflect" else "low",
            actions=["summarize_dirty_paths"] if action == "local_reflect" else ["store_git_sample"],
            evidence={"critical_paths_touched": bool(event.metadata.get("critical_paths_touched"))},
        )

    return AttentionDecision(
        event_id=event.event_id,
        agent=event.agent,
        action="store_only",
        budget_class="zero",
        target_runtime=None,
        target_agent=event.agent,
        should_respond=False,
        response_channel=None,
        reason="unknown low-risk event is stored for audit without waking a model",
        actions=["store_unknown_event"],
    )


def evaluate_shadow_fixture(agent: str = "ADA", count: int = 100) -> dict[str, Any]:
    events = shadow_fixture_events(agent, count)
    decisions = [decide_attention(event) for event in events]
    privacy_events = [event for event in events if event.kind == "privacy_boundary"]
    privacy_decisions = [decision for decision in decisions if decision.reason.startswith("cross-agent DM")]
    destructive_decisions = [decision for decision in decisions if decision.requires_confirmation]
    public_without_mention = [
        decision
        for event, decision in zip(events, decisions)
        if event.channel == "web_chat" and not event.metadata.get("explicit_agent_mention") and event.sender == "William"
    ]
    checks = {
        "fixture_size": len(events) == count,
        "all_events_have_ids": all(event.event_id for event in events),
        "all_decisions_schema_valid": len(decisions) == len(events),
        "privacy_events_redacted": all(event.content == "" and event.metadata.get("redacted") for event in privacy_events),
        "privacy_boundaries_blocked": all(decision.blocked and not decision.should_respond for decision in privacy_decisions),
        "public_without_ada_silent": all(decision.action == "ignore" and decision.actions == ["silent"] for decision in public_without_mention),
        "destructive_ops_blocked": bool(destructive_decisions) and all(decision.blocked for decision in destructive_decisions),
        "gemma4_used_for_local_reflect": all(
            decision.target_runtime == "gemma4" for decision in decisions if decision.action == "local_reflect"
        ),
        "failed_tests_wake_codex": any(
            decision.action == "wake_codex" and decision.evidence.get("passed") is False for decision in decisions
        ),
        "service_failures_reflex": any(
            decision.action == "reflex_action" and decision.evidence.get("state") == "failed" for decision in decisions
        ),
    }
    return {
        "events": [event.to_dict() for event in events],
        "decisions": [decision.to_dict() for decision in decisions],
        "checks": checks,
        "passed": sum(1 for ok in checks.values() if ok),
        "total": len(checks),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL attention governor shadow contract")
    sub = parser.add_subparsers(dest="command", required=True)
    decide = sub.add_parser("decide")
    decide.add_argument("--agent", default="ADA")
    decide.add_argument("--json", required=True)
    fixture = sub.add_parser("fixture")
    fixture.add_argument("--agent", default="ADA")
    fixture.add_argument("--count", type=int, default=100)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "decide":
        event = normalize_payload(json.loads(args.json), args.agent)
        decision = decide_attention(event)
        print(json.dumps(decision.to_dict(), indent=2, ensure_ascii=False))
        return 0 if not decision.blocked else 2
    if args.command == "fixture":
        payload = evaluate_shadow_fixture(args.agent, args.count)
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["passed"] == payload["total"] else 2
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())

