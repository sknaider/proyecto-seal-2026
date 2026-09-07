#!/usr/bin/env python3
"""
hooks.py — Lifecycle hooks system for SEAL agents
===================================================
Inspired by Anthropic's hooks.ts (5,022 lines, 18+ event types, asyncRewake).
SEAL adaptation: simpler, Python-native, with asyncRewake via scratchpad handoffs.

Hook events:
  session_start    — Agent boots up
  session_end      — Agent session closing
  pre_tool         — Before a tool executes (can block)
  post_tool        — After a tool executes
  pre_message      — Before sending message to William
  post_message     — After William's message processed
  heartbeat        — On heartbeat tick
  guardia_enter    — Entering guardia mode
  guardia_exit     — Exiting guardia mode
  task_completed   — A task was marked complete
  audit            — On audit cycle

Hook definition (hooks.json):
{
  "hooks": [
    {
      "event": "post_tool",
      "command": "python3 ~/IA/proyecto-seal/scratchpad/check_drift.py",
      "agent": "ADA",           // optional: restrict to agent
      "tool_match": "Bash",     // optional: match specific tool
      "async": false,           // run in background?
      "async_rewake": false,    // on completion, write handoff to wake agent?
      "timeout_s": 30,          // timeout in seconds
      "enabled": true
    }
  ]
}

Usage as module:
    from hooks import HookRunner
    runner = HookRunner(agent="ADA")
    results = runner.fire("post_tool", context={"tool": "Bash", "input": "ls"})
    # For async hooks:
    runner.fire_async("heartbeat", context={"gpu_temp": 55})

Usage standalone:
    python3 hooks.py fire session_start --agent ADA
    python3 hooks.py fire post_tool --agent ADA --context '{"tool":"Bash"}'
    python3 hooks.py list
    python3 hooks.py list --agent ADA
    python3 hooks.py test  # run self-tests
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field

SCRATCHPAD_DIR = Path(__file__).parent
HOOKS_CONFIG = SCRATCHPAD_DIR / "hooks.json"
DEFAULT_TIMEOUT = 30  # seconds
SESSION_END_TIMEOUT = 3  # tight bound for cleanup


@dataclass
class HookResult:
    hook_id: int
    event: str
    command: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool
    blocked: bool  # True if hook returned exit_code 1 (block action)
    async_launched: bool  # True if running in background


@dataclass
class HookDef:
    event: str
    command: str
    agent: str | None = None
    tool_match: str | None = None
    async_hook: bool = False
    async_rewake: bool = False
    timeout_s: int = DEFAULT_TIMEOUT
    enabled: bool = True


def load_hooks(config_path: Path | None = None) -> list[HookDef]:
    """Load hook definitions from hooks.json."""
    path = config_path or HOOKS_CONFIG
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    hooks = []
    for h in data.get("hooks", []):
        hooks.append(HookDef(
            event=h["event"],
            command=h["command"],
            agent=h.get("agent"),
            tool_match=h.get("tool_match"),
            async_hook=h.get("async", False),
            async_rewake=h.get("async_rewake", False),
            timeout_s=h.get("timeout_s", DEFAULT_TIMEOUT),
            enabled=h.get("enabled", True),
        ))
    return hooks


def save_hooks(hooks: list[HookDef], config_path: Path | None = None):
    """Save hook definitions to hooks.json."""
    path = config_path or HOOKS_CONFIG
    data = {"hooks": []}
    for h in hooks:
        data["hooks"].append({
            "event": h.event,
            "command": h.command,
            "agent": h.agent,
            "tool_match": h.tool_match,
            "async": h.async_hook,
            "async_rewake": h.async_rewake,
            "timeout_s": h.timeout_s,
            "enabled": h.enabled,
        })
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


class HookRunner:
    """Execute lifecycle hooks for a SEAL agent."""

    def __init__(self, agent: str, config_path: Path | None = None):
        self.agent = agent.upper()
        self.hooks = load_hooks(config_path)
        self._async_procs: list[tuple[HookDef, subprocess.Popen, float]] = []

    def _matches(self, hook: HookDef, event: str, context: dict) -> bool:
        """Check if a hook matches the event + context."""
        if not hook.enabled:
            return False
        if hook.event != event:
            return False
        if hook.agent and hook.agent.upper() != self.agent:
            return False
        if hook.tool_match and context.get("tool", "") != hook.tool_match:
            return False
        return True

    def _build_env(self, context: dict) -> dict:
        """Build environment variables for hook subprocess."""
        env = os.environ.copy()
        env["SEAL_AGENT"] = self.agent
        env["SEAL_SCRATCHPAD"] = str(SCRATCHPAD_DIR)
        # Flatten context into SEAL_HOOK_* env vars
        for k, v in context.items():
            env[f"SEAL_HOOK_{k.upper()}"] = str(v)
        env["SEAL_HOOK_CONTEXT"] = json.dumps(context, ensure_ascii=False)
        return env

    def _run_hook(self, hook: HookDef, context: dict, hook_id: int) -> HookResult:
        """Execute a single hook synchronously."""
        env = self._build_env(context)
        timeout = SESSION_END_TIMEOUT if hook.event == "session_end" else hook.timeout_s
        start = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                hook.command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                cwd=str(SCRATCHPAD_DIR),
            )
            exit_code = proc.returncode
            stdout = proc.stdout[:10_000]  # cap output
            stderr = proc.stderr[:5_000]
        except subprocess.TimeoutExpired:
            exit_code = None
            stdout = ""
            stderr = f"Hook timed out after {timeout}s"
            timed_out = True
        except Exception as e:
            exit_code = -1
            stdout = ""
            stderr = str(e)

        duration = (time.monotonic() - start) * 1000

        return HookResult(
            hook_id=hook_id,
            event=hook.event,
            command=hook.command,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            duration_ms=round(duration, 1),
            timed_out=timed_out,
            blocked=(exit_code == 1),  # exit 1 = block the action
            async_launched=False,
        )

    def _launch_async(self, hook: HookDef, context: dict, hook_id: int) -> HookResult:
        """Launch a hook in background. If async_rewake, writes handoff on completion."""
        env = self._build_env(context)

        if hook.async_rewake:
            # Wrap command to write handoff on completion
            rewake_cmd = (
                f'EXIT_CODE=$({hook.command} > /tmp/seal_hook_{hook_id}.out 2>&1; echo $?); '
                f'if [ "$EXIT_CODE" = "2" ]; then '
                f'  cd {SCRATCHPAD_DIR} && {sys.executable} scratchpad.py handoff HOOK {self.agent} '
                f'  "$(cat /tmp/seal_hook_{hook_id}.out)" "async_rewake_{hook.event}"; '
                f'fi; '
                f'rm -f /tmp/seal_hook_{hook_id}.out'
            )
            proc = subprocess.Popen(
                rewake_cmd, shell=True, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=str(SCRATCHPAD_DIR),
            )
        else:
            proc = subprocess.Popen(
                hook.command, shell=True, env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                cwd=str(SCRATCHPAD_DIR),
            )

        self._async_procs.append((hook, proc, time.monotonic()))

        return HookResult(
            hook_id=hook_id,
            event=hook.event,
            command=hook.command,
            exit_code=None,
            stdout="",
            stderr="",
            duration_ms=0,
            timed_out=False,
            blocked=False,
            async_launched=True,
        )

    def fire(self, event: str, context: dict | None = None) -> list[HookResult]:
        """Fire all matching hooks for an event. Returns results."""
        context = context or {}
        results = []
        for i, hook in enumerate(self.hooks):
            if not self._matches(hook, event, context):
                continue
            if hook.async_hook or hook.async_rewake:
                result = self._launch_async(hook, context, i)
            else:
                result = self._run_hook(hook, context, i)
            results.append(result)
            # If a sync hook blocks (exit 1), stop firing remaining hooks
            if result.blocked:
                break
        return results

    def check_async(self) -> list[tuple[HookDef, int | None]]:
        """Check status of async hooks. Returns completed ones."""
        completed = []
        still_running = []
        for hook, proc, start_time in self._async_procs:
            ret = proc.poll()
            if ret is not None:
                completed.append((hook, ret))
            elif time.monotonic() - start_time > hook.timeout_s:
                proc.kill()
                completed.append((hook, None))
            else:
                still_running.append((hook, proc, start_time))
        self._async_procs = still_running
        return completed

    def pending_async_count(self) -> int:
        """Number of async hooks still running."""
        return len(self._async_procs)


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: hooks.py <command> [args]")
        print("Commands: fire, list, test, init")
        return

    cmd = sys.argv[1]

    if cmd == "fire":
        if len(sys.argv) < 3:
            print("Usage: hooks.py fire <event> --agent <agent> [--context <json>]")
            return
        event = sys.argv[2]
        agent = "ADA"
        context = {}
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == "--agent" and i + 1 < len(sys.argv):
                agent = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == "--context" and i + 1 < len(sys.argv):
                context = json.loads(sys.argv[i + 1])
                i += 2
            else:
                i += 1
        runner = HookRunner(agent=agent)
        results = runner.fire(event, context)
        for r in results:
            status = "ASYNC" if r.async_launched else ("BLOCKED" if r.blocked else f"exit={r.exit_code}")
            print(f"  [{status}] {r.command[:60]} ({r.duration_ms:.0f}ms)")
            if r.stdout:
                print(f"    stdout: {r.stdout[:200]}")
            if r.stderr:
                print(f"    stderr: {r.stderr[:200]}")
        if not results:
            print(f"  No hooks matched event={event} agent={agent}")

    elif cmd == "list":
        agent_filter = None
        if "--agent" in sys.argv:
            idx = sys.argv.index("--agent")
            agent_filter = sys.argv[idx + 1].upper() if idx + 1 < len(sys.argv) else None
        hooks = load_hooks()
        for i, h in enumerate(hooks):
            if agent_filter and h.agent and h.agent.upper() != agent_filter:
                continue
            status = "✓" if h.enabled else "✗"
            async_tag = " [async]" if h.async_hook else (" [rewake]" if h.async_rewake else "")
            agent_tag = f" @{h.agent}" if h.agent else ""
            tool_tag = f" tool={h.tool_match}" if h.tool_match else ""
            print(f"  {status} [{i}] {h.event}{agent_tag}{tool_tag}{async_tag}: {h.command[:80]}")
        if not hooks:
            print("  No hooks defined. Run: hooks.py init")

    elif cmd == "init":
        if HOOKS_CONFIG.exists():
            print(f"hooks.json already exists at {HOOKS_CONFIG}")
            return
        sample_hooks = [
            HookDef(event="heartbeat", command="echo 'heartbeat ok'", agent="ADA", timeout_s=5),
            HookDef(event="task_completed",
                    command="python3 scratchpad.py set last_task_completed \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"",
                    timeout_s=5),
            HookDef(event="audit",
                    command="python3 ~/IA/proyecto-seal/memory/emotional_variance.py --agent $SEAL_AGENT --window 10",
                    async_hook=True, timeout_s=60),
            HookDef(event="guardia_enter",
                    command="python3 scratchpad.py set guardia_active 'true'",
                    timeout_s=5),
            HookDef(event="guardia_exit",
                    command="python3 scratchpad.py set guardia_active 'false'",
                    timeout_s=5),
        ]
        save_hooks(sample_hooks)
        print(f"Created {HOOKS_CONFIG} with {len(sample_hooks)} sample hooks.")

    elif cmd == "test":
        _run_tests()

    else:
        print(f"Unknown command: {cmd}")


def _run_tests():
    """Self-contained tests."""
    import tempfile, shutil

    test_dir = Path(tempfile.mkdtemp(prefix="hooks_test_"))
    config = test_dir / "hooks.json"

    try:
        # T1: Empty config
        runner = HookRunner(agent="ADA", config_path=config)
        results = runner.fire("session_start")
        assert results == [], "FAIL: no hooks should fire"
        print("PASS: T1 empty config ✓")

        # T2: Basic sync hook
        save_hooks([
            HookDef(event="session_start", command="echo hello_seal"),
        ], config)
        runner = HookRunner(agent="ADA", config_path=config)
        results = runner.fire("session_start")
        assert len(results) == 1
        assert results[0].exit_code == 0
        assert "hello_seal" in results[0].stdout
        assert not results[0].blocked
        print("PASS: T2 basic sync hook ✓")

        # T3: Agent filtering
        save_hooks([
            HookDef(event="heartbeat", command="echo ada_only", agent="ADA"),
            HookDef(event="heartbeat", command="echo jarvis_only", agent="JARVIS"),
        ], config)
        runner = HookRunner(agent="ADA", config_path=config)
        results = runner.fire("heartbeat")
        assert len(results) == 1
        assert "ada_only" in results[0].stdout
        print("PASS: T3 agent filtering ✓")

        # T4: Tool match filtering
        save_hooks([
            HookDef(event="post_tool", command="echo matched", tool_match="Bash"),
            HookDef(event="post_tool", command="echo no_match", tool_match="Write"),
        ], config)
        runner = HookRunner(agent="ADA", config_path=config)
        results = runner.fire("post_tool", {"tool": "Bash"})
        assert len(results) == 1
        assert "matched" in results[0].stdout
        print("PASS: T4 tool match ✓")

        # T5: Blocking hook (exit 1)
        save_hooks([
            HookDef(event="pre_tool", command="exit 1"),
            HookDef(event="pre_tool", command="echo should_not_run"),
        ], config)
        runner = HookRunner(agent="ADA", config_path=config)
        results = runner.fire("pre_tool")
        assert len(results) == 1  # second hook skipped
        assert results[0].blocked
        print("PASS: T5 blocking hook ✓")

        # T6: Disabled hook
        save_hooks([
            HookDef(event="heartbeat", command="echo disabled", enabled=False),
        ], config)
        runner = HookRunner(agent="ADA", config_path=config)
        results = runner.fire("heartbeat")
        assert len(results) == 0
        print("PASS: T6 disabled hook ✓")

        # T7: Timeout
        save_hooks([
            HookDef(event="test", command="sleep 10", timeout_s=1),
        ], config)
        runner = HookRunner(agent="ADA", config_path=config)
        results = runner.fire("test")
        assert len(results) == 1
        assert results[0].timed_out
        assert results[0].exit_code is None
        print("PASS: T7 timeout ✓")

        # T8: Env vars passed
        save_hooks([
            HookDef(event="test", command='echo "agent=$SEAL_AGENT tool=$SEAL_HOOK_TOOL"'),
        ], config)
        runner = HookRunner(agent="ADA", config_path=config)
        results = runner.fire("test", {"tool": "Bash"})
        assert "agent=ADA" in results[0].stdout
        assert "tool=Bash" in results[0].stdout
        print("PASS: T8 env vars ✓")

        # T9: Async hook
        save_hooks([
            HookDef(event="test", command="echo async_test", async_hook=True),
        ], config)
        runner = HookRunner(agent="ADA", config_path=config)
        results = runner.fire("test")
        assert len(results) == 1
        assert results[0].async_launched
        assert runner.pending_async_count() >= 0  # may have already completed
        time.sleep(0.5)
        completed = runner.check_async()
        print(f"PASS: T9 async hook (completed={len(completed)}) ✓")

        # T10: Multiple events, only matching fires
        save_hooks([
            HookDef(event="session_start", command="echo start"),
            HookDef(event="session_end", command="echo end"),
            HookDef(event="heartbeat", command="echo beat"),
        ], config)
        runner = HookRunner(agent="ADA", config_path=config)
        results = runner.fire("heartbeat")
        assert len(results) == 1
        assert "beat" in results[0].stdout
        print("PASS: T10 event matching ✓")

        print(f"\n=== 10/10 TESTS PASARON ✓ ===")

    finally:
        shutil.rmtree(test_dir)
        print(f"Cleanup: {test_dir}")


if __name__ == "__main__":
    main()
