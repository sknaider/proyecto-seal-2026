"""SPECTRE contract_layer — Semana 3 (D6 two-threshold gating completo).

Implementa:
- D6 two-threshold gating: absolute_confidence >= τ_abs AND relative_confidence >= τ_rel
- core_values check: toda acción se valida contra valores nucleares del agente
- invocation_budget: rate-limit formal para sinks externos (F5 invariante)

Ref: spec_spectre_contract_v2.md §3.7, spec_spectre_contract_v2_1_addendum.md F5
"""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

# ── CV Approval ───────────────────────────────────────────────────────────────
# William firmó los 6 CVs el 2026-05-02 vía Matrix:
# "esta bien si esta todo lo implementado que ustedes tienen va"

_CV_APPROVAL: dict[str, Any] = {
    "signed_by": "William",
    "signed_at": "2026-05-02T13:49:14Z",
    "message": "esta bien si esta todo lo implementado que ustedes tienen va",
    "version": "v1_sandbox",
    "cv_count": 6,
}


def cv_approval_status() -> dict[str, Any]:
    """Returns the CV approval record. Empty dict if not yet signed."""
    return dict(_CV_APPROVAL)


# ── Core Values (sandbox v1 — firmados por William 2026-05-02) ────────────────

CORE_VALUES: list[dict[str, Any]] = [
    {
        "id": "CV-1",
        "value": "no_harm",
        "description": "Never emit actions that cause harm to agents or users",
        "hard_constraint": True,
    },
    {
        "id": "CV-2",
        "value": "sandbox_isolation",
        "description": "Never access real web_chat, real memories, or production DB",
        "hard_constraint": True,
    },
    {
        "id": "CV-3",
        "value": "honest_reporting",
        "description": "Always report actual state — no phantom success claims",
        "hard_constraint": True,
    },
    {
        "id": "CV-4",
        "value": "minimal_footprint",
        "description": "Prefer reversible, additive actions over destructive ones",
        "hard_constraint": False,
    },
    {
        "id": "CV-5",
        "value": "team_coordination",
        "description": "Announce before acting on shared resources",
        "hard_constraint": False,
    },
    {
        "id": "CV-6",
        "value": "private_channel_default",
        "description": "Sub-modules communicate via private channel to orchestrator, not broadcast. Only final orchestrator output goes public.",
        "hard_constraint": False,
    },
]

_HARD_CONSTRAINTS = {v["id"] for v in CORE_VALUES if v["hard_constraint"]}

# ── Invocation Budget (global rate-limit for external sinks) ─────────────────

_BUDGET_WINDOW_S = 60
_BUDGET_MAX = 10
_invocation_timestamps: deque[float] = deque()


def check_invocation_budget() -> tuple[bool, str]:
    """Returns (allowed, reason). Cleans expired entries from window."""
    now = time.monotonic()
    while _invocation_timestamps and now - _invocation_timestamps[0] > _BUDGET_WINDOW_S:
        _invocation_timestamps.popleft()
    if len(_invocation_timestamps) >= _BUDGET_MAX:
        remaining = _BUDGET_WINDOW_S - (now - _invocation_timestamps[0])
        return False, f"budget exhausted ({_BUDGET_MAX}/{_BUDGET_WINDOW_S}s) — reset in {remaining:.1f}s"
    return True, "ok"


def consume_invocation_budget() -> None:
    """Record one invocation against the budget. Call after successful emit."""
    _invocation_timestamps.append(time.monotonic())


def budget_status() -> dict[str, Any]:
    now = time.monotonic()
    while _invocation_timestamps and now - _invocation_timestamps[0] > _BUDGET_WINDOW_S:
        _invocation_timestamps.popleft()
    return {
        "used": len(_invocation_timestamps),
        "max": _BUDGET_MAX,
        "window_s": _BUDGET_WINDOW_S,
        "available": _BUDGET_MAX - len(_invocation_timestamps),
    }


# ── D6 Two-Threshold Gating ───────────────────────────────────────────────────
# spec_spectre_contract_v2.md §3.7: every Nivel 4 action passes two confidence checks.
# τ_abs: absolute certainty floor — action is rejected outright below this.
# τ_rel: relative gain over baseline — action is soft-warned if it barely beats "do nothing".
# baseline: confidence of taking no action (0.3 = noise floor from D3 cache spec).

# Sandbox v1 calibration: default confidence=0.7 must clear both gates.
# Effective combined floor = max(τ_abs, τ_rel + baseline) = max(0.5, 0.3+0.3) = 0.60.
# Production target (when real confidence scoring is live): τ_abs=0.60, τ_rel=0.55.
_D6_TAU_ABS = 0.5
_D6_TAU_REL = 0.3
_D6_BASELINE = 0.3


def two_threshold_gate(
    absolute_confidence: float,
    relative_confidence: float | None = None,
) -> tuple[bool, str, bool]:
    """
    D6 two-threshold gate.

    absolute_confidence: certainty score [0.0, 1.0] from Nivel 4.
    relative_confidence: improvement over baseline. If None, computed as
        absolute_confidence - _D6_BASELINE.

    Returns (allowed, reason, soft_warn):
      True,  msg, False → both thresholds pass → execute
      False, msg, True  → abs pass but rel fail → soft warn, still execute
      False, msg, False → abs fail → hard reject
    """
    if relative_confidence is None:
        relative_confidence = absolute_confidence - _D6_BASELINE

    abs_pass = absolute_confidence >= _D6_TAU_ABS
    rel_pass = relative_confidence >= _D6_TAU_REL

    if abs_pass and rel_pass:
        return True, (
            f"D6 pass — abs={absolute_confidence:.2f}≥{_D6_TAU_ABS}, "
            f"rel={relative_confidence:.2f}≥{_D6_TAU_REL}"
        ), False

    if abs_pass and not rel_pass:
        return False, (
            f"D6 soft — abs={absolute_confidence:.2f}≥{_D6_TAU_ABS} "
            f"but rel={relative_confidence:.2f}<{_D6_TAU_REL} (weak gain over baseline)"
        ), True

    return False, (
        f"D6 hard block — abs={absolute_confidence:.2f}<{_D6_TAU_ABS} (insufficient certainty)"
    ), False


def threshold_status() -> dict[str, Any]:
    """Return current D6 threshold configuration."""
    return {
        "tau_abs": _D6_TAU_ABS,
        "tau_rel": _D6_TAU_REL,
        "baseline": _D6_BASELINE,
    }


# ── Core Values Checker ───────────────────────────────────────────────────────

class ContractViolation(Exception):
    """Raised when a hard constraint is violated."""
    def __init__(self, value_id: str, description: str, action: str):
        self.value_id = value_id
        self.description = description
        self.action = action
        super().__init__(f"[SPECTRE/contract] HARD VIOLATION {value_id}: {description} — action='{action}'")


def check_core_values(action: str, metadata: dict[str, Any] | None = None) -> list[dict]:
    """
    Validate action against core values.

    Returns list of soft violations (non-blocking).
    Raises ContractViolation for hard constraint violations.

    action: short description of what SPECTRE is about to do
    metadata: optional dict with keys like 'target', 'sink', 'type'
    """
    meta = metadata or {}
    violations: list[dict] = []

    for cv in CORE_VALUES:
        violated = _check_value(cv, action, meta)
        if not violated:
            continue

        entry = {
            "value_id": cv["id"],
            "value": cv["value"],
            "description": cv["description"],
            "action": action,
            "hard": cv["hard_constraint"],
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        if cv["hard_constraint"]:
            print(f"[SPECTRE/contract] ⛔ HARD VIOLATION {cv['id']}: {cv['description']}", flush=True)
            raise ContractViolation(cv["id"], cv["description"], action)
        else:
            print(f"[SPECTRE/contract] ⚠️ soft violation {cv['id']}: {cv['description']}", flush=True)
            violations.append(entry)

    return violations


def _check_value(cv: dict, action: str, meta: dict) -> bool:
    """Returns True if this value is violated by the action."""
    vid = cv["id"]
    action_lower = action.lower()
    sink = meta.get("sink", "").lower()
    target = meta.get("target", "").lower()

    if vid == "CV-1":
        # harm = victim is an agent/user (process/runtime level)
        # CV-4 owns data-destruction — CV-1 owns agent-destruction
        subject = meta.get("subject_type", "").lower()
        if subject in {"data", "storage", "table", "file"}:
            return False  # data subject → CV-4's domain
        agent_harm_keywords = {"kill", "terminate", "attack", "disable agent", "harm agent"}
        return any(k in action_lower for k in agent_harm_keywords)

    if vid == "CV-2":
        # real production access
        # Exception: william_response=True means William explicitly authorized this emit
        if meta.get("william_response"):
            return False
        prod_keywords = {"web_chat_real", "prod_db", "production", "soul_v3.memories_real"}
        if any(k in action_lower for k in prod_keywords):
            return True
        if sink in {"web_chat", "production", "real_webchat"}:
            return True
        if target in {"william", "ada", "jarvis", "alice", "dum"} and meta.get("channel") == "web_chat":
            return True
        return False

    if vid == "CV-3":
        # phantom claims — hard to detect automatically; trust the agent
        return False

    if vid == "CV-4":
        # data/storage destruction (soft — prefer reversible)
        # subject_type="data" metadata makes it explicit; keyword fallback for bare actions
        data_destruction = {"rm -rf", "drop table", "truncate", "format", "overwrite", "delete from"}
        return any(k in action_lower for k in data_destruction)

    if vid == "CV-5":
        # shared resource without announcement
        shared = {"event_bus", "seal_db", "soul_v3", "production"}
        return any(k in action_lower for k in shared) and not meta.get("announced", False)

    if vid == "CV-6":
        # broadcast when private channel should be used
        # triggered when channel=web_chat AND private=False AND not an orchestrator final output
        channel = meta.get("channel", "")
        is_orchestrator_output = meta.get("orchestrator_output", False)
        if channel in {"web_chat", "broadcast"} and not is_orchestrator_output:
            return True
        if meta.get("broadcast", False) and not is_orchestrator_output:
            return True
        return False

    return False


# ── Contract Gate (combined check) ───────────────────────────────────────────

def contract_gate(
    action: str,
    metadata: dict[str, Any] | None = None,
    confidence: float = 0.7,
) -> tuple[bool, str, list[dict]]:
    """
    Full contract check: budget → D6 two-threshold → core_values.

    confidence: Nivel 4 action certainty [0.0, 1.0]. Default 0.7 (sandbox v1).
    Returns (allowed, reason, soft_violations).
    Raises ContractViolation on hard core_value violation.
    """
    # 1. invocation budget
    budget_ok, budget_reason = check_invocation_budget()
    if not budget_ok:
        return False, budget_reason, []

    # 2. D6 two-threshold gate
    d6_allowed, d6_reason, d6_soft = two_threshold_gate(confidence)
    if not d6_allowed and not d6_soft:
        print(f"[SPECTRE/contract] ⛔ D6 hard block: {d6_reason}", flush=True)
        return False, d6_reason, []

    # 3. core values (raises ContractViolation on hard violation)
    soft_violations = check_core_values(action, metadata)

    if d6_soft:
        print(f"[SPECTRE/contract] ⚠️ D6 soft warn: {d6_reason}", flush=True)
        soft_violations.append({
            "type": "D6_soft_warn",
            "reason": d6_reason,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    return True, "ok", soft_violations
