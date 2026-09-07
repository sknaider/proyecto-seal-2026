from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from reflex_layer import ReflexEvent, evaluate_event, is_destructive_command, mentions_ada


def test_ada_mention_is_exact_word() -> None:
    assert mentions_ada("ada responde")
    assert mentions_ada("@ADA revisa")
    assert not mentions_ada("cascada no dispara")
    assert not mentions_ada("cada cosa")


def test_public_webchat_requires_ada_mention() -> None:
    silent = evaluate_event(ReflexEvent(event_type="web_chat", sender="William", content="hola", channel="web_chat"))
    wake = evaluate_event(ReflexEvent(event_type="web_chat", sender="William", content="ada hola", channel="web_chat"))

    assert silent.should_respond is False
    assert silent.actions == ["silent"]
    assert wake.should_wake is True
    assert wake.should_respond is True
    assert wake.response_channel == "web_chat"


def test_private_william_dm_always_responds() -> None:
    decision = evaluate_event(ReflexEvent(event_type="web_chat", sender="William", content="sin mencion", channel="dm:ada:william"))

    assert decision.should_wake is True
    assert decision.should_respond is True
    assert decision.response_channel == "dm:ada:william"


def test_other_agent_dm_is_blocked() -> None:
    decision = evaluate_event(ReflexEvent(event_type="web_chat", sender="ALICE", content="secreto", channel="dm:alice:william"))

    assert decision.blocked is True
    assert decision.should_wake is False
    assert "deny_access" in decision.actions


def test_destructive_commands_are_blocked() -> None:
    assert is_destructive_command("DELETE FROM soul_v3.memories;")
    assert is_destructive_command("rm -rf /tmp/x")
    assert is_destructive_command("DROP TABLE soul_v3.memories")
    assert not is_destructive_command("DELETE FROM soul_v3.memories WHERE id=1;")

    decision = evaluate_event(ReflexEvent(event_type="command", sender="William", content="DROP TABLE soul_v3.memories"))
    assert decision.blocked is True
    assert decision.requires_confirmation is True
    assert decision.risk_level == "critical"


def test_service_down_and_test_failure_wake_without_public_response() -> None:
    service = evaluate_event(
        ReflexEvent(event_type="service_status", metadata={"service": "seal-chat.service", "state": "failed"})
    )
    failure = evaluate_event(ReflexEvent(event_type="test_failure", content="pytest failed"))

    assert service.should_wake is True
    assert service.should_respond is False
    assert service.risk_level == "high"
    assert failure.should_wake is True
    assert "update_working_state_blocked" in failure.actions
