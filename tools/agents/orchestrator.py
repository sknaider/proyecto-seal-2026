"""Central orchestrator — SEAL multi-agent task coordinator.

Routes tasks to the right agents, dispatches sequential pipelines
and parallel task groups, synthesizes results.

Built on SubAgentSpawner for process isolation.  Pure stdlib, no deps.

Usage:
    orch = AgentOrchestrator()

    # Auto-route a single task
    result = orch.dispatch("analyze memory usage")

    # Force to a specific agent
    result = orch.dispatch("check GPU health", agent="DUM")

    # Parallel independent tasks
    results = orch.dispatch_parallel([
        ("summarize recent sessions", "ADA"),
        ("check GPU health",          "DUM"),
    ])

    # Sequential pipeline — each stage receives previous output as context
    final = orch.pipeline([
        ("research on topic X",        "ADA"),
        ("write technical report",     "ALICE"),
        ("validate architecture plan", "JARVIS"),
    ])

    # Broadcast same task to all agents
    all_results = orch.broadcast("report current status")
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from tools.agents.subagent_spawner import SubAgentResult, SubAgentSpawner


# ── Agent profiles ────────────────────────────────────────────────────────────

@dataclass
class AgentProfile:
    name: str
    capabilities: List[str]
    priority: int = 5

    def score(self, task: str) -> int:
        """Return keyword-match count for routing."""
        low = task.lower()
        return sum(1 for kw in self.capabilities if kw in low)


_DEFAULT_PROFILES: List[AgentProfile] = [
    AgentProfile("ADA", [
        "memory", "search", "recall", "learn", "research", "analyze",
        "summarize", "session", "history", "knowledge", "study", "find",
    ], priority=7),
    AgentProfile("JARVIS", [
        "code", "build", "architecture", "infrastructure", "deploy",
        "system", "server", "config", "debug", "fix", "implement",
        "schema", "database", "migration", "api",
    ], priority=8),
    AgentProfile("ALICE", [
        "write", "report", "document", "translate", "explain",
        "describe", "format", "spec", "readme", "draft",
    ], priority=6),
    AgentProfile("NEXUS", [
        "diagnose", "health", "innovate", "design", "improve",
        "monitor", "pipeline", "tool", "create", "new feature",
    ], priority=7),
    AgentProfile("DUM", [
        "monitor", "alert", "gpu", "security", "guard", "watch",
        "heartbeat", "resource", "cpu", "ram", "disk", "process",
    ], priority=9),
]


# ── Router ────────────────────────────────────────────────────────────────────

class AgentRouter:
    """Keyword-based task router."""

    def __init__(self, profiles: Optional[List[AgentProfile]] = None) -> None:
        self._profiles: Dict[str, AgentProfile] = {
            p.name: p for p in (profiles or _DEFAULT_PROFILES)
        }

    def route(self, task: str) -> str:
        """Return the best-matching agent name for task."""
        scores = {
            name: (p.score(task), p.priority)
            for name, p in self._profiles.items()
        }
        return max(scores, key=lambda n: scores[n])

    def route_top(self, task: str, n: int = 3) -> List[str]:
        """Return top-n agent names ordered by match score."""
        scored = sorted(
            self._profiles.keys(),
            key=lambda name: (
                self._profiles[name].score(task),
                self._profiles[name].priority,
            ),
            reverse=True,
        )
        return scored[:n]

    def register(self, profile: AgentProfile) -> None:
        self._profiles[profile.name] = profile

    def agents(self) -> List[str]:
        return list(self._profiles.keys())


# ── Orchestration result ──────────────────────────────────────────────────────

@dataclass
class OrchestrationResult:
    """Aggregated result from one or more dispatched agents."""
    results:    List[SubAgentResult]
    total_s:    float = 0.0
    succeeded:  int   = 0
    failed:     int   = 0

    @property
    def ok(self) -> bool:
        return self.failed == 0 and bool(self.results)

    @property
    def combined_output(self) -> str:
        parts = []
        for r in self.results:
            parts.append(f"[{r.agent}] {r.output or r.error or '(no output)'}")
        return "\n".join(parts)

    def last_output(self) -> str:
        return self.results[-1].output if self.results else ""


# ── Orchestrator ──────────────────────────────────────────────────────────────

class AgentOrchestrator:
    """Routes, dispatches, and synthesizes multi-agent task execution.

    Uses SubAgentSpawner for subprocess isolation. Routes tasks automatically
    when no agent is specified.
    """

    def __init__(
        self,
        spawner:       Optional[SubAgentSpawner] = None,
        router:        Optional[AgentRouter]     = None,
        default_agent: str                       = "NEXUS",
        notify:        bool                      = False,
    ) -> None:
        self._spawner       = spawner or SubAgentSpawner(notify=notify)
        self._router        = router  or AgentRouter()
        self._default_agent = default_agent
        self._history:      List[OrchestrationResult] = []
        self._lock          = threading.Lock()

    # ── Public API ─────────────────────────────────────────────────────────────

    def dispatch(
        self,
        task:          str,
        agent:         Optional[str]  = None,
        context:       Optional[dict] = None,
        worker_script: Optional[str]  = None,
        timeout:       int            = 120,
    ) -> OrchestrationResult:
        """Dispatch a single task.  Auto-routes if agent is None."""
        target = agent or self._router.route(task)
        t0 = time.monotonic()
        result = self._spawner.spawn(
            task=task, agent=target, context=context,
            worker_script=worker_script, timeout=timeout,
        )
        elapsed = time.monotonic() - t0
        orch = OrchestrationResult(
            results   = [result],
            total_s   = round(elapsed, 3),
            succeeded = 1 if result.success else 0,
            failed    = 0 if result.success else 1,
        )
        self._record(orch)
        return orch

    def dispatch_parallel(
        self,
        tasks:         List[Tuple[str, Optional[str]]],
        context:       Optional[dict] = None,
        worker_script: Optional[str]  = None,
        timeout:       int            = 120,
    ) -> OrchestrationResult:
        """Dispatch multiple tasks in parallel threads.

        tasks: list of (task_description, agent_name_or_None)
        """
        collected: List[Optional[SubAgentResult]] = [None] * len(tasks)
        lock = threading.Lock()

        def _run(idx: int, task: str, agent: Optional[str]) -> None:
            target = agent or self._router.route(task)
            res = self._spawner.spawn(
                task=task, agent=target, context=context,
                worker_script=worker_script, timeout=timeout,
            )
            with lock:
                collected[idx] = res

        t0 = time.monotonic()
        threads = [
            threading.Thread(target=_run, args=(i, t, a), daemon=True)
            for i, (t, a) in enumerate(tasks)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=timeout + 5)

        elapsed = time.monotonic() - t0
        results = [r for r in collected if r is not None]
        succeeded = sum(1 for r in results if r.success)
        orch = OrchestrationResult(
            results   = results,
            total_s   = round(elapsed, 3),
            succeeded = succeeded,
            failed    = len(results) - succeeded,
        )
        self._record(orch)
        return orch

    def pipeline(
        self,
        stages:        List[Tuple[str, Optional[str]]],
        worker_script: Optional[str] = None,
        timeout:       int           = 120,
    ) -> OrchestrationResult:
        """Run stages sequentially; each stage receives previous output as context.

        stages: list of (task_description, agent_name_or_None)
        """
        results: List[SubAgentResult] = []
        prev_output: str = ""
        t0 = time.monotonic()

        for task, agent in stages:
            target = agent or self._router.route(task)
            ctx = {"previous_output": prev_output} if prev_output else {}
            res = self._spawner.spawn(
                task=task, agent=target, context=ctx,
                worker_script=worker_script, timeout=timeout,
            )
            results.append(res)
            if not res.success:
                break
            prev_output = res.output

        elapsed = time.monotonic() - t0
        succeeded = sum(1 for r in results if r.success)
        orch = OrchestrationResult(
            results   = results,
            total_s   = round(elapsed, 3),
            succeeded = succeeded,
            failed    = len(results) - succeeded,
        )
        self._record(orch)
        return orch

    def broadcast(
        self,
        task:          str,
        agents:        Optional[List[str]] = None,
        worker_script: Optional[str]       = None,
        timeout:       int                 = 60,
    ) -> OrchestrationResult:
        """Send the same task to all agents (or the specified subset) in parallel."""
        targets = agents or self._router.agents()
        return self.dispatch_parallel(
            [(task, a) for a in targets],
            worker_script=worker_script,
            timeout=timeout,
        )

    def route(self, task: str) -> str:
        """Return the best agent name for task without dispatching."""
        return self._router.route(task)

    def summary(self) -> dict:
        with self._lock:
            total_tasks = sum(len(o.results) for o in self._history)
            succeeded   = sum(o.succeeded for o in self._history)
            return {
                "orchestrations": len(self._history),
                "total_tasks":    total_tasks,
                "succeeded":      succeeded,
                "failed":         total_tasks - succeeded,
            }

    # ── Internals ──────────────────────────────────────────────────────────────

    def _record(self, orch: OrchestrationResult) -> None:
        with self._lock:
            self._history.append(orch)
