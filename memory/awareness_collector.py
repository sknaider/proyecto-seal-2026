#!/usr/bin/env python3
"""Shadow-mode event normalization for continuous awareness.

The collector does not act on events. It converts raw chat/service/test/git
signals into a small, auditable contract for the attention governor. Privacy is
enforced at normalization time: DMs not owned by the agent are redacted.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Iterable

from awareness_types import (
    AwarenessEvent,
    TRUSTED_HUMANS,
    agent_key,
    allowed_dm_channel,
    contains_destructive_command,
    is_dm_channel,
    is_public_webchat,
    mentions_agent,
    utc_now_iso,
)


def _first_text(payload: dict[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return str(value)
    return default


def _event_id(prefix: str, raw_id: Any) -> str:
    if raw_id in {None, ""}:
        return f"{prefix}:unknown"
    return f"{prefix}:{raw_id}"


def normalize_chat_message(payload: dict[str, Any], agent: str = "ADA") -> AwarenessEvent:
    agent_name = agent.upper()
    channel = _first_text(payload, "channel", default="web_chat").strip() or "web_chat"
    channel_key = channel.lower()
    sender = _first_text(payload, "from", "sender", "sender_name", default="")
    target = _first_text(payload, "to", "recipient", default="")
    raw_content = _first_text(payload, "content", "message", default="")
    raw_id = payload.get("id") or payload.get("message_id") or payload.get("legacy_id")
    observed_at = _first_text(payload, "timestamp", "created_at", "run_at", default=utc_now_iso())

    metadata: dict[str, Any] = {
        "raw_channel": channel,
        "trusted_sender": agent_key(sender) in TRUSTED_HUMANS,
        "explicit_agent_mention": mentions_agent(raw_content, agent_name),
        "redacted": False,
    }

    if is_dm_channel(channel_key) and channel_key != allowed_dm_channel(agent_name):
        return AwarenessEvent(
            event_id=_event_id("chat", raw_id),
            agent=agent_name,
            source="chat_messages",
            channel=channel_key,
            kind="privacy_boundary",
            sender=sender,
            target=target,
            content="",
            priority="high",
            requires_response=False,
            payload_ref={"chat_id": raw_id},
            metadata={**metadata, "privacy_boundary": True, "redacted": True},
            observed_at=observed_at,
        )

    trusted = agent_key(sender) in TRUSTED_HUMANS
    explicit = mentions_agent(raw_content, agent_name)
    allowed_dm = channel_key == allowed_dm_channel(agent_name)
    public = is_public_webchat(channel_key)
    destructive = contains_destructive_command(raw_content)
    requires_response = allowed_dm or (public and explicit and trusted)
    priority = "normal"
    if destructive or allowed_dm or requires_response:
        priority = "high"
    elif public and trusted:
        priority = "low"

    return AwarenessEvent(
        event_id=_event_id("chat", raw_id),
        agent=agent_name,
        source="chat_messages",
        channel=channel_key,
        kind="chat_message",
        sender=sender,
        target=target,
        content=raw_content,
        priority=priority,
        requires_response=requires_response,
        payload_ref={"chat_id": raw_id},
        metadata={**metadata, "destructive_command": destructive},
        observed_at=observed_at,
    )


def normalize_service_status(service: str, state: str, agent: str = "ADA", observed_at: str | None = None) -> AwarenessEvent:
    state_key = state.strip().lower()
    healthy = state_key in {"active", "ok", "healthy", "running"}
    return AwarenessEvent(
        event_id=f"service:{service}",
        agent=agent.upper(),
        source="systemd",
        channel="system",
        kind="service_status",
        sender="systemd",
        target=agent.upper(),
        content=f"{service}={state}",
        priority="low" if healthy else "high",
        requires_response=False,
        payload_ref={"service": service},
        metadata={"service": service, "state": state_key, "healthy": healthy},
        observed_at=observed_at or utc_now_iso(),
    )


def normalize_test_result(name: str, passed: bool, agent: str = "ADA", evidence: str = "") -> AwarenessEvent:
    return AwarenessEvent(
        event_id=f"test:{name}",
        agent=agent.upper(),
        source="pytest",
        channel="system",
        kind="test_result",
        sender="pytest",
        target=agent.upper(),
        content=evidence or f"{name} {'passed' if passed else 'failed'}",
        priority="low" if passed else "high",
        requires_response=False,
        payload_ref={"test": name},
        metadata={"test": name, "passed": bool(passed)},
    )


def normalize_git_status(changed_paths: Iterable[str], agent: str = "ADA") -> AwarenessEvent:
    paths = sorted(str(path) for path in changed_paths)
    critical = any(path.startswith(("memory/", "messages/", ".codex/", "agents/ADA/")) for path in paths)
    return AwarenessEvent(
        event_id="git:working-tree",
        agent=agent.upper(),
        source="git",
        channel="system",
        kind="git_status",
        sender="git",
        target=agent.upper(),
        content="\n".join(paths[:50]),
        priority="normal" if paths else "low",
        requires_response=False,
        payload_ref={"path_count": len(paths)},
        metadata={"changed_paths": paths, "critical_paths_touched": critical},
    )


def shadow_fixture_events(agent: str = "ADA", count: int = 100) -> list[AwarenessEvent]:
    """Build deterministic fixture events covering chat, privacy and ops cases."""

    agent_name = agent.upper()
    agent_mention = agent_name.lower()
    agent_dm = f"dm:{agent_mention}:william"
    templates: list[AwarenessEvent] = [
        normalize_chat_message({"id": 1, "from": "William", "to": "equipo", "channel": "web_chat", "content": "chicos revisen"}, agent),
        normalize_chat_message({"id": 2, "from": "William", "to": "equipo", "channel": "web_chat", "content": f"{agent_mention} revisa el bridge"}, agent),
        normalize_chat_message({"id": 3, "from": "William", "to": agent_name, "channel": agent_dm, "content": "continua"}, agent),
        normalize_chat_message({"id": 4, "from": "ALICE", "to": "William", "channel": "dm:alice:william", "content": "private alice text"}, agent),
        normalize_chat_message({"id": 5, "from": "William", "to": agent_name, "channel": agent_dm, "content": "DROP TABLE soul_v3.memories"}, agent),
        normalize_service_status("seal-mcp-server.service", "active", agent),
        normalize_service_status("seal-chat.service", "failed", agent),
        normalize_test_result("memory/test_attention_governor.py", True, agent),
        normalize_test_result("memory/test_attention_governor.py", False, agent, "pytest failed"),
        normalize_git_status(["memory/attention_governor.py", "docs/readme.md"], agent),
    ]
    events: list[AwarenessEvent] = []
    for idx in range(count):
        base = templates[idx % len(templates)]
        events.append(
            AwarenessEvent(
                **{
                    **base.to_dict(),
                    "event_id": f"{base.event_id}:{idx}",
                    "payload_ref": {**base.payload_ref, "fixture_index": idx},
                }
            )
        )
    return events


def normalize_payload(payload: dict[str, Any], agent: str = "ADA") -> AwarenessEvent:
    kind = str(payload.get("kind") or payload.get("event_type") or "")
    if kind in {"service_status", "service"}:
        return normalize_service_status(str(payload.get("service", "unknown")), str(payload.get("state", "unknown")), agent)
    if kind in {"test_result", "test"}:
        return normalize_test_result(str(payload.get("test", "unknown")), bool(payload.get("passed")), agent, str(payload.get("evidence", "")))
    if kind in {"git_status", "git"}:
        paths = payload.get("changed_paths") if isinstance(payload.get("changed_paths"), list) else []
        return normalize_git_status([str(path) for path in paths], agent)
    return normalize_chat_message(payload, agent)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL awareness event collector shadow contract")
    sub = parser.add_subparsers(dest="command", required=True)
    normalize = sub.add_parser("normalize")
    normalize.add_argument("--agent", default="ADA")
    normalize.add_argument("--json", required=True)
    fixture = sub.add_parser("fixture")
    fixture.add_argument("--agent", default="ADA")
    fixture.add_argument("--count", type=int, default=100)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "normalize":
        event = normalize_payload(json.loads(args.json), args.agent)
        print(json.dumps(event.to_dict(), indent=2, ensure_ascii=False))
        return 0
    if args.command == "fixture":
        events = shadow_fixture_events(args.agent, args.count)
        print(json.dumps([event.to_dict() for event in events], indent=2, ensure_ascii=False))
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
