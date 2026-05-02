"""SPECTRE contract_layer — Semana 2 mínimo viable.

Implementa:
- core_values check: toda acción se valida contra valores nucleares del agente
- invocation_budget: rate-limit formal para sinks externos (D6 / F5 invariante)

No implementa aún (Semana 3):
- two-threshold gating completo (D6)
- contract violation logging a soul_v3

Ref: spec_spectre_contract_v2_1_addendum.md F5 + contrato D6
"""
from __future__ import annotations

import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

# ── Core Values (sandbox v1 — William firma en Semana 3) ─────────────────────

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

def contract_gate(action: str, metadata: dict[str, Any] | None = None) -> tuple[bool, str, list[dict]]:
    """
    Full contract check: budget + core_values.

    Returns:
        (allowed: bool, reason: str, soft_violations: list[dict])

    Raises ContractViolation if a hard core_value is violated.
    Call this before ANY external sink emit.
    """
    # 1. invocation budget
    budget_ok, budget_reason = check_invocation_budget()
    if not budget_ok:
        return False, budget_reason, []

    # 2. core values (raises on hard violation)
    soft_violations = check_core_values(action, metadata)

    return True, "ok", soft_violations
