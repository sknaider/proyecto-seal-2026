"""Query-time emotional ranking helpers for SOUL memory retrieval."""
from __future__ import annotations

import re
from typing import Any


EMOTIONAL_CATEGORIES = {
    "emotion",
    "emotional",
    "emotional_diary",
    "diary",
    "relationship",
    "identity_emotional",
}

EMOTIONAL_KEYWORDS = frozenset({
    "emotion", "emotional", "feel", "felt", "feeling", "proud", "pride",
    "relief", "relieved", "worried", "worry", "frustrated", "frustration",
    "trust", "trusted", "afraid", "fear", "angry", "sad", "happy",
    "amor", "emocion", "emocional", "siento", "sentir", "sintio",
    "orgullo", "orgulloso", "alivio", "preocupado", "preocupacion",
    "frustrado", "frustracion", "confianza", "triste", "feliz",
})


def emotional_signal_strength(query: str) -> float:
    """Return a 0-1 signal for whether the query asks for emotional memory."""
    lower = (query or "").lower()
    hits = sum(1 for keyword in EMOTIONAL_KEYWORDS if re.search(r"\b" + re.escape(keyword) + r"\b", lower))
    if hits == 0:
        return 0.0
    return min(1.0, 0.35 + hits * 0.18)


def _content_keyword_overlap(query: str, content: str) -> int:
    query_terms = {kw for kw in EMOTIONAL_KEYWORDS if re.search(r"\b" + re.escape(kw) + r"\b", query.lower())}
    if not query_terms:
        return 0
    lower_content = (content or "").lower()
    return sum(1 for kw in query_terms if re.search(r"\b" + re.escape(kw) + r"\b", lower_content))


def emotional_relevance_multiplier(query: str, entry: dict[str, Any]) -> float:
    """Compute a bounded multiplier for emotional memories under emotional queries."""
    signal = emotional_signal_strength(query)
    if signal <= 0:
        return 1.0

    category = str(entry.get("category") or "").lower()
    content = str(entry.get("content") or "")
    valence = abs(float(entry.get("valence") or 0.0))
    arousal = max(0.0, float(entry.get("arousal") or 0.0))
    overlap = _content_keyword_overlap(query, content)

    multiplier = 1.0
    if category in EMOTIONAL_CATEGORIES or "emotion" in category:
        multiplier += 0.75 * signal
    if valence >= 0.3:
        multiplier += min(0.45, valence * 0.35) * signal
    if arousal >= 0.3:
        multiplier += min(0.25, arousal * 0.18) * signal
    if overlap:
        multiplier += min(0.60, overlap * 0.16) * signal
    return min(2.8, multiplier)


def rerank_emotional_results(query: str, entries: list[dict[str, Any]], *, score_key: str) -> None:
    """Apply in-place emotional relevance reranking to a list of result entries."""
    signal = emotional_signal_strength(query)
    if signal <= 0:
        return
    for entry in entries:
        try:
            old_score = float(entry.get(score_key) or 0.0)
        except (TypeError, ValueError):
            continue
        multiplier = emotional_relevance_multiplier(query, entry)
        category = str(entry.get("category") or "").lower()
        content = str(entry.get("content") or "")
        overlap = _content_keyword_overlap(query, content)
        additive = 0.0
        # FIX #3b (ALICE 2026-05-29, William "adelante"): el additive plano era content-blind
        # y rescataba memorias emocionales IRRELEVANTES sobre neutras relevantes (A/B Test 3 FAIL).
        # Ahora el additive de categoría SOLO aplica si la memoria comparte términos del query
        # (overlap>0) → emoción RELEVANTE se boostea, emoción irrelevante NO se rescata.
        if (category in EMOTIONAL_CATEGORIES or "emotion" in category) and overlap > 0:
            additive += 1.60 * signal
        if overlap:
            additive += min(0.30, overlap * 0.08) * signal
        if multiplier <= 1.0 and additive <= 0.0:
            continue
        entry[score_key] = round(old_score * multiplier + additive, 6)
        entry["emotional_query_boost"] = round(multiplier, 3)
