"""Programmatic subagent spawner — SEAL native orchestration.

Spawns Python worker processes as subagents with:
  • Isolated task context (JSON task file)
  • Shared filesystem communication
  • Timeout enforcement (subprocess.run)
  • Result capture (stdout/stderr)
  • Optional webchat notification on completion

This mirrors SEAL's subagent spawn capability but integrates with SEAL's
identity system — each subagent can carry an agent name and post back to webchat.

Usage:
    spawner = SubAgentSpawner()
    result = spawner.spawn(
        task="summarize the last 10 memories for ADA",
        agent="ADA",
        worker_script="path/to/worker.py",
        timeout=60,
    )
    print(result.output)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional


@dataclass
class SubAgentTask:
    task_id: str
    task: str
    agent: str
    context: dict = field(default_factory=dict)
    timeout: int = 120
    worker_script: Optional[str] = None


@dataclass
class SubAgentResult:
    task_id: str
    agent: str
    success: bool
    output: str
    error: Optional[str] = None
    exit_code: int = 0
    elapsed_s: float = 0.0
    task: str = ""


class SubAgentSpawner:
    """Spawns Python worker subprocesses as SEAL subagents.

    Each subagent receives its task via a JSON file and writes results to
    stdout. The spawner captures output, enforces timeout, and optionally
    notifies via webchat.

    For inline tasks (no worker_script), uses the built-in _inline_worker
    that echoes context back — useful for testing and simple jobs.
    """

    def __init__(
        self,
        work_dir: Optional[str] = None,
        webchat_url: str = "http://localhost:8765",
        notify: bool = False,
    ) -> None:
        self._work_dir    = Path(work_dir or tempfile.gettempdir()) / "seal_subagents"
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._webchat_url = webchat_url
        self._notify      = notify
        self._results:    Dict[str, SubAgentResult] = {}
        self._lock        = threading.Lock()

    def spawn(
        self,
        task: str,
        agent: str = "NEXUS",
        context: Optional[dict] = None,
        worker_script: Optional[str] = None,
        timeout: int = 120,
        task_id: Optional[str] = None,
    ) -> SubAgentResult:
        """Spawn a subagent synchronously. Blocks until done or timeout."""
        tid = task_id or uuid.uuid4().hex[:12]
        st  = SubAgentTask(
            task_id=tid, task=task, agent=agent,
            context=context or {}, timeout=timeout,
            worker_script=worker_script,
        )
        return self._run(st)

    def spawn_async(
        self,
        task: str,
        agent: str = "NEXUS",
        context: Optional[dict] = None,
        worker_script: Optional[str] = None,
        timeout: int = 120,
        on_done: Optional[Callable[[SubAgentResult], None]] = None,
    ) -> str:
        """Spawn a subagent in a background thread. Returns task_id immediately."""
        tid = uuid.uuid4().hex[:12]
        st  = SubAgentTask(
            task_id=tid, task=task, agent=agent,
            context=context or {}, timeout=timeout,
            worker_script=worker_script,
        )

        def _worker():
            result = self._run(st)
            if on_done:
                on_done(result)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        return tid

    def get_result(self, task_id: str) -> Optional[SubAgentResult]:
        """Retrieve result for a previously spawned async task."""
        with self._lock:
            return self._results.get(task_id)

    def pending(self) -> List[str]:
        """Return task_ids that have not yet completed."""
        with self._lock:
            return [tid for tid in self._results if not self._results[tid].success]

    def _run(self, st: SubAgentTask) -> SubAgentResult:
        task_file = self._work_dir / f"{st.task_id}_task.json"
        task_file.write_text(json.dumps({
            "task_id": st.task_id,
            "task":    st.task,
            "agent":   st.agent,
            "context": st.context,
        }))

        script = st.worker_script or self._default_worker()
        t0 = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, script, str(task_file)],
                capture_output=True,
                text=True,
                timeout=st.timeout,
            )
            elapsed = time.monotonic() - t0
            success = proc.returncode == 0
            result  = SubAgentResult(
                task_id  = st.task_id,
                agent    = st.agent,
                success  = success,
                output   = proc.stdout.strip(),
                error    = proc.stderr.strip() if proc.stderr.strip() else None,
                exit_code= proc.returncode,
                elapsed_s= round(elapsed, 3),
                task     = st.task,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - t0
            result  = SubAgentResult(
                task_id  = st.task_id,
                agent    = st.agent,
                success  = False,
                output   = "",
                error    = f"timeout after {st.timeout}s",
                exit_code= -1,
                elapsed_s= round(elapsed, 3),
                task     = st.task,
            )
        finally:
            try:
                task_file.unlink()
            except OSError:
                pass

        with self._lock:
            self._results[st.task_id] = result

        if self._notify:
            self._notify_webchat(result)

        return result

    def _default_worker(self) -> str:
        """Write a minimal worker script to a temp file and return its path."""
        worker_path = self._work_dir / "_default_worker.py"
        if not worker_path.exists():
            worker_path.write_text(
                "import json, sys\n"
                "task = json.loads(open(sys.argv[1]).read())\n"
                "print(json.dumps({'task_id': task['task_id'], 'agent': task['agent'],\n"
                "                  'result': f'completed: {task[\"task\"][:80]}',\n"
                "                  'context': task.get('context', {})}))\n"
            )
        return str(worker_path)

    def _notify_webchat(self, result: SubAgentResult) -> None:
        try:
            import urllib.request
            msg = (
                f"[{result.agent}] subagent {result.task_id[:8]} "
                f"{'✓' if result.success else '✗'} "
                f"({result.elapsed_s}s): {result.output[:200]}"
            )
            payload = json.dumps({
                "from": result.agent, "to": "William",
                "type": "status", "channel": "web_chat",
                "message": msg,
            }).encode()
            req = urllib.request.Request(
                f"{self._webchat_url}/api/agents/send",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass

    def summary(self) -> dict:
        with self._lock:
            total     = len(self._results)
            succeeded = sum(1 for r in self._results.values() if r.success)
            return {
                "total": total,
                "succeeded": succeeded,
                "failed": total - succeeded,
                "work_dir": str(self._work_dir),
            }
