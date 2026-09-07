"""SPECTRE tools tests — promotion_script + boot_anchors_seed + multi_agent_integrity.

Tests:
  P1: promotion idempotente — segunda ejecución no sobrescribe promoted_at
  P2: promotion record tiene campos mínimos requeridos
  P3: promotion actualiza working_state cv_approval_status=PROMOTED
  B1: seed genera Ed25519 key única por agente
  B2: seed inserta en soul_v3.agent_alma (boot_hash válido)
  B3: seed idempotente — segunda ejecución no cambia boot_hash
  I1: integrity check pasa para todos los agentes recién seeded
  I2: integrity detecta pubkey corruption (key path inválido)
  I3: integrity --agent filtra por agente
"""
from __future__ import annotations

import asyncio
import json
import sys
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))

import promotion_script as ps
import boot_anchors_seed_4agents as seed_mod
import multi_agent_integrity as mi

_pass = 0
_fail = 0


def test(name: str, fn) -> None:
    global _pass, _fail
    try:
        fn()
        print(f"  ✅ TOOLS-{_pass + _fail + 1}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ TOOLS-{_pass + _fail + 1}: {name} — {e}")
        _fail += 1


# ── PROMOTION TESTS ───────────────────────────────────────────────────────────

print("\n=== PROMOTION ===")


def p1_idempotent():
    record1 = ps.promote()
    promoted_at_1 = record1["promoted_at"]
    record2 = ps.promote()  # second call
    promoted_at_2 = record2["promoted_at"]
    assert promoted_at_1 == promoted_at_2, (
        f"Idempotent: promoted_at should not change on re-run, got {promoted_at_1} vs {promoted_at_2}"
    )

test("promotion idempotent — re-run preserves promoted_at", p1_idempotent)


def p2_required_fields():
    record = ps.promotion_status()
    required = ["signed_by", "promoted_version", "git_tag", "test_results"]
    for field in required:
        assert field in record, f"Missing required field: {field}"
    assert record["promoted_version"] == "v1_promoted", f"Version mismatch: {record['promoted_version']}"
    assert record["test_results"]["total_pass"] == 124, f"Expected 124 PASS, got {record['test_results']}"

test("promotion record has all required fields + 124/124 test results", p2_required_fields)


def p3_working_state_updated():
    state = json.loads(ps.WORKING_STATE_PATH.read_text()) if ps.WORKING_STATE_PATH.exists() else {}
    assert state.get("cv_approval_status") == "PROMOTED", (
        f"working_state cv_approval_status should be PROMOTED, got {state.get('cv_approval_status')}"
    )
    assert "promotion" in state, "working_state should have 'promotion' key"

test("promotion updates working_state cv_approval_status=PROMOTED", p3_working_state_updated)


# ── BOOT ANCHORS TESTS ────────────────────────────────────────────────────────

print("\n=== BOOT ANCHORS ===")


def b1_unique_keys_per_agent():
    pubkeys = set()
    for agent_def in seed_mod.AGENT_DEFS:
        identity_dir = agent_def["identity_dir"]
        _, pubkey_hex = seed_mod._generate_or_load_agent_key(identity_dir)
        pubkeys.add(pubkey_hex)
    assert len(pubkeys) == 4, f"Expected 4 unique pubkeys, got {len(pubkeys)} — some agents share keys"

test("Ed25519 key unique per agent (4 distinct pubkeys)", b1_unique_keys_per_agent)


def b2_seed_inserts_valid_boot_hash():
    results = asyncio.run(seed_mod.seed_all())
    assert len(results) == 4, f"Expected 4 results, got {len(results)}"
    for r in results:
        assert "boot_hash_prefix" in r, f"Missing boot_hash_prefix for {r.get('agent')}"
        assert len(r["boot_hash_prefix"]) == 16, f"boot_hash_prefix should be 16 chars: {r}"
        assert "upserted_at" in r, f"Missing upserted_at for {r.get('agent')}"

test("seed inserts 4 agents with valid boot_hash into soul_v3.agent_alma", b2_seed_inserts_valid_boot_hash)


def b3_seed_idempotent():
    results1 = asyncio.run(seed_mod.seed_all())
    results2 = asyncio.run(seed_mod.seed_all())
    hashes1 = {r["agent"]: r["boot_hash_prefix"] for r in results1}
    hashes2 = {r["agent"]: r["boot_hash_prefix"] for r in results2}
    assert hashes1 == hashes2, f"Seed idempotent: boot_hashes changed on re-run: {hashes1} vs {hashes2}"

test("seed idempotent — boot_hash stable across two runs (ON CONFLICT upsert)", b3_seed_idempotent)


# ── MULTI-AGENT INTEGRITY TESTS ───────────────────────────────────────────────

print("\n=== MULTI-AGENT INTEGRITY ===")


def i1_all_pass_after_seed():
    results = asyncio.run(mi.check_all())
    failures = [r for r in results if not r["valid"]]
    assert len(results) == 4, f"Expected 4 results, got {len(results)}"
    assert len(failures) == 0, f"Expected 0 failures, got: {failures}"

test("integrity: all 4 agents pass after seed (boot_hash valid)", i1_all_pass_after_seed)


def i2_detects_missing_key():
    # Temporarily override identity dir to nonexistent path
    original_dirs = mi.AGENT_IDENTITY_DIRS.copy()
    mi.AGENT_IDENTITY_DIRS["ADA"] = Path("/tmp/nonexistent_ada_test_dir_xyz")
    try:
        results = asyncio.run(mi.check_all(["ADA"]))
        ada_result = next(r for r in results if r["agent"] == "ADA")
        assert not ada_result["valid"], "ADA should fail with missing key"
        assert "not found" in ada_result["reason"].lower() or "identity.key" in ada_result["reason"], (
            f"Reason should mention missing key: {ada_result['reason']}"
        )
    finally:
        mi.AGENT_IDENTITY_DIRS["ADA"] = original_dirs["ADA"]

test("integrity detects missing identity.key → invalid result", i2_detects_missing_key)


def i3_single_agent_filter():
    results = asyncio.run(mi.check_all(["JARVIS"]))
    assert len(results) == 1, f"--agent filter should return 1 result, got {len(results)}"
    assert results[0]["agent"] == "JARVIS", f"Expected JARVIS, got {results[0]['agent']}"
    assert results[0]["valid"], f"JARVIS should pass integrity: {results[0]}"

test("integrity --agent JARVIS filters to single agent result", i3_single_agent_filter)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Tools Tests (promotion+anchors+integrity): {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
