"""SEAL Plugin Lifecycle Hooks.

Provides the SealHook ABC and HookManager dispatcher so any component
(ChannelRunner, agent loop, tool executor, memory layer) can fire events
without knowing which plugins are active.

Usage — registering a hook:

    from seal.hooks import SealHook, registry

    class MyPlugin(SealHook):
        name = "my-plugin"

        async def on_turn_start(self, turn: int, message: str, **ctx) -> None:
            ...

    registry.register(MyPlugin())

Usage — firing a hook from a component:

    from seal.hooks import hooks

    await hooks.turn_start(turn=1, message="hello", session_id=sid)
    result = await hooks.pre_tool("terminal", {"command": "ls"}, session_id=sid)
    summary = await hooks.pre_compress(messages, session_id=sid)

Design rules:
  - All hook methods are async (adapters live in asyncio).
  - Observe hooks (fire-and-forget): return None always.
  - Transform hooks (can mutate): return the transformed value OR None
    to pass the original through unchanged.
  - Hooks are called in registration order; transforms chain.
  - A hook that raises is logged and skipped — never crashes the agent.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("seal.hooks")


class SealHook(ABC):
    """Base class for SEAL plugins.

    Override only the hooks you need — every method has a no-op default.
    """

    #: Short slug used in logs and diagnostics.
    name: str = "unnamed-hook"

    # ── Session lifecycle ───────────────────────────────────────────────

    async def on_session_start(self, session_id: str, **ctx: Any) -> None:
        """Agent session is opening (first turn or explicit /reset)."""

    async def on_session_end(self, messages: List[Dict], **ctx: Any) -> None:
        """Agent session is closing (CLI exit, gateway expiry, /reset).

        ``messages`` is the full conversation history for the session.
        Use for fact extraction, session summaries, etc.
        NOT called after every turn — only at session boundaries.
        """

    # ── Turn lifecycle ──────────────────────────────────────────────────

    async def on_turn_start(self, turn: int, message: str, **ctx: Any) -> None:
        """Called at the start of each user turn, before the LLM call.

        ctx may include: session_id, model, platform, remaining_tokens.
        """

    async def on_turn_end(self, turn: int, response: str, **ctx: Any) -> None:
        """Called after the LLM produces its final response for a turn."""

    # ── LLM call hooks (transform) ──────────────────────────────────────

    async def on_pre_llm(
        self,
        messages: List[Dict],
        tools: List[Dict],
        **ctx: Any,
    ) -> Optional[List[Dict]]:
        """Called immediately before the API request is sent.

        Return a modified ``messages`` list to alter the context, or None
        to leave it unchanged. ``tools`` is informational (read-only).
        """
        return None

    async def on_post_llm(
        self,
        response: str,
        usage: Dict[str, int],
        **ctx: Any,
    ) -> None:
        """Called after the LLM response is received.

        ``usage``: {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}
        """

    # ── Tool hooks (transform) ──────────────────────────────────────────

    async def on_pre_tool(
        self,
        tool_name: str,
        args: Dict[str, Any],
        **ctx: Any,
    ) -> Optional[Dict[str, Any]]:
        """Called before a tool is executed.

        Return a modified ``args`` dict to alter the call, or None to pass
        the original args unchanged. Return an empty dict to short-circuit
        the tool call entirely (the tool will receive no arguments).
        """
        return None

    async def on_post_tool(
        self,
        tool_name: str,
        args: Dict[str, Any],
        result: str,
        **ctx: Any,
    ) -> Optional[str]:
        """Called after a tool returns its result.

        Return a modified result string, or None to leave unchanged.
        """
        return None

    # ── Memory hooks ────────────────────────────────────────────────────

    async def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict] = None,
        **ctx: Any,
    ) -> None:
        """Called when the memory layer writes an entry.

        action: "add" | "replace" | "remove"
        target: "memory" | "user" | tier name
        Use to mirror writes to an external system.
        """

    async def on_pre_compress(
        self,
        messages: List[Dict],
        **ctx: Any,
    ) -> str:
        """Called before context compression discards old messages.

        Return a string of insights to include in the compression summary
        prompt so they are preserved. Return "" to contribute nothing.
        """
        return ""

    # ── Channel / gateway hooks ─────────────────────────────────────────

    async def on_message_received(self, event: Any, **ctx: Any) -> None:
        """Inbound MessageEvent arrived from a channel, before agent processing."""

    async def on_message_sent(
        self,
        channel: str,
        chat_id: str,
        content: str,
        **ctx: Any,
    ) -> None:
        """Outbound message was delivered to a channel."""

    async def on_channel_connect(self, channel: str, **ctx: Any) -> None:
        """A channel adapter successfully connected."""

    async def on_channel_disconnect(
        self,
        channel: str,
        reason: str = "",
        **ctx: Any,
    ) -> None:
        """A channel adapter disconnected (clean or error)."""

    # ── Subagent delegation hooks ────────────────────────────────────────

    async def on_delegation_start(
        self,
        task: str,
        child_session_id: str = "",
        **ctx: Any,
    ) -> None:
        """Parent agent is spawning a subagent for ``task``."""

    async def on_delegation_end(
        self,
        task: str,
        result: str,
        child_session_id: str = "",
        **ctx: Any,
    ) -> None:
        """Subagent completed; ``result`` is its final response."""

    # ── API call hooks ───────────────────────────────────────────────────

    async def on_pre_api(
        self,
        url: str,
        method: str,
        headers: Dict[str, str],
        body: Any,
        **ctx: Any,
    ) -> Optional[Dict[str, Any]]:
        """Called before any outbound HTTP API request.

        Return a dict with optional keys ``headers`` and/or ``body`` to
        override those values, or None to pass through unchanged.
        """
        return None

    async def on_post_api(
        self,
        url: str,
        status_code: int,
        body: Any,
        **ctx: Any,
    ) -> None:
        """Called after an HTTP API response is received.

        Use for audit logging, rate-limit tracking, or metric emission.
        """

    # ── Approval hooks ────────────────────────────────────────────────────

    async def on_approval_needed(
        self,
        action: str,
        surface: str,
        **ctx: Any,
    ) -> Optional[bool]:
        """Called when an action requires user approval.

        surface: "cli" | "matrix" | "telegram" | ...
        Return True to auto-approve, False to auto-deny, None to defer to
        the default approval mechanism.
        """
        return None

    async def on_post_approval(
        self,
        action: str,
        surface: str,
        approved: bool,
        **ctx: Any,
    ) -> None:
        """Called after an approval decision is resolved.

        Use to audit the decision or notify an external system.
        """

    # ── Gateway dispatch hook (transform) ────────────────────────────────

    async def on_gateway_dispatch(
        self,
        event: Any,
        target: str,
        **ctx: Any,
    ) -> Optional[str]:
        """Called when a MessageEvent is routed to an agent/handler.

        Return a different target string to reroute, or None to keep default.
        """
        return None

    # ── Input / output transform hooks ────────────────────────────────────

    async def on_transform_input(
        self,
        text: str,
        channel: str,
        user_id: str,
        **ctx: Any,
    ) -> Optional[str]:
        """Called on raw inbound message text before the agent processes it.

        Return a transformed string, or None to leave unchanged.
        Use for: PII scrubbing, command normalisation, language detection.
        """
        return None

    async def on_transform_output(
        self,
        text: str,
        channel: str,
        chat_id: str,
        **ctx: Any,
    ) -> Optional[str]:
        """Called on the agent's response text before it is sent to a channel.

        Return a transformed string, or None to leave unchanged.
        Use for: markdown→plain-text conversion, length capping, formatting.
        """
        return None

    # ── Interrupt / compact / wake hooks ─────────────────────────────────

    async def on_interrupt(
        self,
        reason: str = "",
        **ctx: Any,
    ) -> None:
        """Called when the agent loop is interrupted.

        ``reason``: "sigint" | "user_command" | "timeout" | ""
        """

    async def on_compact(
        self,
        summary: str,
        **ctx: Any,
    ) -> None:
        """Called after context compaction completes.

        ``summary`` is the compacted context that was injected.
        """

    async def on_wake(
        self,
        agent_name: str,
        context: str = "",
        **ctx: Any,
    ) -> None:
        """Called when the agent wakes from sleep or compact-induced dormancy.

        Use to re-subscribe to channels, re-arm monitors, re-seed caches.
        """

    # ── Error hook ───────────────────────────────────────────────────────

    async def on_error(
        self,
        error: Exception,
        context: str = "",
        **ctx: Any,
    ) -> None:
        """Called when the agent loop or a tool raises an unhandled exception."""


# ── Registry ────────────────────────────────────────────────────────────────


class HookRegistry:
    """Maintains the ordered list of active SealHook instances."""

    def __init__(self) -> None:
        self._hooks: List[SealHook] = []

    def register(self, hook: SealHook) -> None:
        """Add a hook. Called once per plugin at startup."""
        if any(h.name == hook.name for h in self._hooks):
            logger.warning("Hook '%s' already registered — skipping duplicate", hook.name)
            return
        self._hooks.append(hook)
        logger.debug("Hook registered: %s", hook.name)

    def unregister(self, name: str) -> None:
        self._hooks = [h for h in self._hooks if h.name != name]

    def all(self) -> List[SealHook]:
        return list(self._hooks)

    def __len__(self) -> int:
        return len(self._hooks)


#: Module-level singleton — import and use directly.
registry = HookRegistry()


# ── Dispatcher ───────────────────────────────────────────────────────────────


class HookManager:
    """Dispatches lifecycle events to all registered hooks.

    Observe hooks: fire-and-forget, errors logged and swallowed.
    Transform hooks: chain return values; None means "no change".
    """

    def __init__(self, reg: HookRegistry) -> None:
        self._reg = reg

    async def _fire(self, method: str, *args: Any, **kwargs: Any) -> None:
        """Fire an observe hook on all registered plugins."""
        for hook in self._reg.all():
            try:
                await getattr(hook, method)(*args, **kwargs)
            except Exception:
                logger.exception("Hook %s.%s raised", hook.name, method)

    async def _transform(
        self,
        method: str,
        value: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Chain a transform hook; each plugin may return a new value or None."""
        current = value
        for hook in self._reg.all():
            try:
                result = await getattr(hook, method)(current, *args, **kwargs)
                if result is not None:
                    current = result
            except Exception:
                logger.exception("Hook %s.%s raised", hook.name, method)
        return current

    # ── Observe dispatchers ──────────────────────────────────────────────

    async def session_start(self, session_id: str, **ctx: Any) -> None:
        await self._fire("on_session_start", session_id, **ctx)

    async def session_end(self, messages: List[Dict], **ctx: Any) -> None:
        await self._fire("on_session_end", messages, **ctx)

    async def turn_start(self, turn: int, message: str, **ctx: Any) -> None:
        await self._fire("on_turn_start", turn, message, **ctx)

    async def turn_end(self, turn: int, response: str, **ctx: Any) -> None:
        await self._fire("on_turn_end", turn, response, **ctx)

    async def post_llm(self, response: str, usage: Dict, **ctx: Any) -> None:
        await self._fire("on_post_llm", response, usage, **ctx)

    async def memory_write(
        self, action: str, target: str, content: str, **ctx: Any
    ) -> None:
        await self._fire("on_memory_write", action, target, content, **ctx)

    async def message_received(self, event: Any, **ctx: Any) -> None:
        await self._fire("on_message_received", event, **ctx)

    async def message_sent(
        self, channel: str, chat_id: str, content: str, **ctx: Any
    ) -> None:
        await self._fire("on_message_sent", channel, chat_id, content, **ctx)

    async def channel_connect(self, channel: str, **ctx: Any) -> None:
        await self._fire("on_channel_connect", channel, **ctx)

    async def channel_disconnect(
        self, channel: str, reason: str = "", **ctx: Any
    ) -> None:
        await self._fire("on_channel_disconnect", channel, reason, **ctx)

    async def delegation_start(
        self, task: str, child_session_id: str = "", **ctx: Any
    ) -> None:
        await self._fire("on_delegation_start", task, child_session_id, **ctx)

    async def delegation_end(
        self, task: str, result: str, child_session_id: str = "", **ctx: Any
    ) -> None:
        await self._fire("on_delegation_end", task, result, child_session_id, **ctx)

    async def error(
        self, error: Exception, context: str = "", **ctx: Any
    ) -> None:
        await self._fire("on_error", error, context, **ctx)

    async def post_api(
        self, url: str, status_code: int, body: Any, **ctx: Any
    ) -> None:
        await self._fire("on_post_api", url, status_code, body, **ctx)

    async def post_approval(
        self, action: str, surface: str, approved: bool, **ctx: Any
    ) -> None:
        await self._fire("on_post_approval", action, surface, approved, **ctx)

    async def interrupt(self, reason: str = "", **ctx: Any) -> None:
        await self._fire("on_interrupt", reason, **ctx)

    async def compact(self, summary: str, **ctx: Any) -> None:
        await self._fire("on_compact", summary, **ctx)

    async def wake(self, agent_name: str, context: str = "", **ctx: Any) -> None:
        await self._fire("on_wake", agent_name, context, **ctx)

    # ── Transform dispatchers ────────────────────────────────────────────

    async def pre_llm(
        self, messages: List[Dict], tools: List[Dict], **ctx: Any
    ) -> List[Dict]:
        """Returns (possibly transformed) messages list."""
        return await self._transform("on_pre_llm", messages, tools, **ctx)

    async def pre_tool(
        self, tool_name: str, args: Dict[str, Any], **ctx: Any
    ) -> Dict[str, Any]:
        """Returns (possibly transformed) args dict."""
        current = args
        for hook in self._reg.all():
            try:
                result = await hook.on_pre_tool(tool_name, current, **ctx)
                if result is not None:
                    current = result
            except Exception:
                logger.exception("Hook %s.on_pre_tool raised", hook.name)
        return current

    async def post_tool(
        self, tool_name: str, args: Dict[str, Any], result: str, **ctx: Any
    ) -> str:
        """Returns (possibly transformed) result string."""
        current = result
        for hook in self._reg.all():
            try:
                out = await hook.on_post_tool(tool_name, args, current, **ctx)
                if out is not None:
                    current = out
            except Exception:
                logger.exception("Hook %s.on_post_tool raised", hook.name)
        return current

    async def pre_compress(self, messages: List[Dict], **ctx: Any) -> str:
        """Collects insight strings from all hooks into one block."""
        parts: List[str] = []
        for hook in self._reg.all():
            try:
                text = await hook.on_pre_compress(messages, **ctx)
                if text:
                    parts.append(text.strip())
            except Exception:
                logger.exception("Hook %s.on_pre_compress raised", hook.name)
        return "\n\n".join(parts)

    async def approval_needed(
        self, action: str, surface: str, **ctx: Any
    ) -> Optional[bool]:
        """First hook to return non-None wins. Also fires post_approval observe."""
        verdict = None
        for hook in self._reg.all():
            try:
                v = await hook.on_approval_needed(action, surface, **ctx)
                if v is not None and verdict is None:
                    verdict = v
            except Exception:
                logger.exception("Hook %s.on_approval_needed raised", hook.name)
        await self.post_approval(action, surface, bool(verdict), **ctx)
        return verdict

    pre_approval = approval_needed

    async def pre_api(
        self,
        url: str,
        method: str,
        headers: Dict[str, str],
        body: Any,
        **ctx: Any,
    ) -> tuple:
        """Returns (headers, body), possibly transformed by plugins."""
        cur_headers, cur_body = headers, body
        for hook in self._reg.all():
            try:
                result = await hook.on_pre_api(url, method, cur_headers, cur_body, **ctx)
                if result is not None:
                    cur_headers = result.get("headers", cur_headers)
                    cur_body = result.get("body", cur_body)
            except Exception:
                logger.exception("Hook %s.on_pre_api raised", hook.name)
        return cur_headers, cur_body

    async def gateway_dispatch(
        self, event: Any, target: str, **ctx: Any
    ) -> str:
        """Returns the (possibly rerouted) target string."""
        current = target
        for hook in self._reg.all():
            try:
                result = await hook.on_gateway_dispatch(event, current, **ctx)
                if result is not None:
                    current = result
            except Exception:
                logger.exception("Hook %s.on_gateway_dispatch raised", hook.name)
        return current

    async def transform_input(
        self, text: str, channel: str, user_id: str, **ctx: Any
    ) -> str:
        """Returns (possibly transformed) inbound message text."""
        current = text
        for hook in self._reg.all():
            try:
                result = await hook.on_transform_input(current, channel, user_id, **ctx)
                if result is not None:
                    current = result
            except Exception:
                logger.exception("Hook %s.on_transform_input raised", hook.name)
        return current

    async def transform_output(
        self, text: str, channel: str, chat_id: str, **ctx: Any
    ) -> str:
        """Returns (possibly transformed) outbound response text."""
        current = text
        for hook in self._reg.all():
            try:
                result = await hook.on_transform_output(current, channel, chat_id, **ctx)
                if result is not None:
                    current = result
            except Exception:
                logger.exception("Hook %s.on_transform_output raised", hook.name)
        return current


#: Module-level dispatcher — import and use directly.
hooks = HookManager(registry)
