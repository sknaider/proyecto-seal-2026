"""Tests for SEAL Context Management Architecture v3.

Covers:
  - tool_block_flattener: flatten_tool_blocks, _extract_tool_use_id (JSON string bug)
  - context_compressor: _is_cron_noise, _tier1_prune (two-pass tail shift bug)
  - turn_extractor: is_significant, _parse_facts, _summarize_tools
"""
import json
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tool_block_flattener import (
    FlatTurn,
    flatten_tool_blocks,
    flat_turns_to_text,
    _extract_tool_uses,
    _extract_tool_use_id,
    _extract_tool_name,
    _content_summary,
    _input_summary,
)
from context_compressor import _is_cron_noise, _tier1_prune, TIER1_PRUNE_THRESHOLD
from turn_extractor import is_significant, _parse_facts, _summarize_tools, EXTRACT_TOOLS


# ── tool_block_flattener ──────────────────────────────────────────────────────


class TestFlattenEmpty(unittest.TestCase):
    def test_empty_list_returns_empty(self):
        result = flatten_tool_blocks([])
        self.assertEqual(result, [])


class TestFlattenSimpleToolUse(unittest.TestCase):
    def test_tool_use_without_result_becomes_synthetic(self):
        turns = [
            {
                "role": "assistant",
                "content": json.dumps([
                    {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}}
                ]),
                "turn_index": 0,
                "id": 1,
            }
        ]
        flat = flatten_tool_blocks(turns)
        texts = [t.content for t in flat]
        self.assertTrue(any("tool_call: Bash" in t for t in texts))
        self.assertTrue(any("synthetic" in t for t in texts))

    def test_tool_use_with_matching_result_no_synthetic(self):
        turns = [
            {
                "role": "assistant",
                "content": json.dumps([
                    {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"file_path": "/tmp/x"}}
                ]),
                "turn_index": 0,
                "id": 1,
            },
            {
                "role": "tool",
                "content": json.dumps([
                    {"type": "tool_result", "tool_use_id": "tu1", "content": "file contents here"}
                ]),
                "turn_index": 1,
                "id": 2,
            },
        ]
        flat = flatten_tool_blocks(turns)
        synthetic = [t for t in flat if t.is_synthetic]
        self.assertEqual(len(synthetic), 0, "should be no synthetics when result is paired")
        self.assertEqual(len(flat), 2)


class TestExtractToolUseIdJsonString(unittest.TestCase):
    """Regression: _extract_tool_use_id failed when content is a JSON string (DB storage format)."""

    def test_json_string_content_parsed_correctly(self):
        content_str = json.dumps([
            {"type": "tool_result", "tool_use_id": "abc123", "content": "output"}
        ])
        result = _extract_tool_use_id(content_str)
        self.assertEqual(result, "abc123")

    def test_list_content_parsed_correctly(self):
        content_list = [{"type": "tool_result", "tool_use_id": "xyz789", "content": "out"}]
        result = _extract_tool_use_id(content_list)
        self.assertEqual(result, "xyz789")

    def test_missing_id_returns_empty(self):
        result = _extract_tool_use_id(json.dumps([{"type": "tool_result", "content": "x"}]))
        self.assertEqual(result, "")


class TestExtractToolNameJsonString(unittest.TestCase):
    """Regression: _extract_tool_name failed on JSON string content."""

    def test_json_string_returns_name(self):
        content_str = json.dumps([
            {"type": "tool_result", "tool_use_id": "t1", "tool_name": "Edit", "content": "ok"}
        ])
        result = _extract_tool_name(content_str)
        self.assertEqual(result, "Edit")

    def test_no_name_returns_none(self):
        content_str = json.dumps([{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}])
        result = _extract_tool_name(content_str)
        self.assertIsNone(result)


class TestFlattenMatchedPair(unittest.TestCase):
    """Full round-trip: tool_use + matching tool_result → 2 flat turns, 0 synthetics."""

    def test_matched_pair_produces_two_flat_turns(self):
        use_content = json.dumps([
            {"type": "tool_use", "id": "u1", "name": "Write", "input": {"file_path": "/x", "content": "y"}}
        ])
        result_content = json.dumps([
            {"type": "tool_result", "tool_use_id": "u1", "tool_name": "Write", "content": "written"}
        ])
        turns = [
            {"role": "assistant", "content": use_content, "turn_index": 0, "id": 10},
            {"role": "tool", "content": result_content, "turn_index": 1, "id": 11},
        ]
        flat = flatten_tool_blocks(turns)
        self.assertEqual(len(flat), 2)
        self.assertFalse(any(t.is_synthetic for t in flat))
        self.assertIn("tool_call: Write", flat[0].content)
        self.assertIn("tool_result: Write", flat[1].content)


class TestFlatTurnsToText(unittest.TestCase):
    def test_produces_labeled_lines(self):
        flat = [
            FlatTurn(role="user", content="hello", turn_index=0),
            FlatTurn(role="assistant", content="hi", turn_index=1),
        ]
        text = flat_turns_to_text(flat)
        self.assertIn("[USER turn 0]", text)
        self.assertIn("[ASSISTANT turn 1]", text)


class TestMultipleToolCalls(unittest.TestCase):
    def test_two_tool_calls_in_one_assistant_turn(self):
        use_content = json.dumps([
            {"type": "tool_use", "id": "a1", "name": "Read", "input": {}},
            {"type": "tool_use", "id": "a2", "name": "Bash", "input": {"command": "ls"}},
        ])
        result1 = json.dumps([{"type": "tool_result", "tool_use_id": "a1", "tool_name": "Read", "content": "data"}])
        result2 = json.dumps([{"type": "tool_result", "tool_use_id": "a2", "tool_name": "Bash", "content": "ok"}])
        turns = [
            {"role": "assistant", "content": use_content, "turn_index": 0, "id": 1},
            {"role": "tool", "content": result1, "turn_index": 1, "id": 2},
            {"role": "tool", "content": result2, "turn_index": 2, "id": 3},
        ]
        flat = flatten_tool_blocks(turns)
        synthetics = [t for t in flat if t.is_synthetic]
        self.assertEqual(len(synthetics), 0)
        names = [t.content for t in flat if "tool_call" in t.content]
        self.assertEqual(len(names), 2)


class TestNonToolAssistantTurn(unittest.TestCase):
    def test_plain_text_assistant_preserved(self):
        turns = [
            {"role": "assistant", "content": "This is a plain response.", "turn_index": 0, "id": 1}
        ]
        flat = flatten_tool_blocks(turns)
        self.assertEqual(len(flat), 1)
        self.assertNotIn("tool_call", flat[0].content)
        self.assertIn("plain response", flat[0].content)


class TestTurnIndexSort(unittest.TestCase):
    def test_flat_turns_sorted_by_index(self):
        turns = [
            {"role": "user", "content": "last", "turn_index": 10, "id": 3},
            {"role": "user", "content": "first", "turn_index": 1, "id": 1},
            {"role": "user", "content": "mid", "turn_index": 5, "id": 2},
        ]
        flat = flatten_tool_blocks(turns)
        indices = [t.turn_index for t in flat]
        self.assertEqual(indices, sorted(indices))


# ── context_compressor ────────────────────────────────────────────────────────


class TestIsCronNoise(unittest.TestCase):
    def test_heartbeat_tick_is_noise(self):
        self.assertTrue(_is_cron_noise("[heartbeat] tick 42"))

    def test_monitor_no_new_messages(self):
        self.assertTrue(_is_cron_noise("[Monitor] no new messages"))

    def test_cron_create_tag(self):
        self.assertTrue(_is_cron_noise("[CronCreate] scheduled job"))

    def test_normal_text_not_noise(self):
        self.assertFalse(_is_cron_noise("ADA fixed the bug in context_manager.py"))

    def test_empty_string_not_noise(self):
        self.assertFalse(_is_cron_noise(""))


class TestTier1PruneTwoPass(unittest.TestCase):
    """Regression: single-pass pruning shifted tail after noise removal."""

    def _make_row(self, i, role="assistant", content=None, tokens=10):
        return {
            "id": i,
            "turn_index": i,
            "role": role,
            "content": content or f"turn content {i}",
            "tokens_est": tokens,
            "tool_name": "tool",
        }

    def test_cron_noise_removed(self):
        rows = [self._make_row(i) for i in range(10)]
        rows[3]["content"] = "[heartbeat] tick 3"
        result = _tier1_prune(rows, keep_tail=3)
        contents = [r["content"] for r in result]
        self.assertNotIn("[heartbeat] tick 3", contents)

    def test_large_tool_output_in_tail_not_cleared(self):
        rows = [self._make_row(i) for i in range(10)]
        large_content = "x" * (TIER1_PRUNE_THRESHOLD + 100)
        rows[8]["role"] = "tool"
        rows[8]["content"] = large_content
        result = _tier1_prune(rows, keep_tail=3)
        row8 = next(r for r in result if r["turn_index"] == 8)
        self.assertEqual(row8["content"], large_content, "tail tool output must not be cleared")

    def test_large_tool_output_outside_tail_cleared(self):
        rows = [self._make_row(i) for i in range(10)]
        large_content = "x" * (TIER1_PRUNE_THRESHOLD + 100)
        rows[2]["role"] = "tool"
        rows[2]["content"] = large_content
        result = _tier1_prune(rows, keep_tail=3)
        row2 = next(r for r in result if r["turn_index"] == 2)
        self.assertIn("tool output cleared", row2["content"])

    def test_two_pass_tail_correct_after_noise_removal(self):
        """After removing 5 noise rows from 30, last 5 clean rows must not be cleared."""
        rows = []
        for i in range(30):
            rows.append(self._make_row(i, role="tool", content="x" * (TIER1_PRUNE_THRESHOLD + 50)))
        for i in [0, 5, 10, 15, 20]:
            rows[i]["content"] = "[heartbeat] tick noise"
            rows[i]["role"] = "assistant"
        result = _tier1_prune(rows, keep_tail=5)
        result_tool = [r for r in result if r["role"] == "tool"]
        tail_5 = result_tool[-5:]
        for r in tail_5:
            self.assertNotIn("tool output cleared", r["content"],
                             f"turn {r['turn_index']} is in tail and must not be cleared")

    def test_empty_input(self):
        result = _tier1_prune([], keep_tail=5)
        self.assertEqual(result, [])

    def test_all_noise_returns_empty(self):
        rows = [self._make_row(i, content="[heartbeat] tick 1") for i in range(5)]
        result = _tier1_prune(rows, keep_tail=2)
        self.assertEqual(result, [])


# ── turn_extractor ────────────────────────────────────────────────────────────


class TestIsSignificant(unittest.TestCase):
    def test_short_content_not_significant(self):
        self.assertFalse(is_significant("short"))

    def test_long_content_significant(self):
        self.assertTrue(is_significant("x" * 500))

    def test_medium_with_extract_tool_significant(self):
        self.assertTrue(is_significant("x" * 210, tool_names=["Edit"]))

    def test_medium_without_extract_tool_not_significant(self):
        self.assertFalse(is_significant("x" * 210, tool_names=["SomeTool"]))

    def test_skip_patterns_return_false(self):
        self.assertFalse(is_significant("[heartbeat] tick 100 " + "x" * 500))

    def test_monitor_pattern_skipped(self):
        self.assertFalse(is_significant("[Monitor] no new messages " + "x" * 500))


class TestParseFacts(unittest.TestCase):
    def test_valid_facts_parsed(self):
        response = json.dumps([
            {"statement": "ADA fixed a bug.", "category": "error_resolved", "confidence": 0.9},
            {"statement": "File context_manager.py was modified.", "category": "file_modified", "confidence": 0.8},
        ])
        facts = _parse_facts(response)
        self.assertEqual(len(facts), 2)
        self.assertEqual(facts[0]["category"], "error_resolved")
        self.assertAlmostEqual(facts[0]["confidence"], 0.9)

    def test_invalid_json_returns_empty(self):
        facts = _parse_facts("not json at all")
        self.assertEqual(facts, [])

    def test_empty_statement_filtered(self):
        response = json.dumps([
            {"statement": "", "category": "decision", "confidence": 0.5},
            {"statement": "Valid statement.", "category": "decision", "confidence": 0.7},
        ])
        facts = _parse_facts(response)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["statement"], "Valid statement.")

    def test_invalid_category_remapped_to_technical_fact(self):
        response = json.dumps([
            {"statement": "Something happened.", "category": "made_up_category", "confidence": 0.6}
        ])
        facts = _parse_facts(response)
        self.assertEqual(facts[0]["category"], "technical_fact")

    def test_confidence_clamped_to_01(self):
        response = json.dumps([
            {"statement": "Overconfident.", "category": "decision", "confidence": 1.5},
            {"statement": "Underconfident.", "category": "decision", "confidence": -0.3},
        ])
        facts = _parse_facts(response)
        self.assertEqual(facts[0]["confidence"], 1.0)
        self.assertEqual(facts[1]["confidence"], 0.0)

    def test_json_embedded_in_text_extracted(self):
        text = 'Here is my output:\n[{"statement": "ADA deployed v3.", "category": "decision", "confidence": 0.95}]'
        facts = _parse_facts(text)
        self.assertEqual(len(facts), 1)


class TestSummarizeTools(unittest.TestCase):
    def test_no_tools_returns_none(self):
        result = _summarize_tools([])
        self.assertEqual(result, "(none)")

    def test_tools_listed(self):
        result = _summarize_tools(["Edit", "Bash"])
        self.assertIn("Edit", result)
        self.assertIn("Bash", result)

    def test_tool_output_truncated(self):
        long_output = "y" * 200
        result = _summarize_tools(["Read"], [long_output])
        self.assertIn("Read", result)
        self.assertLessEqual(len(result), 300)


if __name__ == "__main__":
    unittest.main(verbosity=2)
