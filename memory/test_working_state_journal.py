from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from working_state_journal import (
    WorkingStateEvent,
    default_events,
    project_events,
    validate_event,
)


def test_default_working_state_events_project_to_success() -> None:
    projection = project_events("ADA", default_events())

    assert projection.score == 100
    assert projection.missing == []
    assert projection.last_action == "run fixed probe"
    assert projection.last_result == "fixed probe passed"
    assert projection.failed_paths == ["run failing probe"]
    assert "memory/test_working_state_journal.py" in projection.evidence_items
    assert projection.next_step == "continue_with_evidence"


def test_validation_rejects_result_without_evidence() -> None:
    valid, missing = validate_event(
        WorkingStateEvent(
            event_type="tool_result",
            action="run tests",
            result="failed",
            status="failed",
            evidence={},
        )
    )

    assert valid is False
    assert "evidence" in missing


def test_projection_blocks_empty_event_list() -> None:
    projection = project_events("ADA", [])

    assert projection.score < 90
    assert "events:not_found" in projection.missing
    assert "attempted_strategies" in projection.missing
    assert projection.next_step == "investigate_blocker"


def test_projection_tracks_blocked_last_result() -> None:
    projection = project_events(
        "ADA",
        [
            WorkingStateEvent("intent", "risky cleanup", "intent recorded", "planned", {"artifact": "contract"}),
            WorkingStateEvent("tool_result", "risky cleanup", "blocked by destructive guard", "blocked", {"command": "DELETE", "output": "blocked"}),
        ],
    )

    assert projection.score < 90
    assert "last_result:not_success" in projection.missing
    assert projection.failed_paths == ["risky cleanup"]
    assert projection.risk_level == "medium"
