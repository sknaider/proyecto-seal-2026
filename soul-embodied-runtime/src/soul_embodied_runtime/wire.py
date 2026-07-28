"""Strict JSON wire encoding for simulation brain-to-edge bundles.

JSON is the v0.2 reference transport. Production remains targeted at canonical
CBOR/COSE; unknown fields fail closed here so the migration surface is explicit.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from .authorization import SignedAuthorization
from .contracts import (
    ActionConstraints,
    ActionIntent,
    BoundedAuthorization,
    Decision,
    RiskTier,
)


BUNDLE_SCHEMA = "ser.ada-sim-plan/1"


def _exact(value: dict[str, Any], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{label} fields do not match schema")


def _dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("wire timestamps must be timezone-aware")
    return parsed


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("wire timestamps must be timezone-aware")
    return value.isoformat().replace("+00:00", "Z")


def constraints_to_dict(value: ActionConstraints) -> dict[str, Any]:
    return {
        "max_speed_m_s": value.max_speed_m_s,
        "max_force_n": value.max_force_n,
        "max_duration_ms": value.max_duration_ms,
        "supervision": value.supervision,
    }


def constraints_from_dict(value: dict[str, Any]) -> ActionConstraints:
    _exact(
        value,
        {"max_speed_m_s", "max_force_n", "max_duration_ms", "supervision"},
        "constraints",
    )
    return ActionConstraints(**value)


def intent_to_dict(value: ActionIntent) -> dict[str, Any]:
    return {
        "schema": value.schema,
        "intent_id": value.intent_id,
        "issuer": value.issuer,
        "body_id": value.body_id,
        "capability": value.capability,
        "goal": value.goal,
        "constraints": constraints_to_dict(value.constraints),
        "context_refs": list(value.context_refs),
        "risk_tier": value.risk_tier.value,
        "created_at": _iso(value.created_at),
        "expires_at": _iso(value.expires_at),
    }


def intent_from_dict(value: dict[str, Any]) -> ActionIntent:
    _exact(
        value,
        {
            "schema", "intent_id", "issuer", "body_id", "capability", "goal",
            "constraints", "context_refs", "risk_tier", "created_at", "expires_at",
        },
        "intent",
    )
    return ActionIntent(
        schema=value["schema"],
        intent_id=value["intent_id"],
        issuer=value["issuer"],
        body_id=value["body_id"],
        capability=value["capability"],
        goal=value["goal"],
        constraints=constraints_from_dict(value["constraints"]),
        context_refs=tuple(value["context_refs"]),
        risk_tier=RiskTier(value["risk_tier"]),
        created_at=_dt(value["created_at"]),
        expires_at=_dt(value["expires_at"]),
    )


def signed_to_dict(value: SignedAuthorization) -> dict[str, Any]:
    auth = value.authorization
    return {
        "key_id": value.key_id,
        "algorithm": value.algorithm,
        "signature_b64": value.signature_b64,
        "authorization": {
            "schema": auth.schema,
            "authorization_id": auth.authorization_id,
            "intent_hash": auth.intent_hash,
            "issuer": auth.issuer,
            "body_id": auth.body_id,
            "allowed_capability": auth.allowed_capability,
            "effective_constraints": constraints_to_dict(auth.effective_constraints),
            "consent_receipts": list(auth.consent_receipts),
            "policy_version": auth.policy_version,
            "not_before": _iso(auth.not_before),
            "expires_at": _iso(auth.expires_at),
            "max_uses": auth.max_uses,
            "nonce": auth.nonce,
            "decision": auth.decision.value,
        },
    }


def signed_from_dict(value: dict[str, Any]) -> SignedAuthorization:
    _exact(value, {"key_id", "algorithm", "signature_b64", "authorization"}, "signed")
    auth = value["authorization"]
    _exact(
        auth,
        {
            "schema", "authorization_id", "intent_hash", "issuer", "body_id",
            "allowed_capability", "effective_constraints", "consent_receipts",
            "policy_version", "not_before", "expires_at", "max_uses", "nonce",
            "decision",
        },
        "authorization",
    )
    bounded = BoundedAuthorization(
        schema=auth["schema"],
        authorization_id=auth["authorization_id"],
        intent_hash=auth["intent_hash"],
        issuer=auth["issuer"],
        body_id=auth["body_id"],
        allowed_capability=auth["allowed_capability"],
        effective_constraints=constraints_from_dict(auth["effective_constraints"]),
        consent_receipts=tuple(auth["consent_receipts"]),
        policy_version=auth["policy_version"],
        not_before=_dt(auth["not_before"]),
        expires_at=_dt(auth["expires_at"]),
        max_uses=auth["max_uses"],
        nonce=auth["nonce"],
        decision=Decision(auth["decision"]),
    )
    return SignedAuthorization(
        authorization=bounded,
        key_id=value["key_id"],
        algorithm=value["algorithm"],
        signature_b64=value["signature_b64"],
    )


def plan_bundle(
    *,
    identity_digest: str,
    session_id: str,
    issued_at: datetime,
    steps: list[tuple[ActionIntent, SignedAuthorization]],
) -> dict[str, Any]:
    return {
        "schema": BUNDLE_SCHEMA,
        "agent": "ADA",
        "issuer": "soul:agent:ada",
        "identity_digest": identity_digest,
        "session_id": session_id,
        "issued_at": _iso(issued_at),
        "steps": [
            {"intent": intent_to_dict(intent), "authorization": signed_to_dict(signed)}
            for intent, signed in steps
        ],
    }


def parse_plan_bundle(value: dict[str, Any]) -> dict[str, Any]:
    _exact(
        value,
        {"schema", "agent", "issuer", "identity_digest", "session_id", "issued_at", "steps"},
        "bundle",
    )
    if value["schema"] != BUNDLE_SCHEMA:
        raise ValueError("unsupported bundle schema")
    if value["agent"] != "ADA" or value["issuer"] != "soul:agent:ada":
        raise PermissionError("bundle is not issued by ADA")
    if not value["identity_digest"].startswith("sha256:"):
        raise ValueError("identity digest is malformed")
    if not isinstance(value["steps"], list) or not 1 <= len(value["steps"]) <= 8:
        raise ValueError("bundle must contain 1..8 steps")
    parsed_steps = []
    for step in value["steps"]:
        _exact(step, {"intent", "authorization"}, "plan step")
        intent = intent_from_dict(step["intent"])
        signed = signed_from_dict(step["authorization"])
        if intent.issuer != value["issuer"]:
            raise PermissionError("step issuer mismatch")
        parsed_steps.append((intent, signed))
    return {
        **value,
        "issued_at": _dt(value["issued_at"]),
        "steps": parsed_steps,
    }
