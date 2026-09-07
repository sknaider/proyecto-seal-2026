"""Adversarial tests for the hardened F3 parity harness."""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "memory"))

from soul_event_interface import HookRegistration, LEARNING_EVENTS
from soul_parity_test import EvidenceKey, FireReceipt, arun_parity


def registry(two_hooks: bool = False) -> list[HookRegistration]:
    rows = [HookRegistration(event, f"{event}-a.py") for event in LEARNING_EVENTS]
    if two_hooks:
        rows.extend(HookRegistration(event, f"{event}-b.py") for event in LEARNING_EVENTS)
    return rows


class Evidence:
    def __init__(self) -> None:
        self.rows: set[tuple[str, str, str, str, str, str, str]] = set()
        self.extra: dict[tuple[str, str, str, str], set[str]] = {}

    def add(self, key: EvidenceKey, script: str | None = None) -> None:
        self.rows.add((
            key.suite_run_id, key.runtime_id, key.soul_event,
            script or key.script_path, key.agent, key.nonce, key.token,
        ))

    async def verify(self, key: EvidenceKey) -> bool:
        return (
            key.suite_run_id, key.runtime_id, key.soul_event,
            key.script_path, key.agent, key.nonce, key.token,
        ) in self.rows

    async def read(self, run_id: str, runtime: str, event: str, agent: str):
        concrete = {
            row[3] for row in self.rows
            if row[:3] == (run_id, runtime, event) and row[4] == agent
        }
        return concrete | self.extra.get((run_id, runtime, event, agent), set())


def fire(runtime: str, evidence: Evidence, scripts: int = 1, process: int | None = None):
    async def execute(key: EvidenceKey, _payload: dict) -> FireReceipt:
        evidence.add(key)
        if scripts == 2:
            evidence.add(key, key.script_path.replace("-a.py", "-b.py"))
        native = "SessionStart" if runtime == "claude_code" else "session_start"
        pid = process or (31001 if runtime == "claude_code" else 31002)
        return FireReceipt(key.suite_run_id, runtime, key.soul_event, f"entry:{runtime}", native, pid)
    return execute


def no_op(runtime: str):
    async def execute(key: EvidenceKey, _payload: dict) -> FireReceipt:
        native = "SessionStart" if runtime == "claude_code" else "session_start"
        pid = 31001 if runtime == "claude_code" else 31002
        return FireReceipt(key.suite_run_id, runtime, key.soul_event, f"entry:{runtime}", native, pid)
    return execute


def run(coro):
    return asyncio.run(coro)


def test_real_parity_requires_separate_runtime_evidence() -> None:
    base, candidate = Evidence(), Evidence()
    report = run(arun_parity(
        "ADA", registry(), "claude_code", "local_llama",
        fire("claude_code", base), fire("local_llama", candidate),
        base.verify, candidate.verify, base.read, candidate.read,
        events=LEARNING_EVENTS, suite_run_id="run-1",
    ))
    assert report["parity"] is True
    assert report["learning_ok"] is True


def test_candidate_noop_cannot_reuse_baseline_rows() -> None:
    base, candidate = Evidence(), Evidence()
    report = run(arun_parity(
        "ADA", registry(), "claude_code", "local_llama",
        fire("claude_code", base), no_op("local_llama"),
        base.verify, candidate.verify, base.read, candidate.read,
        events=LEARNING_EVENTS, suite_run_id="run-2",
    ))
    assert report["parity"] is False
    assert report["learning_ok"] is False


def test_stale_row_wrong_nonce_is_rejected() -> None:
    base, candidate = Evidence(), Evidence()
    candidate.rows.add(("run-3", "local_llama", "on_boot", "on_boot-a.py", "ADA", "old", "old"))
    report = run(arun_parity(
        "ADA", registry(), "claude_code", "local_llama",
        fire("claude_code", base), no_op("local_llama"),
        base.verify, candidate.verify, base.read, candidate.read,
        events=LEARNING_EVENTS, suite_run_id="run-3",
    ))
    assert report["learning_ok"] is False


def test_partially_broken_baseline_is_not_reference() -> None:
    base, candidate = Evidence(), Evidence()
    report = run(arun_parity(
        "ADA", registry(two_hooks=True), "claude_code", "local_llama",
        fire("claude_code", base, scripts=1), fire("local_llama", candidate, scripts=1),
        base.verify, candidate.verify, base.read, candidate.read,
        events=LEARNING_EVENTS, suite_run_id="run-4",
    ))
    assert report["parity"] is False
    assert set(report["baseline_broken"]) == set(LEARNING_EVENTS)


def test_unexpected_effect_is_red() -> None:
    base, candidate = Evidence(), Evidence()
    original_read = candidate.read

    async def candidate_read(run_id: str, runtime: str, event: str, agent: str):
        return set(await original_read(run_id, runtime, event, agent)) | {"unexpected.py"}

    report = run(arun_parity(
        "ADA", registry(), "claude_code", "local_llama",
        fire("claude_code", base), fire("local_llama", candidate),
        base.verify, candidate.verify, base.read, candidate_read,
        events=LEARNING_EVENTS, suite_run_id="run-5",
    ))
    assert report["parity"] is False
    assert report["unexpected_effects"]


def test_same_runtime_or_process_identity_is_rejected() -> None:
    base, candidate = Evidence(), Evidence()
    same_runtime = run(arun_parity(
        "ADA", registry(), "local_llama", "local_llama",
        fire("local_llama", base), fire("local_llama", candidate),
        base.verify, candidate.verify, base.read, candidate.read,
        events=LEARNING_EVENTS,
    ))
    assert same_runtime["runtime_identity_collision"] is True

    same_process = run(arun_parity(
        "ADA", registry(), "claude_code", "local_llama",
        fire("claude_code", base, process=31999),
        fire("local_llama", candidate, process=31999),
        base.verify, candidate.verify, base.read, candidate.read,
        events=LEARNING_EVENTS, suite_run_id="run-6",
    ))
    assert same_process["runtime_identity_collision"] is True


def test_nested_payload_is_deep_copied() -> None:
    base, candidate = Evidence(), Evidence()
    payloads = {event: {"nested": {"value": "original"}} for event in LEARNING_EVENTS}

    async def mutating_fire(key: EvidenceKey, payload: dict) -> FireReceipt:
        payload["nested"]["value"] = "mutated"
        base.add(key)
        return FireReceipt(key.suite_run_id, "claude_code", key.soul_event, "entry:claude", "SessionStart", 31001)

    seen: list[str] = []

    async def candidate_fire(key: EvidenceKey, payload: dict) -> FireReceipt:
        seen.append(payload["nested"]["value"])
        candidate.add(key)
        return FireReceipt(key.suite_run_id, "local_llama", key.soul_event, "entry:local", "session_start", 31002)

    report = run(arun_parity(
        "ADA", registry(), "claude_code", "local_llama",
        mutating_fire, candidate_fire, base.verify, candidate.verify,
        base.read, candidate.read, payloads,
        events=LEARNING_EVENTS, suite_run_id="run-7",
    ))
    assert report["parity"] is True
    assert seen == ["original"] * len(LEARNING_EVENTS)


def test_async_timeout_is_fail_loud() -> None:
    evidence = Evidence()

    async def hanging(_key: EvidenceKey, _payload: dict):
        await asyncio.sleep(1)

    with pytest.raises(asyncio.TimeoutError):
        run(arun_parity(
            "ADA", registry(), "claude_code", "local_llama",
            hanging, fire("local_llama", evidence),
            evidence.verify, evidence.verify, evidence.read, evidence.read,
            events=("on_boot",), timeout_seconds=0.01,
        ))


def test_empty_baseline_is_not_a_healthy_reference() -> None:
    base, candidate = Evidence(), Evidence()
    report = run(arun_parity(
        "ADA", [], "claude_code", "local_llama",
        no_op("claude_code"), no_op("local_llama"),
        base.verify, candidate.verify, base.read, candidate.read,
        events=("on_boot",), suite_run_id="run-empty",
    ))
    assert report["parity"] is False
    assert report["baseline_broken"] == ["on_boot"]


def test_script_hash_drift_between_runtimes_is_red(tmp_path: Path) -> None:
    script = tmp_path / "hook.py"
    script.write_text("VERSION = 'baseline'\n")
    rows = [HookRegistration("on_boot", str(script))]
    base, candidate = Evidence(), Evidence()

    async def baseline_fire(key: EvidenceKey, _payload: dict) -> FireReceipt:
        base.add(key)
        script.write_text("VERSION = 'candidate'\n")
        return FireReceipt(key.suite_run_id, "claude_code", key.soul_event, "entry:claude", "SessionStart", 32001)

    report = run(arun_parity(
        "ADA", rows, "claude_code", "local_llama",
        baseline_fire, fire("local_llama", candidate),
        base.verify, candidate.verify, base.read, candidate.read,
        events=("on_boot",), suite_run_id="run-digest-drift",
    ))
    assert report["parity"] is False
    assert "on_boot" in report["script_digest_mismatch"]
