from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json

import pytest

from memory.ssai_shadow import demo as demo_module
from memory.ssai_shadow.crypto import generate_private_key, manifest_digest
from memory.ssai_shadow.demo import run_shadow_genesis
from memory.ssai_shadow.governance import create_approval
from memory.ssai_shadow.ledger import ShadowLedger, WitnessStore
from memory.ssai_shadow.verifier import verify_demo_artifacts, verify_identity_ledger


def test_demo_creates_verified_public_artifacts_without_private_keys(tmp_path) -> None:
    report = run_shadow_genesis(tmp_path)

    assert report["mode"] == "SHADOW"
    assert report["assurance"] == "TOFU_UNANCHORED"
    assert report["production_activated"] is False
    assert report["private_keys_persisted"] is False
    assert report["approval_roles"] == ["agent_identity", "genesis_root"]
    assert report["ledger_sequence"] == 1
    assert report["witness_verified"] is True
    assert report["trust_anchor_verified"] is False

    ledger_result = ShadowLedger(tmp_path / "identity-ledger.jsonl").verify()
    assert ledger_result.ok
    assert WitnessStore(tmp_path / "external-witness.json").verify(ledger_result).ok

    combined = b"".join(path.read_bytes() for path in tmp_path.iterdir())
    assert b"PRIVATE KEY" not in combined
    assert b'"private_key":' not in combined
    assert b'"private_key_b64":' not in combined
    assert b"postgresql://" not in combined
    assert b"dm:ada:william" not in combined


def test_public_report_matches_verified_ledger_head(tmp_path) -> None:
    expected = run_shadow_genesis(tmp_path)
    stored = json.loads((tmp_path / "public-report.json").read_text())
    ledger = ShadowLedger(tmp_path / "identity-ledger.jsonl").verify()
    assert stored == expected
    assert stored["ledger_head_hash"] == ledger.head_hash


def test_demo_is_idempotent_for_an_existing_verified_genesis(tmp_path) -> None:
    first = run_shadow_genesis(tmp_path)
    before = (tmp_path / "identity-ledger.jsonl").read_bytes()
    assert run_shadow_genesis(tmp_path) == first
    assert (tmp_path / "identity-ledger.jsonl").read_bytes() == before


def test_demo_rejects_an_incomplete_unverifiable_artifact_set(tmp_path) -> None:
    (tmp_path / "identity-ledger.jsonl").write_text("partial")
    with pytest.raises(FileExistsError, match="ledger-only SHADOW recovery"):
        run_shadow_genesis(tmp_path)


def test_demo_recovers_report_after_post_witness_interruption(tmp_path) -> None:
    expected = run_shadow_genesis(tmp_path)
    (tmp_path / "public-report.json").unlink()
    recovered = run_shadow_genesis(tmp_path)
    assert recovered == expected
    assert verify_demo_artifacts(tmp_path).ok


def test_demo_fails_closed_after_pre_witness_interruption(tmp_path) -> None:
    run_shadow_genesis(tmp_path)
    (tmp_path / "external-witness.json").unlink()
    (tmp_path / "public-report.json").unlink()
    with pytest.raises(FileExistsError, match="automatic re-anchoring could hide rollback"):
        run_shadow_genesis(tmp_path)


def test_demo_cannot_reanchor_rolled_back_ledger_after_witness_deletion(tmp_path) -> None:
    run_shadow_genesis(tmp_path)
    ledger_path = tmp_path / "identity-ledger.jsonl"
    genesis_only = ledger_path.read_bytes().split(b"\n", 1)[0] + b"\n"

    # La pérdida del witness hace indistinguible un crash legítimo de un rollback.
    # M1 debe fallar cerrado y exigir recuperación/break-glass fuera de esta demo.
    ledger_path.write_bytes(genesis_only)
    (tmp_path / "external-witness.json").unlink()
    (tmp_path / "public-report.json").unlink()
    with pytest.raises(FileExistsError, match="automatic re-anchoring could hide rollback"):
        run_shadow_genesis(tmp_path)


def test_demo_rejects_conflicting_display_name_on_idempotent_run(tmp_path) -> None:
    run_shadow_genesis(tmp_path, display_name="ADA")
    with pytest.raises(ValueError, match="not 'EVE'"):
        run_shadow_genesis(tmp_path, display_name="EVE")


def test_tampered_public_report_is_detected_against_verified_ledger(tmp_path) -> None:
    run_shadow_genesis(tmp_path)
    report_path = tmp_path / "public-report.json"
    report = json.loads(report_path.read_text())
    report["trust_anchor_verified"] = True
    report_path.write_text(json.dumps(report))
    result = verify_demo_artifacts(tmp_path)
    assert not result.ok
    assert "does not match verified ledger" in result.errors[0]


def test_valid_hash_chain_with_invalid_signature_is_rejected_semantically(tmp_path) -> None:
    source = tmp_path / "source"
    run_shadow_genesis(source)
    event = ShadowLedger(source / "identity-ledger.jsonl").read_records()[0].event
    event["approvals"][0]["signature"] = "A" * 86

    forged = tmp_path / "forged"
    ledger = ShadowLedger(forged / "identity-ledger.jsonl")
    ledger.append(event)
    result = ledger.verify()
    witness = WitnessStore(forged / "external-witness.json")
    witness.record(result.sequence, result.head_hash)
    semantic = verify_identity_ledger(ledger, witness)
    assert not semantic.ok
    assert "invalid Ed25519" in semantic.errors[0]


def test_external_root_pin_upgrades_trust_or_rejects_mismatch(tmp_path) -> None:
    run_shadow_genesis(tmp_path)
    ledger = ShadowLedger(tmp_path / "identity-ledger.jsonl")
    witness = WitnessStore(tmp_path / "external-witness.json")
    manifest = ledger.read_records()[0].event["manifest"]
    root_key = next(
        item["public_key"]
        for item in manifest["controllers"]
        if item["role"] == "genesis_root"
    )
    anchored = verify_identity_ledger(
        ledger, witness, trusted_genesis_public_key=root_key
    )
    assert anchored.ok
    assert anchored.trust_anchor_verified is True

    mismatched = verify_identity_ledger(
        ledger, witness, trusted_genesis_public_key="A" * 43
    )
    assert not mismatched.ok
    assert "external trust anchor" in mismatched.errors[0]


def test_semantic_verifier_does_not_reopen_records_after_snapshot(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_shadow_genesis(tmp_path)
    ledger = ShadowLedger(tmp_path / "identity-ledger.jsonl")
    witness = WitnessStore(tmp_path / "external-witness.json")

    def forbidden_second_read():
        raise AssertionError("read_records must not be called")

    monkeypatch.setattr(ledger, "read_records", forbidden_second_read)
    assert verify_identity_ledger(ledger, witness).ok


def test_stale_witness_requires_explicit_semantic_promotion_and_blocks_rollback(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retained = {
        role: generate_private_key()
        for role in ("genesis_root", "agent_identity", "custodian")
    }
    generated = iter(retained.values())
    monkeypatch.setattr(demo_module, "generate_private_key", lambda: next(generated))
    run_shadow_genesis(tmp_path)

    ledger = ShadowLedger(tmp_path / "identity-ledger.jsonl")
    witness = WitnessStore(tmp_path / "external-witness.json")
    original = ledger.read_records()[0].event["manifest"]
    previous_hash = manifest_digest(original)
    evolved = copy.deepcopy(original)
    # The evolution must be monotonic relative to the generated genesis.  A fixed
    # wall-clock instant made this test expire once real time passed that date.
    genesis_instant = datetime.fromisoformat(
        original["issued_at"].removesuffix("Z") + "+00:00"
    ).astimezone(timezone.utc)
    evolved_instant = (genesis_instant + timedelta(seconds=1)).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    evolved.update(
        sequence=2,
        previous_manifest_hash=previous_hash,
        genesis_manifest_hash=previous_hash,
        reason_code="PERSONALITY_EVOLUTION",
        issued_at=evolved_instant,
        effective_at=evolved_instant,
    )
    evolved["identity_state"]["ocean_baseline_hash"] = "sha256:" + "8" * 64
    agent = next(
        item for item in evolved["controllers"] if item["role"] == "agent_identity"
    )
    approval = create_approval(
        evolved,
        key_id=agent["key_id"],
        role="agent_identity",
        private_key=retained["agent_identity"],
        previous_manifest=original,
        previous_manifest_hash=previous_hash,
    )
    ledger.append(
        {
            "type": "IDENTITY_MANIFEST_ACCEPTED",
            "assurance": "SHADOW_TEST_ONLY",
            "manifest": evolved,
            "manifest_digest": manifest_digest(evolved),
            "approvals": [approval],
            "public_keys": {
                item["key_id"]: item["public_key"] for item in evolved["controllers"]
            },
        }
    )

    stale = verify_identity_ledger(ledger, witness)
    assert not stale.ok
    assert "high-water is behind" in stale.errors[0]
    promoted = verify_identity_ledger(ledger, witness, advance_witness=True)
    assert promoted.ok
    assert witness.read().sequence == 2

    first_line = ledger.path.read_bytes().split(b"\n", 1)[0] + b"\n"
    ledger.path.write_bytes(first_line)
    rolled_back = verify_identity_ledger(ledger, witness)
    assert not rolled_back.ok
    assert "rollback detected" in rolled_back.errors[0]
