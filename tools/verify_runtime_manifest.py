#!/usr/bin/env python3
"""Verifica firma y hashes de un manifest SOUL sin modificar el runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from build_runtime_manifest import runtime_controls, runtime_files, systemd_units

REPO = Path(__file__).resolve().parent.parent


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify(path: Path) -> tuple[bool, list[str]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    expected_signature = manifest.pop("signature_sha256", "")
    payload = json.dumps(manifest, sort_keys=True, ensure_ascii=False).encode()
    actual_signature = hashlib.sha256(payload).hexdigest()
    errors: list[str] = []
    if expected_signature != actual_signature:
        errors.append("manifest_signature_mismatch")

    files = manifest.get("runtime_layer", {}).get("files", {})
    current_files = {str(p.relative_to(REPO)) for p in runtime_files()}
    recorded_files = set(files)
    for relative in sorted(current_files - recorded_files):
        errors.append(f"untracked_runtime:{relative}")
    for relative in sorted(recorded_files - current_files):
        errors.append(f"stale_runtime:{relative}")
    for relative, expected in files.items():
        candidate = REPO / relative
        if not candidate.is_file():
            errors.append(f"missing:{relative}")
            continue
        actual = file_sha256(candidate)
        if actual != expected:
            errors.append(f"hash_mismatch:{relative}")

    recorded_units = manifest.get("systemd_units", {})
    current_units = systemd_units()
    for unit in sorted(set(current_units) - set(recorded_units)):
        errors.append(f"untracked_unit:{unit}")
    for unit in sorted(set(recorded_units) - set(current_units)):
        errors.append(f"stale_unit:{unit}")
    for unit, expected in recorded_units.items():
        if current_units.get(unit) != expected:
            errors.append(f"unit_hash_mismatch:{unit}")

    if manifest.get("runtime_controls") != runtime_controls():
        errors.append("runtime_controls_mismatch")

    migrations = manifest.get("migrations", {})
    if not isinstance(migrations, dict):
        errors.append("legacy_unhashed_migrations")
    else:
        for relative, expected in migrations.items():
            candidate = REPO / relative
            if not candidate.is_file():
                errors.append(f"missing_migration:{relative}")
            elif file_sha256(candidate) != expected:
                errors.append(f"migration_hash_mismatch:{relative}")
    return not errors, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    ok, errors = verify(args.manifest)
    print(f"manifest_ok={str(ok).lower()} errors={len(errors)}")
    for error in errors[:50]:
        print(error)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
