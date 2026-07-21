"""Local deterministic extractive processor with measurable coverage."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime

from .contracts import (
    CoverageMap,
    Derivation,
    NormalizedDocument,
    canonical_json,
    sha256_bytes,
    sha256_text,
    stable_uuid,
)
from .profiles import Profile, get_profile
from .segment import Sentence, sentence_anchor, split_sentences


TOKEN_RE = re.compile(r"[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)
STOPWORDS = frozenset(
    "a al algo ante bajo con contra de del desde durante e el ella ellas ellos en entre era es esa ese eso esta este esto fue ha hay la las lo los más muy ni no o para pero por que se sin sobre su sus un una y ya the of to in is and for on with as by".split()
)


def tokens(value: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in TOKEN_RE.findall(value) if token.casefold() not in STOPWORDS)


def _similarity(left: Sentence, right: Sentence) -> float:
    a, b = set(tokens(left.text)), set(tokens(right.text))
    return len(a & b) / len(a | b) if a and b else 0.0


def _sentence_scores(sentences: tuple[Sentence, ...], profile: Profile) -> dict[int, float]:
    frequencies = Counter(token for sentence in sentences for token in tokens(sentence.text))
    max_frequency = max(frequencies.values(), default=1)
    section_positions: dict[str, int] = defaultdict(int)
    scores: dict[int, float] = {}
    for sentence in sentences:
        sentence_tokens = tokens(sentence.text)
        lexical = sum(frequencies[token] / max_frequency for token in sentence_tokens)
        lexical /= math.sqrt(max(len(sentence_tokens), 1))
        keyword_hits = sum(1 for token in sentence_tokens if token in profile.keywords)
        structural = 0.7 if section_positions[sentence.section] == 0 else 0.0
        section_positions[sentence.section] += 1
        protection = min(4.0, 0.8 * len(sentence.protected_spans))
        heading = 0.35 if sentence.heading_path else 0.0
        length_penalty = 0.5 if len(sentence.text) < 20 or len(sentence.text) > 800 else 0.0
        scores[sentence.ordinal] = lexical + keyword_hits * 1.25 + structural + protection + heading - length_penalty
    return scores


def _select_sentences(sentences: tuple[Sentence, ...], profile: Profile) -> tuple[Sentence, ...]:
    if not sentences:
        return ()
    target = max(profile.min_sentences, math.ceil(len(sentences) * profile.target_ratio))
    target = min(profile.max_sentences, max(target, 1))
    required = {sentence.ordinal for sentence in sentences if sentence.protected_spans}
    section_first: dict[str, int] = {}
    for sentence in sentences:
        section_first.setdefault(sentence.section, sentence.ordinal)
    required.update(section_first.values())
    target = max(target, len(required))

    scores = _sentence_scores(sentences, profile)
    selected: list[Sentence] = [sentence for sentence in sentences if sentence.ordinal in required]
    candidates = sorted(
        (sentence for sentence in sentences if sentence.ordinal not in required),
        key=lambda sentence: (-scores[sentence.ordinal], sentence.ordinal),
    )
    while candidates and len(selected) < target:
        best = max(
            candidates,
            key=lambda sentence: (
                scores[sentence.ordinal]
                - 0.65 * max((_similarity(sentence, chosen) for chosen in selected), default=0.0),
                -sentence.ordinal,
            ),
        )
        selected.append(best)
        candidates.remove(best)
    return tuple(sorted(selected, key=lambda sentence: sentence.ordinal))


def derive_digest(
    document: NormalizedDocument,
    *,
    profile_id: str = "generic_v1",
    now: datetime | None = None,
) -> Derivation:
    profile = get_profile(profile_id)
    sentences = split_sentences(document)
    selected = _select_sentences(sentences, profile)
    content = "\n".join(sentence.text for sentence in selected)
    anchors = tuple(sentence_anchor(document, sentence) for sentence in selected)
    sections = tuple(dict.fromkeys(sentence.section for sentence in sentences))
    covered = {sentence.section for sentence in selected}
    all_spans = [span for sentence in sentences for span in sentence.protected_spans]
    preserved_spans = [span for sentence in selected for span in sentence.protected_spans]
    warnings: list[str] = []
    if len(covered) != len(sections):
        warnings.append("section_coverage_incomplete")
    if len(preserved_spans) != len(all_spans):
        warnings.append("protected_span_coverage_incomplete")
    gate_passed = not warnings and all(
        document.normalized_text[anchor.start_char : anchor.end_char] == sentence.text
        for anchor, sentence in zip(anchors, selected, strict=True)
    )
    coverage = CoverageMap(
        total_sentences=len(sentences),
        selected_sentences=len(selected),
        total_sections=len(sections),
        covered_sections=len(covered),
        total_protected_spans=len(all_spans),
        preserved_protected_spans=len(preserved_spans),
        compression_ratio=len(content) / max(len(document.normalized_text), 1),
        section_coverage={section: section in covered for section in sections},
        omitted_sections=tuple(section for section in sections if section not in covered),
        warnings=tuple(warnings),
        gate_passed=gate_passed,
    )
    config = {
        "processor": "extractive_v1",
        "version": "1.1.0",
        "profile": profile.profile_id,
        "target_ratio": profile.target_ratio,
        "protected_spans_required": True,
        "section_minimum": 1,
        "mmr_lambda": 0.65,
    }
    config_hash = sha256_bytes(canonical_json(config))
    output_hash = sha256_text(content)
    return Derivation(
        derivation_id=stable_uuid(
            "derivation", str(document.document_id), "extractive_v1", config_hash, output_hash
        ),
        document_id=document.document_id,
        kind="digest",
        processor_id="extractive_v1",
        processor_version="1.1.0",
        config_hash_sha256=config_hash,
        input_hash_sha256=document.normalized_hash_sha256,
        output_hash_sha256=output_hash,
        content=content,
        coverage=coverage,
        evidence_anchors=anchors,
        created_at=now or datetime.now(UTC),
    )
