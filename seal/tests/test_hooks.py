"""Contract tests for seal/hooks plugin lifecycle system.

Run:
    python3 -m pytest seal/tests/test_hooks.py -v

Or standalone:
    python3 -m unittest seal.tests.test_hooks -v
"""
from __future__ import annotations

import asyncio
import unittest
from typing import Any, Dict, List, Optional

from seal.hooks import HookManager, HookRegistry, SealHook, hooks, registry


# ── Helpers ──────────────────────────────────────────────────────────────────


class _Recorder(SealHook):
    """Hook that records every call made to it."""

    name = "recorder"

    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.turn_starts: List[int] = []
        self.last_response: str = ""

    async def on_turn_start(self, turn: int, message: str, **ctx: Any) -> None:
        self.turn_starts.append(turn)
        self.calls.append(("on_turn_start", turn, message))

    async def on_turn_end(self, turn: int, response: str, **ctx: Any) -> None:
        self.last_response = response
        self.calls.append(("on_turn_end", turn, response))

    async def on_session_start(self, session_id: str, **ctx: Any) -> None:
        self.calls.append(("on_session_start", session_id))

    async def on_error(self, error: Exception, context: str = "", **ctx: Any) -> None:
        self.calls.append(("on_error", type(error).__name__, context))


class _Raiser(SealHook):
    """Hook that always raises to verify error isolation."""

    name = "raiser"

    async def on_turn_start(self, turn: int, message: str, **ctx: Any) -> None:
        raise RuntimeError("intentional raise")

    async def on_pre_tool(
        self,
        tool_name: str,
        args: Dict[str, Any],
        **ctx: Any,
    ) -> Optional[Dict[str, Any]]:
        raise ValueError("tool raise")


class _ToolTransformer(SealHook):
    """Hook that appends '_transformed' to a string arg."""

    name = "tool-transformer"

    async def on_pre_tool(
        self,
        tool_name: str,
        args: Dict[str, Any],
        **ctx: Any,
    ) -> Optional[Dict[str, Any]]:
        if "command" in args:
            return {**args, "command": args["command"] + "_transformed"}
        return None


class _PostToolTransformer(SealHook):
    name = "post-tool-transformer"

    async def on_post_tool(
        self,
        tool_name: str,
        args: Dict[str, Any],
        result: str,
        **ctx: Any,
    ) -> Optional[str]:
        return result.upper()


class _Approver(SealHook):
    name = "approver"
    verdict: Optional[bool] = True

    async def on_approval_needed(
        self,
        action: str,
        surface: str,
        **ctx: Any,
    ) -> Optional[bool]:
        return self.verdict


class _InsightHook(SealHook):
    name = "insight"

    async def on_pre_compress(
        self,
        messages: List[Dict],
        **ctx: Any,
    ) -> str:
        return "important insight"


# ── Registry tests ────────────────────────────────────────────────────────────


class HookRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = HookRegistry()

    def test_register_and_count(self) -> None:
        self.reg.register(_Recorder())
        self.assertEqual(len(self.reg), 1)

    def test_duplicate_name_skipped(self) -> None:
        self.reg.register(_Recorder())
        self.reg.register(_Recorder())  # same name
        self.assertEqual(len(self.reg), 1)

    def test_unregister(self) -> None:
        self.reg.register(_Recorder())
        self.reg.unregister("recorder")
        self.assertEqual(len(self.reg), 0)

    def test_unregister_nonexistent_is_noop(self) -> None:
        self.reg.unregister("ghost")  # must not raise

    def test_all_returns_copy(self) -> None:
        self.reg.register(_Recorder())
        snap = self.reg.all()
        snap.clear()
        self.assertEqual(len(self.reg), 1)

    def test_multiple_hooks_ordered(self) -> None:
        r1 = _Recorder()
        r2 = _Raiser()
        self.reg.register(r1)
        self.reg.register(r2)
        names = [h.name for h in self.reg.all()]
        self.assertEqual(names, ["recorder", "raiser"])


# ── Abstract base tests ───────────────────────────────────────────────────────


class SealHookAbstractTests(unittest.TestCase):
    def test_no_op_defaults_do_not_raise(self) -> None:
        """All default implementations must be callable without errors."""

        class _Minimal(SealHook):
            name = "minimal"

        hook = _Minimal()
        asyncio.run(hook.on_session_start("sid"))
        asyncio.run(hook.on_session_end([]))
        asyncio.run(hook.on_turn_start(1, "msg"))
        asyncio.run(hook.on_turn_end(1, "resp"))
        asyncio.run(hook.on_post_llm("resp", {}))
        asyncio.run(hook.on_memory_write("add", "memory", "content"))
        asyncio.run(hook.on_message_received(object()))
        asyncio.run(hook.on_message_sent("matrix", "room1", "hi"))
        asyncio.run(hook.on_channel_connect("matrix"))
        asyncio.run(hook.on_channel_disconnect("matrix"))
        asyncio.run(hook.on_delegation_start("task"))
        asyncio.run(hook.on_delegation_end("task", "result"))
        asyncio.run(hook.on_error(RuntimeError("x")))

    def test_default_pre_llm_returns_none(self) -> None:
        class _Minimal(SealHook):
            name = "minimal2"

        hook = _Minimal()
        result = asyncio.run(hook.on_pre_llm([], []))
        self.assertIsNone(result)

    def test_default_pre_compress_returns_empty_string(self) -> None:
        class _Minimal(SealHook):
            name = "minimal3"

        hook = _Minimal()
        result = asyncio.run(hook.on_pre_compress([]))
        self.assertEqual(result, "")

    def test_default_approval_returns_none(self) -> None:
        class _Minimal(SealHook):
            name = "minimal4"

        hook = _Minimal()
        result = asyncio.run(hook.on_approval_needed("action", "cli"))
        self.assertIsNone(result)


# ── HookManager observe dispatch ─────────────────────────────────────────────


class HookManagerObserveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.reg = HookRegistry()
        self.mgr = HookManager(self.reg)
        self.rec = _Recorder()
        self.reg.register(self.rec)

    async def test_turn_start_reaches_hook(self) -> None:
        await self.mgr.turn_start(1, "hello")
        self.assertEqual(self.rec.turn_starts, [1])

    async def test_turn_end_reaches_hook(self) -> None:
        await self.mgr.turn_end(2, "done")
        self.assertEqual(self.rec.last_response, "done")

    async def test_session_start_reaches_hook(self) -> None:
        await self.mgr.session_start("sid-42")
        self.assertIn(("on_session_start", "sid-42"), self.rec.calls)

    async def test_error_hook_receives_exception(self) -> None:
        err = ValueError("boom")
        await self.mgr.error(err, context="loop")
        self.assertIn(("on_error", "ValueError", "loop"), self.rec.calls)

    async def test_raiser_hook_is_swallowed(self) -> None:
        """A hook that raises must not crash the manager."""
        self.reg.register(_Raiser())
        # Both hooks are present; raiser fires first-ish but recorder survives
        await self.mgr.turn_start(1, "msg")
        self.assertEqual(self.rec.turn_starts, [1])

    async def test_multiple_hooks_all_called(self) -> None:
        rec2 = _Recorder()
        rec2.name = "recorder-2"
        self.reg.register(rec2)
        await self.mgr.turn_start(5, "x")
        self.assertEqual(self.rec.turn_starts, [5])
        self.assertEqual(rec2.turn_starts, [5])


# ── HookManager transform dispatch ───────────────────────────────────────────


class HookManagerTransformTests(unittest.IsolatedAsyncioTestCase):
    async def test_pre_tool_none_passthrough(self) -> None:
        """When no hook transforms args, original args returned."""
        reg = HookRegistry()
        mgr = HookManager(reg)
        original = {"command": "ls"}
        result = await mgr.pre_tool("terminal", original)
        self.assertEqual(result, original)

    async def test_pre_tool_transform_applied(self) -> None:
        reg = HookRegistry()
        reg.register(_ToolTransformer())
        mgr = HookManager(reg)
        result = await mgr.pre_tool("terminal", {"command": "ls"})
        self.assertEqual(result["command"], "ls_transformed")

    async def test_pre_tool_chained(self) -> None:
        """Two transformers chain: second sees first's output."""

        class _Append1(SealHook):
            name = "append1"

            async def on_pre_tool(self, tool_name, args, **ctx):
                return {**args, "command": args.get("command", "") + "_A"}

        class _Append2(SealHook):
            name = "append2"

            async def on_pre_tool(self, tool_name, args, **ctx):
                return {**args, "command": args.get("command", "") + "_B"}

        reg = HookRegistry()
        reg.register(_Append1())
        reg.register(_Append2())
        mgr = HookManager(reg)
        result = await mgr.pre_tool("t", {"command": "base"})
        self.assertEqual(result["command"], "base_A_B")

    async def test_post_tool_transform(self) -> None:
        reg = HookRegistry()
        reg.register(_PostToolTransformer())
        mgr = HookManager(reg)
        result = await mgr.post_tool("terminal", {}, "hello")
        self.assertEqual(result, "HELLO")

    async def test_pre_tool_raiser_skipped_chain_continues(self) -> None:
        """Raiser in transform chain is skipped; subsequent hooks still run."""
        reg = HookRegistry()
        reg.register(_Raiser())
        reg.register(_ToolTransformer())
        mgr = HookManager(reg)
        result = await mgr.pre_tool("terminal", {"command": "ls"})
        self.assertEqual(result["command"], "ls_transformed")

    async def test_pre_llm_passthrough(self) -> None:
        reg = HookRegistry()
        mgr = HookManager(reg)
        msgs = [{"role": "user", "content": "hi"}]
        result = await mgr.pre_llm(msgs, [])
        self.assertIs(result, msgs)

    async def test_pre_compress_collects_all_insights(self) -> None:
        reg = HookRegistry()
        h1 = _InsightHook()
        h2 = _InsightHook()
        h2.name = "insight2"

        async def _insight2(messages, **ctx):
            return "second insight"

        h2.on_pre_compress = _insight2  # type: ignore[method-assign]
        reg.register(h1)
        reg.register(h2)
        mgr = HookManager(reg)
        result = await mgr.pre_compress([])
        self.assertIn("important insight", result)
        self.assertIn("second insight", result)

    async def test_pre_compress_empty_when_no_hooks(self) -> None:
        reg = HookRegistry()
        mgr = HookManager(reg)
        result = await mgr.pre_compress([])
        self.assertEqual(result, "")

    async def test_approval_first_non_none_wins(self) -> None:
        reg = HookRegistry()
        approver = _Approver()
        approver.verdict = True
        denier = _Approver()
        denier.name = "denier"
        denier.verdict = False
        reg.register(approver)
        reg.register(denier)
        mgr = HookManager(reg)
        verdict = await mgr.approval_needed("rm -rf /", "cli")
        self.assertTrue(verdict)

    async def test_approval_none_when_all_abstain(self) -> None:
        reg = HookRegistry()
        abstain = _Approver()
        abstain.verdict = None
        reg.register(abstain)
        mgr = HookManager(reg)
        verdict = await mgr.approval_needed("git push", "cli")
        self.assertIsNone(verdict)


# ── Module-level singletons ───────────────────────────────────────────────────


class ModuleSingletonTests(unittest.TestCase):
    def test_registry_singleton_is_hookregistry(self) -> None:
        self.assertIsInstance(registry, HookRegistry)

    def test_hooks_singleton_is_hookmanager(self) -> None:
        self.assertIsInstance(hooks, HookManager)

    def test_hooks_uses_module_registry(self) -> None:
        # HookManager._reg should be the same object as registry
        self.assertIs(hooks._reg, registry)


if __name__ == "__main__":
    unittest.main()
