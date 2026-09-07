"""Conservative deterministic memory proposals isolated from recall."""

from __future__ import annotations

from .contracts import Derivation, MemoryCandidate, NormalizedDocument, stable_uuid


CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("correction", ("corrijo", "corrección", "retiro", "rectifico", "era falso")),
    ("decision", ("decisión", "se decidió", "luz verde", "autorizó", "ordenó")),
    ("milestone", ("cerrado", "completado", "desplegado", "verificado", "pasa ")),
    ("fact", ("es ", "son ", "tiene ", "resultado", "responsable")),
)
HUMAN_CAPTURE_PREFIX = "human_capture:"


def propose_memory_candidates(
    document: NormalizedDocument,
    derivation: Derivation,
    *,
    proposed_agent: str,
) -> tuple[MemoryCandidate, ...]:
    candidates: list[MemoryCandidate] = []
    for anchor in derivation.evidence_anchors:
        content = document.normalized_text[anchor.start_char : anchor.end_char]
        folded = content.casefold()
        category = next(
            (candidate_category for candidate_category, cues in CUES if any(cue in folded for cue in cues)),
            None,
        )
        if category is None or len(content) < 20:
            continue
        risk_flags: list[str] = ["requires_human_review"]
        quarantined = document.source.trust_tier.value.startswith("external")
        if quarantined:
            risk_flags.append("external_untrusted_source")
        if document.sensitivity.value in {"confidential", "medical"}:
            risk_flags.append(f"sensitivity:{document.sensitivity.value}")
            quarantined = True
        confidence = 0.75 if category in {"decision", "correction", "milestone"} else 0.6
        confidence_factors = {
            "explicit_category_cue": 0.5 if category != "fact" else 0.35,
            "exact_source_anchor": 0.2,
            "human_review_required": 0.05,
        }
        candidates.append(
            MemoryCandidate(
                candidate_id=stable_uuid(
                    "candidate",
                    str(derivation.derivation_id),
                    proposed_agent,
                    category,
                    anchor.text_hash_sha256,
                ),
                document_id=document.document_id,
                derivation_id=derivation.derivation_id,
                proposed_agent=proposed_agent,
                proposed_category=category,
                proposed_content=content,
                proposed_importance=6 if category != "fact" else 5,
                confidence=confidence,
                confidence_factors=confidence_factors,
                source_anchors=(anchor,),
                risk_flags=tuple(risk_flags),
                state="review_blocked" if quarantined else "candidate",
                approval_verified=False,
            )
        )
    # The authenticated human inbox must not silently discard prose that lacks
    # a keyword cue. It still creates only a quarantined proposal; William must
    # review it before the canonical writer sees it.
    if not candidates and document.source.source_ref.startswith(HUMAN_CAPTURE_PREFIX):
        risk_flags = ["requires_human_review", "human_capture_fallback"]
        quarantined = document.sensitivity.value in {"confidential", "medical"}
        if quarantined:
            risk_flags.append(f"sensitivity:{document.sensitivity.value}")
        candidates.append(
            MemoryCandidate(
                candidate_id=stable_uuid(
                    "candidate",
                    str(derivation.derivation_id),
                    proposed_agent,
                    "insight",
                    derivation.output_hash_sha256,
                ),
                document_id=document.document_id,
                derivation_id=derivation.derivation_id,
                proposed_agent=proposed_agent,
                proposed_category="insight",
                proposed_content=derivation.content,
                proposed_importance=5,
                confidence=0.55,
                confidence_factors={
                    "authenticated_human_capture": 0.3,
                    "exact_source_anchor": 0.2,
                    "human_review_required": 0.05,
                },
                source_anchors=derivation.evidence_anchors,
                risk_flags=tuple(risk_flags),
                state="review_blocked" if quarantined else "candidate",
                approval_verified=False,
            )
        )
    return tuple(candidates)
