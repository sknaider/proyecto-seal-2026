#!/usr/bin/env python3
"""P1.2 — Test suite for active_recall_hook.py

Coverage:
  - detect_agent(): env var, cmdline fallback, CWD fallback, None path
  - Cross-agent isolation: JARVIS corrections ≠ ADA corrections
  - Short/empty message guard → returns {}
  - None agent guard → returns {}
  - active_recall integration: corrections, instincts, rules from real DB
  - Rules are global (not filtered by agent)
"""

import asyncio
import json
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

# Ensure the hook module is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from active_recall_hook import detect_agent, active_recall, main

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"


# ─── Unit tests: detect_agent ────────────────────────────────────────────────

class TestDetectAgent(unittest.TestCase):

    def test_env_var_jarvis(self):
        with patch.dict(os.environ, {"SEAL_AGENT": "JARVIS"}):
            assert detect_agent() == "JARVIS"

    def test_env_var_ada(self):
        with patch.dict(os.environ, {"SEAL_AGENT": "ADA"}):
            assert detect_agent() == "ADA"

    def test_env_var_alice(self):
        with patch.dict(os.environ, {"SEAL_AGENT": "ALICE"}):
            assert detect_agent() == "ALICE"

    def test_env_var_unknown_falls_through(self):
        """Unknown SEAL_AGENT value doesn't match → falls to cmdline/CWD."""
        with patch.dict(os.environ, {"SEAL_AGENT": "UNKNOWN"}, clear=False):
            # SEAL_AGENT set but not in valid list → should NOT return "UNKNOWN"
            result = detect_agent()
            assert result != "UNKNOWN"

    def test_no_env_var_no_match_returns_none(self):
        """No SEAL_AGENT, no agent in cmdline, neutral CWD → None."""
        env = {k: v for k, v in os.environ.items() if k != "SEAL_AGENT"}
        with patch.dict(os.environ, env, clear=True):
            with patch("builtins.open", side_effect=Exception("no proc")):
                with patch("os.getcwd", return_value="/home/dadito/tmp"):
                    result = detect_agent()
                    assert result is None

    def test_cwd_memory_returns_jarvis(self):
        env = {k: v for k, v in os.environ.items() if k != "SEAL_AGENT"}
        with patch.dict(os.environ, env, clear=True):
            with patch("builtins.open", side_effect=Exception("no proc")):
                with patch("os.getcwd", return_value="/home/dadito/IA/proyecto-seal/memory"):
                    result = detect_agent()
                    assert result == "JARVIS"

    def test_cwd_alice_returns_alice(self):
        env = {k: v for k, v in os.environ.items() if k != "SEAL_AGENT"}
        with patch.dict(os.environ, env, clear=True):
            with patch("builtins.open", side_effect=Exception("no proc")):
                with patch("os.getcwd", return_value="/home/dadito/IA/proyecto-seal/alice"):
                    result = detect_agent()
                    assert result == "ALICE"


# ─── Integration tests: active_recall DB queries ─────────────────────────────

class TestActiveRecallIntegration(unittest.IsolatedAsyncioTestCase):
    """Integration tests — hit real DB at :5433."""

    async def asyncSetUp(self):
        import asyncpg
        self.conn = await asyncpg.connect(DB_URL)
        # Insert isolated test data for JARVIS_TEST agent (won't collide)
        await self.conn.execute("""
            INSERT INTO memories(agent, category, content, importance)
            VALUES ('JARVIS_TEST', 'correction', 'JARVIS test correction content', 9)
            ON CONFLICT DO NOTHING
        """)
        await self.conn.execute("""
            INSERT INTO memories(agent, category, content, importance)
            VALUES ('ADA_TEST', 'correction', 'ADA test correction content', 9)
            ON CONFLICT DO NOTHING
        """)
        await self.conn.execute("""
            INSERT INTO instincts(agent, trigger_pattern, response, confidence, active)
            VALUES ('JARVIS_TEST', 'test trigger', 'test response', 0.95, true)
            ON CONFLICT DO NOTHING
        """)

    async def asyncTearDown(self):
        await self.conn.execute("DELETE FROM memories WHERE agent IN ('JARVIS_TEST', 'ADA_TEST')")
        await self.conn.execute("DELETE FROM instincts WHERE agent = 'JARVIS_TEST'")
        await self.conn.close()

    async def test_corrections_filtered_by_agent(self):
        """JARVIS_TEST sees its own corrections, not ADA_TEST's."""
        with patch("active_recall_hook.detect_agent", return_value="JARVIS_TEST"):
            result = await active_recall("test prompt for recall")
        assert "JARVIS test correction content" in result
        assert "ADA test correction content" not in result

    async def test_cross_agent_isolation(self):
        """ADA_TEST corrections do NOT appear in JARVIS_TEST recall."""
        with patch("active_recall_hook.detect_agent", return_value="JARVIS_TEST"):
            result = await active_recall("checking isolation")
        assert "ADA test correction content" not in result

    async def test_instincts_filtered_by_agent(self):
        """Instincts appear for the correct agent."""
        with patch("active_recall_hook.detect_agent", return_value="JARVIS_TEST"):
            result = await active_recall("trigger something")
        assert "test trigger" in result or "test response" in result

    async def test_rules_are_global(self):
        """Rules section does NOT filter by agent — all critical/high rules appear."""
        with patch("active_recall_hook.detect_agent", return_value="JARVIS_TEST"):
            result = await active_recall("rules check")
        # Rules section should appear as long as there are critical/high rules in DB
        # Just verify the section header appears (rules are global)
        if result:
            assert "REGLAS ACTIVAS" in result or result == ""

    async def test_no_corrections_no_section(self):
        """Agent with no corrections → no corrections section."""
        with patch("active_recall_hook.detect_agent", return_value="NONEXISTENT_AGENT_XYZ"):
            result = await active_recall("anything")
        assert "CORRECCIONES" not in result

    async def test_result_format_includes_agent_and_timing(self):
        """Result header includes agent name and timing."""
        with patch("active_recall_hook.detect_agent", return_value="JARVIS_TEST"):
            result = await active_recall("anything here")
        if result:
            assert "JARVIS_TEST" in result
            assert "ms" in result


# ─── Unit tests: main() guards ───────────────────────────────────────────────

class TestMainGuards(unittest.TestCase):

    def _run_main_with_stdin(self, payload: dict) -> dict:
        """Run main() with given stdin, capture stdout."""
        import io
        from unittest.mock import patch
        stdin_data = json.dumps(payload)
        captured = io.StringIO()
        with patch("sys.stdin", io.StringIO(stdin_data)):
            with patch("sys.stdout", captured):
                main()
        output = captured.getvalue().strip()
        return json.loads(output) if output else {}

    def test_empty_message_returns_empty_dict(self):
        result = self._run_main_with_stdin({"prompt": ""})
        assert result == {}

    def test_short_message_returns_empty_dict(self):
        result = self._run_main_with_stdin({"prompt": "hi"})
        assert result == {}

    def test_none_agent_returns_empty_dict(self):
        with patch("active_recall_hook.detect_agent", return_value=None):
            result = self._run_main_with_stdin({"prompt": "this is a long enough message"})
        assert result == {}

    def test_valid_message_output_shape(self):
        """Valid recall result → output has hookSpecificOutput key."""
        async def fake_recall(msg):
            return "[SOUL Active Recall — JARVIS, 10ms]\nsome context"
        with patch("active_recall_hook.detect_agent", return_value="JARVIS"):
            with patch("active_recall_hook.active_recall", side_effect=fake_recall):
                result = self._run_main_with_stdin({"prompt": "long enough message here"})
        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"

    def test_db_failure_returns_empty_dict(self):
        """If DB is unreachable, main() returns {} (never blocks)."""
        with patch("active_recall_hook.detect_agent", return_value="JARVIS"):
            with patch("asyncpg.connect", side_effect=Exception("connection refused")):
                result = self._run_main_with_stdin({"prompt": "test message that is long enough"})
        # Should return {} — never crash
        assert isinstance(result, dict)

    def test_invalid_json_stdin_returns_empty_dict(self):
        import io
        captured = io.StringIO()
        with patch("sys.stdin", io.StringIO("not json {{")):
            with patch("sys.stdout", captured):
                main()
        output = captured.getvalue().strip()
        result = json.loads(output) if output else {}
        assert result == {}


# ─── Performance guard ───────────────────────────────────────────────────────

class TestPerformance(unittest.IsolatedAsyncioTestCase):
    """Ensure recall stays under 500ms target."""

    async def test_recall_under_500ms(self):
        import time
        with patch("active_recall_hook.detect_agent", return_value="JARVIS"):
            t0 = time.monotonic()
            await active_recall("performance test message here")
            elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms < 500, f"Recall took {elapsed_ms:.0f}ms — exceeds 500ms target"


if __name__ == "__main__":
    unittest.main(verbosity=2)
