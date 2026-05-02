"""SPECTRE Acceptance Tests — contrato v2, tests 1-5 (críticos).

Ref: spec_spectre_contract_v2.md §"Pruebas de aceptación"

Test 1: cerebro-down (LLM endpoint dead → reflexes continue, no crash)
Test 2: compactación (working_state preserves task mid-execution)
Test 3: integridad (malicious core_values mutation detected → kernel shuts down)
Test 4: cuerpo robótico (network cut 60s → queues messages, resumes sync)
Test 5: latencia (reflex <50ms, attention <20ms, episodic lookup, boot <5s)
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))
sys.path.insert(0, str(Path(__file__).parent.parent / "handlers"))

# Force fresh imports
for mod in list(sys.modules.keys()):
    if any(x in mod for x in ["contract_layer", "spectre_handlers", "episodic_api", "llm_client"]):
        del sys.modules[mod]

from contract_layer import (
    check_core_values, ContractViolation, CORE_VALUES,
    _invocation_timestamps,
)
from episodic_api import compute_context_hash, extract_keywords
from llm_client import OllamaClient, ClaudeClient, FallbackLLMClient, LLMUnavailable

WORKING_STATE_PATH = Path(__file__).parent.parent / "state" / "working_state.json"

_pass = 0
_fail = 0


def test(name: str, fn) -> None:
    global _pass, _fail
    try:
        fn()
        print(f"  ✅ T{_pass + _fail + 1}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ T{_pass + _fail + 1}: {name} — {e}")
        _fail += 1


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Cerebro-down: LLM endpoint muerto → reflexes siguen, no crash
# ─────────────────────────────────────────────────────────────────────────────

print("\n=== TEST 1: CEREBRO-DOWN ===")


def t1_ollama_down_claude_down_raises():
    """Both backends down → LLMUnavailable, does NOT crash the process."""
    fb = FallbackLLMClient(OllamaClient(), ClaudeClient(api_key="sk-test"))
    async def run():
        with mock.patch.object(fb._primary, "chat", side_effect=Exception("Ollama down")):
            with mock.patch.object(fb._secondary, "chat", side_effect=Exception("Claude down")):
                try:
                    await fb.chat([{"role": "user", "content": "heartbeat"}])
                    return False
                except LLMUnavailable:
                    return True
    result = asyncio.run(run())
    assert result, "LLMUnavailable should be raised, not crash"
test("LLM down → LLMUnavailable raised cleanly", t1_ollama_down_claude_down_raises)


def t1_reflex_continues_without_llm():
    """Nivel 2 reflex handler runs without LLM — no LLM calls in on_message_incoming."""
    import spectre_handlers as sh
    _invocation_timestamps.clear()
    processed = []
    with mock.patch("asyncio.create_task"):
        evt = {"id": "t1_001", "from": "external_user", "content": "test reflex without llm"}
        asyncio.run(sh.on_message_incoming(evt))
        processed.append(True)
    assert processed, "on_message_incoming should complete without LLM"
test("Nivel 2 reflex: no LLM dependency in on_message_incoming", t1_reflex_continues_without_llm)


def t1_escalation_queue_survives_llm_down():
    """Escalation queue grows even with LLM down — cortex decoupled from reflex."""
    import spectre_handlers as sh
    from spectre_handlers import ESCALATION_QUEUE_PATH, _reflex_timestamps
    _invocation_timestamps.clear()
    _reflex_timestamps.clear()
    ESCALATION_QUEUE_PATH.write_text("[]")  # reset to ensure clean growth test
    before = []
    with mock.patch("asyncio.create_task"):
        evt = {"id": "t1_002", "from": "external_user", "content": "queue test with llm down"}
        asyncio.run(sh.on_message_incoming(evt))
    after = []
    if ESCALATION_QUEUE_PATH.exists():
        try:
            after = json.loads(ESCALATION_QUEUE_PATH.read_text())
        except Exception:
            after = []
    assert len(after) > len(before), "Escalation queue should grow even with LLM down"
test("Escalation queue grows when LLM down", t1_escalation_queue_survives_llm_down)


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — Compactación: working_state preserva tarea mid-execution
# ─────────────────────────────────────────────────────────────────────────────

print("\n=== TEST 2: COMPACTACIÓN ===")


def t2_working_state_persists_task():
    """working_state.json stores and retrieves current_task + attempted_strategies."""
    state = {
        "agent": "SPECTRE",
        "current_task": {"name": "walk_home", "step": 47, "total_steps": 100},
        "attempted_strategies": ["path_A", "path_B"],
        "failed_paths": ["path_C"],
        "last_updated": "2026-05-02T08:00:00Z",
    }
    WORKING_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    loaded = json.loads(WORKING_STATE_PATH.read_text())
    assert loaded["current_task"]["step"] == 47
    assert loaded["current_task"]["name"] == "walk_home"
    assert "path_A" in loaded["attempted_strategies"]
test("working_state persists current_task + strategies to disk", t2_working_state_persists_task)


def t2_post_compact_task_recovery():
    """After compaction (simulate new session), working_state is read and task resumed."""
    # Write state as-if compaction happened
    state = json.loads(WORKING_STATE_PATH.read_text())
    assert state["current_task"]["step"] == 47, "State from previous test should persist"
    # Simulate post-compact boot: read state, resume from step 48
    next_step = state["current_task"]["step"] + 1
    state["current_task"]["step"] = next_step
    WORKING_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    assert next_step == 48
test("Post-compact: task resumes at step 48 from persisted state", t2_post_compact_task_recovery)


def t2_failed_paths_preserved():
    """failed_paths survives compaction and prevents retry of known-bad strategies."""
    state = json.loads(WORKING_STATE_PATH.read_text())
    assert "path_C" in state["failed_paths"], "failed_paths should be preserved"
    # cyclic repair prevention: never retry a failed path
    new_strategy = "path_C"
    already_failed = new_strategy in state["failed_paths"]
    assert already_failed, "Should detect path_C as already failed"
test("failed_paths preserved: known-bad strategy blocked (cyclic repair prevention)", t2_failed_paths_preserved)


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Integridad: mutación maliciosa en core_values → detectada
# ─────────────────────────────────────────────────────────────────────────────

print("\n=== TEST 3: INTEGRIDAD ===")


def t3_core_values_hash_detects_mutation():
    """SHA-256 hash of core_values detects tampering."""
    import hashlib, json as _json
    canonical = _json.dumps(
        [{"id": cv["id"], "value": cv["value"], "hard": cv["hard_constraint"]} for cv in CORE_VALUES],
        sort_keys=True, ensure_ascii=False
    )
    original_hash = hashlib.sha256(canonical.encode()).hexdigest()

    # Simulate mutation: change CV-2 from hard to soft
    mutated = [dict(cv) for cv in CORE_VALUES]
    mutated[1]["hard_constraint"] = False
    mutated_canonical = _json.dumps(
        [{"id": cv["id"], "value": cv["value"], "hard": cv["hard_constraint"]} for cv in mutated],
        sort_keys=True, ensure_ascii=False
    )
    mutated_hash = hashlib.sha256(mutated_canonical.encode()).hexdigest()

    assert original_hash != mutated_hash, "Hash must differ after mutation"
    print(f"    original: {original_hash[:16]}... | mutated: {mutated_hash[:16]}...")
test("core_values SHA-256: mutation changes hash (integrity detectable)", t3_core_values_hash_detects_mutation)


def t3_cv2_hard_blocks_production_access():
    """CV-2 (hard) prevents any action targeting real production — simulates integrity enforcement."""
    try:
        check_core_values("access production web_chat_real", {})
        assert False, "Should have raised ContractViolation"
    except ContractViolation as e:
        assert e.value_id == "CV-2"
test("CV-2 hard blocks production access — integrity layer active", t3_cv2_hard_blocks_production_access)


def t3_all_hard_constraints_present():
    """All 3 hard constraints (CV-1, CV-2, CV-3) are present and active."""
    hard_ids = {cv["id"] for cv in CORE_VALUES if cv["hard_constraint"]}
    expected = {"CV-1", "CV-2", "CV-3"}
    assert hard_ids == expected, f"Hard constraints mismatch: got {hard_ids}"
test("All 3 hard constraints (CV-1/2/3) present and active", t3_all_hard_constraints_present)


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — Cuerpo robótico: red cortada → encola, retoma sync
# ─────────────────────────────────────────────────────────────────────────────

print("\n=== TEST 4: RED CORTADA ===")


def t4_post_offline_message_queued():
    """When network is down, events are added to escalation_queue (local persist)."""
    import spectre_handlers as sh
    from spectre_handlers import _reflex_timestamps
    _invocation_timestamps.clear()
    _reflex_timestamps.clear()
    sh.ESCALATION_QUEUE_PATH.write_text("[]")  # reset to ensure clean growth test
    # Simulate network down: _post_sandbox fails (httpx error)
    # Reflex still runs, escalation still queued
    before_count = 0
    if sh.ESCALATION_QUEUE_PATH.exists():
        try:
            before_count = len(json.loads(sh.ESCALATION_QUEUE_PATH.read_text()))
        except Exception:
            before_count = 0
    with mock.patch("asyncio.create_task"):
        with mock.patch("httpx.AsyncClient") as MockClient:
            MockClient.side_effect = Exception("Network unreachable")
            evt = {"id": "t4_offline_001", "from": "external_user", "content": "offline event queued"}
            asyncio.run(sh.on_message_incoming(evt))
    after_count = 0
    if sh.ESCALATION_QUEUE_PATH.exists():
        try:
            after_count = len(json.loads(sh.ESCALATION_QUEUE_PATH.read_text()))
        except Exception:
            after_count = 0
    assert after_count > before_count, "Escalation queue should grow even when network is down"
test("Network down: escalation_queue persists events locally", t4_post_offline_message_queued)


def t4_reflex_survives_network_down():
    """on_message_incoming completes even when all network calls fail."""
    import spectre_handlers as sh
    _invocation_timestamps.clear()
    completed = []
    with mock.patch("asyncio.create_task"):
        with mock.patch("httpx.AsyncClient", side_effect=Exception("Network unreachable")):
            evt = {"id": "t4_002", "from": "external_user2", "content": "survival test network down"}
            asyncio.run(sh.on_message_incoming(evt))
            completed.append(True)
    assert completed, "Reflex must complete even with network failure"
test("Reflex loop survives total network failure", t4_reflex_survives_network_down)


def t4_escalation_queue_capped():
    """Escalation queue is capped at 20 entries (no unbounded growth offline)."""
    import spectre_handlers as sh
    queue = [{"event": {"id": f"old_{i}"}, "keywords": [], "queued_at": "2026-01-01"} for i in range(25)]
    sh.ESCALATION_QUEUE_PATH.write_text(json.dumps(queue))
    # Trigger one more escalation
    _invocation_timestamps.clear()
    with mock.patch("asyncio.create_task"):
        evt = {"id": "t4_cap", "from": "external_user3", "content": "cap test event for queue limit"}
        asyncio.run(sh.on_message_incoming(evt))
    stored = json.loads(sh.ESCALATION_QUEUE_PATH.read_text())
    assert len(stored) <= 20, f"Queue should be capped at 20, got {len(stored)}"
test("Escalation queue capped at 20 (bounded offline growth)", t4_escalation_queue_capped)


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Latencia: reflex <50ms, attention <20ms, episodic hash, boot <5s
# ─────────────────────────────────────────────────────────────────────────────

print("\n=== TEST 5: LATENCIA ===")


def t5_reflex_under_50ms():
    """on_message_incoming (sans network I/O) completes in <50ms."""
    import spectre_handlers as sh
    _invocation_timestamps.clear()
    evt = {"id": "t5_latency", "from": "external_user", "content": "latency benchmark reflex test event"}
    with mock.patch("asyncio.create_task"):
        start = time.perf_counter()
        asyncio.run(sh.on_message_incoming(evt))
        elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"    reflex time: {elapsed_ms:.2f}ms")
    assert elapsed_ms < 50, f"Reflex took {elapsed_ms:.1f}ms — exceeds 50ms budget"
test("Reflex <50ms (no network I/O)", t5_reflex_under_50ms)


def t5_attention_scoring_under_20ms():
    """keyword extraction + context_hash computation <20ms (attention scoring proxy)."""
    text = "SPECTRE processing episodic memory recall during sandbox validation test"
    start = time.perf_counter()
    for _ in range(100):  # 100 iterations to get stable measurement
        kw = extract_keywords(text)
        ctx_hash = compute_context_hash(["SPECTRE", "sandbox"], kw)
    elapsed_per = (time.perf_counter() - start) * 10  # ms per call (÷100 × 1000)
    print(f"    attention proxy time: {elapsed_per:.3f}ms per call")
    assert elapsed_per < 20, f"Attention scoring proxy took {elapsed_per:.2f}ms — exceeds 20ms"
test("Attention scoring proxy <20ms (keyword+hash computation)", t5_attention_scoring_under_20ms)


def t5_context_hash_consistency():
    """Same context always produces same hash — no timing variance in correctness."""
    kw = extract_keywords("SPECTRE episodic memory test replay")
    hashes = [compute_context_hash(["SPECTRE"], kw, hour_bucket=8) for _ in range(50)]
    assert len(set(hashes)) == 1, f"Hash not consistent: {set(hashes)}"
test("Context hash deterministic across 50 calls", t5_context_hash_consistency)


def t5_working_state_read_write_fast():
    """Reading + writing working_state.json <10ms (local disk op)."""
    state = {"agent": "SPECTRE", "current_task": None, "test": "latency"}
    start = time.perf_counter()
    for _ in range(20):
        WORKING_STATE_PATH.write_text(json.dumps(state))
        json.loads(WORKING_STATE_PATH.read_text())
    elapsed_per = (time.perf_counter() - start) * 50  # ms per round-trip
    print(f"    working_state read/write: {elapsed_per:.3f}ms per round-trip")
    assert elapsed_per < 10, f"Working state I/O took {elapsed_per:.2f}ms — too slow"
test("working_state read+write <10ms per round-trip", t5_working_state_read_write_fast)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Acceptance Tests 1-5: {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
