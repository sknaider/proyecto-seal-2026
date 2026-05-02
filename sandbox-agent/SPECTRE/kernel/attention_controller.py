"""SPECTRE Nivel 2 — Attention Controller (D1, sandbox v1).

Scores incoming events deterministically (<20ms, no LLM) on 4 dimensions:
  a) Goal match: keyword overlap with goal_stack[0]
  b) Context match: overlap with last_user_message content
  c) Urgency signals: procedure_failed, integrity, alert keywords
  d) Sender trust level

Score 0.0–1.0. Events above threshold → escalate to cortex.
Events below threshold → log as low-attention and return score.

Invariants:
  - All scoring is rule-based (no LLM, no DB reads in hot path)
  - goal_stack and last_user_message read from working_state at module level
    and refreshed lazily (max once per 5s) to stay within 20ms budget
  - Hard urgency signals always yield score >= URGENCY_FLOOR (0.7)

Ref: spec_spectre_contract_v2.md §NIVEL 2 D1
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

# ── Config ────────────────────────────────────────────────────────────────────

ATTENTION_THRESHOLD = 0.35    # events below this are low-attention (not escalated)
URGENCY_FLOOR = 0.75          # hard urgency signals always score at least this
_CACHE_TTL_S = 5.0            # working_state refresh interval (seconds)

# ── Urgency keywords (D1 invariant: always escalate) ──────────────────────────

_URGENCY_KEYWORDS = {
    "procedure_failed", "integrity violation", "violation", "critical",
    "alert", "alarm", "failure", "crashed", "error", "exception",
    "unauthorized", "intrusion", "anomaly", "degraded", "offline",
    "checkpoint_restored", "kernel", "corruption",
}

# ── Sender trust tiers ────────────────────────────────────────────────────────
# Higher trust = higher base score (external signals matter more)

_SENDER_TRUST: dict[str, float] = {
    "william": 1.0,
    "henry": 1.0,
    "external_user": 0.6,
    "user": 0.6,
    "api_gateway": 0.5,
    "webhook_client": 0.45,
    "sensor_a": 0.4,
    "sensor_b": 0.4,
    "monitor": 0.35,
}
_SENDER_TRUST_DEFAULT = 0.4


# ── Working state cache (lazy refresh) ───────────────────────────────────────

_ws_cache: dict[str, Any] = {}
_ws_last_read: float = 0.0
_WS_PATH: Path | None = None


def _get_ws_path() -> Path:
    global _WS_PATH
    if _WS_PATH is None:
        _WS_PATH = Path(__file__).parent.parent / "state" / "working_state.json"
    return _WS_PATH


def _read_working_state() -> dict[str, Any]:
    global _ws_cache, _ws_last_read
    now = time.monotonic()
    if now - _ws_last_read < _CACHE_TTL_S and _ws_cache:
        return _ws_cache
    try:
        ws_path = _get_ws_path()
        if ws_path.exists():
            _ws_cache = json.loads(ws_path.read_text())
            _ws_last_read = now
    except Exception:
        pass
    return _ws_cache


def _invalidate_ws_cache() -> None:
    """Call after working_state is modified to force next read."""
    global _ws_last_read
    _ws_last_read = 0.0


# ── Keyword extraction (shared with spectre_handlers, no import cycle) ────────

_STOPWORDS = {
    "a", "de", "el", "la", "los", "las", "en", "con", "que", "es",
    "se", "no", "por", "del", "una", "un", "al", "lo", "su", "si",
    "the", "is", "in", "of", "to", "and", "for", "are", "was", "it",
    "this", "that", "from", "with", "have", "has", "been", "will",
}


def _keywords(text: str, max_n: int = 8) -> set[str]:
    words = [w.strip(".,!?¿¡:;\"'()[]") for w in text.lower().split() if len(w) > 3]
    filtered = [w for w in words if w not in _STOPWORDS]
    return set(filtered[:max_n])


# ── Scoring dimensions ────────────────────────────────────────────────────────

def _score_goal_match(content_kw: set[str], state: dict) -> float:
    """(a) Overlap of event keywords with current goal_stack[0]."""
    goal_stack: list[dict] = state.get("goal_stack", [])
    if not goal_stack:
        return 0.0
    top_goal = goal_stack[0]
    goal_text = ""
    if isinstance(top_goal, dict):
        goal_text = top_goal.get("description", "") or top_goal.get("name", "")
    elif isinstance(top_goal, str):
        goal_text = top_goal
    if not goal_text:
        return 0.0
    goal_kw = _keywords(goal_text)
    if not goal_kw:
        return 0.0
    overlap = len(content_kw & goal_kw)
    return min(1.0, overlap / max(len(goal_kw), 1) * 2.0)


def _score_context_match(content_kw: set[str], state: dict) -> float:
    """(b) Overlap with last_user_message content."""
    last_msg = state.get("last_event") or state.get("last_user_message") or {}
    if isinstance(last_msg, dict):
        msg_text = last_msg.get("content_preview", "") or last_msg.get("content", "")
    else:
        msg_text = str(last_msg)
    if not msg_text:
        return 0.0
    msg_kw = _keywords(msg_text)
    if not msg_kw:
        return 0.0
    overlap = len(content_kw & msg_kw)
    return min(1.0, overlap / max(len(msg_kw), 1) * 2.0)


def _score_urgency(content: str) -> float:
    """(c) Hard urgency signals — if present, guarantee >= URGENCY_FLOOR."""
    content_lower = content.lower()
    for kw in _URGENCY_KEYWORDS:
        if kw in content_lower:
            return URGENCY_FLOOR
    return 0.0


def _score_sender(sender: str) -> float:
    """(d) Sender trust tier."""
    return _SENDER_TRUST.get(sender.lower(), _SENDER_TRUST_DEFAULT)


# ── Public API ─────────────────────────────────────────────────────────────────

def attention_score(
    content: str,
    sender: str,
    metadata: dict[str, Any] | None = None,
) -> float:
    """
    Compute attention score [0.0, 1.0] for an incoming event.

    Weights:
      urgency:  0.40 (hard floor)
      sender:   0.25
      goal:     0.20
      context:  0.15

    Returns score. Does NOT modify state.
    """
    meta = metadata or {}
    t0 = time.monotonic()

    state = _read_working_state()
    content_kw = _keywords(content)

    urgency = _score_urgency(content)
    sender_score = _score_sender(sender)
    goal = _score_goal_match(content_kw, state)
    context = _score_context_match(content_kw, state)

    # Weighted sum
    score = (
        urgency * 0.40
        + sender_score * 0.25
        + goal * 0.20
        + context * 0.15
    )
    score = max(urgency, score)  # urgency is a floor, not just weight
    score = min(1.0, score)

    elapsed_ms = (time.monotonic() - t0) * 1000
    if elapsed_ms > 15:
        print(f"[spectre/attention] WARNING: scoring took {elapsed_ms:.1f}ms > 15ms budget", flush=True)

    return round(score, 4)


def should_escalate(
    content: str,
    sender: str,
    metadata: dict[str, Any] | None = None,
    threshold: float = ATTENTION_THRESHOLD,
) -> tuple[bool, float]:
    """
    Returns (escalate: bool, score: float).

    escalate=True  → route to cortex escalation queue
    escalate=False → low-attention, safe to drop or defer
    """
    score = attention_score(content, sender, metadata)
    return score >= threshold, score


def explain_score(content: str, sender: str) -> dict[str, float]:
    """Return per-dimension breakdown for debugging/tests."""
    state = _read_working_state()
    content_kw = _keywords(content)
    return {
        "urgency": _score_urgency(content),
        "sender": _score_sender(sender),
        "goal_match": _score_goal_match(content_kw, state),
        "context_match": _score_context_match(content_kw, state),
        "total": attention_score(content, sender),
    }
