"""Pure orchestration: artifact -> normalized document -> segments -> derivation."""

from __future__ import annotations

from datetime import UTC, datetime

from .contracts import IngestResult, RawArtifact
from .candidates import propose_memory_candidates
from .extractive import derive_digest
from .normalize import normalize_artifact
from .segment import build_segments


class IngestionEngine:
    """Deterministic engine with no network, DB, LLM or memory side effects."""

    def process(
        self,
        artifact: RawArtifact,
        *,
        profile_id: str = "generic_v1",
        propose_candidates: bool = False,
        now: datetime | None = None,
    ) -> IngestResult:
        timestamp = now or datetime.now(UTC)
        document = normalize_artifact(artifact, now=timestamp)
        segments = build_segments(document)
        derivation = derive_digest(document, profile_id=profile_id, now=timestamp)
        candidates = (
            propose_memory_candidates(
                document,
                derivation,
                proposed_agent=document.source.owner_agent or "TEAM",
            )
            if propose_candidates
            else ()
        )
        warnings = derivation.coverage.warnings
        state = "processed" if derivation.coverage.gate_passed else "quarantined"
        return IngestResult(
            ok=state == "processed",
            document=document,
            segments=segments,
            derivations=(derivation,),
            candidates=candidates,
            state=state,
            warnings=warnings,
            provenance_complete=True,
        )
