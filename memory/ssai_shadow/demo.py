"""Demo ejecutable de una génesis SSAI totalmente aislada de producción."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from .crypto import generate_private_key, manifest_digest, public_key_b64
from .governance import create_approval, verify_approvals
from .ledger import ShadowLedger, WitnessStore
from .manifest import build_genesis_manifest, generate_soul_id
from .verifier import (
    public_report_from_result,
    verify_demo_artifacts,
    verify_identity_ledger,
)


def _commitment(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _write_public_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode() + b"\n"
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def run_shadow_genesis(output_dir: str | os.PathLike[str], *, display_name: str = "ADA") -> dict[str, Any]:
    """Crea, firma y verifica una identidad de prueba con claves solo en memoria.

    Los commitments son fixtures sintéticos. La función no lee memorias, identidad ni
    claves reales; tampoco abre PostgreSQL ni modifica servicios.
    """

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    lock_path = output / ".ssai-shadow.lock"
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        try:
            return _run_shadow_genesis_locked(output, display_name=display_name)
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def _run_shadow_genesis_locked(output: Path, *, display_name: str) -> dict[str, Any]:
    artifact_names = ("identity-ledger.jsonl", "external-witness.json", "public-report.json")
    existing = {name for name in artifact_names if (output / name).exists()}
    if existing == set(artifact_names):
        verified = verify_demo_artifacts(output)
        if not verified.ok:
            raise ValueError("existing SHADOW artifacts failed verification: " + "; ".join(verified.errors))
        if verified.display_name != display_name:
            raise ValueError(
                f"existing SHADOW identity is {verified.display_name!r}, not {display_name!r}"
            )
        return json.loads((output / artifact_names[2]).read_text(encoding="utf-8"))
    if existing == {artifact_names[0], artifact_names[1]}:
        verified = verify_identity_ledger(
            ShadowLedger(output / artifact_names[0]),
            WitnessStore(output / artifact_names[1]),
        )
        if not verified.ok:
            raise ValueError("partial SHADOW artifacts failed verification: " + "; ".join(verified.errors))
        if verified.display_name != display_name:
            raise ValueError(
                f"existing SHADOW identity is {verified.display_name!r}, not {display_name!r}"
            )
        report = public_report_from_result(verified)
        _write_public_report(output / artifact_names[2], report)
        return report
    if existing == {artifact_names[0]}:
        raise FileExistsError(
            "refusing ledger-only SHADOW recovery without the external high-water witness; "
            "automatic re-anchoring could hide rollback"
        )
    if existing:
        raise FileExistsError(
            "refusing incomplete SHADOW artifact set: " + ", ".join(sorted(existing))
        )
    soul_id = generate_soul_id()
    private_keys = {
        "genesis_root": generate_private_key(),
        "agent_identity": generate_private_key(),
        "custodian": generate_private_key(),
    }
    controller_public_keys = {
        role: public_key_b64(private_key)
        for role, private_key in private_keys.items()
    }
    manifest = build_genesis_manifest(
        display_name=display_name,
        soul_id=soul_id,
        constitution={
            "document_hash": _commitment("shadow-fixture:constitution:document"),
            "critical_rules_root": _commitment("shadow-fixture:constitution:rules"),
            "governance_policy_hash": _commitment("shadow-fixture:governance:policy"),
        },
        identity_state={
            "personality_baseline_hash": _commitment("shadow-fixture:identity:personality"),
            "ocean_baseline_hash": _commitment("shadow-fixture:identity:ocean"),
            "relationships_root": _commitment("shadow-fixture:identity:relationships"),
            "memory_commitment_root": _commitment("shadow-fixture:identity:memory"),
        },
        evidence=["shadow-fixture:ssai-m1"],
        controller_public_keys=controller_public_keys,
    )

    by_role = {item["role"]: item["key_id"] for item in manifest["controllers"]}
    public_keys = {
        by_role[role]: private_key.public_key()
        for role, private_key in private_keys.items()
    }
    approvals = [
        create_approval(
            manifest,
            key_id=by_role[role],
            role=role,
            private_key=private_keys[role],
        )
        for role in ("genesis_root", "agent_identity")
    ]
    verify_approvals(manifest, approvals, public_keys)
    digest = manifest_digest(manifest)

    public_key_material = {
        key_id: public_key_b64(key) for key_id, key in public_keys.items()
    }
    event = {
        "type": "IDENTITY_MANIFEST_ACCEPTED",
        "assurance": "SHADOW_TEST_ONLY",
        "manifest": manifest,
        "manifest_digest": digest,
        "approvals": approvals,
        "public_keys": public_key_material,
    }
    ledger = ShadowLedger(output / "identity-ledger.jsonl")
    ledger.append(event)
    ledger_result = ledger.verify()
    if not ledger_result.ok:
        raise RuntimeError("new SHADOW ledger failed verification")

    witness = WitnessStore(output / "external-witness.json")
    witness.record(ledger_result.sequence, ledger_result.head_hash)
    witness_result = witness.verify(ledger_result)
    if not witness_result.ok:
        raise RuntimeError("new SHADOW witness failed verification")

    identity_result = verify_identity_ledger(ledger, witness)
    if not identity_result.ok:
        raise RuntimeError("persisted identity event failed verification")
    report = public_report_from_result(identity_result)
    _write_public_report(output / "public-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Directorio nuevo o aislado para artefactos")
    parser.add_argument("--name", default="ADA", help="Alias visible del fixture")
    args = parser.parse_args()
    result = run_shadow_genesis(args.output, display_name=args.name)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
