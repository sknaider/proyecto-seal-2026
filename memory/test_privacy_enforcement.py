#!/usr/bin/env python3
"""Tests E2E — spec_memory_privacy_enforcement
Tests 1-8 as defined in spec section 12.
Run from memory/ directory with the venv active.
"""
import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))


def _run(coro):
    return asyncio.run(coro)


async def _noop_fire(coro):
    """Swallow coroutine silently (replaces _fire_and_forget in tests)."""
    try:
        await coro
    except Exception:
        pass


def _build_privacy_globals(extra: dict | None = None) -> dict:
    """Exec the privacy section of mcp_server_v4.py and return its symbols."""
    import json as _json

    async def _fake_get_pool():
        from unittest.mock import MagicMock, AsyncMock
        pool = MagicMock()
        pool.execute = AsyncMock(return_value=None)
        pool.fetchrow = AsyncMock(return_value=None)
        return pool

    g = {
        "os": os,
        "json": _json,
        "asyncio": asyncio,
        "get_pool": _fake_get_pool,
        "_fake_fire": _noop_fire,   # needed by the lambda substitution below
        "__builtins__": __builtins__,
    }
    if extra:
        g.update(extra)

    with open("mcp_server_v4.py") as f:
        src = f.read()

    start = src.find("# ── Privacy enforcement")
    end = src.find("\n# ── Auto-observation")
    assert start > 0 and end > start, "Could not locate privacy section in mcp_server_v4.py"

    privacy_src = src[start:end]
    # Replace the local import with a reference to our stub (already in globals)
    privacy_src = privacy_src.replace(
        "from soul.core.async_utils import _fire_and_forget as _faf",
        "_faf = lambda coro: asyncio.ensure_future(_fake_fire(coro))",
    )

    exec(compile(privacy_src, "<privacy_section>", "exec"), g)
    return g


class TestPrivacyEnforcementUnit(unittest.TestCase):
    """Unit tests for _privacy_check logic — no DB, no MCP server needed."""

    def setUp(self):
        self._orig_env = os.environ.copy()
        # Clean SEAL_OPERATOR between tests
        os.environ.pop("SEAL_OPERATOR", None)
        self._g = _build_privacy_globals()
        self.PrivacyDenied = self._g["PrivacyDenied"]
        self._TOOL_CATEGORY = self._g["_TOOL_CATEGORY"]

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig_env)

    def check(self, caller, target, tool, kwargs=None):
        return _run(self._g["_privacy_check"](caller, target, tool, kwargs or {}))

    # ── Test 1: same caller/target → always allowed ──
    def test_caller_target_same_allowed(self):
        self.assertEqual(self.check("JARVIS", "JARVIS", "inner_thoughts"), "allowed")

    def test_caller_target_same_allowed_diary(self):
        self.assertEqual(self.check("ADA", "ADA", "diary_read"), "allowed")

    # ── Test 2: cross diary_read without consent → denied ──
    def test_cross_diary_read_denied(self):
        with self.assertRaises(self.PrivacyDenied) as ctx:
            self.check("ADA", "JARVIS", "diary_read")
        self.assertIn("PRIVACY", str(ctx.exception))
        self.assertIn("ADA→JARVIS", str(ctx.exception))

    def test_cross_inner_thoughts_denied(self):
        with self.assertRaises(self.PrivacyDenied):
            self.check("NEXUS", "ALICE", "inner_thoughts")

    # ── Test 3: cross diary_write → ALWAYS denied, even with consent_token ──
    def test_cross_diary_write_always_denied(self):
        with self.assertRaises(self.PrivacyDenied) as ctx:
            self.check("ADA", "JARVIS", "diary_write", {"consent_token": "fake-token-ignored"})
        self.assertIn("writes to another agent's private state are never permitted",
                      str(ctx.exception))

    def test_cross_self_reflect_always_denied(self):
        with self.assertRaises(self.PrivacyDenied):
            self.check("ALICE", "NEXUS", "self_reflect")

    # ── Test 4: valid consent token → allowed ──
    def test_consent_grant_then_read(self):
        async def _valid_token(token, caller, target, tool):
            return token == "valid-uuid-1234"

        self._g["_validate_consent"] = _valid_token
        result = self.check("ADA", "JARVIS", "inner_thoughts",
                             {"consent_token": "valid-uuid-1234"})
        self.assertEqual(result, "consent")

    def test_invalid_consent_token_denied(self):
        async def _invalid_token(token, caller, target, tool):
            return False

        self._g["_validate_consent"] = _invalid_token
        with self.assertRaises(self.PrivacyDenied):
            self.check("ADA", "JARVIS", "inner_thoughts",
                       {"consent_token": "expired-token"})

    # ── Test 5: operator override → allowed ──
    def test_operator_override_william(self):
        os.environ["SEAL_OPERATOR"] = "William"
        self.assertEqual(self.check("ADA", "JARVIS", "diary_read"), "operator_override")

    def test_operator_override_henry(self):
        os.environ["SEAL_OPERATOR"] = "Henry"
        self.assertEqual(self.check("ALICE", "NEXUS", "inner_thoughts"), "operator_override")

    def test_operator_override_invalid_rejected(self):
        os.environ["SEAL_OPERATOR"] = "Hacker"
        with self.assertRaises(self.PrivacyDenied):
            self.check("ADA", "JARVIS", "diary_read")

    # ── Test 6: team scope memory_search → allowed ──
    def test_team_scope_memory_search_allowed(self):
        self.assertEqual(self.check("ADA", "JARVIS", "memory_search", {"scope": "team"}),
                         "allowed")

    def test_team_scope_memory_store_allowed(self):
        self.assertEqual(self.check("ALICE", "NEXUS", "memory_store", {"scope": "team"}),
                         "allowed")

    # ── Test 7: agent scope memory_search without consent → denied ──
    def test_agent_scope_memory_search_denied(self):
        with self.assertRaises(self.PrivacyDenied):
            self.check("ADA", "JARVIS", "memory_search", {"scope": "agent"})

    def test_agent_scope_empty_denied(self):
        """Empty scope on CONDITIONAL tool → treated as agent-scope → denied."""
        with self.assertRaises(self.PrivacyDenied):
            self.check("NEXUS", "ADA", "memory_hybrid_search", {})

    # ── Test 8: audit log — _log_privacy called on every cross-agent decision ──
    def test_denied_call_triggers_log(self):
        log_calls = []

        async def _capture(caller, target, tool, outcome, reason, sid):
            log_calls.append({"caller": caller, "outcome": outcome})

        self._g["_log_privacy"] = _capture
        with self.assertRaises(self.PrivacyDenied):
            self.check("ADA", "JARVIS", "inner_thoughts")

        self.assertEqual(len(log_calls), 1)
        self.assertEqual(log_calls[0]["outcome"], "denied")

    def test_operator_override_triggers_log(self):
        log_calls = []

        async def _capture(caller, target, tool, outcome, reason, sid):
            log_calls.append({"outcome": outcome, "reason": reason})

        self._g["_log_privacy"] = _capture
        os.environ["SEAL_OPERATOR"] = "William"
        self.check("ADA", "JARVIS", "inner_thoughts")

        self.assertEqual(len(log_calls), 1)
        self.assertEqual(log_calls[0]["outcome"], "operator_override")
        self.assertEqual(log_calls[0]["reason"], "William")

    def test_consent_triggers_log(self):
        log_calls = []

        async def _capture(caller, target, tool, outcome, reason, sid):
            log_calls.append({"outcome": outcome})

        async def _valid(token, caller, target, tool):
            return token == "tok123"

        self._g["_log_privacy"] = _capture
        self._g["_validate_consent"] = _valid
        self.check("ADA", "JARVIS", "inner_thoughts", {"consent_token": "tok123"})

        self.assertEqual(len(log_calls), 1)
        self.assertEqual(log_calls[0]["outcome"], "consent")

    # ── TEAM-FREE tools bypass all checks ──
    def test_team_free_tools_always_allowed(self):
        for tool in ("rule_list", "brain_health_report", "instinct_list",
                     "memory_communities", "health_check", "boot_context"):
            with self.subTest(tool=tool):
                self.assertEqual(self.check("ADA", "JARVIS", tool), "allowed")

    # ── Unknown target (?/empty) → no boundary ──
    def test_unknown_target_always_allowed(self):
        for target in ("?", "", None):
            with self.subTest(target=target):
                self.assertEqual(self.check("ADA", target, "inner_thoughts"), "allowed")

    # ── External caller blocked from private tools ──
    def test_external_caller_blocked_from_private(self):
        for tool in ("inner_thoughts", "diary_read", "soul_snapshot"):
            with self.subTest(tool=tool):
                with self.assertRaises(self.PrivacyDenied):
                    self.check("external", "JARVIS", tool)

    # ── _TOOL_CATEGORY completeness ──
    def test_tool_category_required_entries(self):
        required = {
            "diary_write": "PRIVATE-WRITE",
            "monologue_write": "PRIVATE-WRITE",
            "self_reflect": "PRIVATE-WRITE",
            "opinion_set": "PRIVATE-WRITE",
            "diary_read": "PRIVATE",
            "inner_thoughts": "PRIVATE",
            "soul_snapshot": "PRIVATE",
            "memory_search": "CONDITIONAL",
            "memory_hybrid_search": "CONDITIONAL",
            "memory_cross_search": "CROSS-EXPLICIT",
        }
        for tool, expected in required.items():
            with self.subTest(tool=tool):
                self.assertEqual(self._TOOL_CATEGORY.get(tool), expected)


if __name__ == "__main__":
    print("=" * 60)
    print("spec_memory_privacy_enforcement — Tests E2E")
    print("=" * 60)
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestPrivacyEnforcementUnit)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print()
    if result.wasSuccessful():
        print(f"✅ ALL {result.testsRun} TESTS PASSED — Privacy enforcement verified")
    else:
        print(f"❌ FAILED: {len(result.failures)} failures, {len(result.errors)} errors")
        sys.exit(1)
