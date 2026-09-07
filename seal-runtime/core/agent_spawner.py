#!/usr/bin/env python3
"""
agent_spawner.py — SEAL Agent Spawner
=======================================
Clean-room reimplementation of Claude Code's Agent tool (SPEC_02).
Manages spawning, tracking, messaging, and lifecycle of sub-agents.

Anthropic has two modes:
  - Subagent (own context, own tools, own system prompt)
  - Fork (inherits parent context + cache sharing)

SEAL adds:
  - SOUL integration per agent (OCEAN, identity, memory)
  - Scratchpad handoffs between agents
  - Agent-specific tool permissions via ToolRegistry

Usage:
    spawner = AgentSpawner(parent_agent="ADA")
    agent_id = spawner.spawn(AgentDef(
        name="researcher",
        agent_type="explore",
        prompt="Find all files related to authentication",
    ))
    # Check status
    status = spawner.status(agent_id)
    # Send follow-up message
    spawner.send_message(agent_id, "Also check the OAuth module")
    # Get result when done
    result = spawner.get_result(agent_id)

Standalone:
    python3 agent_spawner.py test
"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable
from pathlib import Path


class AgentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class AgentType(Enum):
    """Built-in agent types (inspired by Anthropic's built-in agents)."""
    GENERAL = "general-purpose"
    EXPLORE = "explore"        # Fast codebase exploration
    PLAN = "plan"              # Architecture planning
    VERIFY = "verify"          # Verification / testing
    DREAM = "dream"            # Memory consolidation (SEAL-specific)
    GUARDIA = "guardia"        # Guard mode (SEAL-specific)


@dataclass
class AgentDef:
    """Definition for spawning a new agent."""
    name: str
    prompt: str
    agent_type: str = "general-purpose"
    model: str | None = None          # override parent model
    tools: list[str] | None = None    # allowed tools (None = inherit parent)
    denied_tools: list[str] | None = None
    max_turns: int = 30
    run_in_background: bool = False
    isolation: str | None = None      # "worktree" for git isolation
    memory_scope: str | None = None   # "user", "project", "local"
    description: str = ""             # short description (3-5 words)


@dataclass
class AgentInstance:
    """A running or completed agent instance."""
    id: str
    name: str
    definition: AgentDef
    parent_agent: str
    status: AgentStatus = AgentStatus.PENDING
    result: str | None = None
    error: str | None = None
    start_time: float = 0.0
    end_time: float = 0.0
    turns_used: int = 0
    tool_uses: int = 0
    tokens_used: int = 0
    pending_messages: list[str] = field(default_factory=list)
    progress: list[str] = field(default_factory=list)  # last 5 activities


@dataclass
class TaskNotification:
    """
    Notification sent back to parent when agent completes.
    Equivalent to Anthropic's <task-notification> XML.
    """
    agent_id: str
    status: str  # completed, failed, stopped
    summary: str
    result: str
    usage: dict = field(default_factory=dict)

    def to_xml(self) -> str:
        """Format as XML like Anthropic does."""
        return (
            f"<task-notification>\n"
            f"  <task-id>{self.agent_id}</task-id>\n"
            f"  <status>{self.status}</status>\n"
            f"  <summary>{self.summary}</summary>\n"
            f"  <result>{self.result}</result>\n"
            f"  <usage>\n"
            f"    <total_tokens>{self.usage.get('total_tokens', 0)}</total_tokens>\n"
            f"    <tool_uses>{self.usage.get('tool_uses', 0)}</tool_uses>\n"
            f"    <duration_ms>{self.usage.get('duration_ms', 0)}</duration_ms>\n"
            f"  </usage>\n"
            f"</task-notification>"
        )

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "status": self.status,
            "summary": self.summary,
            "result": self.result,
            "usage": self.usage,
        }


class AgentSpawner:
    """
    Manages agent lifecycle: spawn, track, message, collect results.

    Inspired by Anthropic's AgentTool.tsx but Python-native:
    - No fork mode yet (requires shared prompt cache infrastructure)
    - Subagent mode with own context and tool restrictions
    - Background execution with notification on completion
    - Send follow-up messages to running agents
    """

    def __init__(self, parent_agent: str = "ADA",
                 default_model: str = "claude-sonnet-4-6",
                 on_notification: Callable[[TaskNotification], None] | None = None):
        self.parent_agent = parent_agent.upper()
        self.default_model = default_model
        self._agents: dict[str, AgentInstance] = {}
        self._notifications: list[TaskNotification] = []
        self._on_notification = on_notification
        self._background_tasks: dict[str, asyncio.Task] = {}

    def _generate_id(self, label: str = "") -> str:
        """Generate agent ID: 'a' + optional label + 16 hex chars."""
        hex_part = uuid.uuid4().hex[:16]
        if label:
            safe_label = "".join(c for c in label[:10] if c.isalnum())
            return f"a{safe_label}{hex_part}"
        return f"a{hex_part}"

    def spawn(self, definition: AgentDef) -> str:
        """
        Spawn a new agent. Returns agent_id.

        The agent is registered immediately but execution depends on
        whether it's background (async) or foreground (needs await).
        """
        agent_id = self._generate_id(definition.name)

        instance = AgentInstance(
            id=agent_id,
            name=definition.name,
            definition=definition,
            parent_agent=self.parent_agent,
            status=AgentStatus.PENDING,
            start_time=time.monotonic(),
        )

        self._agents[agent_id] = instance
        return agent_id

    async def run_agent(self, agent_id: str,
                        executor: Callable[[AgentDef, list[str]], Awaitable[str]] | None = None) -> TaskNotification:
        """
        Execute an agent's task.

        If executor is provided, uses it to run the agent's prompt.
        Otherwise, returns a placeholder (real execution requires QueryLoop integration).
        """
        instance = self._agents.get(agent_id)
        if not instance:
            raise ValueError(f"Agent {agent_id} not found")

        instance.status = AgentStatus.RUNNING
        start = time.monotonic()

        try:
            if executor:
                # Real execution via provided executor
                result = await executor(instance.definition, instance.pending_messages)
            else:
                # Placeholder for testing
                result = f"[Agent {instance.name}] Completed prompt: {instance.definition.prompt[:100]}"

            instance.status = AgentStatus.COMPLETED
            instance.result = result
            instance.end_time = time.monotonic()

        except asyncio.CancelledError:
            instance.status = AgentStatus.STOPPED
            instance.error = "Cancelled"
            instance.end_time = time.monotonic()

        except Exception as e:
            instance.status = AgentStatus.FAILED
            instance.error = str(e)
            instance.end_time = time.monotonic()

        duration_ms = (instance.end_time - start) * 1000

        notification = TaskNotification(
            agent_id=agent_id,
            status=instance.status.value,
            summary=instance.definition.description or instance.name,
            result=instance.result or instance.error or "",
            usage={
                "total_tokens": instance.tokens_used,
                "tool_uses": instance.tool_uses,
                "duration_ms": round(duration_ms, 1),
            },
        )

        self._notifications.append(notification)
        if self._on_notification:
            self._on_notification(notification)

        return notification

    async def spawn_and_run(self, definition: AgentDef,
                            executor: Callable | None = None) -> TaskNotification:
        """Spawn + run in one call. Convenience method."""
        agent_id = self.spawn(definition)
        return await self.run_agent(agent_id, executor)

    async def spawn_background(self, definition: AgentDef,
                               executor: Callable | None = None) -> str:
        """Spawn agent in background. Returns agent_id immediately."""
        agent_id = self.spawn(definition)
        task = asyncio.create_task(self.run_agent(agent_id, executor))
        self._background_tasks[agent_id] = task
        return agent_id

    def send_message(self, agent_id: str, message: str) -> bool:
        """
        Send a follow-up message to an agent.

        If running: queued in pending_messages, delivered on next tool round.
        If stopped: triggers resume (not implemented yet).
        """
        instance = self._agents.get(agent_id)
        if not instance:
            return False

        if instance.status in (AgentStatus.COMPLETED, AgentStatus.FAILED):
            return False

        instance.pending_messages.append(message)
        return True

    def stop(self, agent_id: str) -> bool:
        """Stop a running agent."""
        instance = self._agents.get(agent_id)
        if not instance or instance.status != AgentStatus.RUNNING:
            return False

        # Cancel background task if exists
        task = self._background_tasks.get(agent_id)
        if task and not task.done():
            task.cancel()

        instance.status = AgentStatus.STOPPED
        instance.end_time = time.monotonic()
        return True

    def status(self, agent_id: str) -> dict | None:
        """Get agent status."""
        instance = self._agents.get(agent_id)
        if not instance:
            return None
        return {
            "id": instance.id,
            "name": instance.name,
            "status": instance.status.value,
            "turns": instance.turns_used,
            "tool_uses": instance.tool_uses,
            "pending_messages": len(instance.pending_messages),
            "progress": instance.progress[-5:],
            "duration_ms": round((instance.end_time or time.monotonic()) - instance.start_time, 1) * 1000
                           if instance.start_time else 0,
        }

    def get_result(self, agent_id: str) -> str | None:
        """Get agent result if completed."""
        instance = self._agents.get(agent_id)
        if not instance:
            return None
        return instance.result

    def list_agents(self, status_filter: AgentStatus | None = None) -> list[dict]:
        """List all agents, optionally filtered by status."""
        results = []
        for inst in self._agents.values():
            if status_filter and inst.status != status_filter:
                continue
            results.append(self.status(inst.id))
        return results

    def pending_notifications(self) -> list[TaskNotification]:
        """Get and clear pending notifications."""
        notifs = self._notifications.copy()
        self._notifications.clear()
        return notifs

    def active_count(self) -> int:
        """Number of currently running agents."""
        return sum(1 for a in self._agents.values()
                   if a.status in (AgentStatus.PENDING, AgentStatus.RUNNING))

    async def spawn_batch(
        self,
        definitions: list[AgentDef],
        max_concurrent: int = 5,
        executor: Callable[..., Awaitable[str]] | None = None,
    ) -> list[TaskNotification]:
        """
        Spawn and run multiple agents in parallel with concurrency limit.
        +1% improvement: fan-out/fan-in pattern with semaphore.

        Returns list of TaskNotifications in same order as definitions.
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def bounded_run(defn: AgentDef) -> TaskNotification:
            async with semaphore:
                return await self.spawn_and_run(defn, executor)

        tasks = [asyncio.create_task(bounded_run(d)) for d in definitions]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        notifications = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                notifications.append(TaskNotification(
                    agent_id=f"batch_error_{i}",
                    status="failed",
                    summary=definitions[i].name,
                    result=str(result),
                ))
            else:
                notifications.append(result)
        return notifications

    def cleanup_completed(self, max_age_seconds: float = 3600) -> int:
        """Remove completed/failed/stopped agents older than max_age."""
        now = time.monotonic()
        to_remove = []
        for agent_id, inst in self._agents.items():
            if inst.status in (AgentStatus.COMPLETED, AgentStatus.FAILED, AgentStatus.STOPPED):
                if inst.end_time and (now - inst.end_time) > max_age_seconds:
                    to_remove.append(agent_id)
        for agent_id in to_remove:
            del self._agents[agent_id]
            self._background_tasks.pop(agent_id, None)
        return len(to_remove)


# ── Tests ───────────────────────────────────────────────────────────

async def _run_tests():
    notifications_received = []

    def on_notif(n):
        notifications_received.append(n)

    spawner = AgentSpawner(parent_agent="ADA", on_notification=on_notif)

    # T1: Spawn
    agent_id = spawner.spawn(AgentDef(
        name="researcher",
        prompt="Find all auth files",
        description="Search auth files",
    ))
    assert agent_id.startswith("a")
    assert spawner.status(agent_id)["status"] == "pending"
    print("PASS: T1 spawn ✓")

    # T2: Run agent (placeholder executor)
    notif = await spawner.run_agent(agent_id)
    assert notif.status == "completed"
    assert "Find all auth files" in notif.result
    assert spawner.status(agent_id)["status"] == "completed"
    print("PASS: T2 run agent ✓")

    # T3: Notification callback
    assert len(notifications_received) == 1
    assert notifications_received[0].agent_id == agent_id
    print("PASS: T3 notification callback ✓")

    # T4: TaskNotification XML format
    xml = notif.to_xml()
    assert "<task-notification>" in xml
    assert "<status>completed</status>" in xml
    print("PASS: T4 XML notification ✓")

    # T5: Custom executor
    async def my_executor(defn, msgs):
        return f"Custom result for: {defn.prompt[:30]}"

    notif2 = await spawner.spawn_and_run(
        AgentDef(name="custom", prompt="Do something custom"),
        executor=my_executor,
    )
    assert "Custom result" in notif2.result
    print("PASS: T5 custom executor ✓")

    # T6: Send message
    agent_id3 = spawner.spawn(AgentDef(name="worker", prompt="Wait for instructions"))
    assert spawner.send_message(agent_id3, "New instruction")
    instance = spawner._agents[agent_id3]
    assert len(instance.pending_messages) == 1
    print("PASS: T6 send message ✓")

    # T7: Cannot message completed agent
    assert not spawner.send_message(agent_id, "Too late")
    print("PASS: T7 no message to completed ✓")

    # T8: List agents
    all_agents = spawner.list_agents()
    assert len(all_agents) >= 3
    running = spawner.list_agents(AgentStatus.COMPLETED)
    assert len(running) >= 2
    print("PASS: T8 list agents ✓")

    # T9: Active count
    assert spawner.active_count() >= 1  # agent_id3 still pending
    print("PASS: T9 active count ✓")

    # T10: Background spawn
    bg_id = await spawner.spawn_background(
        AgentDef(name="background", prompt="Background task"),
    )
    await asyncio.sleep(0.1)  # let it complete
    assert spawner.status(bg_id)["status"] == "completed"
    print("PASS: T10 background spawn ✓")

    # T11: Stop agent
    agent_id4 = spawner.spawn(AgentDef(name="stoppable", prompt="Long task"))
    spawner._agents[agent_id4].status = AgentStatus.RUNNING
    assert spawner.stop(agent_id4)
    assert spawner.status(agent_id4)["status"] == "stopped"
    print("PASS: T11 stop agent ✓")

    # T12: Failed executor
    async def failing_executor(defn, msgs):
        raise RuntimeError("Something went wrong")

    notif_fail = await spawner.spawn_and_run(
        AgentDef(name="failer", prompt="Will fail"),
        executor=failing_executor,
    )
    assert notif_fail.status == "failed"
    assert "Something went wrong" in notif_fail.result
    print("PASS: T12 failed executor ✓")

    # T13: Cleanup
    count = spawner.cleanup_completed(max_age_seconds=0)  # 0 = clean all
    assert count >= 3
    print(f"PASS: T13 cleanup ({count} removed) ✓")

    # T14: Agent types
    assert AgentType.DREAM.value == "dream"
    assert AgentType.GUARDIA.value == "guardia"
    print("PASS: T14 agent types ✓")

    # T15: Notification dict format
    d = notif.to_dict()
    assert "agent_id" in d and "usage" in d
    print("PASS: T15 notification dict ✓")

    print(f"\n=== 15/15 TESTS PASARON ✓ ===")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        asyncio.run(_run_tests())
    else:
        print("Usage: python3 agent_spawner.py test")
