from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from soul_ingestion.adapters.text import TextAdapter
from soul_ingestion.contracts import MemoryCandidate, Scope, SourceDescriptor, SourceKind, TrustTier
from soul_ingestion.engine import IngestionEngine
from soul_ingestion.normalize import NormalizationError, normalize_artifact


TENANT = UUID("11111111-1111-1111-1111-111111111111")
NOW = datetime(2026, 7, 21, 5, 0, tzinfo=UTC)


def artifact(text: str | bytes):
    return TextAdapter().acquire(
        text,
        tenant_id=TENANT,
        owner_agent="ADA",
        scope=Scope.PRIVATE,
        observed_at=NOW,
    )


def test_unicode_nfc_newlines_and_raw_offsets_are_preserved() -> None:
    raw = "Cafe\u0301\r\nPerú\rNo"
    document = normalize_artifact(artifact(raw), now=NOW)

    assert document.normalized_text == "Café\nPerú\nNo"
    assert document.normalized_hash_sha256 != document.raw_hash_sha256
    assert document.offset_map[4].raw_offset == 5
    assert document.offset_map[-1].raw_offset == len(raw)


def test_normalization_and_ids_are_deterministic() -> None:
    first = normalize_artifact(artifact("Árbol y acción."), now=NOW)
    second = normalize_artifact(artifact("Árbol y acción."), now=NOW)
    assert first.document_id == second.document_id
    assert first.normalized_hash_sha256 == second.normalized_hash_sha256


def test_raw_evidence_and_profile_define_idempotency_not_normalized_output() -> None:
    first = normalize_artifact(artifact("línea 1\r\nlínea 2"), now=NOW)
    second_artifact = TextAdapter().acquire(
        "línea 1\nlínea 2",
        tenant_id=TENANT,
        owner_agent="ADA",
        scope=Scope.PRIVATE,
        source_ref=first.source.source_ref,
        observed_at=NOW,
    )
    second = normalize_artifact(second_artifact, now=NOW)

    assert first.normalized_text == second.normalized_text
    assert first.normalized_hash_sha256 == second.normalized_hash_sha256
    assert first.raw_hash_sha256 != second.raw_hash_sha256
    assert first.document_id != second.document_id
    assert first.normalization_profile == second.normalization_profile == "unicode_nfc_text_v2"
    assert first.identity_profile == second.identity_profile == "document_identity_v2"


def test_authority_boundaries_are_part_of_document_identity() -> None:
    common = {
        "tenant_id": TENANT,
        "owner_agent": "ADA",
        "source_ref": "same-evidence-different-boundary",
        "observed_at": NOW,
    }
    private = TextAdapter().acquire(
        "Evidencia idéntica.",
        scope=Scope.PRIVATE,
        trust_tier=TrustTier.OWNER_VERIFIED,
        **common,
    )
    team = TextAdapter().acquire(
        "Evidencia idéntica.",
        scope=Scope.TEAM,
        trust_tier=TrustTier.TEAM_VERIFIED,
        **common,
    )
    private_document = normalize_artifact(private, now=NOW)
    team_document = normalize_artifact(team, now=NOW)
    assert private_document.normalized_hash_sha256 == team_document.normalized_hash_sha256
    assert private_document.document_id != team_document.document_id


def test_strict_contract_rejects_unknown_authority_fields() -> None:
    with pytest.raises(ValidationError):
        SourceDescriptor.model_validate(
            {
                "source_kind": SourceKind.TEXT,
                "source_ref": "x",
                "tenant_id": TENANT,
                "scope": Scope.PRIVATE,
                "trust_tier": TrustTier.OWNER_VERIFIED,
                "adapter_id": "text_v1",
                "adapter_version": "1.0.0",
                "observed_at": NOW,
                "execute_tools": True,
            }
        )


def test_invalid_encoding_fails_closed() -> None:
    with pytest.raises(NormalizationError):
        normalize_artifact(artifact(b"\x80\x81\x82"), now=NOW)


def test_approved_candidate_requires_verified_human_gate() -> None:
    result = IngestionEngine().process(artifact("Hecho comprobado."), now=NOW)
    derivation = result.derivations[0]
    with pytest.raises(ValidationError):
        MemoryCandidate(
            candidate_id=UUID(int=2),
            document_id=result.document.document_id,
            derivation_id=derivation.derivation_id,
            proposed_agent="ADA",
            proposed_category="fact",
            proposed_content="Hecho comprobado.",
            proposed_importance=7,
            confidence=0.9,
            confidence_factors={"test": 0.9},
            source_anchors=derivation.evidence_anchors,
            state="approved",
            approval_verified=False,
        )
