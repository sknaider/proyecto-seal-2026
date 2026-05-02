"""SPECTRE attention_controller tests — D1 Active Sensing (sandbox v1).

A1: urgency keywords always produce score >= URGENCY_FLOOR (0.75)
A2: internal-tier sender scores higher than unknown sender
A3: goal match raises score when event content overlaps goal_stack[0]
A4: context match raises score when event overlaps last_user_message
A5: low-relevance event scores below ATTENTION_THRESHOLD (not escalated)
A6: should_escalate returns (True, score) above threshold
A7: should_escalate returns (False, score) below threshold
A8: attention_score stays < 20ms per call (latency budget)
A9: explain_score returns all 5 keys
A10: multiple urgency keywords → score capped at 1.0
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))

for mod in list(sys.modules.keys()):
    if "attention_controller" in mod:
        del sys.modules[mod]

import attention_controller as ac

_pass = 0
_fail = 0

WORKING_STATE_PATH = Path(__file__).parent.parent / "state" / "working_state.json"


def test(name: str, fn) -> None:
    global _pass, _fail
    num = _pass + _fail + 1
    try:
        fn()
        print(f"  ✅ A{num}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ A{num}: {name} — {e}")
        _fail += 1


def _reset_state(extra: dict | None = None) -> None:
    state = {
        "agent": "SPECTRE",
        "current_task": None,
        "goal_stack": [],
        "last_event": None,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        state.update(extra)
    WORKING_STATE_PATH.write_text(json.dumps(state, indent=2))
    ac._invalidate_ws_cache()


# ─────────────────────────────────────────────────────────────────────────────
print("\n=== TEST ATTENTION CONTROLLER: D1 ===")


def a1_urgency_floor():
    """Urgency keywords guarantee score >= URGENCY_FLOOR."""
    _reset_state()
    for kw in ["procedure_failed", "alert", "integrity violation", "critical", "anomaly"]:
        score = ac.attention_score(f"system reports {kw} detected", sender="monitor")
        assert score >= ac.URGENCY_FLOOR, f"'{kw}' gave score {score} < URGENCY_FLOOR={ac.URGENCY_FLOOR}"
test("Urgency keywords → score >= URGENCY_FLOOR (0.75)", a1_urgency_floor)


def a2_sender_trust_ordering():
    """William > external_user > monitor in sender trust tier."""
    _reset_state()
    neutral = "status update from the system"
    w_score = ac.attention_score(neutral, sender="william")
    u_score = ac.attention_score(neutral, sender="external_user")
    m_score = ac.attention_score(neutral, sender="monitor")
    assert w_score > u_score, f"william ({w_score}) should beat external_user ({u_score})"
    assert u_score > m_score, f"external_user ({u_score}) should beat monitor ({m_score})"
    print(f"    william={w_score:.3f}, external_user={u_score:.3f}, monitor={m_score:.3f}")
test("Sender trust: william > external_user > monitor", a2_sender_trust_ordering)


def a3_goal_match_raises_score():
    """Event content overlapping goal_stack[0] raises score."""
    _reset_state({"goal_stack": [{"name": "analyze_episodic_memory", "description": "analyze episodic memory retrieval patterns"}]})
    # Event with overlap
    score_high = ac.attention_score("episodic retrieval pattern analysis complete", sender="sensor_a")
    # Event with no overlap
    score_low = ac.attention_score("weather data for tomorrow morning", sender="sensor_a")
    assert score_high > score_low, f"goal-matched ({score_high}) should beat non-matched ({score_low})"
    print(f"    goal-matched={score_high:.3f}, unrelated={score_low:.3f}")
test("Goal match: content overlapping goal_stack[0] scores higher", a3_goal_match_raises_score)


def a4_context_match_raises_score():
    """Content overlapping last_event raises score."""
    _reset_state({"last_event": {"content_preview": "cortex processing memory consolidation pipeline"}})
    score_ctx = ac.attention_score("memory consolidation pipeline update", sender="sensor_a")
    score_off = ac.attention_score("weather data for tomorrow", sender="sensor_a")
    assert score_ctx > score_off, f"context-matched ({score_ctx}) should beat off-topic ({score_off})"
    print(f"    context-matched={score_ctx:.3f}, off-topic={score_off:.3f}")
test("Context match: content overlapping last_event scores higher", a4_context_match_raises_score)


def a5_low_relevance_below_threshold():
    """Generic low-relevance event scores below ATTENTION_THRESHOLD."""
    _reset_state()
    score = ac.attention_score("ping", sender="monitor")
    assert score < ac.ATTENTION_THRESHOLD, f"Low-relevance event scored {score} >= threshold {ac.ATTENTION_THRESHOLD}"
    print(f"    low-relevance score: {score:.3f} < threshold {ac.ATTENTION_THRESHOLD}")
test("Low-relevance event scores below ATTENTION_THRESHOLD", a5_low_relevance_below_threshold)


def a6_should_escalate_true():
    """should_escalate returns (True, score) for urgent events."""
    _reset_state()
    escalate, score = ac.should_escalate("critical failure detected in pipeline", sender="external_user")
    assert escalate is True, f"Urgent event should escalate: got {escalate}, score={score}"
    assert score >= ac.URGENCY_FLOOR
test("should_escalate=True for urgent events above threshold", a6_should_escalate_true)


def a7_should_escalate_false():
    """should_escalate returns (False, score) for low-relevance events."""
    _reset_state()
    escalate, score = ac.should_escalate("ping", sender="monitor")
    assert escalate is False, f"Low-relevance event should NOT escalate: got {escalate}, score={score}"
    print(f"    score={score:.3f}, threshold={ac.ATTENTION_THRESHOLD}")
test("should_escalate=False for low-relevance events below threshold", a7_should_escalate_false)


def a8_latency_under_20ms():
    """attention_score completes in < 20ms (D1 invariant)."""
    _reset_state({"goal_stack": [{"description": "process incoming events and triage them"}]})
    text = "episodic memory retrieval from hippocampus index lookup"
    times = []
    for _ in range(50):
        t0 = time.perf_counter()
        ac.attention_score(text, sender="external_user")
        times.append((time.perf_counter() - t0) * 1000)
    avg_ms = sum(times) / len(times)
    max_ms = max(times)
    print(f"    avg={avg_ms:.3f}ms, max={max_ms:.3f}ms (n=50)")
    assert avg_ms < 20, f"Average latency {avg_ms:.2f}ms exceeds 20ms budget"
    assert max_ms < 50, f"Max latency {max_ms:.2f}ms exceeds 50ms hard limit"
test("attention_score < 20ms avg, < 50ms max (n=50)", a8_latency_under_20ms)


def a9_explain_score_keys():
    """explain_score returns all 5 keys."""
    _reset_state()
    breakdown = ac.explain_score("test event content here", "external_user")
    expected = {"urgency", "sender", "goal_match", "context_match", "total"}
    assert set(breakdown.keys()) == expected, f"Missing keys: {expected - set(breakdown.keys())}"
    assert 0.0 <= breakdown["total"] <= 1.0
test("explain_score returns all 5 keys with valid ranges", a9_explain_score_keys)


def a10_score_capped_at_1():
    """Multiple urgency signals don't push score above 1.0."""
    _reset_state()
    score = ac.attention_score(
        "critical alert: integrity violation procedure_failed kernel anomaly crashed",
        sender="william",
    )
    assert score <= 1.0, f"Score {score} exceeds 1.0 cap"
    assert score >= ac.URGENCY_FLOOR
    print(f"    max-urgency score: {score:.4f} (capped)")
test("Score capped at 1.0 even with multiple urgency signals", a10_score_capped_at_1)


# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"Attention Controller D1 Tests: {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
