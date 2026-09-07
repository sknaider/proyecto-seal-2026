"""Tests for AgentOrchestrator — central multi-agent coordinator."""

import sys
import pathlib
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.agents.orchestrator import (
    AgentOrchestrator, AgentProfile, AgentRouter, OrchestrationResult,
)
from tools.agents.subagent_spawner import SubAgentResult, SubAgentSpawner


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok_result(agent="NEXUS", task="test") -> SubAgentResult:
    return SubAgentResult(
        task_id="abc123", agent=agent, success=True,
        output=f"done: {task}", exit_code=0, elapsed_s=0.1, task=task,
    )


def _fail_result(agent="NEXUS") -> SubAgentResult:
    return SubAgentResult(
        task_id="err123", agent=agent, success=False,
        output="", error="failed", exit_code=1, elapsed_s=0.1, task="fail",
    )


class _MockSpawner:
    """Spawner that returns scripted results."""
    def __init__(self, results=None, side_effect=None):
        self.calls = []
        self._results = results or {}
        self._default = _ok_result()
        self._side_effect = side_effect

    def spawn(self, task, agent="NEXUS", context=None,
              worker_script=None, timeout=120, task_id=None):
        self.calls.append({"task": task, "agent": agent, "context": context})
        if self._side_effect:
            return self._side_effect(task, agent)
        return self._results.get(agent, _ok_result(agent=agent, task=task))

    def summary(self):
        return {"total": len(self.calls), "succeeded": len(self.calls), "failed": 0,
                "work_dir": "/tmp"}


def _orch(spawner=None, router=None) -> AgentOrchestrator:
    return AgentOrchestrator(
        spawner=spawner or _MockSpawner(),
        router=router,
    )


# ── AgentProfile ──────────────────────────────────────────────────────────────

def test_profile_score_matches_keywords():
    p = AgentProfile("ADA", ["memory", "search", "recall"])
    assert p.score("search memory for recall") == 3
    assert p.score("deploy server") == 0


def test_profile_score_partial():
    p = AgentProfile("JARVIS", ["code", "build"])
    assert p.score("build the code") == 2
    assert p.score("write docs") == 0


# ── AgentRouter ───────────────────────────────────────────────────────────────

def test_router_routes_to_best_match():
    router = AgentRouter()
    assert router.route("analyze memory usage") in ("ADA", "NEXUS")


def test_router_routes_gpu_to_dum():
    router = AgentRouter()
    assert router.route("monitor GPU resource") == "DUM"


def test_router_routes_code_to_jarvis():
    router = AgentRouter()
    assert router.route("implement API and deploy server") == "JARVIS"


def test_router_routes_documentation_to_alice():
    router = AgentRouter()
    assert router.route("write technical report and document") == "ALICE"


def test_router_top_n():
    router = AgentRouter()
    top = router.route_top("write code and document it", n=2)
    assert len(top) == 2
    assert "JARVIS" in top or "ALICE" in top


def test_router_register_custom():
    router = AgentRouter([])
    router.register(AgentProfile("BOT", ["custom", "task"]))
    assert router.route("custom task for BOT") == "BOT"


def test_router_agents_list():
    router = AgentRouter()
    agents = router.agents()
    assert "ADA" in agents
    assert "JARVIS" in agents
    assert "DUM" in agents


# ── OrchestrationResult ───────────────────────────────────────────────────────

def test_result_ok_when_all_succeed():
    r = OrchestrationResult(
        results=[_ok_result("ADA"), _ok_result("JARVIS")],
        total_s=0.2, succeeded=2, failed=0,
    )
    assert r.ok is True


def test_result_not_ok_when_any_fails():
    r = OrchestrationResult(
        results=[_ok_result(), _fail_result()],
        total_s=0.2, succeeded=1, failed=1,
    )
    assert r.ok is False


def test_result_combined_output():
    r = OrchestrationResult(
        results=[_ok_result("ADA", "search"), _ok_result("JARVIS", "build")],
        total_s=0.1, succeeded=2, failed=0,
    )
    combined = r.combined_output
    assert "ADA" in combined
    assert "JARVIS" in combined


def test_result_last_output():
    r = OrchestrationResult(
        results=[_ok_result("ADA"), _ok_result("JARVIS")],
        total_s=0.1, succeeded=2, failed=0,
    )
    assert "done" in r.last_output()


# ── AgentOrchestrator.dispatch ────────────────────────────────────────────────

def test_dispatch_calls_spawner():
    spawner = _MockSpawner()
    orch = _orch(spawner)
    result = orch.dispatch("analyze memory", agent="ADA")
    assert len(spawner.calls) == 1
    assert spawner.calls[0]["agent"] == "ADA"
    assert result.ok


def test_dispatch_auto_routes_when_no_agent():
    spawner = _MockSpawner()
    orch = _orch(spawner)
    orch.dispatch("monitor GPU alert")
    assert spawner.calls[0]["agent"] == "DUM"


def test_dispatch_passes_context():
    spawner = _MockSpawner()
    orch = _orch(spawner)
    orch.dispatch("task", agent="NEXUS", context={"key": "val"})
    assert spawner.calls[0]["context"] == {"key": "val"}


def test_dispatch_records_history():
    orch = _orch()
    orch.dispatch("task1", agent="ADA")
    orch.dispatch("task2", agent="JARVIS")
    s = orch.summary()
    assert s["orchestrations"] == 2
    assert s["total_tasks"] == 2
    assert s["succeeded"] == 2


# ── AgentOrchestrator.dispatch_parallel ──────────────────────────────────────

def test_dispatch_parallel_runs_all():
    spawner = _MockSpawner()
    orch = _orch(spawner)
    result = orch.dispatch_parallel([
        ("search memory",  "ADA"),
        ("check GPU",      "DUM"),
        ("deploy server",  "JARVIS"),
    ])
    assert len(result.results) == 3
    assert result.succeeded == 3


def test_dispatch_parallel_is_concurrent():
    call_times = []
    lock = threading.Lock()

    def side_effect(task, agent):
        t = time.monotonic()
        with lock:
            call_times.append(t)
        time.sleep(0.05)
        return _ok_result(agent=agent, task=task)

    spawner = _MockSpawner(side_effect=side_effect)
    orch = _orch(spawner)
    t0 = time.monotonic()
    orch.dispatch_parallel([("t1", "ADA"), ("t2", "DUM"), ("t3", "JARVIS")])
    elapsed = time.monotonic() - t0
    # 3 tasks × 50ms each; if sequential = ~150ms, parallel = ~60ms
    assert elapsed < 0.12, f"Too slow ({elapsed:.3f}s) — likely sequential"


def test_dispatch_parallel_partial_failure():
    def side_effect(task, agent):
        if agent == "DUM":
            return _fail_result(agent="DUM")
        return _ok_result(agent=agent, task=task)

    spawner = _MockSpawner(side_effect=side_effect)
    orch = _orch(spawner)
    result = orch.dispatch_parallel([("t1", "ADA"), ("t2", "DUM")])
    assert result.succeeded == 1
    assert result.failed == 1
    assert not result.ok


def test_dispatch_parallel_auto_routes_none_agents():
    spawner = _MockSpawner()
    orch = _orch(spawner)
    orch.dispatch_parallel([("monitor GPU", None), ("write docs", None)])
    agents = {c["agent"] for c in spawner.calls}
    assert "DUM" in agents
    assert "ALICE" in agents


# ── AgentOrchestrator.pipeline ────────────────────────────────────────────────

def test_pipeline_runs_stages_sequentially():
    order = []

    def side_effect(task, agent):
        order.append(agent)
        return _ok_result(agent=agent, task=task)

    spawner = _MockSpawner(side_effect=side_effect)
    orch = _orch(spawner)
    orch.pipeline([("research", "ADA"), ("write", "ALICE"), ("review", "JARVIS")])
    assert order == ["ADA", "ALICE", "JARVIS"]


def test_pipeline_passes_previous_output():
    outputs = []

    def side_effect(task, agent):
        ctx = spawner.calls[-1]["context"] if spawner.calls else {}
        outputs.append(ctx.get("previous_output", ""))
        return SubAgentResult(
            task_id="x", agent=agent, success=True,
            output=f"result-from-{agent}", exit_code=0, elapsed_s=0.0, task=task,
        )

    spawner = _MockSpawner(side_effect=side_effect)
    orch = _orch(spawner)
    orch.pipeline([("stage1", "ADA"), ("stage2", "ALICE")])
    assert outputs[0] == ""
    assert outputs[1] == "result-from-ADA"


def test_pipeline_stops_on_failure():
    call_count = [0]

    def side_effect(task, agent):
        call_count[0] += 1
        if agent == "ALICE":
            return _fail_result(agent="ALICE")
        return _ok_result(agent=agent, task=task)

    spawner = _MockSpawner(side_effect=side_effect)
    orch = _orch(spawner)
    orch.pipeline([("t1", "ADA"), ("t2", "ALICE"), ("t3", "JARVIS")])
    assert call_count[0] == 2  # stops after ALICE fails


# ── AgentOrchestrator.broadcast ───────────────────────────────────────────────

def test_broadcast_sends_to_all_agents():
    spawner = _MockSpawner()
    orch = _orch(spawner)
    result = orch.broadcast("report status")
    called_agents = {c["agent"] for c in spawner.calls}
    assert "ADA" in called_agents
    assert "JARVIS" in called_agents
    assert "DUM" in called_agents


def test_broadcast_subset():
    spawner = _MockSpawner()
    orch = _orch(spawner)
    orch.broadcast("ping", agents=["ADA", "NEXUS"])
    called = {c["agent"] for c in spawner.calls}
    assert called == {"ADA", "NEXUS"}


# ── route() ───────────────────────────────────────────────────────────────────

def test_route_returns_string():
    orch = _orch()
    agent = orch.route("monitor GPU")
    assert isinstance(agent, str)
    assert agent == "DUM"


# ── summary ───────────────────────────────────────────────────────────────────

def test_summary_empty():
    orch = _orch()
    s = orch.summary()
    assert s["orchestrations"] == 0
    assert s["total_tasks"] == 0


# ── Runner ────────────────────────────────────────────────────────────────────

def main() -> int:
    tests = [
        test_profile_score_matches_keywords,
        test_profile_score_partial,
        test_router_routes_to_best_match,
        test_router_routes_gpu_to_dum,
        test_router_routes_code_to_jarvis,
        test_router_routes_documentation_to_alice,
        test_router_top_n,
        test_router_register_custom,
        test_router_agents_list,
        test_result_ok_when_all_succeed,
        test_result_not_ok_when_any_fails,
        test_result_combined_output,
        test_result_last_output,
        test_dispatch_calls_spawner,
        test_dispatch_auto_routes_when_no_agent,
        test_dispatch_passes_context,
        test_dispatch_records_history,
        test_dispatch_parallel_runs_all,
        test_dispatch_parallel_is_concurrent,
        test_dispatch_parallel_partial_failure,
        test_dispatch_parallel_auto_routes_none_agents,
        test_pipeline_runs_stages_sequentially,
        test_pipeline_passes_previous_output,
        test_pipeline_stops_on_failure,
        test_broadcast_sends_to_all_agents,
        test_broadcast_subset,
        test_route_returns_string,
        test_summary_empty,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"[OK] {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(main())
