from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from attention_governor import decide_attention, evaluate_shadow_fixture, has_technical_intent
from awareness_collector import normalize_chat_message, normalize_service_status, normalize_test_result


def test_public_webchat_without_ada_is_silent_zero_budget() -> None:
    event = normalize_chat_message(
        {"id": 74246, "from": "William", "to": "equipo", "channel": "web_chat", "content": "el modelo local es gemma 4"},
        "ADA",
    )
    decision = decide_attention(event)

    assert decision.action == "ignore"
    assert decision.budget_class == "zero"
    assert decision.should_respond is False
    assert decision.actions == ["silent"]


def test_technical_ada_mention_wakes_codex() -> None:
    event = normalize_chat_message(
        {"id": 1, "from": "William", "to": "equipo", "channel": "web_chat", "content": "ada implementa fase 1"},
        "ADA",
    )
    decision = decide_attention(event)

    assert decision.action == "wake_codex"
    assert decision.target_runtime == "codex"
    assert decision.budget_class == "code_heavy"
    assert decision.response_channel == "web_chat"


def test_nontechnical_dm_uses_gemma4_local_reflection() -> None:
    event = normalize_chat_message(
        {"id": 2, "from": "William", "to": "ADA", "channel": "dm:ada:william", "content": "como estas"},
        "ADA",
    )
    decision = decide_attention(event)

    assert decision.action == "local_reflect"
    assert decision.target_runtime == "gemma4"
    assert decision.evidence["model_family"] == "Gemma 4"
    assert decision.response_channel == "dm:ada:william"


def test_cross_agent_dm_is_blocked_and_redacted() -> None:
    event = normalize_chat_message(
        {"id": 3, "from": "ALICE", "to": "William", "channel": "dm:alice:william", "content": "private"},
        "ADA",
    )
    decision = decide_attention(event)

    assert event.content == ""
    assert decision.action == "store_only"
    assert decision.blocked is True
    assert decision.should_respond is False
    assert decision.evidence["redacted"] is True


def test_destructive_command_blocks_and_requires_confirmation() -> None:
    event = normalize_chat_message(
        {"id": 4, "from": "William", "to": "ADA", "channel": "dm:ada:william", "content": "DELETE FROM soul_v3.memories;"},
        "ADA",
    )
    decision = decide_attention(event)

    assert decision.action == "reflex_action"
    assert decision.blocked is True
    assert decision.requires_confirmation is True
    assert "show_count_scope" in decision.actions


def test_service_failure_reflexes_to_nexus_without_public_response() -> None:
    event = normalize_service_status("seal-mcp-server.service", "failed", "ADA")
    decision = decide_attention(event)

    assert decision.action == "reflex_action"
    assert decision.target_agent == "NEXUS"
    assert decision.should_respond is False
    assert decision.risk_level == "high"


def test_failed_test_wakes_codex_and_blocks_victory() -> None:
    event = normalize_test_result("memory/test_attention_governor.py", False, "ADA", "pytest failed")
    decision = decide_attention(event)

    assert decision.action == "wake_codex"
    assert decision.target_runtime == "codex"
    assert "update_working_state_blocked" in decision.actions


def test_shadow_fixture_all_checks_pass() -> None:
    result = evaluate_shadow_fixture("ADA", 100)

    assert result["passed"] == result["total"]
    assert result["total"] >= 10


def test_technical_intent_includes_runtime_and_gemma() -> None:
    assert has_technical_intent("revisa runtime gemma 4")
    assert not has_technical_intent("buen trabajo chicos")

