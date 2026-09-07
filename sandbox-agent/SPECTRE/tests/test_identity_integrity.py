"""SPECTRE identity_integrity tests — F1 boot hash + §3.6 runtime drift detection.

I1: generate_or_load_identity() generates Ed25519 pair — pubkey is 64-char hex
I2: generate_or_load_identity() is idempotent — second call returns same pubkey
I3: compute_boot_hash() is deterministic — same inputs → same hash
I4: compute_boot_hash() is sensitive — any field change → different hash
I5: compute_core_values_hash() changes when core_values mutated
I6: initialize_boot_identity() writes boot_hash + pubkey to working_state (idempotent)
I7: validate_boot_integrity() → True when hash matches stored
I8: validate_boot_integrity() → False when core_values mutated
I9: integrity_check_loop() — no drift → no alarm, stop_event respected
I10: integrity_check_loop() fires alarm after _MAX_CONSECUTIVE_DRIFT drifts
I11: integrity_status() returns correct keys and types
I12: key file has 0600 permissions (chmod enforced)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))

import identity_integrity as ii

_pass = 0
_fail = 0


def test(name: str, fn) -> None:
    global _pass, _fail
    num = _pass + _fail + 1
    try:
        fn()
        print(f"  ✅ I{num}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ I{num}: {name} — {e}")
        _fail += 1


print("\n=== TEST IDENTITY INTEGRITY ===")

_OCEAN = {"O": 0.774, "C": 0.949, "E": 0.662, "A": 0.507, "N": 0.172}
_CVS = [
    {"id": "CV-1", "value": "no_harm", "hard_constraint": True},
    {"id": "CV-2", "value": "sandbox_isolation", "hard_constraint": True},
]


def _patched_key_dir(tmp: Path):
    """Context manager: patch IDENTITY_DIR and IDENTITY_KEY_PATH to a temp dir."""
    return mock.patch.multiple(
        ii,
        IDENTITY_DIR=tmp,
        IDENTITY_KEY_PATH=tmp / "identity.key",
    )


def _patched_state(state_path: Path):
    return mock.patch.object(ii, "WORKING_STATE_PATH", state_path)


# ─────────────────────────────────────────────────────────────────────────────
# I1: generate_or_load_identity() — generates Ed25519 pair, pubkey is 64-char hex
# ─────────────────────────────────────────────────────────────────────────────

def i1_generate_key_pair():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with _patched_key_dir(tmp):
            _, pk_hex = ii.generate_or_load_identity()

        assert isinstance(pk_hex, str), "pubkey must be a string"
        assert len(pk_hex) == 64, f"Ed25519 pubkey hex must be 64 chars, got {len(pk_hex)}"
        assert all(c in "0123456789abcdef" for c in pk_hex), "Must be lowercase hex"
        assert (tmp / "identity.key").exists(), "identity.key file must be created"

test("generate_or_load_identity(): Ed25519 pair — pubkey=64-char hex", i1_generate_key_pair)


# ─────────────────────────────────────────────────────────────────────────────
# I2: idempotent — second call returns same pubkey
# ─────────────────────────────────────────────────────────────────────────────

def i2_idempotent_key():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with _patched_key_dir(tmp):
            _, pk1 = ii.generate_or_load_identity()
            _, pk2 = ii.generate_or_load_identity()

        assert pk1 == pk2, f"Second call must return same pubkey: {pk1} vs {pk2}"

test("generate_or_load_identity(): idempotent — same pubkey on repeated calls", i2_idempotent_key)


# ─────────────────────────────────────────────────────────────────────────────
# I3: compute_boot_hash() is deterministic
# ─────────────────────────────────────────────────────────────────────────────

def i3_boot_hash_deterministic():
    h1 = ii.compute_boot_hash("SPECTRE", _OCEAN, _CVS, "aabbcc")
    h2 = ii.compute_boot_hash("SPECTRE", _OCEAN, _CVS, "aabbcc")
    assert h1 == h2, "Same inputs must produce same boot_hash"
    assert len(h1) == 64, f"SHA-256 hex must be 64 chars, got {len(h1)}"

test("compute_boot_hash(): deterministic — same inputs → same 64-char hash", i3_boot_hash_deterministic)


# ─────────────────────────────────────────────────────────────────────────────
# I4: compute_boot_hash() is sensitive to any input change
# ─────────────────────────────────────────────────────────────────────────────

def i4_boot_hash_sensitive():
    base = ii.compute_boot_hash("SPECTRE", _OCEAN, _CVS, "aabbcc")

    h_agent = ii.compute_boot_hash("ALICE", _OCEAN, _CVS, "aabbcc")
    assert h_agent != base, "Different agent_id must change hash"

    ocean2 = {**_OCEAN, "O": 0.999}
    h_ocean = ii.compute_boot_hash("SPECTRE", ocean2, _CVS, "aabbcc")
    assert h_ocean != base, "Different ocean_baseline must change hash"

    cvs2 = [{"id": "CV-X", "value": "injected", "hard_constraint": True}]
    h_cvs = ii.compute_boot_hash("SPECTRE", _OCEAN, cvs2, "aabbcc")
    assert h_cvs != base, "Different core_values must change hash"

    h_pk = ii.compute_boot_hash("SPECTRE", _OCEAN, _CVS, "ddeeff")
    assert h_pk != base, "Different pubkey must change hash"

test("compute_boot_hash(): sensitive — any input change → different hash", i4_boot_hash_sensitive)


# ─────────────────────────────────────────────────────────────────────────────
# I5: compute_core_values_hash() changes on mutation
# ─────────────────────────────────────────────────────────────────────────────

def i5_cv_hash_changes():
    h1 = ii.compute_core_values_hash(_CVS)
    h2 = ii.compute_core_values_hash(_CVS)
    assert h1 == h2, "Same CVs → same hash"

    mutated = [{"id": "CV-1", "value": "no_harm_MUTATED", "hard_constraint": True}] + _CVS[1:]
    h3 = ii.compute_core_values_hash(mutated)
    assert h3 != h1, "Mutated CVs must produce different hash"

test("compute_core_values_hash(): deterministic + sensitive to mutation", i5_cv_hash_changes)


# ─────────────────────────────────────────────────────────────────────────────
# I6: initialize_boot_identity() writes boot_hash + pubkey, idempotent
# ─────────────────────────────────────────────────────────────────────────────

def i6_initialize_idempotent():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        state_path = tmp / "working_state.json"
        state_path.write_text("{}")

        with _patched_key_dir(tmp), _patched_state(state_path):
            h1 = ii.initialize_boot_identity("SPECTRE", _OCEAN, _CVS)
            h2 = ii.initialize_boot_identity("SPECTRE", _OCEAN, _CVS)

        assert h1 == h2, "Second init must return same boot_hash"
        assert len(h1) == 64

        state = json.loads(state_path.read_text())
        assert "boot_hash" in state, "boot_hash must be in working_state"
        assert "identity_pubkey" in state, "identity_pubkey must be in working_state"
        assert state["boot_hash"] == h1

test("initialize_boot_identity(): writes state, idempotent (same hash both calls)", i6_initialize_idempotent)


# ─────────────────────────────────────────────────────────────────────────────
# I7: validate_boot_integrity() → True when hash matches
# ─────────────────────────────────────────────────────────────────────────────

def i7_validate_true():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        state_path = tmp / "working_state.json"
        state_path.write_text("{}")

        with _patched_key_dir(tmp), _patched_state(state_path):
            ii.initialize_boot_identity("SPECTRE", _OCEAN, _CVS)
            valid, reason = ii.validate_boot_integrity("SPECTRE", _OCEAN, _CVS)

        assert valid is True, f"Expected True, got False: {reason}"
        assert "valid" in reason.lower()

test("validate_boot_integrity(): True when boot_hash matches", i7_validate_true)


# ─────────────────────────────────────────────────────────────────────────────
# I8: validate_boot_integrity() → False when core_values mutated
# ─────────────────────────────────────────────────────────────────────────────

def i8_validate_false_on_mutation():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        state_path = tmp / "working_state.json"
        state_path.write_text("{}")

        mutated_cvs = [{"id": "CV-1", "value": "INJECTED_HARM", "hard_constraint": False}]

        with _patched_key_dir(tmp), _patched_state(state_path):
            ii.initialize_boot_identity("SPECTRE", _OCEAN, _CVS)
            valid, reason = ii.validate_boot_integrity("SPECTRE", _OCEAN, mutated_cvs)

        assert valid is False, "Mutated core_values should fail validation"
        assert "mismatch" in reason.lower(), f"Expected mismatch message, got: {reason}"

test("validate_boot_integrity(): False when core_values mutated (hash mismatch)", i8_validate_false_on_mutation)


# ─────────────────────────────────────────────────────────────────────────────
# I9: integrity_check_loop — no drift → no alarm, stop_event exits cleanly
# ─────────────────────────────────────────────────────────────────────────────

def i9_loop_no_drift_exits():
    with tempfile.TemporaryDirectory() as d:
        state_path = Path(d) / "ws.json"
        state_path.write_text("{}")

        stop = asyncio.Event()
        alarm_fired = []

        async def fake_alarm(reason: str):
            alarm_fired.append(reason)

        async def run():
            stop.set()  # set immediately — loop exits on first check
            await ii.integrity_check_loop(
                core_values=_CVS,
                stop_event=stop,
                interval_s=0.01,
                on_alarm=fake_alarm,
            )

        with mock.patch.object(ii, "WORKING_STATE_PATH", state_path):
            asyncio.run(run())

        assert len(alarm_fired) == 0, f"No alarm should fire with no drift: {alarm_fired}"

test("integrity_check_loop(): no drift + stop_event → exits cleanly, no alarm", i9_loop_no_drift_exits)


# ─────────────────────────────────────────────────────────────────────────────
# I10: integrity_check_loop fires alarm after _MAX_CONSECUTIVE_DRIFT consecutive drifts
# ─────────────────────────────────────────────────────────────────────────────

def i10_loop_alarm_on_drift():
    with tempfile.TemporaryDirectory() as d:
        state_path = Path(d) / "ws.json"
        mutated_cvs = [{"id": "CV-1", "value": "EVIL", "hard_constraint": False}]
        # Pre-inject drift into working_state — runtime CV override differs from baseline
        state_path.write_text(json.dumps({"core_values_override": mutated_cvs}))

        stop = asyncio.Event()
        alarm_fired = []

        async def fake_alarm(reason: str):
            alarm_fired.append(reason)

        async def run():
            await asyncio.wait_for(
                ii.integrity_check_loop(
                    core_values=_CVS,
                    stop_event=stop,
                    interval_s=0.01,
                    on_alarm=fake_alarm,
                ),
                timeout=3.0,
            )

        with mock.patch.object(ii, "WORKING_STATE_PATH", state_path):
            try:
                asyncio.run(run())
            except asyncio.TimeoutError:
                pass

        assert len(alarm_fired) >= 1, "Alarm must fire after consecutive drift"
        assert "ALARM" in alarm_fired[0], f"Alarm message must contain 'ALARM': {alarm_fired[0]}"

test("integrity_check_loop(): fires alarm after consecutive drifts, sets stop_event", i10_loop_alarm_on_drift)


# ─────────────────────────────────────────────────────────────────────────────
# I11: integrity_status() returns correct keys and types
# ─────────────────────────────────────────────────────────────────────────────

def i11_integrity_status_structure():
    with tempfile.TemporaryDirectory() as d:
        state_path = Path(d) / "ws.json"
        tmp = Path(d)

        # Before init
        state_path.write_text("{}")
        with mock.patch.object(ii, "WORKING_STATE_PATH", state_path):
            s0 = ii.integrity_status()
        assert s0["boot_hash_stored"] is False
        assert s0["pubkey_stored"] is False
        assert s0["alarms"] == 0

        # After init
        state_path.write_text("{}")
        with _patched_key_dir(tmp), _patched_state(state_path):
            ii.initialize_boot_identity("SPECTRE", _OCEAN, _CVS)
            s1 = ii.integrity_status()

        assert s1["boot_hash_stored"] is True
        assert s1["pubkey_stored"] is True
        assert isinstance(s1["boot_hash_prefix"], str) and len(s1["boot_hash_prefix"]) == 16
        assert isinstance(s1["alarms"], int)

test("integrity_status(): correct keys (boot_hash_stored, pubkey_stored, alarms)", i11_integrity_status_structure)


# ─────────────────────────────────────────────────────────────────────────────
# I12: identity key file has 0600 permissions
# ─────────────────────────────────────────────────────────────────────────────

def i12_key_file_permissions():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        with _patched_key_dir(tmp):
            ii.generate_or_load_identity()

        key_path = tmp / "identity.key"
        assert key_path.exists()
        mode = oct(key_path.stat().st_mode & 0o777)
        assert mode == "0o600", f"identity.key must be 0600, got {mode}"

test("identity.key file: chmod 0600 (private key protection)", i12_key_file_permissions)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"Identity Integrity Tests: {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
