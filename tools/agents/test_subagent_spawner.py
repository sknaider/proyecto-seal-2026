"""Tests for SubAgentSpawner — programmatic subagent orchestration."""

import json
import sys
import pathlib
import tempfile
import time
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.agents.subagent_spawner import SubAgentSpawner, SubAgentResult, SubAgentTask


# ── Helpers ───────────────────────────────────────────────────────────────────

_worker_counter = [0]

def _make_worker(tmp_path: str, code: str) -> str:
    """Write a one-shot worker script and return its path."""
    _worker_counter[0] += 1
    p = pathlib.Path(tmp_path) / f"worker_{_worker_counter[0]}.py"
    p.write_text(code)
    return str(p)


# ── Unit tests ─────────────────────────────────────────────────────────────────

def test_spawn_default_worker_success():
    with tempfile.TemporaryDirectory() as tmp:
        spawner = SubAgentSpawner(work_dir=tmp)
        result = spawner.spawn("summarize memories", agent="ADA")
    assert result.success
    assert "completed" in result.output
    assert result.exit_code == 0
    assert result.elapsed_s >= 0


def test_spawn_custom_worker_output_captured():
    with tempfile.TemporaryDirectory() as tmp:
        worker = _make_worker(tmp, (
            "import json, sys\n"
            "task = json.loads(open(sys.argv[1]).read())\n"
            "print(f'custom:{task[\"task\"]}')\n"
        ))
        spawner = SubAgentSpawner(work_dir=tmp)
        result = spawner.spawn("my special task", worker_script=worker)
    assert result.success
    assert "custom:my special task" in result.output


def test_spawn_worker_failure_captured():
    with tempfile.TemporaryDirectory() as tmp:
        worker = _make_worker(tmp,
            "import sys\nprint('error output', file=sys.stderr)\nsys.exit(1)\n"
        )
        spawner = SubAgentSpawner(work_dir=tmp)
        result = spawner.spawn("fail task", worker_script=worker)
    assert not result.success
    assert result.exit_code == 1
    assert result.error is not None


def test_spawn_timeout_returns_failure():
    with tempfile.TemporaryDirectory() as tmp:
        worker = _make_worker(tmp, "import time\ntime.sleep(10)\n")
        spawner = SubAgentSpawner(work_dir=tmp)
        result = spawner.spawn("slow task", worker_script=worker, timeout=1)
    assert not result.success
    assert "timeout" in result.error


def test_spawn_result_stores_task():
    with tempfile.TemporaryDirectory() as tmp:
        spawner = SubAgentSpawner(work_dir=tmp)
        result = spawner.spawn("check soul db", agent="JARVIS")
    assert result.task == "check soul db"
    assert result.agent == "JARVIS"
    assert result.task_id


def test_spawn_context_passed_to_worker():
    with tempfile.TemporaryDirectory() as tmp:
        worker = _make_worker(tmp, (
            "import json, sys\n"
            "task = json.loads(open(sys.argv[1]).read())\n"
            "print(json.dumps(task['context']))\n"
        ))
        spawner = SubAgentSpawner(work_dir=tmp)
        result = spawner.spawn("task", context={"db": "seal_memory"}, worker_script=worker)
    data = json.loads(result.output)
    assert data["db"] == "seal_memory"


def test_spawn_async_returns_task_id_immediately():
    with tempfile.TemporaryDirectory() as tmp:
        worker = _make_worker(tmp, "import time\ntime.sleep(0.2)\nprint('done')\n")
        spawner = SubAgentSpawner(work_dir=tmp)
        t0 = time.monotonic()
        tid = spawner.spawn_async("async task", worker_script=worker)
        elapsed = time.monotonic() - t0
    assert isinstance(tid, str)
    assert len(tid) == 12
    assert elapsed < 0.1  # returned immediately, not after worker completes


def test_spawn_async_result_available_after_done():
    with tempfile.TemporaryDirectory() as tmp:
        worker = _make_worker(tmp, "print('async_result')\n")
        spawner = SubAgentSpawner(work_dir=tmp)
        done = threading.Event()

        def on_done(r):
            done.set()

        tid = spawner.spawn_async("async job", worker_script=worker, on_done=on_done)
        done.wait(timeout=5)
        result = spawner.get_result(tid)
    assert result is not None
    assert result.success
    assert "async_result" in result.output


def test_spawn_async_on_done_callback_fires():
    with tempfile.TemporaryDirectory() as tmp:
        worker = _make_worker(tmp, "print('callback_test')\n")
        spawner = SubAgentSpawner(work_dir=tmp)
        results = []
        done = threading.Event()

        def on_done(r):
            results.append(r)
            done.set()

        spawner.spawn_async("callback", worker_script=worker, on_done=on_done)
        done.wait(timeout=5)
    assert len(results) == 1
    assert results[0].success


def test_get_result_missing_returns_none():
    spawner = SubAgentSpawner()
    assert spawner.get_result("nonexistent-id-xyz") is None


def test_summary_tracks_succeeded_failed():
    with tempfile.TemporaryDirectory() as tmp:
        ok_worker   = _make_worker(tmp, "print('ok')\n")
        fail_worker = _make_worker(tmp, "import sys; sys.exit(1)\n")
        spawner = SubAgentSpawner(work_dir=tmp)
        spawner.spawn("t1", worker_script=ok_worker)
        spawner.spawn("t2", worker_script=fail_worker)
        s = spawner.summary()
    assert s["total"] == 2
    assert s["succeeded"] == 1
    assert s["failed"] == 1


def test_summary_empty():
    spawner = SubAgentSpawner()
    s = spawner.summary()
    assert s["total"] == 0
    assert s["succeeded"] == 0


def test_result_dataclass_fields():
    r = SubAgentResult(
        task_id="abc", agent="ADA", success=True,
        output="hello", error=None, exit_code=0,
        elapsed_s=1.5, task="do stuff",
    )
    assert r.task_id == "abc"
    assert r.elapsed_s == 1.5


def test_multiple_spawns_independent():
    with tempfile.TemporaryDirectory() as tmp:
        spawner = SubAgentSpawner(work_dir=tmp)
        r1 = spawner.spawn("task one", agent="ADA")
        r2 = spawner.spawn("task two", agent="JARVIS")
    assert r1.task_id != r2.task_id
    assert r1.agent == "ADA"
    assert r2.agent == "JARVIS"


def main() -> int:
    tests = [
        test_spawn_default_worker_success,
        test_spawn_custom_worker_output_captured,
        test_spawn_worker_failure_captured,
        test_spawn_timeout_returns_failure,
        test_spawn_result_stores_task,
        test_spawn_context_passed_to_worker,
        test_spawn_async_returns_task_id_immediately,
        test_spawn_async_result_available_after_done,
        test_spawn_async_on_done_callback_fires,
        test_get_result_missing_returns_none,
        test_summary_tracks_succeeded_failed,
        test_summary_empty,
        test_result_dataclass_fields,
        test_multiple_spawns_independent,
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
