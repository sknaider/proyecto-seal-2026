"""Executable role contract for per-user SEAL agent instances.

This module is deliberately pure: it decides which runtime may exist, but does
not start processes or trust model-provided identity.  The reconciler/launcher
must supply ``user_id``, ``role`` and assignments from an authenticated server
session.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
import re
from typing import Iterable, Mapping


POLICY_VERSION = "user-clone-v1"
ASSIGNABLE_AGENTS = frozenset({"ADA", "ALICE", "DUM", "FABLE", "JARVIS", "NEXUS"})
PRIVILEGED_HUMAN_ROLES = frozenset({"superuser", "admin"})


class RuntimeMode(str, Enum):
    CANONICAL = "canonical"
    ISOLATED_CLONE = "isolated_clone"
    DENIED = "denied"


@dataclass(frozen=True)
class UserClonePolicy:
    role: str
    runtime_mode: RuntimeMode
    spawn_clone: bool
    canonical_memory_read: bool
    own_user_memory_read: bool
    all_user_memory_audit: bool
    core_code_visibility: bool


_POLICIES = {
    "superuser": UserClonePolicy(
        role="superuser",
        runtime_mode=RuntimeMode.CANONICAL,
        spawn_clone=False,
        canonical_memory_read=True,
        own_user_memory_read=True,
        all_user_memory_audit=True,
        core_code_visibility=True,
    ),
    "admin": UserClonePolicy(
        role="admin",
        runtime_mode=RuntimeMode.CANONICAL,
        spawn_clone=False,
        canonical_memory_read=True,
        own_user_memory_read=True,
        all_user_memory_audit=True,
        core_code_visibility=True,
    ),
    "basic": UserClonePolicy(
        role="basic",
        runtime_mode=RuntimeMode.ISOLATED_CLONE,
        spawn_clone=True,
        canonical_memory_read=False,
        own_user_memory_read=True,
        all_user_memory_audit=False,
        core_code_visibility=False,
    ),
}


def normalize_agent(agent: str) -> str:
    value = str(agent or "").strip().upper()
    if value not in ASSIGNABLE_AGENTS:
        raise ValueError(f"unknown or unassignable agent: {value or '<empty>'}")
    return value


def normalize_user_id(user_id: int | str) -> int:
    try:
        value = int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("user_id must be a positive integer from the authenticated session") from exc
    if value <= 0:
        raise ValueError("user_id must be a positive integer from the authenticated session")
    return value


def policy_for_role(role: str) -> UserClonePolicy:
    """Return a fail-closed human policy.

    ``agent`` is intentionally rejected: it is an internal service identity,
    never a human viewer role and cannot be used to bypass human RBAC.
    """

    value = str(role or "").strip().lower()
    try:
        return _POLICIES[value]
    except KeyError as exc:
        raise ValueError(f"unknown or non-human role: {value or '<empty>'}") from exc


def instance_key(agent: str, user_id: int | str) -> str:
    """Stable process/storage key. Usernames are deliberately excluded."""

    return f"{normalize_agent(agent)}-u{normalize_user_id(user_id)}"


def resolve_runtime(role: str, agent: str, assigned_agents: Iterable[str]) -> RuntimeMode:
    """Resolve the only legal runtime for an authenticated human request."""

    policy = policy_for_role(role)
    normalized_agent = normalize_agent(agent)
    if policy.runtime_mode is RuntimeMode.CANONICAL:
        return RuntimeMode.CANONICAL
    assigned = {normalize_agent(item) for item in assigned_agents}
    if normalized_agent not in assigned:
        return RuntimeMode.DENIED
    return RuntimeMode.ISOLATED_CLONE


def build_instance_manifest(
    *,
    user_id: int | str,
    role: str,
    agent: str,
    assigned_agents: Iterable[str],
    instance_id: str,
    identity_projection_sha256: str,
    technical_projection_sha256: str,
) -> dict[str, object]:
    """Build a non-secret, launch-authoritative clone manifest.

    Only ``basic`` produces a clone manifest. Privileged roles use canonical
    seats; an unassigned basic user fails closed.
    """

    uid = normalize_user_id(user_id)
    normalized_agent = normalize_agent(agent)
    mode = resolve_runtime(role, normalized_agent, assigned_agents)
    if mode is not RuntimeMode.ISOLATED_CLONE:
        raise ValueError(f"clone manifest forbidden for runtime mode: {mode.value}")
    expected_instance = instance_key(normalized_agent, uid)
    if str(instance_id or "") != expected_instance:
        raise ValueError(f"instance_id must equal the immutable pair key {expected_instance}")
    digest = str(identity_projection_sha256 or "").lower()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("identity projection requires an exact sha256 digest")
    technical_digest = str(technical_projection_sha256 or "").lower()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", technical_digest):
        raise ValueError("technical projection requires an exact sha256 digest")
    policy = policy_for_role(role)
    return {
        "schema": "seal.user-agent-instance.v1",
        "policy_version": POLICY_VERSION,
        "instance_id": str(instance_id),
        "instance_key": instance_key(normalized_agent, uid),
        "user_id": uid,
        "role": policy.role,
        "agent": normalized_agent,
        "runtime_mode": mode.value,
        "identity_projection_sha256": digest,
        "technical_projection_sha256": technical_digest,
        "technical_projection_read": True,
        "canonical_memory_read": policy.canonical_memory_read,
        "core_code_visibility": policy.core_code_visibility,
        "memory_namespace": f"user:{uid}:agent:{normalized_agent}",
    }


_FORBIDDEN_BASIC_ENV = frozenset(
    {
        "SOUL_DATABASE_URL",
        "SEAL_PG_ADMIN_DSN",
        "SEAL_MCP_RUNTIME_DB",
        "SEAL_DB_DSN",
        "SEAL_DB_URL",
    }
)


def basic_runtime_violations(
    environment: Mapping[str, str],
    mounted_paths: Iterable[str | Path],
) -> list[str]:
    """Return structural isolation violations for a basic clone.

    This is suitable for a pre-start gate. It checks presence, not whether a
    secret happens to work: a basic clone must not receive the carrier at all.
    """

    violations: list[str] = []
    for key in sorted(_FORBIDDEN_BASIC_ENV):
        if str(environment.get(key, "")).strip():
            violations.append(f"forbidden_env:{key}")
    mcp_url = str(environment.get("MCP_URL", "")).strip()
    if mcp_url and any(port in mcp_url for port in (":8771", ":8768", ":8780")):
        violations.append("forbidden_canonical_mcp_url")
    roots = {
        Path.home() / "IA" / "proyecto-seal",
        Path.home() / ".claude",
        Path.home() / ".codex",
        Path("/var/run/docker.sock"),
    }
    for raw in mounted_paths:
        path = Path(raw).expanduser()
        if any(path == root or root in path.parents for root in roots):
            violations.append(f"forbidden_mount:{path}")
    return violations


def policy_as_dict(role: str) -> dict[str, object]:
    """Stable serialization used by specs, preflights and audit receipts."""

    result = asdict(policy_for_role(role))
    result["runtime_mode"] = result["runtime_mode"].value
    result["policy_version"] = POLICY_VERSION
    return result
