import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from memory.agent_loop_guard import ToolLoopGuard, call_key
from memory.compaction_observer import observe_compaction


def test_loop_warns_then_stops_and_resets_on_progress():
    guard = ToolLoopGuard()
    decisions = [guard.observe("t", "Read", {"path": "/tmp/a"}) for _ in range(5)]
    assert [d.action for d in decisions] == ["allow", "allow", "warn", "warn", "stop"]
    changed = guard.observe("t", "Read", {"path": "/tmp/b"})
    assert changed.action == "allow" and changed.repetitions == 1


def test_call_key_is_order_independent_and_does_not_expose_secret():
    a = call_key("tool", {"b": 2, "a": 1, "token": "super-secret"})
    b = call_key("tool", {"token": "super-secret", "a": 1, "b": 2})
    assert a == b and "super-secret" not in a


def test_compaction_observer_records_effect_and_sink_failure_is_nonfatal():
    seen = []
    result, obs = observe_compaction("ADA", ["a", "b", "c"], lambda xs: ["summary"], threshold=90, sink=lambda _: (_ for _ in ()).throw(RuntimeError("sink")))
    assert result == ["summary"]
    assert obs.messages_before == 3 and obs.messages_after == 1
    assert obs.chars_before == 3 and obs.chars_after == 7
    assert obs.degraded is False


def test_compaction_failure_is_explicit_and_passthrough():
    result, obs = observe_compaction("ADA", ["keep"], lambda _: 42)
    assert result == ["keep"]
    assert obs.degraded is True
    assert obs.exit_reason == "compaction_failed_passthrough"
