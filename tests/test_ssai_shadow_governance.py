from __future__ import annotations

import copy

import pytest

from memory.ssai_shadow.crypto import generate_private_key, manifest_digest, public_key_b64
from memory.ssai_shadow.governance import (
    GovernanceError,
    create_approval,
    verify_approvals,
)
from memory.ssai_shadow.manifest import (
    ManifestValidationError,
    build_genesis_manifest,
    generate_soul_id,
)


def sample_manifest():
    private_keys = {
        role: generate_private_key()
        for role in ("genesis_root", "agent_identity", "custodian")
    }
    manifest = build_genesis_manifest(
        display_name="ADA",
        soul_id=generate_soul_id(timestamp_ms=1_721_177_600_000, random_bits=77),
        issued_at="2026-07-16T23:00:00Z",
        constitution={
            "document_hash": "sha256:" + "1" * 64,
            "critical_rules_root": "sha256:" + "2" * 64,
            "governance_policy_hash": "sha256:" + "3" * 64,
        },
        identity_state={
            "personality_baseline_hash": "sha256:" + "4" * 64,
            "ocean_baseline_hash": "sha256:" + "5" * 64,
            "relationships_root": "sha256:" + "6" * 64,
            "memory_commitment_root": "sha256:" + "7" * 64,
        },
        evidence=["soul-memory:315218"],
        controller_public_keys={
            role: public_key_b64(key) for role, key in private_keys.items()
        },
    )
    return manifest, private_keys


def approvals_for(manifest: dict[str, object], private_keys):
    controllers = {item["role"]: item["key_id"] for item in manifest["controllers"]}
    approvals = [
        create_approval(
            manifest,
            key_id=controllers["genesis_root"],
            role="genesis_root",
            private_key=private_keys["genesis_root"],
        ),
        create_approval(
            manifest,
            key_id=controllers["agent_identity"],
            role="agent_identity",
            private_key=private_keys["agent_identity"],
        ),
    ]
    public = {
        controllers[role]: private_keys[role].public_key()
        for role in ("genesis_root", "agent_identity", "custodian")
    }
    return approvals, public


def test_genesis_requires_two_valid_independent_roles() -> None:
    manifest, private_keys = sample_manifest()
    approvals, public = approvals_for(manifest, private_keys)
    assert verify_approvals(manifest, approvals, public) == {
        "genesis_root",
        "agent_identity",
    }


def test_missing_agent_approval_fails_closed() -> None:
    manifest, private_keys = sample_manifest()
    approvals, public = approvals_for(manifest, private_keys)
    with pytest.raises(GovernanceError, match="missing required"):
        verify_approvals(manifest, approvals[:1], public)


def test_signature_cannot_be_replayed_after_one_byte_change() -> None:
    manifest, private_keys = sample_manifest()
    approvals, public = approvals_for(manifest, private_keys)
    tampered = copy.deepcopy(manifest)
    tampered["display_name"] = "EVA"
    with pytest.raises(GovernanceError, match="digest does not match"):
        verify_approvals(tampered, approvals, public)


def test_declared_role_cannot_be_spoofed() -> None:
    manifest, private_keys = sample_manifest()
    approvals, public = approvals_for(manifest, private_keys)
    approvals[0]["role"] = "agent_identity"
    with pytest.raises(GovernanceError, match="does not match controller"):
        verify_approvals(manifest, approvals, public)


def test_normal_evolution_accepts_agent_identity_only_with_chain_context() -> None:
    original, private_keys = sample_manifest()
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
    agent_controller = next(
        item for item in evolved["controllers"] if item["role"] == "agent_identity"
    )
    agent_private = private_keys["agent_identity"]
    approval = create_approval(
        evolved,
        key_id=agent_controller["key_id"],
        role="agent_identity",
        private_key=agent_private,
        previous_manifest=original,
        previous_manifest_hash=previous_hash,
    )
    assert verify_approvals(
        evolved,
        [approval],
        {agent_controller["key_id"]: agent_private.public_key()},
        previous_manifest=original,
        previous_manifest_hash=previous_hash,
    ) == {"agent_identity"}


def test_same_physical_key_cannot_fill_two_controller_roles() -> None:
    duplicated = generate_private_key()
    with pytest.raises(ManifestValidationError, match="physical public keys"):
        build_genesis_manifest(
            display_name="ADA",
            soul_id=generate_soul_id(timestamp_ms=1_721_177_600_000, random_bits=88),
            issued_at="2026-07-16T23:00:00Z",
            constitution={
                "document_hash": "sha256:" + "1" * 64,
                "critical_rules_root": "sha256:" + "2" * 64,
                "governance_policy_hash": "sha256:" + "3" * 64,
            },
            identity_state={
                "personality_baseline_hash": "sha256:" + "4" * 64,
                "ocean_baseline_hash": "sha256:" + "5" * 64,
                "relationships_root": "sha256:" + "6" * 64,
                "memory_commitment_root": "sha256:" + "7" * 64,
            },
            evidence=["shadow-fixture:test"],
            controller_public_keys={
                "genesis_root": public_key_b64(duplicated),
                "agent_identity": public_key_b64(duplicated),
                "custodian": public_key_b64(generate_private_key()),
            },
        )


def test_external_public_key_substitution_is_rejected() -> None:
    manifest, private_keys = sample_manifest()
    approvals, public = approvals_for(manifest, private_keys)
    root_id = next(
        item["key_id"] for item in manifest["controllers"] if item["role"] == "genesis_root"
    )
    public[root_id] = generate_private_key().public_key()
    with pytest.raises(GovernanceError, match="signed controller commitment"):
        verify_approvals(manifest, approvals, public)


def test_signer_private_key_must_match_committed_controller() -> None:
    manifest, _ = sample_manifest()
    agent = next(
        item for item in manifest["controllers"] if item["role"] == "agent_identity"
    )
    with pytest.raises(GovernanceError, match="does not match the committed"):
        create_approval(
            manifest,
            key_id=agent["key_id"],
            role="agent_identity",
            private_key=generate_private_key(),
        )


def test_constitutional_diff_cannot_hide_behind_evolution_reason() -> None:
    original, private_keys = sample_manifest()
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
    evolved["constitution"]["document_hash"] = "sha256:" + "9" * 64
    agent = next(
        item for item in evolved["controllers"] if item["role"] == "agent_identity"
    )
    approval = create_approval(
        evolved,
        key_id=agent["key_id"],
        role="agent_identity",
        private_key=private_keys["agent_identity"],
        previous_manifest=original,
        previous_manifest_hash=previous_hash,
    )
    with pytest.raises(GovernanceError, match="constitutional field changes"):
        verify_approvals(
            evolved,
            [approval],
            {agent["key_id"]: private_keys["agent_identity"].public_key()},
            previous_manifest=original,
            previous_manifest_hash=previous_hash,
        )


def test_real_constitutional_diff_requires_custodian_and_agent() -> None:
    original, private_keys = sample_manifest()
    previous_hash = manifest_digest(original)
    evolved = copy.deepcopy(original)
    evolved.update(
        sequence=2,
        previous_manifest_hash=previous_hash,
        genesis_manifest_hash=previous_hash,
        reason_code="CONSTITUTION_CHANGE",
        issued_at="2026-07-17T00:00:00Z",
        effective_at="2026-07-17T00:00:00Z",
    )
    evolved["constitution"]["document_hash"] = "sha256:" + "9" * 64
    controllers = {item["role"]: item for item in evolved["controllers"]}
    approvals = [
        create_approval(
            evolved,
            key_id=controllers[role]["key_id"],
            role=role,
            private_key=private_keys[role],
            previous_manifest=original,
            previous_manifest_hash=previous_hash,
        )
        for role in ("agent_identity", "custodian")
    ]
    public = {
        controllers[role]["key_id"]: private_keys[role].public_key()
        for role in ("agent_identity", "custodian")
    }
    assert verify_approvals(
        evolved,
        approvals,
        public,
        previous_manifest=original,
        previous_manifest_hash=previous_hash,
    ) == {"agent_identity", "custodian"}


def test_evolution_rejects_fake_previous_hash_and_time_rollback() -> None:
    original, private_keys = sample_manifest()
    real_hash = manifest_digest(original)
    evolved = copy.deepcopy(original)
    evolved.update(
        sequence=2,
        previous_manifest_hash="sha256:" + "f" * 64,
        genesis_manifest_hash="sha256:" + "f" * 64,
        reason_code="PERSONALITY_EVOLUTION",
        issued_at="2026-07-16T22:00:00Z",
        effective_at="2026-07-16T22:00:00Z",
    )
    agent = next(
        item for item in evolved["controllers"] if item["role"] == "agent_identity"
    )
    with pytest.raises(ManifestValidationError, match="does not match previous manifest"):
        create_approval(
            evolved,
            key_id=agent["key_id"],
            role="agent_identity",
            private_key=private_keys["agent_identity"],
            previous_manifest=original,
            previous_manifest_hash="sha256:" + "f" * 64,
        )

    evolved["previous_manifest_hash"] = real_hash
    evolved["genesis_manifest_hash"] = real_hash
    with pytest.raises(ManifestValidationError, match="timestamps must be monotonic"):
        create_approval(
            evolved,
            key_id=agent["key_id"],
            role="agent_identity",
            private_key=private_keys["agent_identity"],
            previous_manifest=original,
            previous_manifest_hash=real_hash,
        )


def test_evolution_cannot_replace_controller_keyset_in_m1() -> None:
    original, private_keys = sample_manifest()
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
    evolved["controllers"][1]["public_key"] = public_key_b64(generate_private_key())
    agent = evolved["controllers"][1]
    with pytest.raises(ManifestValidationError, match="controller rotation is unsupported"):
        create_approval(
            evolved,
            key_id=agent["key_id"],
            role="agent_identity",
            private_key=private_keys["agent_identity"],
            previous_manifest=original,
            previous_manifest_hash=previous_hash,
        )


def test_evolution_cannot_redeclare_itself_as_genesis() -> None:
    original, private_keys = sample_manifest()
    previous_hash = manifest_digest(original)
    evolved = copy.deepcopy(original)
    evolved.update(
        sequence=2,
        previous_manifest_hash=previous_hash,
        genesis_manifest_hash=previous_hash,
        reason_code="GENESIS",
        issued_at="2026-07-17T00:00:00Z",
        effective_at="2026-07-17T00:00:00Z",
    )
    agent = next(
        item for item in evolved["controllers"] if item["role"] == "agent_identity"
    )
    with pytest.raises(ManifestValidationError, match="only valid at sequence 1"):
        create_approval(
            evolved,
            key_id=agent["key_id"],
            role="agent_identity",
            private_key=private_keys["agent_identity"],
            previous_manifest=original,
            previous_manifest_hash=previous_hash,
        )
