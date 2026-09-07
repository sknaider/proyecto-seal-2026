import json

import pytest

from memory.reasoning_quality_validator import (
    detect_missing_premise_gap,
    score_and_update_reasoning_trace,
    validate_trace,
    validate_trace_with_gap_awareness,
)


class FakeConnection:
    def __init__(self, row):
        self.row = row
        self.execute_calls = []

    async def fetchrow(self, query, trace_id):
        assert trace_id == 42
        normalized = " ".join(query.split())
        assert normalized == (
            "SELECT task, premises, reasoning, conclusion "
            "FROM soul_v3.reasoning_traces WHERE id=$1"
        )
        return self.row

    async def execute(self, query, *args):
        normalized = " ".join(query.split())
        assert normalized == (
            "UPDATE soul_v3.reasoning_traces "
            "SET causal_quality_score=$2, exploration_regime=$3, "
            "reasoning=$4, updated_at=NOW() WHERE id=$1"
        )
        assert len(args) == 4
        self.execute_calls.append((query, args))
        if self.row is not None:
            self.row["reasoning"] = args[3]
        return "UPDATE 1"


def test_missing_premise_caps_quality_and_adds_annotation():
    trace = """1. The network service returned error code 503 from the gateway endpoint.
2. Given that the network service returned error code 503, the gateway endpoint was unavailable.
3. Therefore the network service gateway endpoint outage was confirmed.
4. The database is assumed healthy."""
    assert validate_trace(trace)["quality_score"] > 0.35
    report = validate_trace_with_gap_awareness(
        trace,
        premises=["The service returned HTTP 500."],
    )

    assert report["missing_premise_detected"] is True
    assert report["quality_score"] <= 0.35
    assert report["gap_annotation"] == "[GAP: missing verified premise before conclusion]"


def test_verified_trace_has_no_gap_annotation():
    report = validate_trace_with_gap_awareness(
        "The probe returned 503. Therefore the service was unavailable.",
        premises=["The probe returned 503."],
    )

    assert report["missing_premise_detected"] is False
    assert "gap_annotation" not in report


@pytest.mark.asyncio
async def test_score_update_accepts_json_premises_and_persists_annotation_once():
    conn = FakeConnection(
        {
            "task": "Diagnose outage",
            "premises": json.dumps(["The service returned HTTP 500."]),
            "reasoning": "The network was assumed healthy.",
            "conclusion": "The database failed.",
        }
    )

    report = await score_and_update_reasoning_trace(conn, 42)
    second_report = await score_and_update_reasoning_trace(conn, 42)

    assert report["missing_premise_detected"] is True
    assert second_report["missing_premise_detected"] is True
    assert len(conn.execute_calls) == 2
    _query, args = conn.execute_calls[0]
    assert args[0] == 42
    assert args[1] <= 0.35
    assert args[3].count("[GAP: missing verified premise before conclusion]") == 1
    assert conn.execute_calls[1][1][3].count(
        "[GAP: missing verified premise before conclusion]"
    ) == 1


@pytest.mark.asyncio
async def test_score_update_does_not_duplicate_existing_annotation():
    annotation = "[GAP: missing verified premise before conclusion]"
    conn = FakeConnection(
        {
            "task": "Diagnose outage",
            "premises": [],
            "reasoning": f"The network was assumed healthy.\n{annotation}",
            "conclusion": "The database failed.",
        }
    )

    await score_and_update_reasoning_trace(conn, 42)

    _query, args = conn.execute_calls[0]
    assert args[3].count(annotation) == 1


@pytest.mark.asyncio
async def test_score_update_rejects_unknown_trace_without_write():
    conn = FakeConnection(None)

    with pytest.raises(ValueError, match="reasoning_trace not found: 42"):
        await score_and_update_reasoning_trace(conn, 42)

    assert conn.execute_calls == []


def test_assumed_term_present_in_premises_still_records_explicit_marker():
    report = detect_missing_premise_gap(
        "The network is assumed healthy.",
        premises=["the network"],
    )

    assert report["missing_premise_detected"] is True
    assert "assumed" in report["markers"]
