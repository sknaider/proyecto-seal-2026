from __future__ import annotations

import copy
import uuid

import pytest

from memory.ssai_shadow.crypto import generate_private_key, manifest_digest, public_key_b64
from memory.ssai_shadow.manifest import (
    ManifestValidationError,
    build_genesis_manifest,
    generate_soul_id,
    validate_manifest,
)

HASHES = {
    "constitution": {
        "document_hash": "sha256:" + "1" * 64,
        "critical_rules_root": "sha256:" + "2" * 64,
        "governance_policy_hash": "sha256:" + "3" * 64,
    },
    "identity_state": {
        "personality_baseline_hash": "sha256:" + "4" * 64,
        "ocean_baseline_hash": "sha256:" + "5" * 64,
        "relationships_root": "sha256:" + "6" * 64,
        "memory_commitment_root": "sha256:" + "7" * 64,
    },
}


def genesis() -> dict[str, object]:
    controller_public_keys = {
        role: public_key_b64(generate_private_key())
        for role in ("genesis_root", "agent_identity", "custodian")
    }
    return build_genesis_manifest(
        display_name="ADA",
        constitution=HASHES["constitution"],
        identity_state=HASHES["identity_state"],
        evidence=["soul-memory:315218"],
        controller_public_keys=controller_public_keys,
        soul_id=generate_soul_id(timestamp_ms=1_721_177_600_000, random_bits=42),
        issued_at="2026-07-16T23:00:00Z",
    )


def test_uuid7_generator_sets_version_and_variant() -> None:
    first = uuid.UUID(generate_soul_id(timestamp_ms=1, random_bits=0))
    second = uuid.UUID(generate_soul_id(timestamp_ms=2, random_bits=0))
    assert first.version == 7
    assert first.variant == uuid.RFC_4122
    assert first.int < second.int


def test_genesis_is_valid_and_has_no_circular_self_hash() -> None:
    manifest = genesis()
    validate_manifest(manifest)
    assert manifest["sequence"] == 1
    assert manifest["previous_manifest_hash"] is None
    assert manifest["genesis_manifest_hash"] is None


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("soul_dni",), "urn:soul:agent:wrong", "derived"),
        (("sequence",), 0, "positive integer"),
        (("previous_manifest_hash",), "sha256:" + "a" * 64, "must be null"),
        (("genesis_manifest_hash",), "sha256:" + "b" * 64, "must be null"),
        (("reason_code",), "EVOLUTION", "GENESIS"),
        (("evidence",), ["dm:ada:william"], "private locator"),
    ],
)
def test_genesis_rejects_contract_violations(
    path: tuple[str, ...], value: object, message: str
) -> None:
    manifest = copy.deepcopy(genesis())
    manifest[path[0]] = value
    with pytest.raises(ManifestValidationError, match=message):
        validate_manifest(manifest)


def test_evolution_requires_strict_chain_continuity() -> None:
    original = genesis()
    previous_hash = manifest_digest(original)
    evolved = copy.deepcopy(original)
    evolved.update(
        sequence=2,
        previous_manifest_hash=previous_hash,
        genesis_manifest_hash=previous_hash,
        reason_code="PERSONALITY_EVOLUTION",
        issued_at="2026-07-17T00:00:00Z",
        effective_at="2026-07-17T00:00:00Z",
    )
    validate_manifest(
        evolved,
        previous_manifest=original,
        previous_manifest_hash=previous_hash,
    )

    evolved["sequence"] = 3
    with pytest.raises(ManifestValidationError, match="increment by one"):
        validate_manifest(
            evolved,
            previous_manifest=original,
            previous_manifest_hash=previous_hash,
        )


def test_timestamps_reject_precision_that_python_cannot_compare_exactly() -> None:
    manifest = genesis()
    manifest["issued_at"] = "2026-07-16T23:00:00.123456789Z"
    manifest["effective_at"] = "2026-07-16T23:00:00.123456789Z"
    with pytest.raises(ManifestValidationError, match="RFC3339 UTC"):
        validate_manifest(manifest)
