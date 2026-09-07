#!/usr/bin/env python3
"""Regression contracts for the passive SessionStart:compact hook."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


HOOK = Path(__file__).with_name("post_compact_session_start_hook.py")
sys.path.insert(0, str(HOOK.parent))
SPEC = importlib.util.spec_from_file_location("seal_post_compact_hook", HOOK)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PostCompactHookContractTest(unittest.TestCase):
    def test_utf8_budget_is_strict(self):
        bounded = MODULE.bound_utf8("á" * 10_000, 257)
        self.assertLessEqual(len(bounded.encode("utf-8")), 257)
        self.assertIn("truncated", bounded)

    def test_main_is_passive_and_never_starts_a_user_turn(self):
        async def fake_context(_agent):
            return "bounded state"

        stdin = io.StringIO('{"source":"compact"}')
        stdout = io.StringIO()
        with mock.patch.object(MODULE, "build_additional_context", fake_context), \
             mock.patch.object(MODULE.sys, "stdin", stdin), \
             contextlib.redirect_stdout(stdout):
            MODULE.main()

        payload = json.loads(stdout.getvalue())
        hook = payload["hookSpecificOutput"]
        self.assertEqual(hook["hookEventName"], "SessionStart")
        self.assertEqual(hook["additionalContext"], "bounded state")
        self.assertNotIn("initialUserMessage", hook)

    def test_source_has_no_reboot_side_effects(self):
        source = HOOK.read_text(encoding="utf-8")
        forbidden = ("initialUserMessage", "boot_context(", "webchat", "api/agents/send")
        # Mentions in the module contract/docstring are allowed; executable
        # string literals or calls are not. The only expected occurrence is
        # the explanatory docstring for initialUserMessage.
        self.assertEqual(source.count("initialUserMessage"), 0)
        for token in forbidden[1:]:
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
