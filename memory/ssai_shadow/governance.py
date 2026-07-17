"""Umbrales de aprobación para manifiestos SSAI SHADOW."""

from __future__ import annotations

import hmac
from collections.abc import Mapping, Sequence
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .crypto import manifest_digest, public_key_b64, sign_manifest, verify_manifest
from .manifest import validate_manifest

_CONSTITUTIONAL_REASONS = {
    "CONSTITUTION_CHANGE",
    "CRITICAL_RULE_CHANGE",
    "GOVERNANCE_CHANGE",
}


class GovernanceError(ValueError):
    """La aprobación no satisface la política o no es criptográficamente válida."""


def required_roles(
    manifest: Mapping[str, Any],
    previous_manifest: Mapping[str, Any] | None = None,
) -> frozenset[str]:
    if manifest.get("sequence") == 1:
        return frozenset({"genesis_root", "agent_identity"})
    if previous_manifest is None:
        raise GovernanceError("evolution policy requires the previous manifest")
    constitution_changed = manifest.get("constitution") != previous_manifest.get(
        "constitution"
    )
    declared_constitutional = manifest.get("reason_code") in _CONSTITUTIONAL_REASONS
    if constitution_changed and not declared_constitutional:
        raise GovernanceError(
            "constitutional field changes require an explicit constitutional reason"
        )
    if declared_constitutional and not constitution_changed:
        raise GovernanceError("constitutional reason does not match the actual manifest diff")
    if manifest.get("reason_code") in {"RECOVERY", "KEY_ROTATION", "RETIREMENT"}:
        raise GovernanceError("recovery, rotation and retirement are unsupported in SHADOW M1")
    if constitution_changed:
        return frozenset({"custodian", "agent_identity"})
    return frozenset({"agent_identity"})


def create_approval(
    manifest: Mapping[str, Any],
    *,
    key_id: str,
    role: str,
    private_key: Ed25519PrivateKey,
    previous_manifest: Mapping[str, Any] | None = None,
    previous_manifest_hash: str | None = None,
) -> dict[str, str]:
    """Firma un manifiesto y liga la firma a un controller declarado."""

    validate_manifest(
        manifest,
        previous_manifest=previous_manifest,
        previous_manifest_hash=previous_manifest_hash,
    )
    controllers = {item["key_id"]: item for item in manifest["controllers"]}
    controller = controllers.get(key_id)
    if controller is None or controller["role"] != role:
        raise GovernanceError("approval key/role is not a declared controller")
    if controller["public_key"] != public_key_b64(private_key):
        raise GovernanceError("private key does not match the committed controller public key")
    return {
        "key_id": key_id,
        "role": role,
        "manifest_digest": manifest_digest(manifest),
        "signature": sign_manifest(manifest, private_key),
    }


def verify_approvals(
    manifest: Mapping[str, Any],
    approvals: Sequence[Mapping[str, str]],
    public_keys: Mapping[str, Ed25519PublicKey],
    *,
    previous_manifest: Mapping[str, Any] | None = None,
    previous_manifest_hash: str | None = None,
) -> frozenset[str]:
    """Verifica firmas, controller binding, unicidad y umbral de política."""

    validate_manifest(
        manifest,
        previous_manifest=previous_manifest,
        previous_manifest_hash=previous_manifest_hash,
    )
    if not isinstance(approvals, list):
        raise GovernanceError("approvals must be a list")
    expected_digest = manifest_digest(manifest)
    controllers = {item["key_id"]: item for item in manifest["controllers"]}
    seen_keys: set[str] = set()
    seen_public_keys: set[str] = set()
    valid_roles: set[str] = set()

    for approval in approvals:
        if not isinstance(approval, dict) or set(approval) != {
            "key_id",
            "role",
            "manifest_digest",
            "signature",
        }:
            raise GovernanceError("approval has an invalid field set")
        key_id = approval["key_id"]
        role = approval["role"]
        if not all(isinstance(approval[field], str) for field in approval):
            raise GovernanceError("approval fields must be strings")
        if key_id in seen_keys:
            raise GovernanceError("duplicate approval key")
        seen_keys.add(key_id)
        controller = controllers.get(key_id)
        if controller is None or controller["role"] != role:
            raise GovernanceError("approval role does not match controller")
        if key_id not in public_keys:
            raise GovernanceError("public key is unavailable")
        observed_public_key = public_key_b64(public_keys[key_id])
        if observed_public_key != controller["public_key"]:
            raise GovernanceError("public key does not match signed controller commitment")
        if observed_public_key in seen_public_keys:
            raise GovernanceError("one physical key cannot satisfy multiple approval roles")
        seen_public_keys.add(observed_public_key)
        if not hmac.compare_digest(approval["manifest_digest"], expected_digest):
            raise GovernanceError("approval digest does not match manifest")
        if not verify_manifest(manifest, approval["signature"], public_keys[key_id]):
            raise GovernanceError("invalid Ed25519 approval signature")
        valid_roles.add(role)

    missing = required_roles(manifest, previous_manifest) - valid_roles
    if missing:
        raise GovernanceError(f"missing required approval roles: {sorted(missing)}")
    return frozenset(valid_roles)
