"""Contract tests for seal/cost.py

Run:
    python3 -m pytest seal/tests/test_cost.py -v

Or standalone:
    python3 -m unittest seal.tests.test_cost -v
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from seal.cost import (
    CostEvent,
    CostTracker,
    _append_event,
    _cost_log_path,
    _count_tokens,
    _estimate_cost,
    _read_events,
    get_session_cost,
    reset_cost_log,
    _parse_args,
    _MODEL_PRICING,
)


# ---------------------------------------------------------------------------
# Token counting
# ---------------------------------------------------------------------------


class TokenCountingTests(unittest.TestCase):
    def test_fallback_returns_non_negative(self) -> None:
        count = _count_tokens("hello world", "claude-sonnet-4-6")
        self.assertGreaterEqual(count, 0)

    def test_empty_string_returns_zero(self) -> None:
        self.assertEqual(_count_tokens("", "claude-sonnet-4-6"), 0)

    def test_returns_int(self) -> None:
        self.assertIsInstance(_count_tokens("test", "claude-sonnet-4-6"), int)

    def test_longer_text_more_tokens(self) -> None:
        short = _count_tokens("hi", "claude-sonnet-4-6")
        long  = _count_tokens("hi " * 100, "claude-sonnet-4-6")
        self.assertGreater(long, short)


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


class EstimateCostTests(unittest.TestCase):
    def test_known_model_returns_expected_rates(self) -> None:
        in_c, out_c = _estimate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
        self.assertAlmostEqual(in_c,  3.0,  places=4)
        self.assertAlmostEqual(out_c, 15.0, places=4)

    def test_unknown_model_uses_default(self) -> None:
        in_c, out_c = _estimate_cost("unknown-model-xyz", 1_000_000, 0)
        rate_in = _MODEL_PRICING["default"][0]
        self.assertAlmostEqual(in_c, rate_in, places=4)

    def test_zero_tokens_zero_cost(self) -> None:
        in_c, out_c = _estimate_cost("claude-sonnet-4-6", 0, 0)
        self.assertEqual(in_c,  0.0)
        self.assertEqual(out_c, 0.0)

    def test_output_more_expensive_than_input(self) -> None:
        in_c, out_c = _estimate_cost("claude-sonnet-4-6", 100, 100)
        self.assertGreater(out_c, in_c)

    def test_opus_more_expensive_than_sonnet(self) -> None:
        _, out_sonnet = _estimate_cost("claude-sonnet-4-6", 0, 1_000_000)
        _, out_opus   = _estimate_cost("claude-opus-4-7",   0, 1_000_000)
        self.assertGreater(out_opus, out_sonnet)


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------


class CostTrackerTests(unittest.TestCase):
    def _tracker(self, tmp: str, **kwargs) -> CostTracker:
        return CostTracker(
            profile="test_p",
            model="claude-sonnet-4-6",
            seal_home=Path(tmp) / ".seal",
            **kwargs,
        )

    def test_session_id_auto_generated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = self._tracker(tmp)
            self.assertTrue(t.session_id)

    def test_custom_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = self._tracker(tmp, session_id="my-sess")
            self.assertEqual(t.session_id, "my-sess")

    def test_record_returns_cost_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = self._tracker(tmp, persist=False)
            ev = t.record(input_text="hello", output_text="world")
            self.assertIsInstance(ev, CostEvent)

    def test_record_increments_tick_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = self._tracker(tmp, persist=False)
            t.record(input_text="a", output_text="b")
            t.record(input_text="c", output_text="d")
            self.assertEqual(t.summary()["tick_count"], 2)

    def test_multiple_records_accumulate_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = self._tracker(tmp, persist=False)
            t.record(input_tokens=10, output_tokens=5)
            t.record(input_tokens=20, output_tokens=10)
            s = t.summary()
            self.assertEqual(s["total_input_tokens"],  30)
            self.assertEqual(s["total_output_tokens"], 15)
            self.assertEqual(s["total_tokens"],        45)

    def test_summary_has_required_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = self._tracker(tmp, persist=False)
            t.record(input_text="hi", output_text="there")
            keys = t.summary().keys()
            for k in ("session_id", "model", "profile",
                      "total_input_tokens", "total_output_tokens",
                      "total_tokens", "total_cost_usd", "tick_count"):
                self.assertIn(k, keys)

    def test_persist_false_writes_no_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seal_home = Path(tmp) / ".seal"
            t = CostTracker(profile="p", seal_home=seal_home, persist=False)
            t.record(input_text="hello")
            log = seal_home / "profiles" / "p" / "cost_log.jsonl"
            self.assertFalse(log.exists())

    def test_persist_true_writes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seal_home = Path(tmp) / ".seal"
            t = CostTracker(profile="p", seal_home=seal_home, persist=True)
            t.record(input_text="hello", output_text="world")
            log = seal_home / "profiles" / "p" / "cost_log.jsonl"
            self.assertTrue(log.exists())
            lines = [l for l in log.read_text().splitlines() if l.strip()]
            self.assertEqual(len(lines), 1)

    def test_explicit_tokens_bypass_text_counting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = self._tracker(tmp, persist=False)
            ev = t.record(input_tokens=999, output_tokens=1)
            self.assertEqual(ev.input_tokens, 999)
            self.assertEqual(ev.output_tokens, 1)

    def test_total_cost_usd_property(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = self._tracker(tmp, persist=False)
            t.record(input_tokens=1_000_000, output_tokens=0)
            self.assertGreater(t.total_cost_usd, 0.0)

    def test_total_tokens_property(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = self._tracker(tmp, persist=False)
            t.record(input_tokens=7, output_tokens=3)
            self.assertEqual(t.total_tokens, 10)

    def test_cost_is_positive_for_nonzero_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = self._tracker(tmp, persist=False)
            ev = t.record(input_tokens=100, output_tokens=50)
            self.assertGreater(ev.total_cost_usd, 0.0)

    def test_event_type_is_cost_tick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = self._tracker(tmp, persist=False)
            ev = t.record(input_text="x")
            self.assertEqual(ev.event_type, "cost_tick")


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------


class StorageTests(unittest.TestCase):
    def test_append_and_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seal_home = Path(tmp) / ".seal"
            event = CostEvent(
                session_id="s1", model="claude-sonnet-4-6",
                input_tokens=10, output_tokens=5,
                input_cost_usd=0.00003, output_cost_usd=0.000075,
                total_cost_usd=0.000105,
            )
            _append_event(event, "myprofile", seal_home)
            events = _read_events("myprofile", seal_home)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["session_id"], "s1")
            self.assertEqual(events[0]["input_tokens"], 10)

    def test_read_nonexistent_profile_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            events = _read_events("ghost", Path(tmp) / ".seal")
            self.assertEqual(events, [])

    def test_filter_by_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seal_home = Path(tmp) / ".seal"
            for sid in ("sess-A", "sess-B", "sess-A"):
                ev = CostEvent(
                    session_id=sid, model="m",
                    input_tokens=1, output_tokens=1,
                    input_cost_usd=0.0, output_cost_usd=0.0,
                    total_cost_usd=0.0,
                )
                _append_event(ev, "prof", seal_home)
            only_a = _read_events("prof", seal_home, session_id="sess-A")
            self.assertEqual(len(only_a), 2)

    def test_reset_removes_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seal_home = Path(tmp) / ".seal"
            ev = CostEvent(
                session_id="x", model="m",
                input_tokens=1, output_tokens=1,
                input_cost_usd=0.0, output_cost_usd=0.0,
                total_cost_usd=0.0,
            )
            _append_event(ev, "prof", seal_home)
            self.assertTrue(reset_cost_log("prof", seal_home))
            self.assertEqual(_read_events("prof", seal_home), [])

    def test_reset_nonexistent_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(reset_cost_log("ghost", Path(tmp) / ".seal"))


# ---------------------------------------------------------------------------
# get_session_cost
# ---------------------------------------------------------------------------


class GetSessionCostTests(unittest.TestCase):
    def _write_events(self, seal_home: Path, profile: str, count: int,
                      session_id: str = "s1") -> None:
        for _ in range(count):
            ev = CostEvent(
                session_id=session_id, model="claude-sonnet-4-6",
                input_tokens=100, output_tokens=50,
                input_cost_usd=0.0003, output_cost_usd=0.00075,
                total_cost_usd=0.00105,
            )
            _append_event(ev, profile, seal_home)

    def test_empty_profile_returns_zeros(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cost = get_session_cost("ghost", seal_home=Path(tmp) / ".seal")
            self.assertEqual(cost["total_tokens"], 0)
            self.assertEqual(cost["tick_count"],   0)
            self.assertEqual(cost["sessions"],     0)

    def test_aggregates_multiple_ticks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seal_home = Path(tmp) / ".seal"
            self._write_events(seal_home, "prof", 3)
            cost = get_session_cost("prof", seal_home=seal_home)
            self.assertEqual(cost["tick_count"],          3)
            self.assertEqual(cost["total_input_tokens"],  300)
            self.assertEqual(cost["total_output_tokens"], 150)

    def test_filter_by_session_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seal_home = Path(tmp) / ".seal"
            self._write_events(seal_home, "prof", 2, session_id="A")
            self._write_events(seal_home, "prof", 5, session_id="B")
            cost_a = get_session_cost("prof", session_id="A", seal_home=seal_home)
            self.assertEqual(cost_a["tick_count"], 2)

    def test_counts_distinct_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seal_home = Path(tmp) / ".seal"
            self._write_events(seal_home, "prof", 2, session_id="X")
            self._write_events(seal_home, "prof", 3, session_id="Y")
            cost = get_session_cost("prof", seal_home=seal_home)
            self.assertEqual(cost["sessions"], 2)

    def test_total_cost_is_sum(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seal_home = Path(tmp) / ".seal"
            self._write_events(seal_home, "prof", 4)
            cost = get_session_cost("prof", seal_home=seal_home)
            self.assertAlmostEqual(cost["total_cost_usd"], 4 * 0.00105, places=6)

    def test_result_has_all_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cost = get_session_cost("ghost", seal_home=Path(tmp) / ".seal")
            for k in ("profile", "session_id", "total_input_tokens",
                      "total_output_tokens", "total_tokens",
                      "total_cost_usd", "tick_count", "sessions"):
                self.assertIn(k, cost)


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------


class ParseArgsTests(unittest.TestCase):
    def test_status_requires_profile(self) -> None:
        with self.assertRaises(SystemExit):
            _parse_args(["status"])

    def test_status_command_parsed(self) -> None:
        args = _parse_args(["status", "--profile", "acme"])
        self.assertEqual(args.command, "status")
        self.assertEqual(args.profile, "acme")
        self.assertIsNone(args.session)

    def test_status_with_session(self) -> None:
        args = _parse_args(["status", "--profile", "p", "--session", "my-id"])
        self.assertEqual(args.session, "my-id")

    def test_reset_command_parsed(self) -> None:
        args = _parse_args(["reset", "--profile", "acme"])
        self.assertEqual(args.command, "reset")
        self.assertEqual(args.profile, "acme")

    def test_reset_requires_profile(self) -> None:
        with self.assertRaises(SystemExit):
            _parse_args(["reset"])


if __name__ == "__main__":
    unittest.main()
