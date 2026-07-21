"""Versioned contracts for SUIE.

The contracts are strict on purpose: an adapter cannot silently add authority,
scope or provenance fields. All hashes use concrete UTF-8 bytes.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SUIE_NAMESPACE = UUID("0be28c74-2ba0-59bc-aa5b-d6c0461690f7")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)


class SourceKind(StrEnum):
    CHAT = "chat"
    TEXT = "text"
    MARKDOWN = "markdown"
    PDF = "pdf"
    EMAIL = "email"
    GTL_AWB = "gtl_awb"
    YOUTUBE = "youtube"
    PAPER = "paper"
    MEDICAL_NOTE = "medical_note"


class Scope(StrEnum):
    PRIVATE = "private"
    SHARED = "shared"
    TEAM = "team"
    WILLIAM = "william"
    DOMAIN = "domain"


class TrustTier(StrEnum):
    OWNER_VERIFIED = "owner_verified"
    TEAM_VERIFIED = "team_verified"
    EXTERNAL_TRUSTED = "external_trusted"
    EXTERNAL_UNTRUSTED = "external_untrusted"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    MEDICAL = "medical"


class SourceDescriptor(StrictModel):
    source_kind: SourceKind
    source_ref: str = Field(min_length=1, max_length=2048)
    source_uri: str | None = Field(default=None, max_length=4096)
    tenant_id: UUID
    owner_agent: str | None = Field(default=None, max_length=64)
    scope: Scope
    event_time: datetime | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    trust_tier: TrustTier
    adapter_id: str = Field(min_length=1, max_length=128)
    adapter_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")

    @field_validator("source_ref", "source_uri")
    @classmethod
    def reject_control_characters(cls, value: str | None) -> str | None:
        if value is not None and any(ord(char) < 32 for char in value):
            raise ValueError("control characters are forbidden in source identifiers")
        return value


class RawArtifact(StrictModel):
    source: SourceDescriptor
    media_type: str = Field(min_length=1, max_length=255)
    content: bytes = Field(repr=False)
    extracted_text: str | None = Field(default=None, repr=False)
    title: str | None = Field(default=None, max_length=2048)
    authors: tuple[str, ...] = ()
    language: str = Field(default="und", pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]+)*$|^und$")
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    retention_policy: str = Field(default="standard", min_length=1, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def raw_hash_sha256(self) -> str:
        return sha256_bytes(self.content)


class OffsetPoint(StrictModel):
    normalized_offset: int = Field(ge=0)
    raw_offset: int = Field(ge=0)


class NormalizedDocument(StrictModel):
    document_id: UUID
    schema_version: Literal["soul.normalized_document/1"] = "soul.normalized_document/1"
    source: SourceDescriptor
    media_type: str
    language: str
    title: str | None = None
    authors: tuple[str, ...] = ()
    normalized_text: str
    raw_artifact_ref: str
    raw_hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalization_profile: Literal["unicode_nfc_text_v2"] = "unicode_nfc_text_v2"
    identity_profile: Literal["document_identity_v2"] = "document_identity_v2"
    char_count: int = Field(ge=0)
    byte_count: int = Field(ge=0)
    sensitivity: Sensitivity
    retention_policy: str
    ingested_at: datetime
    offset_map: tuple[OffsetPoint, ...] = Field(exclude=True, repr=False)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def verify_derived_fields(self) -> "NormalizedDocument":
        if self.normalized_hash_sha256 != sha256_text(self.normalized_text):
            raise ValueError("normalized hash does not match concrete UTF-8 bytes")
        if self.char_count != len(self.normalized_text):
            raise ValueError("char_count mismatch")
        if self.byte_count != len(self.normalized_text.encode("utf-8")):
            raise ValueError("byte_count mismatch")
        if not self.offset_map or self.offset_map[-1].normalized_offset != self.char_count:
            raise ValueError("offset_map must cover the normalized document")
        return self


class EvidenceAnchor(StrictModel):
    kind: Literal["message", "page", "paragraph", "timestamp", "char_range"]
    start: str
    end: str
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    raw_start_char: int | None = Field(default=None, ge=0)
    raw_end_char: int | None = Field(default=None, ge=0)
    text_hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def offsets_in_order(self) -> "EvidenceAnchor":
        if self.end_char < self.start_char:
            raise ValueError("anchor end precedes start")
        return self


class ProtectedSpan(StrictModel):
    kind: Literal[
        "date", "amount", "quantity", "id", "awb", "dose", "negation", "owner", "deadline"
    ]
    text: str
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class DocumentSegment(StrictModel):
    segment_id: UUID
    document_id: UUID
    ordinal: int = Field(ge=0)
    text: str
    text_hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_char: int = Field(ge=0)
    end_char: int = Field(ge=0)
    anchor: EvidenceAnchor
    heading_path: tuple[str, ...] = ()
    protected_spans: tuple[ProtectedSpan, ...] = ()

    @model_validator(mode="after")
    def segment_is_coherent(self) -> "DocumentSegment":
        if self.end_char - self.start_char != len(self.text):
            raise ValueError("segment offsets do not match text length")
        if self.text_hash_sha256 != sha256_text(self.text):
            raise ValueError("segment hash mismatch")
        return self


class CoverageMap(StrictModel):
    total_sentences: int = Field(ge=0)
    selected_sentences: int = Field(ge=0)
    total_sections: int = Field(ge=0)
    covered_sections: int = Field(ge=0)
    total_protected_spans: int = Field(ge=0)
    preserved_protected_spans: int = Field(ge=0)
    compression_ratio: float = Field(ge=0.0, le=1.0)
    section_coverage: dict[str, bool]
    omitted_sections: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    gate_passed: bool


class Derivation(StrictModel):
    derivation_id: UUID
    document_id: UUID
    kind: Literal["digest", "structured_extract", "entities", "candidate_set"]
    processor_id: str
    processor_version: str
    config_hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content: str
    coverage: CoverageMap
    evidence_anchors: tuple[EvidenceAnchor, ...]
    created_at: datetime


class MemoryCandidate(StrictModel):
    candidate_id: UUID
    document_id: UUID
    derivation_id: UUID
    proposed_agent: str
    proposed_category: Literal["fact", "decision", "correction", "milestone", "pattern", "insight"]
    proposed_content: str
    proposed_importance: int = Field(ge=1, le=10)
    importance_advisory: Literal[True] = True
    importance_method: Literal["candidate_rules_v1"] = "candidate_rules_v1"
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_scorer: Literal["candidate_rules_v1"] = "candidate_rules_v1"
    confidence_factors: dict[str, float]
    source_anchors: tuple[EvidenceAnchor, ...]
    risk_flags: tuple[str, ...] = ()
    state: Literal["candidate", "review_blocked", "approved", "rejected", "revoked"] = "candidate"
    approval_verified: bool = False

    @model_validator(mode="after")
    def candidate_cannot_self_promote(self) -> "MemoryCandidate":
        if self.state == "approved" and not self.approval_verified:
            raise ValueError("approved candidates require verified approval")
        if any(value < 0.0 or value > 1.0 for value in self.confidence_factors.values()):
            raise ValueError("confidence factors must be between 0 and 1")
        if abs(sum(self.confidence_factors.values()) - self.confidence) > 1e-9:
            raise ValueError("confidence must equal the sum of its versioned factors")
        return self


class IngestResult(StrictModel):
    ok: bool
    document: NormalizedDocument
    segments: tuple[DocumentSegment, ...]
    derivations: tuple[Derivation, ...]
    candidates: tuple[MemoryCandidate, ...] = ()
    state: Literal["normalized", "processed", "quarantined", "failed"]
    idempotent_replay: bool = False
    warnings: tuple[str, ...] = ()
    provenance_complete: bool


def stable_uuid(kind: str, *parts: str) -> UUID:
    return uuid5(SUIE_NAMESPACE, "\x1f".join((kind, *parts)))
