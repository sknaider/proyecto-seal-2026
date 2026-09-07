#!/usr/bin/env python3
"""
SEAL Round-Trip Validator (DELEGATE-52 Acción 1)

Protege contra corrupción silenciosa en refactors masivos.
Antes de cualquier refactor que toque >5 archivos O archivos críticos:
  1. SHA256 snapshot pre-refactor
  2. Aplicar forward (refactor real)
  3. Aplicar backward (revertir)
  4. SHA256 snapshot post-roundtrip
  5. Si difieren → archivo corrupto detectado → ALERT

Referencia: arXiv 2604.15597 — DELEGATE-52 (Laban et al., 2026)
"""
import hashlib
import subprocess
import json
import fnmatch
from pathlib import Path
from typing import Callable

CRITICAL_PATHS_CONFIG = Path(__file__).parent / "critical_paths.yaml"


def sha256_file(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot_files(paths: list[str]) -> dict:
    return {p: sha256_file(p) for p in paths if Path(p).exists()}


def load_critical_paths() -> dict:
    try:
        import yaml
        with open(CRITICAL_PATHS_CONFIG) as f:
            return yaml.safe_load(f)
    except Exception:
        return {"paths": [], "bulk_refactor_threshold": 5}


def is_critical_path(path: str) -> bool:
    """Check if path matches any critical path pattern (glob/fnmatch-style)."""
    config = load_critical_paths()
    p = Path(path).resolve()
    for pattern in config.get("paths", []):
        if str(p) == pattern or fnmatch.fnmatch(str(p), pattern) or p.match(pattern):
            return True
    return False


def should_validate(paths: list[str]) -> bool:
    """Return True if round-trip validation is required for these paths."""
    config = load_critical_paths()
    threshold = config.get("bulk_refactor_threshold", 5)
    if len(paths) > threshold:
        return True
    return any(is_critical_path(p) for p in paths)


def validate_roundtrip(
    paths: list[str],
    forward_fn: Callable,
    backward_fn: Callable,
    refactor_id: str,
) -> dict:
    """
    Ejecuta forward + backward sobre paths y verifica integridad.

    Args:
        paths: Lista de archivos a validar.
        forward_fn: Función que aplica el refactor.
        backward_fn: Función que revierte el refactor.
        refactor_id: Identificador del refactor para logging.

    Returns:
        {'ok': bool, 'corrupted': list[str], 'clean': list[str], 'refactor_id': str}
    """
    before = snapshot_files(paths)

    try:
        forward_fn()
    except Exception as e:
        return {
            "ok": False,
            "error": f"forward_fn failed: {e}",
            "corrupted": [],
            "clean": [],
            "refactor_id": refactor_id,
        }

    try:
        backward_fn()
    except Exception as e:
        return {
            "ok": False,
            "error": f"backward_fn failed: {e}",
            "corrupted": [],
            "clean": [],
            "refactor_id": refactor_id,
        }

    after = snapshot_files(paths)

    corrupted = [p for p in paths if before.get(p) != after.get(p)]
    clean = [p for p in paths if p not in corrupted]

    result = {
        "ok": len(corrupted) == 0,
        "corrupted": corrupted,
        "clean": clean,
        "refactor_id": refactor_id,
    }

    if corrupted:
        print(f"[ROUNDTRIP ALERT] refactor_id={refactor_id} — {len(corrupted)} archivos corruptos:")
        for p in corrupted:
            print(f"  CORRUPTED: {p}")
            print(f"    before: {before.get(p, 'MISSING')}")
            print(f"    after:  {after.get(p, 'MISSING')}")
    else:
        print(f"[ROUNDTRIP OK] refactor_id={refactor_id} — {len(clean)} archivos limpios")

    return result


def git_stash_snapshot(refactor_id: str) -> str:
    """Create a git stash snapshot before a refactor. Returns stash ref."""
    result = subprocess.run(
        ["git", "stash", "push", "-m", f"ROUND-TRIP-PRE refactor_id={refactor_id}"],
        capture_output=True, text=True
    )
    return result.stdout.strip()


if __name__ == "__main__":
    # Quick self-test: roundtrip of a temp file
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("original content\n")
        tmp = f.name

    def fwd():
        Path(tmp).write_text("modified content\n")

    def bwd():
        Path(tmp).write_text("original content\n")

    result = validate_roundtrip([tmp], fwd, bwd, refactor_id="self_test")
    os.unlink(tmp)

    assert result["ok"], f"Self-test failed: {result}"
    print("\nSelf-test PASSED")
