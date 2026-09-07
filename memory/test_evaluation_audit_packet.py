from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from evaluation_audit_packet import audit_verdict, build_parser, render_markdown


def sample_packet() -> dict:
    return {
        "generated_at": "2026-05-20T00:00:00+00:00",
        "agent": "ADA",
        "verdict": "READY_FOR_NEXUS_EXTERNAL_REVIEW",
        "plan_status": {
            "ok": True,
            "phase": "P7",
            "latest": [
                {"id": 76, "suite_name": "soul_db_integrity", "score": 100, "passed": True, "evidence": "tables_found=5/5"},
                {"id": 86, "suite_name": "final_audit_readiness", "score": 100, "passed": True, "evidence": "readiness_checks=7/7"},
            ],
            "working_state": {
                "task_name": "AGI Plan — P7 Auditoria final NEXUS-ready",
                "step": 8,
                "total_steps": 8,
                "agent_state": "REVIEWING",
                "technical_state": "evaluation_spine run-all passed 11/11",
                "pending_validations": ["NEXUS external review"],
            },
        },
        "cleanup_counts": {
            "evaluation_spine_temp_memories": 0,
            "causal_chain_temp_events": 0,
            "governance_temp_debates": 0,
        },
        "audited_files": ["memory/evaluation_spine.py"],
        "git_status": [],
        "reproduction_commands": ["python memory/evaluation_spine.py --agent ADA plan-status"],
    }


def test_audit_verdict_ready_requires_p7_green_and_zero_cleanup() -> None:
    packet = sample_packet()

    assert audit_verdict(packet) == "READY_FOR_NEXUS_EXTERNAL_REVIEW"


def test_audit_verdict_blocks_on_cleanup_residue() -> None:
    packet = sample_packet()
    packet["cleanup_counts"]["causal_chain_temp_events"] = 1

    assert audit_verdict(packet) == "BLOCKED_PENDING_FIXES"


def test_render_markdown_contains_non_agi_boundary_and_suite_table() -> None:
    packet = sample_packet()
    markdown = render_markdown(packet)

    assert "does **not** declare AGI" in markdown
    assert "| 76 | soul_db_integrity | 100 | true | tables_found=5/5 |" in markdown
    assert "NEXUS Review Checklist" in markdown


def test_cli_parser_accepts_write_and_json() -> None:
    args = build_parser().parse_args(["--agent", "ADA", "--json", "--write", "agents/NEXUS/out.md"])

    assert args.agent == "ADA"
    assert args.json is True
    assert str(args.write) == "agents/NEXUS/out.md"
