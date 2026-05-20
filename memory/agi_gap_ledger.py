#!/usr/bin/env python3
"""Current evidence-gated AGI gap ledger for SEAL."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class AgiGap:
    gap_id: str
    title: str
    layer: str
    status: str
    blocking: bool
    owner: str
    next_evidence: str
    source: str


def current_gap_catalog(
    *,
    natural_bridge_observed: bool = False,
    autonomous_lifecycle_resolved: bool = False,
    cross_agent_governance_resolved: bool = False,
    skill_instinct_factory_resolved: bool = False,
    daily_evidence_dashboard_resolved: bool = False,
    auxiliary_secrets_resolved: bool = False,
    kernel_forge_resolved: bool = False,
) -> list[AgiGap]:
    natural_status = "resolved" if natural_bridge_observed else "blocked"
    g2_status = "resolved" if autonomous_lifecycle_resolved else "partial"
    g3_status = "resolved" if cross_agent_governance_resolved else "partial"
    g4_status = "resolved" if skill_instinct_factory_resolved else "missing"
    g5_status = "resolved" if daily_evidence_dashboard_resolved else "missing"
    g6_status = "resolved" if auxiliary_secrets_resolved else "partial"
    g7_status = "resolved" if kernel_forge_resolved else "deferred"
    return [
        AgiGap(
            "G1",
            "First natural William/Henry bridge event",
            "L1/L4 event-to-working-state loop",
            natural_status,
            not natural_bridge_observed,
            "ADA",
            "memory/bridge_natural_journal.py require-observed exits 0 and task 425 closes from real bridge:* event",
            "Sprint 11",
        ),
        AgiGap(
            "G2",
            "Autonomous cognitive lifecycle",
            "L2/L3 task-goal-reflex loop",
            g2_status,
            not autonomous_lifecycle_resolved,
            "ADA+NEXUS",
            (
                "task 438 closed after NEXUS-reviewed lifecycle proposals and execution-gate evidence"
                if autonomous_lifecycle_resolved
                else "NEXUS reviews active delegated lifecycle tasks and records approve/reject/more-evidence outcome before ADA executes anything"
            ),
            "agents/ADA/soul_stabilization_closure_20260519.md",
        ),
        AgiGap(
            "G3",
            "Cross-agent governance automation",
            "L5 judge layer",
            g3_status,
            not cross_agent_governance_resolved,
            "NEXUS",
            (
                "NEXUS audit requests are tracked and closed by independent runs"
                if cross_agent_governance_resolved
                else "NEXUS audit requests are tracked as tasks and closed by independent runs without manual chat polling"
            ),
            "SEAL_AGI_PLAN_CONSOLIDADO_WEBCHAT_20260519.md",
        ),
        AgiGap(
            "G4",
            "Skill / instinct factory",
            "L6 learning loop",
            g4_status,
            not skill_instinct_factory_resolved,
            "ADA+JARVIS",
            (
                "skill/guardrail promotion loop has keep/revert evidence"
                if skill_instinct_factory_resolved
                else "repeated success promotes a skill; repeated failure creates a guardrail/test; both require keep/revert evidence"
            ),
            "SEAL_AGI_PLAN_CONSOLIDADO_WEBCHAT_20260519.md P7",
        ),
        AgiGap(
            "G5",
            "Daily evidence dashboard",
            "L6 observability",
            g5_status,
            False,
            "ALICE+ADA",
            (
                "Soul Dashboard :8850 exposes evaluation_runs, pending validations, watcher status, stale agents and skill review debt"
                if daily_evidence_dashboard_resolved
                else "Soul App shows latest evaluation_runs, pending validations, watcher status and stale agents"
            ),
            "agents/ADA/soul_stabilization_closure_20260519.md",
        ),
        AgiGap(
            "G6",
            "Auxiliary secrets debt",
            "security/runtime hygiene",
            g6_status,
            False,
            "ADA+NEXUS",
            (
                "active and auxiliary memory/**/*.py scripts have no hardcoded DB password literals and use seal_secrets/credentials.env"
                if auxiliary_secrets_resolved
                else "all active and auxiliary memory/**/*.py scripts resolve DB credentials through seal_secrets/credentials.env"
            ),
            "agents/ADA/soul_stabilization_closure_20260519.md",
        ),
        AgiGap(
            "G7",
            "Kernel Forge RTX/DGX",
            "domain execution loop",
            g7_status,
            False,
            "ADA+JARVIS",
            (
                "kernel_forge suite proves profiler, Amdahl bottleneck, correctness judge, keep/revert and CUDA/Nsight tool probes"
                if kernel_forge_resolved
                else "profiler/coder/judge/keep-revert loop with Nsight/correctness benchmarks"
            ),
            "SEAL_AGI_PLAN_CONSOLIDADO_WEBCHAT_20260519.md P8",
        ),
    ]


def summarize_gaps(gaps: list[AgiGap]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for gap in gaps:
        counts[gap.status] = counts.get(gap.status, 0) + 1
    blocking = [gap for gap in gaps if gap.blocking and gap.status != "resolved"]
    return {
        "total": len(gaps),
        "counts": counts,
        "blocking_count": len(blocking),
        "blocking_gap_ids": [gap.gap_id for gap in blocking],
        "next_focus": blocking[0].gap_id if blocking else None,
        "gaps": [asdict(gap) for gap in gaps],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEAL AGI gap ledger")
    parser.add_argument("--natural-bridge-observed", action="store_true")
    parser.add_argument("--autonomous-lifecycle-resolved", action="store_true")
    parser.add_argument("--cross-agent-governance-resolved", action="store_true")
    parser.add_argument("--skill-instinct-factory-resolved", action="store_true")
    parser.add_argument("--daily-evidence-dashboard-resolved", action="store_true")
    parser.add_argument("--auxiliary-secrets-resolved", action="store_true")
    parser.add_argument("--kernel-forge-resolved", action="store_true")
    parser.add_argument("command", choices=["summary", "list"])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    gaps = current_gap_catalog(
        natural_bridge_observed=args.natural_bridge_observed,
        autonomous_lifecycle_resolved=args.autonomous_lifecycle_resolved,
        cross_agent_governance_resolved=args.cross_agent_governance_resolved,
        skill_instinct_factory_resolved=args.skill_instinct_factory_resolved,
        daily_evidence_dashboard_resolved=args.daily_evidence_dashboard_resolved,
        auxiliary_secrets_resolved=args.auxiliary_secrets_resolved,
        kernel_forge_resolved=args.kernel_forge_resolved,
    )
    payload: Any = summarize_gaps(gaps) if args.command == "summary" else [asdict(gap) for gap in gaps]
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
