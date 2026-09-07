"""Conservative guard for automatic memory replacement.

Semantic proximity is useful for recall but is not proof that two historical
memories are duplicates.  Automatic invalidation is allowed only for exact or
near-verbatim content; all merely related memories must coexist.
"""

from __future__ import annotations

import re


_TOKEN_RE = re.compile(r"[\wáéíóúüñ]+", re.IGNORECASE)


def _normalized_text(value: str) -> str:
    return " ".join(_TOKEN_RE.findall((value or "").casefold()))


def _tokens(value: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall((value or "").casefold()) if len(token) >= 3}


def safe_to_auto_replace(
    old_content: str,
    new_content: str,
    similarity: float,
    *,
    old_importance: int = 0,
) -> bool:
    """Return True only when invalidation is demonstrably duplicate-safe.

    William's preservation floor is absolute for automatic paths: memories at
    importance 7 or above are never replaced, even by an exact duplicate.
    """
    if old_importance >= 7:
        return False
    old_norm = _normalized_text(old_content)
    new_norm = _normalized_text(new_content)
    if not old_norm or not new_norm:
        return False
    if old_norm == new_norm:
        return True
    if similarity < 0.97:
        return False

    old_tokens = _tokens(old_content)
    new_tokens = _tokens(new_content)
    if not old_tokens or not new_tokens:
        return False
    overlap = old_tokens & new_tokens
    union = old_tokens | new_tokens
    jaccard = len(overlap) / len(union)
    containment = len(overlap) / min(len(old_tokens), len(new_tokens))
    return jaccard >= 0.80 and containment >= 0.90
