"""Token estimator — fast local count without external API calls.

Uses char/4 approximation (good enough for thresholds; not billing-grade).
Compatible with asyncpg row objects and plain strings.
"""
from __future__ import annotations

CHARS_PER_TOKEN = 4
CLAUDE_CONTEXT_WINDOW = 200_000  # tokens — Claude Sonnet/Opus


def estimate(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text) // CHARS_PER_TOKEN)


def estimate_turns(turns: list[dict]) -> int:
    return sum(estimate(t.get("content") or t.get("content", "")) for t in turns)


def context_pct(active_tokens: int, window: int = CLAUDE_CONTEXT_WINDOW) -> float:
    """Return 0.0–1.0 fraction of context window used."""
    return min(1.0, active_tokens / window)
