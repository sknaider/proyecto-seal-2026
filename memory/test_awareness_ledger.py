from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from attention_governor import decide_attention
from awareness_collector import normalize_chat_message
from awareness_ledger import build_parser, decision_score, summarize_decision, utc_tick_id


def test_tick_id_includes_agent_and_sanitized_event() -> None:
    tick_id = utc_tick_id("ADA", "chat:74246/with space")

    assert tick_id.startswith("awareness:ADA:")
    assert "chat_74246_with_space" in tick_id


def test_summary_is_compact_and_includes_action_budget_risk() -> None:
    event = normalize_chat_message(
        {"id": 1, "from": "William", "channel": "web_chat", "content": "ada implementa"},
        "ADA",
    )
    decision = decide_attention(event)
    summary = summarize_decision(event, decision)

    assert "chat_message" in summary
    assert "wake_codex" in summary
    assert "budget=code_heavy" in summary
    assert len(summary) <= 500


def test_decision_score_penalizes_bad_contracts_only() -> None:
    event = normalize_chat_message(
        {"id": 2, "from": "William", "channel": "dm:ada:william", "content": "hola"},
        "ADA",
    )
    good = decide_attention(event)

    assert decision_score(good) == 1.0


def test_cli_parser_accepts_record_state_recent_cleanup() -> None:
    parser = build_parser()

    record = parser.parse_args(["record", "--agent", "ADA", "--json", "{}"])
    state = parser.parse_args(["state", "--agent", "ADA"])
    recent = parser.parse_args(["recent", "--agent", "ADA", "--limit", "3"])
    cleanup = parser.parse_args(["cleanup-agent", "--agent", "ADA_AWARENESS_TEST"])

    assert record.command == "record"
    assert state.command == "state"
    assert recent.limit == 3
    assert cleanup.command == "cleanup-agent"

