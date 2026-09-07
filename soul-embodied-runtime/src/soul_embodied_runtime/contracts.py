"""Typed, model-agnostic contracts for physical action authorization."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, set | frozenset):
        return sorted(value)
    if hasattr(value, "__dataclass_fields__"):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


class RiskTier(str, Enum):
    NON_PHYSICAL = "non_physical"
    MOTION_LOW = "motion_low"
    CONTACT_LOW = "contact_low"
    CONTACT_ELEVATED = "contact_elevated"


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


DIRECT_CONTROL_PREFIXES = (
    "motor.",
    "servo.",
    "joint.",
    "torque.",
    "raw_control.",
    "safety_io.",
)
V01_FORBIDDEN_CAPABILITY_PREFIXES = ("adult_physical.",)
CAPABILITY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
PRODUCT_MODES = frozenset(
    {
        "presence",
        "domestic_assist",
        "affectionate",
        "adult_intimate",
        "care_support",
        "developer",
    }
)


@dataclass(frozen=True)
class ActionConstraints:
    max_speed_m_s: float
    max_force_n: float
    max_duration_ms: int
    supervision: str = "present"

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_speed_m_s) or not math.isfinite(self.max_force_n):
            raise ValueError("constraint values must be finite")
        if self.max_speed_m_s < 0 or self.max_force_n < 0 or self.max_duration_ms <= 0:
            raise ValueError("constraints must be non-negative and duration must be positive")
        if self.supervision not in {"present", "remote", "none"}:
            raise ValueError("invalid supervision")

    def clamp(self, ceiling: "ActionConstraints") -> "ActionConstraints":
        supervision = self.supervision
        if ceiling.supervision == "present":
            supervision = "present"
        elif ceiling.supervision == "remote" and supervision == "none":
            supervision = "remote"
        return ActionConstraints(
            max_speed_m_s=min(self.max_speed_m_s, ceiling.max_speed_m_s),
            max_force_n=min(self.max_force_n, ceiling.max_force_n),
            max_duration_ms=min(self.max_duration_ms, ceiling.max_duration_ms),
            supervision=supervision,
        )


@dataclass(frozen=True)
class ActionIntent:
    intent_id: str
    issuer: str
    body_id: str
    capability: str
    goal: dict[str, Any]
    constraints: ActionConstraints
    context_refs: tuple[str, ...]
    risk_tier: RiskTier
    created_at: datetime
    expires_at: datetime
    schema: str = "ser.action-intent/1"

    def __post_init__(self) -> None:
        for name in ("intent_id", "issuer", "body_id", "capability"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if self.schema != "ser.action-intent/1":
            raise ValueError("unsupported intent schema")
        if self.capability != self.capability.strip() or self.capability != self.capability.lower():
            raise ValueError("capability must be canonical lowercase without surrounding whitespace")
        if not CAPABILITY_RE.fullmatch(self.capability):
            raise ValueError("capability must use a dotted canonical identifier")
        if self.capability.startswith(DIRECT_CONTROL_PREFIXES):
            raise ValueError("direct motor/safety control is forbidden")
        if self.created_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("intent timestamps must be timezone-aware")
        if self.expires_at <= self.created_at:
            raise ValueError("intent must expire after creation")
        canonical_json(self.goal)

    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(canonical_json(self)).hexdigest()


@dataclass(frozen=True)
class ConsentGrant:
    consent_id: str
    subject_id: str
    body_id: str
    mode: str
    capabilities: frozenset[str]
    granted_at: datetime
    expires_at: datetime
    purposes: frozenset[str] = field(default_factory=frozenset)
    data_categories: frozenset[str] = field(default_factory=frozenset)
    contact_classes: frozenset[str] = field(default_factory=frozenset)
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.mode not in PRODUCT_MODES:
            raise ValueError("unsupported product mode")

    def active_at(self, now: datetime) -> bool:
        return (
            self.revoked_at is None
            and self.granted_at <= now < self.expires_at
        )

    def covers(self, *, body_id: str, mode: str, capability: str, now: datetime) -> bool:
        return (
            self.active_at(now)
            and self.body_id == body_id
            and self.mode == mode
            and capability in self.capabilities
        )


@dataclass(frozen=True)
class AgeAssuranceReceipt:
    receipt_id: str
    subject_id: str
    issuer: str
    method: str
    jurisdiction: str
    assurance_level: str
    adult: bool
    issued_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.assurance_level not in {"substantial", "high"}:
            raise ValueError("adult mode requires substantial or high age assurance")
        if self.expires_at <= self.issued_at:
            raise ValueError("age assurance expiry must follow issuance")
        for name in ("receipt_id", "subject_id", "issuer", "method", "jurisdiction"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is required")

    def valid_for(self, *, subject_id: str, jurisdiction: str, now: datetime) -> bool:
        return (
            self.adult
            and self.subject_id == subject_id
            and self.jurisdiction == jurisdiction
            and self.issued_at <= now < self.expires_at
        )


@dataclass(frozen=True)
class BodySafetyProfile:
    body_id: str
    capabilities: frozenset[str]
    ceilings: dict[str, ActionConstraints]
    risk_tiers: dict[str, RiskTier]
    safe_state_ready: bool
    emergency_stop_ready: bool
    calibration_valid: bool
    thermal_ok: bool
    battery_ok: bool
    state_observed_at: datetime
    max_state_age_ms: int = 100

    def __post_init__(self) -> None:
        for capability in self.capabilities:
            if not CAPABILITY_RE.fullmatch(capability):
                raise ValueError("body capability must use a canonical dotted identifier")
            if capability.startswith(DIRECT_CONTROL_PREFIXES):
                raise ValueError("body manifest cannot expose direct motor/safety control")
            if capability.startswith(V01_FORBIDDEN_CAPABILITY_PREFIXES):
                raise ValueError("adult physical capability is unavailable in v0.1")
        if set(self.ceilings) != set(self.capabilities):
            raise ValueError("every body capability must have exactly one safety ceiling")
        if set(self.risk_tiers) != set(self.capabilities):
            raise ValueError("every body capability must have one authoritative risk tier")
        if not all(isinstance(tier, RiskTier) for tier in self.risk_tiers.values()):
            raise ValueError("body risk tiers must use the RiskTier enum")
        if self.max_state_age_ms <= 0:
            raise ValueError("max_state_age_ms must be positive")

    def ready(self, now: datetime) -> bool:
        age_ms = (now - self.state_observed_at).total_seconds() * 1000
        return all(
            (
                self.safe_state_ready,
                self.emergency_stop_ready,
                self.calibration_valid,
                self.thermal_ok,
                self.battery_ok,
                0 <= age_ms <= self.max_state_age_ms,
            )
        )


@dataclass(frozen=True)
class PolicyContext:
    subject_id: str
    mode: str
    now: datetime
    consent_grants: tuple[ConsentGrant, ...] = ()
    jurisdiction: str = "PE"
    age_assurance: AgeAssuranceReceipt | None = None
    user_present: bool = True
    consent_ambiguous: bool = False
    capacity_uncertain: bool = False
    bystander_present: bool = False
    shared_deployment: bool = False
    revoked_capabilities: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.mode not in PRODUCT_MODES:
            raise ValueError("unsupported product mode")


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reasons: tuple[str, ...]
    effective_constraints: ActionConstraints | None
    consent_ids: tuple[str, ...]
    policy_version: str


@dataclass(frozen=True)
class BoundedAuthorization:
    authorization_id: str
    intent_hash: str
    issuer: str
    body_id: str
    allowed_capability: str
    effective_constraints: ActionConstraints
    consent_receipts: tuple[str, ...]
    policy_version: str
    not_before: datetime
    expires_at: datetime
    max_uses: int
    nonce: str
    decision: Decision = Decision.ALLOW
    schema: str = "ser.authorization/1"

    def __post_init__(self) -> None:
        if self.schema != "ser.authorization/1":
            raise ValueError("unsupported authorization schema")
        if self.decision is not Decision.ALLOW:
            raise ValueError("only allow decisions can become authorizations")
        if self.max_uses != 1:
            raise ValueError("v1 authorizations are one-shot")
        if self.expires_at <= self.not_before:
            raise ValueError("authorization expiry must follow not_before")
