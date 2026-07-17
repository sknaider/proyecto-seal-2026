"""Verificación semántica y criptográfica de artefactos SSAI SHADOW."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from .crypto import manifest_digest
from .governance import GovernanceError, verify_approvals
from .ledger import ShadowLedger, WitnessStore
from .manifest import ManifestValidationError


@dataclass(frozen=True, slots=True)
class IdentityVerificationResult:
    ok: bool
    errors: tuple[str, ...]
    soul_id: str | None
    soul_dni: str | None
    display_name: str | None
    sequence: int
    head_hash: str
    manifest_digest: str | None
    approval_roles: tuple[str, ...]
    public_keys: dict[str, str]
    trust_anchor_verified: bool


def verify_identity_ledger(
    ledger: ShadowLedger,
    witness: WitnessStore,
    *,
    trusted_genesis_public_key: str | None = None,
    advance_witness: bool = False,
) -> IdentityVerificationResult:
    """Verify chain, witness, manifests, key bindings, signatures and policy.

    A stale witness is fail-closed by default. ``advance_witness=True`` promotes
    it only after the complete new suffix has passed semantic verification.
    """

    snapshot = ledger.snapshot()
    ledger_result = snapshot.verification
    empty = IdentityVerificationResult(
        False,
        (),
        None,
        None,
        None,
        ledger_result.sequence,
        ledger_result.head_hash,
        None,
        (),
        {},
        False,
    )
    if not ledger_result.ok:
        return replace(empty, errors=("ledger: " + "; ".join(ledger_result.errors),))
    witness_result = witness.verify(ledger_result)
    if not witness_result.ok:
        return replace(empty, errors=("witness: " + "; ".join(witness_result.errors),))
    witness_lagging = witness_result.sequence < ledger_result.sequence

    records = snapshot.records
    if not records:
        return replace(empty, errors=("identity ledger is empty",))

    previous_manifest: Mapping[str, Any] | None = None
    previous_hash: str | None = None
    genesis_soul_id: str | None = None
    last_manifest: Mapping[str, Any] | None = None
    last_roles: frozenset[str] = frozenset()
    last_public_keys: dict[str, str] = {}
    trust_anchor_verified = False

    try:
        for record in records:
            event = record.event
            expected_fields = {
                "type",
                "assurance",
                "manifest",
                "manifest_digest",
                "approvals",
                "public_keys",
            }
            if set(event) != expected_fields:
                raise ValueError("identity event has missing or unexpected fields")
            if event["type"] != "IDENTITY_MANIFEST_ACCEPTED":
                raise ValueError("unsupported identity event type")
            if event["assurance"] != "SHADOW_TEST_ONLY":
                raise ValueError("unsupported event assurance")
            manifest = event["manifest"]
            if not isinstance(manifest, dict):
                raise ValueError("manifest must be an object")
            if manifest.get("sequence") != record.sequence:
                raise ValueError("manifest sequence must match ledger sequence")
            if genesis_soul_id is None:
                genesis_soul_id = manifest.get("soul_id")
            elif manifest.get("soul_id") != genesis_soul_id:
                raise ValueError("one ledger cannot mix multiple soul_id values")

            digest = manifest_digest(manifest)
            if event["manifest_digest"] != digest:
                raise ValueError("persisted manifest_digest mismatch")
            committed_public_keys = {
                item["key_id"]: item["public_key"] for item in manifest["controllers"]
            }
            if event["public_keys"] != committed_public_keys:
                raise ValueError("event public_keys differ from signed controller commitments")
            last_roles = verify_approvals(
                manifest,
                event["approvals"],
                committed_public_keys,
                previous_manifest=previous_manifest,
                previous_manifest_hash=previous_hash,
            )
            if record.sequence == 1 and trusted_genesis_public_key is not None:
                root = next(
                    item for item in manifest["controllers"] if item["role"] == "genesis_root"
                )
                if root["public_key"] != trusted_genesis_public_key:
                    raise ValueError("genesis root does not match external trust anchor")
                trust_anchor_verified = True
            previous_manifest = manifest
            previous_hash = digest
            last_manifest = manifest
            last_public_keys = committed_public_keys
    except (GovernanceError, ManifestValidationError, KeyError, TypeError, ValueError) as exc:
        return IdentityVerificationResult(
            False,
            (str(exc),),
            genesis_soul_id,
            None,
            None,
            ledger_result.sequence,
            ledger_result.head_hash,
            previous_hash,
            tuple(sorted(last_roles)),
            last_public_keys,
            False,
        )

    assert last_manifest is not None
    final_result = IdentityVerificationResult(
        True,
        (),
        last_manifest["soul_id"],
        last_manifest["soul_dni"],
        last_manifest["display_name"],
        ledger_result.sequence,
        ledger_result.head_hash,
        previous_hash,
        tuple(sorted(last_roles)),
        last_public_keys,
        trust_anchor_verified,
    )
    if witness_lagging and not advance_witness:
        return replace(
            final_result,
            ok=False,
            errors=(
                f"witness high-water is behind verified ledger "
                f"({witness_result.sequence} < {ledger_result.sequence})",
            ),
        )
    if witness_lagging:
        witness.record(ledger_result.sequence, ledger_result.head_hash)
        promoted = witness.verify(ledger_result)
        if not promoted.ok or promoted.sequence != ledger_result.sequence:
            return replace(
                final_result,
                ok=False,
                errors=("failed to promote witness high-water",),
            )
    return final_result


def public_report_from_result(result: IdentityVerificationResult) -> dict[str, Any]:
    if not result.ok:
        raise ValueError("cannot report an invalid identity ledger")
    return {
        "mode": "SHADOW",
        "assurance": "TOFU_UNANCHORED"
        if not result.trust_anchor_verified
        else "EXTERNALLY_ANCHORED",
        "production_activated": False,
        "private_keys_persisted": False,
        "display_name": result.display_name,
        "soul_dni": result.soul_dni,
        "manifest_digest": result.manifest_digest,
        "approval_roles": list(result.approval_roles),
        "ledger_sequence": result.sequence,
        "ledger_head_hash": result.head_hash,
        "witness_verified": True,
        "trust_anchor_verified": result.trust_anchor_verified,
        "public_keys": result.public_keys,
        "artifacts": {
            "ledger": "identity-ledger.jsonl",
            "witness": "external-witness.json",
            "report": "public-report.json",
        },
    }


def verify_demo_artifacts(output_dir: str | Path) -> IdentityVerificationResult:
    output = Path(output_dir)
    result = verify_identity_ledger(
        ShadowLedger(output / "identity-ledger.jsonl"),
        WitnessStore(output / "external-witness.json"),
    )
    if not result.ok:
        return result
    try:
        stored_report = json.loads((output / "public-report.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return replace(result, ok=False, errors=(f"public report: {exc}",))
    if stored_report != public_report_from_result(result):
        return replace(
            result,
            ok=False,
            errors=("public report does not match verified ledger state",),
        )
    return result
