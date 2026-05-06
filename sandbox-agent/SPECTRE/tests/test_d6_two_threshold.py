"""SPECTRE D6 two-threshold gating — 7 tests.

Verifica two_threshold_gate() y su integración en contract_gate():
  - abs≥τ_abs AND rel≥τ_rel → pass
  - abs≥τ_abs AND rel<τ_rel → soft warn (allowed via contract_gate)
  - abs<τ_abs → hard block (rejected)
  - contract_gate default confidence (0.7) → passes D6
  - contract_gate low confidence (0.2) → D6 hard block
  - contract_gate medium confidence (0.55) → D6 soft warn in violations
  - threshold_status() returns correct τ values
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "kernel"))

from contract_layer import (
    two_threshold_gate,
    contract_gate,
    threshold_status,
    _D6_TAU_ABS,
    _D6_TAU_REL,
    _D6_BASELINE,
)

_pass = 0
_fail = 0


def test(name: str, fn) -> None:
    global _pass, _fail
    try:
        fn()
        print(f"  ✅ D6-{_pass + _fail + 1}: {name}")
        _pass += 1
    except Exception as e:
        print(f"  ❌ D6-{_pass + _fail + 1}: {name} — {e}")
        _fail += 1


# ─────────────────────────────────────────────────────────────────────────────
# D6-1: abs≥τ_abs AND rel≥τ_rel → both pass → allowed=True, soft_warn=False
# ─────────────────────────────────────────────────────────────────────────────

def d6_both_pass():
    conf = 0.8
    rel = conf - _D6_BASELINE  # 0.8 - 0.3 = 0.5 ≥ τ_rel (0.3)
    allowed, reason, soft = two_threshold_gate(conf)
    assert allowed is True, f"Expected allowed=True, got {allowed}"
    assert soft is False, f"Expected soft_warn=False, got {soft}"
    assert "D6 pass" in reason, f"Expected 'D6 pass' in reason, got: {reason}"

test("abs=0.8≥0.5, rel=0.5≥0.3 → both pass (allowed=True, soft=False)", d6_both_pass)


# ─────────────────────────────────────────────────────────────────────────────
# D6-2: abs≥τ_abs but rel<τ_rel → soft warn → allowed=False, soft_warn=True
# ─────────────────────────────────────────────────────────────────────────────

def d6_abs_pass_rel_fail():
    # abs=0.55 ≥ 0.5 (abs pass), rel = 0.55 - 0.3 = 0.25 < 0.3 (rel fail)
    conf = 0.55
    allowed, reason, soft = two_threshold_gate(conf)
    assert allowed is False, f"Expected allowed=False (soft block), got {allowed}"
    assert soft is True, f"Expected soft_warn=True, got {soft}"
    assert "soft" in reason.lower(), f"Expected 'soft' in reason, got: {reason}"

test("abs=0.55≥0.5 but rel=0.25<0.3 → soft warn (allowed=False, soft=True)", d6_abs_pass_rel_fail)


# ─────────────────────────────────────────────────────────────────────────────
# D6-3: abs<τ_abs → hard block → allowed=False, soft_warn=False
# ─────────────────────────────────────────────────────────────────────────────

def d6_abs_fail_hard_block():
    conf = 0.3  # 0.3 < τ_abs (0.5) → hard block
    allowed, reason, soft = two_threshold_gate(conf)
    assert allowed is False, f"Expected allowed=False, got {allowed}"
    assert soft is False, f"Expected soft_warn=False (hard block), got {soft}"
    assert "hard block" in reason.lower(), f"Expected 'hard block' in reason, got: {reason}"

test("abs=0.3<0.5 → hard block (allowed=False, soft=False)", d6_abs_fail_hard_block)


# ─────────────────────────────────────────────────────────────────────────────
# D6-4: explicit relative_confidence overrides auto-compute
# ─────────────────────────────────────────────────────────────────────────────

def d6_explicit_rel_confidence():
    # abs=0.6 ≥ 0.5, but explicit rel=0.1 < 0.3 → soft warn
    allowed, reason, soft = two_threshold_gate(0.6, relative_confidence=0.1)
    assert allowed is False and soft is True, (
        f"Explicit rel=0.1 should give soft warn, got allowed={allowed}, soft={soft}"
    )

    # abs=0.6, explicit rel=0.5 ≥ 0.3 → full pass
    allowed2, _, soft2 = two_threshold_gate(0.6, relative_confidence=0.5)
    assert allowed2 is True and soft2 is False, (
        f"Explicit rel=0.5 should give full pass, got allowed={allowed2}, soft={soft2}"
    )

test("explicit relative_confidence overrides auto-compute", d6_explicit_rel_confidence)


# ─────────────────────────────────────────────────────────────────────────────
# D6-5: contract_gate default confidence (0.7) → D6 passes, returns allowed=True
# ─────────────────────────────────────────────────────────────────────────────

def d6_contract_gate_default_confidence():
    # default confidence=0.7: abs=0.7≥0.5, rel=0.4≥0.3 → both pass
    allowed, reason, violations = contract_gate(
        "emit sandbox response",
        metadata={"sink": "sandbox", "channel": "sandbox"},
    )
    assert allowed is True, f"Default confidence=0.7 should pass D6, got allowed={allowed}: {reason}"
    d6_warns = [v for v in violations if isinstance(v, dict) and v.get("type") == "D6_soft_warn"]
    assert len(d6_warns) == 0, f"No D6 soft warn expected at default confidence, got: {d6_warns}"

test("contract_gate(confidence=0.7 default) → D6 passes, no soft warns", d6_contract_gate_default_confidence)


# ─────────────────────────────────────────────────────────────────────────────
# D6-6: contract_gate with low confidence (0.2) → D6 hard block → allowed=False
# ─────────────────────────────────────────────────────────────────────────────

def d6_contract_gate_low_confidence():
    allowed, reason, violations = contract_gate(
        "risky action with low certainty",
        metadata={"sink": "sandbox", "channel": "sandbox"},
        confidence=0.2,
    )
    assert allowed is False, f"confidence=0.2 should be D6 hard blocked, got allowed={allowed}"
    assert "D6 hard block" in reason, f"Expected D6 hard block reason, got: {reason}"

test("contract_gate(confidence=0.2) → D6 hard block (allowed=False)", d6_contract_gate_low_confidence)


# ─────────────────────────────────────────────────────────────────────────────
# D6-7: contract_gate medium confidence (0.55) → soft warn in violations, still allowed
# ─────────────────────────────────────────────────────────────────────────────

def d6_contract_gate_medium_confidence():
    # abs=0.55≥0.5 (pass), rel=0.25<0.3 (soft) → allowed=True with D6_soft_warn
    allowed, reason, violations = contract_gate(
        "moderate confidence action",
        metadata={"sink": "sandbox", "channel": "sandbox"},
        confidence=0.55,
    )
    assert allowed is True, f"confidence=0.55 soft warn should still allow, got allowed={allowed}"
    d6_warns = [v for v in violations if isinstance(v, dict) and v.get("type") == "D6_soft_warn"]
    assert len(d6_warns) == 1, f"Expected 1 D6_soft_warn in violations, got: {d6_warns}"

test("contract_gate(confidence=0.55) → soft warn in violations, still allowed", d6_contract_gate_medium_confidence)


# ─────────────────────────────────────────────────────────────────────────────
# Bonus: threshold_status() returns correct τ values
# ─────────────────────────────────────────────────────────────────────────────

def d6_threshold_status():
    status = threshold_status()
    assert status["tau_abs"] == _D6_TAU_ABS, f"tau_abs mismatch: {status}"
    assert status["tau_rel"] == _D6_TAU_REL, f"tau_rel mismatch: {status}"
    assert status["baseline"] == _D6_BASELINE, f"baseline mismatch: {status}"

test("threshold_status() returns τ_abs, τ_rel, baseline correctly", d6_threshold_status)


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────

print(f"\n{'='*50}")
print(f"D6 Two-Threshold Tests: {_pass}/{_pass + _fail} PASS")
if _fail:
    sys.exit(1)
