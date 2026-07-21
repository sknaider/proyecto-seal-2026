from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from soul_ingestion.adapters.text import TextAdapter
from soul_ingestion.contracts import Scope, Sensitivity, TrustTier
from soul_ingestion.engine import IngestionEngine


NOW = datetime(2026, 7, 21, 10, 0, tzinfo=UTC)
TENANT = UUID("66666666-4444-4444-8888-000000000001")


def test_candidates_are_opt_in_unapproved_and_outside_recall_contract() -> None:
    adapter = TextAdapter()
    artifact = adapter.acquire(
        "William dio luz verde al motor. El despliegue fue verificado. Responsable: ADA.",
        tenant_id=TENANT,
        owner_agent="ADA",
        scope=Scope.PRIVATE,
        observed_at=NOW,
    )
    no_candidates = IngestionEngine().process(artifact, now=NOW)
    proposed = IngestionEngine().process(artifact, now=NOW, propose_candidates=True)
    assert no_candidates.candidates == ()
    assert proposed.candidates
    assert all(not candidate.approval_verified for candidate in proposed.candidates)
    assert all(candidate.state == "candidate" for candidate in proposed.candidates)
    assert all(candidate.importance_advisory for candidate in proposed.candidates)
    assert all(candidate.confidence_scorer == "candidate_rules_v1" for candidate in proposed.candidates)
    assert all(sum(candidate.confidence_factors.values()) == candidate.confidence for candidate in proposed.candidates)
    assert all("requires_human_review" in candidate.risk_flags for candidate in proposed.candidates)


def test_external_or_medical_candidates_are_quarantined() -> None:
    artifact = TextAdapter().acquire(
        "El resultado fue verificado. Responsable: ADA.",
        tenant_id=TENANT,
        owner_agent="ADA",
        scope=Scope.PRIVATE,
        sensitivity=Sensitivity.MEDICAL,
        trust_tier=TrustTier.EXTERNAL_UNTRUSTED,
        observed_at=NOW,
    )
    result = IngestionEngine().process(artifact, now=NOW, propose_candidates=True)
    assert result.candidates
    assert all(candidate.state == "review_blocked" for candidate in result.candidates)
    assert all("external_untrusted_source" in candidate.risk_flags for candidate in result.candidates)
    assert all("sensitivity:medical" in candidate.risk_flags for candidate in result.candidates)


def test_trust_tier_is_advisory_and_does_not_change_scoring_or_digest() -> None:
    adapter = TextAdapter()
    kwargs = {
        "tenant_id": TENANT,
        "owner_agent": "ADA",
        "scope": Scope.PRIVATE,
        "source_ref": "trust-tier-invariance",
        "observed_at": NOW,
    }
    trusted = adapter.acquire(
        "William dio luz verde. El despliegue fue verificado por ADA.",
        trust_tier=TrustTier.OWNER_VERIFIED,
        **kwargs,
    )
    untrusted = adapter.acquire(
        "William dio luz verde. El despliegue fue verificado por ADA.",
        trust_tier=TrustTier.EXTERNAL_UNTRUSTED,
        **kwargs,
    )
    trusted_result = IngestionEngine().process(trusted, now=NOW, propose_candidates=True)
    untrusted_result = IngestionEngine().process(untrusted, now=NOW, propose_candidates=True)

    assert trusted_result.derivations[0].content == untrusted_result.derivations[0].content
    assert trusted_result.derivations[0].coverage == untrusted_result.derivations[0].coverage
    assert [candidate.confidence for candidate in trusted_result.candidates] == [
        candidate.confidence for candidate in untrusted_result.candidates
    ]
    assert [candidate.proposed_importance for candidate in trusted_result.candidates] == [
        candidate.proposed_importance for candidate in untrusted_result.candidates
    ]
    assert all(candidate.state == "candidate" for candidate in trusted_result.candidates)
    assert all(candidate.state == "review_blocked" for candidate in untrusted_result.candidates)
