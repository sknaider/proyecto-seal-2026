from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agi_gap_ledger import build_parser, current_gap_catalog, summarize_gaps


def test_gap_catalog_starts_with_natural_bridge_blocker() -> None:
    gaps = current_gap_catalog(natural_bridge_observed=False)

    assert gaps[0].gap_id == "G1"
    assert gaps[0].status == "blocked"
    assert gaps[0].blocking is True
    assert "require-observed exits 0" in gaps[0].next_evidence


def test_gap_catalog_resolves_natural_bridge_when_observed() -> None:
    gaps = current_gap_catalog(natural_bridge_observed=True)
    summary = summarize_gaps(gaps)

    assert gaps[0].status == "resolved"
    assert gaps[0].blocking is False
    assert "G1" not in summary["blocking_gap_ids"]


def test_gap_summary_names_next_blocking_focus() -> None:
    summary = summarize_gaps(current_gap_catalog(natural_bridge_observed=False))

    assert summary["total"] == 7
    assert summary["blocking_count"] == 4
    assert summary["next_focus"] == "G1"
    assert summary["counts"]["blocked"] == 1
    assert summary["counts"]["missing"] == 2


def test_g2_next_evidence_tracks_nexus_review_after_delegation() -> None:
    gaps = current_gap_catalog(natural_bridge_observed=False)
    g2 = next(gap for gap in gaps if gap.gap_id == "G2")

    assert g2.status == "partial"
    assert g2.blocking is True
    assert "NEXUS reviews active delegated lifecycle tasks" in g2.next_evidence


def test_blocking_gaps_resolve_when_evidence_flags_are_true() -> None:
    summary = summarize_gaps(
        current_gap_catalog(
            natural_bridge_observed=True,
            autonomous_lifecycle_resolved=True,
            cross_agent_governance_resolved=True,
            skill_instinct_factory_resolved=True,
        )
    )

    assert summary["blocking_count"] == 0
    assert summary["next_focus"] is None
    assert summary["counts"]["resolved"] == 4


def test_g5_resolves_when_dashboard_evidence_is_true() -> None:
    gaps = current_gap_catalog(daily_evidence_dashboard_resolved=True)
    g5 = next(gap for gap in gaps if gap.gap_id == "G5")

    assert g5.status == "resolved"
    assert g5.blocking is False
    assert "skill review debt" in g5.next_evidence


def test_g6_resolves_when_auxiliary_secrets_evidence_is_true() -> None:
    gaps = current_gap_catalog(auxiliary_secrets_resolved=True)
    g6 = next(gap for gap in gaps if gap.gap_id == "G6")

    assert g6.status == "resolved"
    assert g6.blocking is False
    assert "no hardcoded DB password literals" in g6.next_evidence


def test_g7_resolves_when_kernel_forge_evidence_is_true() -> None:
    gaps = current_gap_catalog(kernel_forge_resolved=True)
    g7 = next(gap for gap in gaps if gap.gap_id == "G7")

    assert g7.status == "resolved"
    assert g7.blocking is False
    assert "keep/revert" in g7.next_evidence


def test_cli_parser_accepts_summary_and_list() -> None:
    summary = build_parser().parse_args(["summary"])
    listed = build_parser().parse_args([
        "--natural-bridge-observed",
        "--daily-evidence-dashboard-resolved",
        "--auxiliary-secrets-resolved",
        "--kernel-forge-resolved",
        "list",
    ])

    assert summary.command == "summary"
    assert summary.natural_bridge_observed is False
    assert listed.command == "list"
    assert listed.natural_bridge_observed is True
    assert listed.daily_evidence_dashboard_resolved is True
    assert listed.auxiliary_secrets_resolved is True
    assert listed.kernel_forge_resolved is True
