"""SEAL SOUL — Wave 3 scoring functions (standalone, no DB dependency).

Pre-computes new Wave 3 scores for memories:
- surprise_score: novelty relative to existing knowledge (A-MEM Zettelkasten)
- decay_score: time-based relevance decay (HALO + Learning to Forget)
- recall_boost: boost factor based on recall history (MemRL Bellman)

These functions are DB-agnostic. They accept raw values and return scores.
Wiring to the actual DB columns happens after William approves Wave 3 schema migration.

References:
- A-MEM: arxiv:2502.12110 (Zettelkasten-inspired memory)
- HALO: arxiv:2505.07509 (half-life decay)
- Learning to Forget: arxiv:2603.14517 (KV cache decay, adapted)
- MemRL: arxiv:2601.03192 (Bellman utility updates)
- MAGMA: multi-graph temporal memory
"""
from __future__ import annotations

import math
from datetime import datetime, timezone


def compute_surprise_score(
    new_embedding: list[float],
    existing_similarities: list[float],
    novelty_threshold: float = 0.3,
) -> float:
    """Compute surprise score for a new memory (A-MEM Zettelkasten).

    Surprise = how different is this memory from what's already stored?
    High surprise → memory is more noteworthy → ranks higher in retrieval.

    Args:
        new_embedding: Vector embedding of the new memory content.
        existing_similarities: Cosine similarities to nearest neighbors
            (from Qdrant search, top-5 is sufficient).
        novelty_threshold: Below this similarity → memory is surprising.

    Returns:
        Float in [0.0, 1.0]. 1.0 = completely novel, 0.0 = exact duplicate.

    Formula (from A-MEM):
        If max_similarity < novelty_threshold: surprise = 1.0
        Else: surprise = 1.0 - max_similarity (linear falloff)
    """
    if not existing_similarities:
        return 1.0  # No existing memories → maximum surprise

    max_sim = max(existing_similarities)

    if max_sim < novelty_threshold:
        return 1.0  # Very different from anything stored
    else:
        return round(max(0.0, 1.0 - max_sim), 4)


def compute_decay_score(
    importance: int,
    category: str,
    created_at: datetime,
    valence: float = 0.0,
    arousal: float = 0.0,
    now: datetime | None = None,
) -> float:
    """Pre-compute decay score for a memory (HALO half-life model).

    Stored in DB as `decay_score` column. Updated by SleepGate nightly.
    Allows fast retrieval ranking without recomputing decay at query time.

    Args:
        importance: Memory importance (1-10). >= 10 = immortal.
        category: Memory category (determines half-life).
        created_at: When the memory was created.
        valence: Emotional valence [-1, 1]. High |valence| → slower decay.
        arousal: Emotional arousal [0, 1]. High arousal → slower decay.
        now: Current time (injectable for testing).

    Returns:
        Float in [0.0, 1.0]. 1.0 = fully relevant, 0.0 = fully decayed.
    """
    from soul.core.scoring import HALF_LIFE_BY_CATEGORY, HALF_LIFE_DEFAULT

    if now is None:
        now = datetime.now(timezone.utc)

    # Ensure timezone-aware comparison
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    days_old = (now - created_at).total_seconds() / 86400.0

    if importance >= 10:
        return 1.0  # Immortal

    half_life = HALF_LIFE_BY_CATEGORY.get(category, HALF_LIFE_DEFAULT)

    if importance >= 8:
        half_life = max(half_life * 2.0, 180.0)

    # Emotional modulation: high intensity → slower decay
    emotional_intensity = max(abs(valence), arousal)
    if emotional_intensity > 0.5:
        half_life *= 1.0 + min(emotional_intensity, 1.0)

    decay = math.pow(0.5, days_old / half_life) if half_life > 0 else 0.0
    return round(decay, 6)


def compute_recall_boost(
    recall_count: int,
    last_recalled_at: datetime | None,
    now: datetime | None = None,
) -> float:
    """Compute recall-based boost factor (MemRL Bellman utility).

    Memories that are recalled frequently AND recently get a boost.
    This is the "RL utility" signal: frequent use = valuable memory.

    Args:
        recall_count: How many times this memory was retrieved.
        last_recalled_at: Timestamp of most recent retrieval. None = never.
        now: Current time (injectable for testing).

    Returns:
        Float in [1.0, 1.5]. Base 1.0, max 1.5× boost.

    Formula:
        frequency_boost = min(recall_count / 10.0, 0.3)  → up to +30%
        recency_boost   = 0.2 * exp(-days_since_recall / 7.0)  → up to +20%
        total           = 1.0 + frequency_boost + recency_boost
    """
    if now is None:
        now = datetime.now(timezone.utc)

    frequency_boost = min(recall_count / 10.0, 0.3)

    recency_boost = 0.0
    if last_recalled_at is not None:
        if last_recalled_at.tzinfo is None:
            last_recalled_at = last_recalled_at.replace(tzinfo=timezone.utc)
        days_since = (now - last_recalled_at).total_seconds() / 86400.0
        recency_boost = 0.2 * math.exp(-days_since / 7.0)

    total = 1.0 + frequency_boost + recency_boost
    return round(min(total, 1.5), 4)


def blend_v3_score(
    semantic_similarity: float,
    decay_score: float,
    surprise_score: float,
    recall_boost: float,
    importance: int,
    confidence: float = 1.0,
    utility: float = 0.5,
) -> float:
    """Wave 3 blended retrieval score.

    Combines all signals into final ranking score.

    Weights (tunable):
        semantic:    0.40  — core relevance
        utility:     0.20  — RL learned value
        confidence:  0.15  — Hindsight degradation
        surprise:    0.10  — novelty bonus
        decay:       0.10  — time relevance (pre-computed)
        recall:      0.05  — frequency/recency bonus (multiplicative)

    Returns:
        Float in [0.0, ~1.5]. Higher = more relevant.
    """
    conf = max(0.0, min(1.0, confidence))
    base = (
        semantic_similarity * 0.40
        + utility * 0.20
        + conf * 0.15
        + surprise_score * 0.10
        + decay_score * 0.10
    )
    imp_weight = 0.5 + (importance / 20.0)
    return round(base * imp_weight * recall_boost, 6)
