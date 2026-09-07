from __future__ import annotations

import asyncio
import pytest
from typing import Any
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skill_instinct_factory import assess_skill_instinct_factory, persist_factory_candidates


def test_assessment_promotes_success_to_skill_and_failure_to_guardrail() -> None:
    rows = [
        {"agent": "ADA", "suite_name": "alpha", "score": 100, "passed": True},
        {"agent": "ADA", "suite_name": "alpha", "score": 95, "passed": True},
        {"agent": "ADA", "suite_name": "alpha", "score": 92, "passed": True},
        {"agent": "ADA", "suite_name": "beta", "score": 40, "passed": False},
        {"agent": "ADA", "suite_name": "beta", "score": 30, "passed": False},
    ]

    assessment = assess_skill_instinct_factory("ADA", evaluation_rows=rows)

    assert assessment.skill_candidate_count == 1
    assert assessment.guardrail_candidate_count == 1
    assert {c.kind for c in assessment.candidates} == {"skill", "guardrail_instinct"}
    assert all(c.keep_revert_evidence for c in assessment.candidates)


class FakeConn:
    def __init__(self) -> None:
        self.skill_id = 0
        self.instinct_id = 0

    async def fetchrow(self, query: str, *args: Any):
        if query.lstrip().startswith("SELECT id FROM soul_v3.skills"):
            return None
        if query.lstrip().startswith("SELECT id FROM soul_v3.instincts"):
            return None
        if "INSERT INTO soul_v3.skills" in query:
            self.skill_id = 101
            return {"id": 101}
        if "INSERT INTO soul_v3.instincts" in query:
            self.instinct_id = 202
            return {"id": 202}
        raise AssertionError(f"unexpected fetchrow: {query}")


def test_persist_factory_candidates_records_skill_and_instinct() -> None:
    assessment = assess_skill_instinct_factory(
        "ADA",
        evaluation_rows=[
            {"agent": "ADA", "suite_name": "alpha", "score": 100, "passed": True},
            {"agent": "ADA", "suite_name": "alpha", "score": 95, "passed": True},
            {"agent": "ADA", "suite_name": "alpha", "score": 92, "passed": True},
            {"agent": "ADA", "suite_name": "beta", "score": 40, "passed": False},
            {"agent": "ADA", "suite_name": "beta", "score": 30, "passed": False},
        ],
    )
    result = asyncio.run(persist_factory_candidates(FakeConn(), assessment, temporary=True, marker="unit"))

    assert result.inserted_count == 2
    assert {r.record_kind for r in result.records} == {"skill", "instinct"}
    assert all(r.evidence for r in result.records)


def test_production_persistence_refuses_generated_skill_placeholder() -> None:
    assessment = assess_skill_instinct_factory(
        "ADA",
        evaluation_rows=[
            {"agent": "ADA", "suite_name": "alpha", "score": 100, "passed": True},
            {"agent": "ADA", "suite_name": "alpha", "score": 95, "passed": True},
            {"agent": "ADA", "suite_name": "alpha", "score": 92, "passed": True},
        ],
    )
    with pytest.raises(RuntimeError, match="real_skill_bundle_required"):
        asyncio.run(persist_factory_candidates(FakeConn(), assessment))
