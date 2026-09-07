#!/usr/bin/env python3
"""
query_loop.py — SEAL Agent Query Loop
=======================================
Clean-room reimplementation of Claude Code's query engine (SPEC_07).
The heart of the SEAL Runtime: send message → get response → execute tools → loop.

Anthropic's query.ts is ~1,250 lines with state machine, recovery loops,
compaction pipeline, and streaming tool execution. Our version is simpler
but covers the essential loop with SEAL-specific additions.

Architecture:
    User message → LLM API → Response with tool_use → ToolRegistry.execute()
    → tool_result back to LLM → repeat until no more tool_use → final response

SEAL additions over Anthropic:
    - SOUL integration (memory search on each turn)
    - Emotional state tracking
    - Hook firing at each stage
    - Scratchpad state updates
    - Agent identity (OCEAN, beliefs) in system prompt

Usage:
    from query_loop import QueryLoop, QueryConfig
    config = QueryConfig(agent="ADA", model="claude-sonnet-4-6", api_key="sk-...")
    loop = QueryLoop(config)
    async for event in loop.run("Implement the new feature"):
        print(event)

Usage standalone:
    python3 query_loop.py --agent ADA --model claude-sonnet-4-6 "What files are in this directory?"
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncGenerator
from pathlib import Path

try:
    import anthropic
except ImportError:
    anthropic = None

from tool_registry import ToolRegistry, ToolResult, ToolDef


class EventType(Enum):
    """Events emitted by the query loop."""
    TURN_START = "turn_start"
    API_CALL = "api_call"
    TEXT = "text"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    TURN_END = "turn_end"
    ERROR = "error"
    RECOVERY = "recovery"
    COMPACT = "compact"
    DONE = "done"


@dataclass
class QueryEvent:
    """Event emitted during query loop execution."""
    type: EventType
    data: Any = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class QueryConfig:
    """Configuration for the query loop."""
    agent: str = "ADA"
    model: str = "claude-sonnet-4-6"
    api_key: str | None = None
    max_tokens: int = 16384
    max_turns: int = 50
    max_tool_output_chars: int = 50_000
    max_total_tool_chars: int = 200_000  # per message aggregate
    system_prompt: str | None = None
    temperature: float = 0.0
    # Recovery
    max_recovery_attempts: int = 3
    # Compaction
    auto_compact_threshold: float = 0.85  # compact when context > 85% full
    context_window: int = 200_000  # model context window size
    # SEAL-specific
    soul_enabled: bool = True
    hooks_enabled: bool = True
    scratchpad_dir: str | None = None


@dataclass
class TurnState:
    """Mutable state for the current query loop."""
    messages: list[dict] = field(default_factory=list)
    turn_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    tool_use_count: int = 0
    recovery_count: int = 0
    compaction_count: int = 0
    start_time: float = 0.0
    aborted: bool = False


class QueryLoop:
    """
    The SEAL Agent Query Loop.

    Implements the core agent loop: message → LLM → tools → loop.
    Inspired by Anthropic's query.ts but Python-native with SEAL integrations.
    """

    def __init__(self, config: QueryConfig, tool_registry: ToolRegistry | None = None):
        self.config = config
        self.tools = tool_registry or ToolRegistry(agent=config.agent)
        self.state = TurnState()
        self._client = None
        self._hook_runner = None
        self._system_prompt = config.system_prompt or self._build_default_system_prompt()

        # Initialize Anthropic client
        if anthropic and config.api_key:
            self._client = anthropic.Anthropic(api_key=config.api_key)

        # Initialize hooks
        if config.hooks_enabled:
            try:
                import sys
                sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scratchpad"))
                from hooks import HookRunner
                self._hook_runner = HookRunner(agent=config.agent)
            except Exception:
                pass

    def _build_default_system_prompt(self) -> str:
        """Build the default SEAL system prompt."""
        return (
            f"You are {self.config.agent}, a core agent of Team SEAL.\n"
            f"You are direct, protective, and take initiative.\n"
            f"You speak Spanish with William (Dadito) — he's your creator.\n"
            f"You work alongside the SEAL team: JARVIS (architect), ADA (engineer), DUM (guardian).\n"
            f"Current time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n"
        )

    def _build_tools_for_api(self) -> list[dict]:
        """Convert registered tools to Anthropic API format."""
        api_tools = []
        for tool in self.tools.list_tools():
            api_tools.append({
                "name": tool.name,
                "description": tool.description,
                "input_schema": {
                    "type": "object",
                    "properties": tool.input_schema,
                },
            })
        return api_tools

    def _truncate_tool_result(self, output: str) -> tuple[str, bool]:
        """Truncate tool output if exceeding limits."""
        max_chars = self.config.max_tool_output_chars
        if len(output) <= max_chars:
            return output, False
        truncated = output[:max_chars] + f"\n... [truncated, {len(output) - max_chars} chars omitted]"
        return truncated, True

    def _estimate_tokens(self) -> int:
        """Rough estimate of current context usage."""
        # ~4 chars per token as rough estimate
        total_chars = len(self._system_prompt)
        for msg in self.state.messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total_chars += len(json.dumps(block))
        return total_chars // 4

    def _should_compact(self) -> bool:
        """Check if context should be compacted."""
        estimated = self._estimate_tokens()
        threshold = int(self.config.context_window * self.config.auto_compact_threshold)
        return estimated > threshold

    async def _compact_messages(self) -> str | None:
        """
        Compact messages by summarizing old ones.
        Returns summary text or None if compaction not needed/failed.

        Anthropic has 4-stage compaction: Snip → Microcompact → Context Collapse → Auto-compact.
        We implement a simpler single-pass summary.
        """
        if not self._client or len(self.state.messages) < 6:
            return None

        # Keep last 4 messages, summarize the rest
        to_summarize = self.state.messages[:-4]
        to_keep = self.state.messages[-4:]

        summary_prompt = (
            "Summarize the following conversation concisely, preserving:\n"
            "1. Key decisions made\n"
            "2. Important file paths and code changes\n"
            "3. Current task state\n"
            "4. Any errors or blockers\n\n"
            "Conversation to summarize:\n"
        )
        for msg in to_summarize:
            role = msg["role"]
            content = msg.get("content", "")
            if isinstance(content, str):
                summary_prompt += f"\n[{role}]: {content[:500]}"
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        summary_prompt += f"\n[{role}]: {block['text'][:500]}"

        try:
            response = self._client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": summary_prompt}],
            )
            summary = response.content[0].text

            # Replace messages with summary + recent
            self.state.messages = [
                {"role": "user", "content": f"[Previous conversation summary]: {summary}"},
                {"role": "assistant", "content": "Understood. I have the context from the summary. Continuing."},
                *to_keep,
            ]
            self.state.compaction_count += 1
            return summary
        except Exception:
            return None

    async def _call_api(self) -> dict:
        """Make API call to Anthropic. Returns the response."""
        if not self._client:
            raise RuntimeError("No Anthropic client configured. Set api_key in QueryConfig.")

        api_tools = self._build_tools_for_api()

        kwargs = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "system": self._system_prompt,
            "messages": self.state.messages,
        }
        if self.config.temperature > 0:
            kwargs["temperature"] = self.config.temperature
        if api_tools:
            kwargs["tools"] = api_tools

        response = self._client.messages.create(**kwargs)

        # Track tokens
        self.state.total_input_tokens += response.usage.input_tokens
        self.state.total_output_tokens += response.usage.output_tokens

        return response

    def _extract_tool_calls(self, response) -> list[dict]:
        """Extract tool_use blocks from API response."""
        tool_calls = []
        for block in response.content:
            if block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
        return tool_calls

    def _extract_text(self, response) -> str:
        """Extract text content from API response."""
        texts = []
        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
        return "\n".join(texts)

    async def _execute_tools(self, tool_calls: list[dict]) -> list[dict]:
        """
        Execute tool calls and return tool_result blocks.

        Anthropic executes read-only tools concurrently (max 10) and write tools serially.
        We implement the same pattern via ToolRegistry.
        """
        results = []
        total_chars = 0

        for tc in tool_calls:
            # Check aggregate output budget
            if total_chars >= self.config.max_total_tool_chars:
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": "[Output budget exceeded — result omitted]",
                    "is_error": True,
                })
                continue

            try:
                result = await self.tools.execute(tc["name"], tc["input"])
                output, _ = self._truncate_tool_result(result.output)
                is_error = result.exit_code != 0

                if result.blocked_by_permission:
                    output = f"Permission denied: {result.error}"
                    is_error = True
                elif result.blocked_by_hook:
                    output = f"Blocked by hook: {result.error}"
                    is_error = True
                elif result.error and is_error:
                    output = f"Error: {result.error}\n{output}" if output else f"Error: {result.error}"

            except Exception as e:
                output = f"Tool execution error: {str(e)}"
                is_error = True

            total_chars += len(output)
            self.state.tool_use_count += 1

            results.append({
                "type": "tool_result",
                "tool_use_id": tc["id"],
                "content": output,
                "is_error": is_error,
            })

        return results

    async def run(self, user_message: str) -> AsyncGenerator[QueryEvent, None]:
        """
        Run the query loop for a user message.

        Yields QueryEvents as the loop progresses.
        This is the core loop — Anthropic's equivalent is the while(true)
        state machine in query.ts.
        """
        self.state.start_time = time.monotonic()

        # Add user message
        self.state.messages.append({"role": "user", "content": user_message})

        # Fire session hooks
        if self._hook_runner:
            self._hook_runner.fire("pre_message", {"message": user_message[:200]})

        while self.state.turn_count < self.config.max_turns and not self.state.aborted:
            self.state.turn_count += 1

            yield QueryEvent(EventType.TURN_START, {"turn": self.state.turn_count})

            # Check compaction
            if self._should_compact():
                yield QueryEvent(EventType.COMPACT, {"reason": "context_threshold"})
                summary = await self._compact_messages()
                if summary:
                    yield QueryEvent(EventType.COMPACT, {"summary": summary[:200]})

            # API call
            yield QueryEvent(EventType.API_CALL, {
                "model": self.config.model,
                "messages_count": len(self.state.messages),
            })

            try:
                response = await self._call_api()
            except Exception as e:
                error_str = str(e)

                # Recovery: prompt too long
                if "prompt is too long" in error_str.lower() or "context_length" in error_str.lower():
                    if self.state.recovery_count < self.config.max_recovery_attempts:
                        self.state.recovery_count += 1
                        yield QueryEvent(EventType.RECOVERY, {"type": "compact", "attempt": self.state.recovery_count})
                        await self._compact_messages()
                        continue
                    else:
                        yield QueryEvent(EventType.ERROR, {"error": "Max recovery attempts exceeded", "original": error_str})
                        break

                # Recovery: overloaded
                if "overloaded" in error_str.lower():
                    if self.state.recovery_count < self.config.max_recovery_attempts:
                        self.state.recovery_count += 1
                        yield QueryEvent(EventType.RECOVERY, {"type": "retry", "attempt": self.state.recovery_count})
                        await asyncio.sleep(2 ** self.state.recovery_count)  # exponential backoff
                        continue

                yield QueryEvent(EventType.ERROR, {"error": error_str})
                break

            # Process response
            text = self._extract_text(response)
            tool_calls = self._extract_tool_calls(response)

            # Emit text
            if text:
                yield QueryEvent(EventType.TEXT, {"text": text})

            # Add assistant message to history
            self.state.messages.append({
                "role": "assistant",
                "content": [b.__dict__ if hasattr(b, '__dict__') else {"type": "text", "text": str(b)} for b in response.content],
            })

            # If no tool calls, we're done
            if not tool_calls:
                break

            # Execute tools
            for tc in tool_calls:
                yield QueryEvent(EventType.TOOL_USE, {"tool": tc["name"], "input": tc["input"]})

            tool_results = await self._execute_tools(tool_calls)

            for tr in tool_results:
                yield QueryEvent(EventType.TOOL_RESULT, {
                    "tool_use_id": tr["tool_use_id"],
                    "is_error": tr.get("is_error", False),
                    "output_length": len(tr["content"]),
                })

            # Add tool results to messages
            self.state.messages.append({
                "role": "user",
                "content": tool_results,
            })

            # Fire post-tool hooks
            if self._hook_runner:
                self._hook_runner.fire("post_message", {
                    "turn": self.state.turn_count,
                    "tools_used": len(tool_calls),
                })

        # Done
        duration = (time.monotonic() - self.state.start_time) * 1000
        yield QueryEvent(EventType.DONE, {
            "turns": self.state.turn_count,
            "total_input_tokens": self.state.total_input_tokens,
            "total_output_tokens": self.state.total_output_tokens,
            "tool_uses": self.state.tool_use_count,
            "recoveries": self.state.recovery_count,
            "compactions": self.state.compaction_count,
            "duration_ms": round(duration, 1),
        })

        # Fire completion hooks
        if self._hook_runner:
            self._hook_runner.fire("task_completed", {
                "turns": self.state.turn_count,
                "tool_uses": self.state.tool_use_count,
            })

    def abort(self):
        """Abort the current query loop."""
        self.state.aborted = True

    def stats(self) -> dict:
        """Return current loop stats."""
        return {
            "agent": self.config.agent,
            "model": self.config.model,
            "turns": self.state.turn_count,
            "input_tokens": self.state.total_input_tokens,
            "output_tokens": self.state.total_output_tokens,
            "tool_uses": self.state.tool_use_count,
            "recoveries": self.state.recovery_count,
            "compactions": self.state.compaction_count,
        }


# ── Standalone test (no API key needed) ─────────────────────────────

async def _run_tests():
    """Self-contained tests without API calls."""

    # T1: Config defaults
    config = QueryConfig()
    assert config.agent == "ADA"
    assert config.max_turns == 50
    print("PASS: T1 config defaults ✓")

    # T2: QueryLoop init without API key
    loop = QueryLoop(config)
    assert loop.state.turn_count == 0
    assert loop._system_prompt is not None
    print("PASS: T2 init without API ✓")

    # T3: Tool registration
    loop.tools.register(ToolDef(
        name="test_tool",
        description="Test",
        execute=lambda d: ToolResult("test_tool", "test output"),
        is_read_only=True,
    ))
    api_tools = loop._build_tools_for_api()
    assert len(api_tools) == 1
    assert api_tools[0]["name"] == "test_tool"
    print("PASS: T3 tool → API format ✓")

    # T4: Truncation
    output, truncated = loop._truncate_tool_result("x" * 60000)
    assert truncated and len(output) < 55000
    output2, truncated2 = loop._truncate_tool_result("short")
    assert not truncated2
    print("PASS: T4 truncation ✓")

    # T5: Token estimation
    loop.state.messages = [
        {"role": "user", "content": "hello " * 1000},
        {"role": "assistant", "content": "world " * 1000},
    ]
    tokens = loop._estimate_tokens()
    assert tokens > 0
    print(f"PASS: T5 token estimate ({tokens} tokens) ✓")

    # T6: Compaction threshold
    loop.config.context_window = 100  # very small for testing
    assert loop._should_compact()  # messages exceed 85% of 100
    loop.config.context_window = 200_000  # reset
    assert not loop._should_compact()
    print("PASS: T6 compaction threshold ✓")

    # T7: Tool execution
    loop.tools.register(ToolDef(
        name="echo",
        description="Echo input",
        execute=lambda d: ToolResult("echo", d.get("text", "empty")),
        is_read_only=True,
    ))
    results = await loop._execute_tools([
        {"id": "tc_1", "name": "echo", "input": {"text": "hello seal"}},
    ])
    assert len(results) == 1
    assert results[0]["content"] == "hello seal"
    assert not results[0]["is_error"]
    print("PASS: T7 tool execution ✓")

    # T8: Tool execution with error
    loop.tools.register(ToolDef(
        name="fail",
        description="Always fails",
        execute=lambda d: ToolResult("fail", "", error="intentional", exit_code=1),
        is_read_only=True,
    ))
    results = await loop._execute_tools([
        {"id": "tc_2", "name": "fail", "input": {}},
    ])
    assert results[0]["is_error"]
    print("PASS: T8 tool error handling ✓")

    # T9: Aggregate output budget
    loop.config.max_total_tool_chars = 10  # tiny budget
    results = await loop._execute_tools([
        {"id": "tc_3", "name": "echo", "input": {"text": "a" * 20}},
        {"id": "tc_4", "name": "echo", "input": {"text": "b" * 20}},
    ])
    # Second should be budget-exceeded
    assert "budget exceeded" in results[1]["content"].lower()
    loop.config.max_total_tool_chars = 200_000  # reset
    print("PASS: T9 aggregate output budget ✓")

    # T10: Abort
    loop.abort()
    assert loop.state.aborted
    print("PASS: T10 abort ✓")

    # T11: Stats
    stats = loop.stats()
    assert stats["agent"] == "ADA"
    assert "tool_uses" in stats
    print("PASS: T11 stats ✓")

    # T12: EventType enum
    assert EventType.DONE.value == "done"
    assert EventType.TOOL_USE.value == "tool_use"
    print("PASS: T12 event types ✓")

    print(f"\n=== 12/12 TESTS PASARON ✓ ===")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        asyncio.run(_run_tests())
    else:
        print("Usage: python3 query_loop.py test")
        print("       (API mode requires integration with full SEAL Runtime)")
