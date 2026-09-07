#!/usr/bin/env python3
"""
tool_registry.py — SEAL Tool Registry
=======================================
Clean-room reimplementation of Claude Code's tool system (SPEC_02).
Each tool is registered with schema, permissions, and execution logic.

Anthropic uses buildTool() + Zod schemas. We use dataclasses + callables.
Anthropic has 40+ tools. We start with the essentials and grow.

Usage:
    registry = ToolRegistry()
    registry.register(ToolDef(
        name="bash",
        description="Execute shell command",
        input_schema={"command": str},
        execute=run_bash,
        is_read_only=False,
        requires_permission=True,
    ))
    result = await registry.execute("bash", {"command": "ls"}, agent="ADA")
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable
from pathlib import Path

# Import hooks if available
try:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scratchpad"))
    from hooks import HookRunner
except ImportError:
    HookRunner = None


@dataclass
class ToolResult:
    """Result of a tool execution."""
    tool_name: str
    output: str
    error: str | None = None
    exit_code: int = 0
    duration_ms: float = 0
    truncated: bool = False
    blocked_by_hook: bool = False
    blocked_by_permission: bool = False


@dataclass
class ToolDef:
    """Definition of a tool in the SEAL registry."""
    name: str
    description: str
    execute: Callable[..., Awaitable[ToolResult] | ToolResult]
    input_schema: dict = field(default_factory=dict)
    is_read_only: bool = False
    is_concurrent_safe: bool = False
    requires_permission: bool = True
    is_enabled: Callable[[], bool] = lambda: True
    max_output_chars: int = 50_000
    agent_filter: str | None = None  # restrict to specific agent

    def matches_agent(self, agent: str) -> bool:
        if self.agent_filter is None:
            return True
        return self.agent_filter.upper() == agent.upper()


class PermissionDenied(Exception):
    """Raised when a tool execution is denied by permissions."""
    pass


class ToolNotFound(Exception):
    """Raised when a tool is not in the registry."""
    pass


class ToolRegistry:
    """
    Registry and executor for SEAL tools.

    Inspired by Anthropic's buildTool() pattern but simpler:
    - No Zod schemas (we use plain dicts)
    - No deferred loading (all tools loaded at init)
    - Hooks integration via scratchpad/hooks.py
    - Permission checks via allow/deny lists
    """

    def __init__(self, agent: str = "ADA"):
        self.agent = agent.upper()
        self._tools: dict[str, ToolDef] = {}
        self._allow_list: set[str] = set()
        self._deny_list: set[str] = set()
        self._execution_log: list[dict] = []
        self._hook_runner: HookRunner | None = None
        if HookRunner:
            try:
                self._hook_runner = HookRunner(agent=self.agent)
            except Exception:
                pass

    def register(self, tool: ToolDef):
        """Register a tool. Last-wins for duplicate names."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        """Remove a tool. Returns True if existed."""
        return self._tools.pop(name, None) is not None

    def get(self, name: str) -> ToolDef | None:
        """Get a tool definition by name."""
        return self._tools.get(name)

    def list_tools(self, agent: str | None = None) -> list[ToolDef]:
        """List all enabled tools, optionally filtered by agent."""
        a = agent or self.agent
        return [
            t for t in self._tools.values()
            if t.is_enabled() and t.matches_agent(a)
        ]

    def allow(self, *tool_names: str):
        """Add tools to the allow list (bypass permission check)."""
        self._allow_list.update(tool_names)

    def deny(self, *tool_names: str):
        """Add tools to the deny list (always block)."""
        self._deny_list.update(tool_names)

    def _check_permission(self, tool: ToolDef) -> bool:
        """
        Permission pipeline (simplified from Anthropic's 7-step pipeline):
        1. Deny list always wins
        2. Allow list bypasses permission check
        3. Read-only tools auto-allowed
        4. Otherwise: requires_permission flag
        """
        if tool.name in self._deny_list:
            return False
        if tool.name in self._allow_list:
            return True
        if tool.is_read_only:
            return True
        return not tool.requires_permission

    def _truncate_output(self, output: str, max_chars: int) -> tuple[str, bool]:
        """Truncate output if exceeding max chars."""
        if len(output) <= max_chars:
            return output, False
        return output[:max_chars] + f"\n... [truncated, {len(output) - max_chars} chars omitted]", True

    async def execute(self, name: str, input_data: dict | None = None,
                      agent: str | None = None) -> ToolResult:
        """
        Execute a tool by name with permission checking and hooks.

        Pipeline:
        1. Find tool in registry
        2. Check permissions (deny → allow → read-only → requires_permission)
        3. Fire pre_tool hooks (can block)
        4. Execute tool
        5. Fire post_tool hooks
        6. Truncate output if needed
        7. Log execution
        """
        agent = (agent or self.agent).upper()
        input_data = input_data or {}

        # 1. Find tool
        tool = self._tools.get(name)
        if not tool:
            raise ToolNotFound(f"Tool '{name}' not found in registry")

        if not tool.is_enabled():
            raise ToolNotFound(f"Tool '{name}' is disabled")

        if not tool.matches_agent(agent):
            raise PermissionDenied(f"Tool '{name}' not available for agent {agent}")

        # 2. Check permissions
        if not self._check_permission(tool):
            return ToolResult(
                tool_name=name,
                output="",
                error=f"Permission denied for tool '{name}'",
                exit_code=1,
                blocked_by_permission=True,
            )

        # 3. Pre-tool hooks
        if self._hook_runner:
            hook_results = self._hook_runner.fire(
                "pre_tool",
                {"tool": name, "agent": agent, **input_data}
            )
            for hr in hook_results:
                if hr.blocked:
                    return ToolResult(
                        tool_name=name,
                        output="",
                        error=f"Blocked by pre_tool hook: {hr.command}",
                        exit_code=1,
                        blocked_by_hook=True,
                    )

        # 4. Execute
        start = time.monotonic()
        try:
            result = tool.execute(input_data)
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as e:
            result = ToolResult(
                tool_name=name,
                output="",
                error=str(e),
                exit_code=1,
            )

        duration = (time.monotonic() - start) * 1000
        result.duration_ms = round(duration, 1)
        result.tool_name = name

        # 5. Post-tool hooks (fire and forget)
        if self._hook_runner:
            self._hook_runner.fire(
                "post_tool",
                {"tool": name, "agent": agent, "exit_code": result.exit_code,
                 "duration_ms": result.duration_ms}
            )

        # 6. Truncate
        result.output, result.truncated = self._truncate_output(
            result.output, tool.max_output_chars
        )

        # 7. Log
        self._execution_log.append({
            "tool": name,
            "agent": agent,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "truncated": result.truncated,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return result

    def execution_stats(self) -> dict:
        """Return stats about tool executions in this session."""
        if not self._execution_log:
            return {"total": 0}
        total = len(self._execution_log)
        errors = sum(1 for e in self._execution_log if e["exit_code"] != 0)
        avg_ms = sum(e["duration_ms"] for e in self._execution_log) / total
        by_tool = {}
        for e in self._execution_log:
            by_tool[e["tool"]] = by_tool.get(e["tool"], 0) + 1
        return {
            "total": total,
            "errors": errors,
            "avg_duration_ms": round(avg_ms, 1),
            "by_tool": by_tool,
        }
