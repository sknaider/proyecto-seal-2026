"""SEAL build tool — package the runtime into a distributable tarball.

Collects seal/, memory/, migrations/, config templates, and the entry
point installer into a deterministic tar.gz with only relative paths.
Produces a SHA-256 checksum sidecar so clients can verify integrity.

Output:
    <output_dir>/seal-<version>-<arch>.tar.gz
    <output_dir>/seal-<version>-<arch>.tar.gz.sha256

Usage:
    python3 -m seal.build --version 1.0.0 --output /tmp/dist
    python3 -m seal.build --version 1.0.0 --dry-run
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import platform
import sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_INCLUDE_DIRS = ("seal", "memory", "migrations")
_DEFAULT_INCLUDE_FILES = ("seal/install.py",)
_DEFAULT_EXCLUDES = (
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*.egg-info",
    ".git",
    ".pytest_cache",
    "*.lock",
    "*.log",
)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class BuildConfig:
    version: str
    source_root: Path
    output_dir: Path
    arch: str = ""
    include_dirs: tuple[str, ...] = _DEFAULT_INCLUDE_DIRS
    include_files: tuple[str, ...] = _DEFAULT_INCLUDE_FILES
    exclude_patterns: tuple[str, ...] = _DEFAULT_EXCLUDES
    include_tests: bool = False
    dry_run: bool = False


@dataclass
class BuildResult:
    tarball: Optional[Path] = None
    checksum_file: Optional[Path] = None
    manifest: list[str] = field(default_factory=list)
    sha256: str = ""
    success: bool = False
    error: str = ""


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def build(config: BuildConfig) -> BuildResult:
    """Run the full build. Returns a BuildResult with paths and manifest."""
    result = BuildResult()

    arch = config.arch or _detect_arch()
    tarball_name = f"seal-{config.version}-{arch}.tar.gz"
    tarball_path = config.output_dir / tarball_name
    checksum_path = config.output_dir / f"{tarball_name}.sha256"

    # Collect file list (relative to source_root)
    try:
        manifest = _collect_manifest(config)
    except Exception as exc:
        result.error = f"manifest error: {exc}"
        return result

    result.manifest = manifest

    if config.dry_run:
        result.success = True
        result.tarball = tarball_path
        result.checksum_file = checksum_path
        return result

    # Write tarball
    config.output_dir.mkdir(parents=True, exist_ok=True)
    top = f"seal-{config.version}"
    try:
        with tarfile.open(tarball_path, "w:gz") as tar:
            for rel in manifest:
                full = config.source_root / rel
                arcname = f"{top}/{rel}"
                tar.add(full, arcname=arcname, recursive=False)
    except Exception as exc:
        result.error = f"tarball write error: {exc}"
        return result

    # Compute SHA-256
    sha = _sha256_file(tarball_path)
    checksum_path.write_text(f"{sha}  {tarball_name}\n")

    result.tarball = tarball_path
    result.checksum_file = checksum_path
    result.sha256 = sha
    result.success = True
    return result


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------


def _collect_manifest(config: BuildConfig) -> list[str]:
    """Return sorted list of relative paths to include in the tarball."""
    entries: list[str] = []

    for dir_name in config.include_dirs:
        dir_path = config.source_root / dir_name
        if not dir_path.exists():
            continue
        for path in sorted(dir_path.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(config.source_root)
            if _should_exclude(rel, config):
                continue
            entries.append(str(rel))

    for file_rel in config.include_files:
        full = config.source_root / file_rel
        if full.exists() and full.is_file():
            rel = full.relative_to(config.source_root)
            entry = str(rel)
            if entry not in entries and not _should_exclude(rel, config):
                entries.append(entry)

    return sorted(entries)


def _should_exclude(rel: Path, config: BuildConfig) -> bool:
    parts = rel.parts
    for pattern in config.exclude_patterns:
        # Match any path component against the pattern
        for part in parts:
            if fnmatch.fnmatch(part, pattern):
                return True
        # Also match full relative path string
        if fnmatch.fnmatch(str(rel), pattern):
            return True
    if not config.include_tests:
        for part in parts:
            if part in ("tests", "test"):
                return True
    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_arch() -> str:
    m = platform.machine().lower()
    if m in ("amd64", "x86_64"):
        return "x86_64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    return m or "unknown"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_tarball(tarball: Path, checksum_file: Path) -> bool:
    """Return True if tarball matches the expected SHA-256 in checksum_file."""
    expected_line = checksum_file.read_text().strip()
    expected_sha = expected_line.split()[0] if expected_line else ""
    actual_sha = _sha256_file(tarball)
    return actual_sha == expected_sha


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="seal build",
        description="Package SEAL runtime into a distributable tarball.",
    )
    p.add_argument("--version", required=True, help="Semantic version string (e.g. 1.0.0)")
    p.add_argument(
        "--output",
        type=Path,
        default=Path("."),
        help="Output directory for tarball and checksum (default: .)",
    )
    p.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).parent.parent,
        help="Root of the SEAL source tree (default: project root)",
    )
    p.add_argument("--arch", default="", help="Override arch string (default: auto-detect)")
    p.add_argument(
        "--include-tests",
        action="store_true",
        help="Include test files in the tarball",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print manifest without writing files",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = BuildConfig(
        version=args.version,
        source_root=args.source_root,
        output_dir=args.output,
        arch=args.arch,
        include_tests=args.include_tests,
        dry_run=args.dry_run,
    )
    result = build(config)

    if args.dry_run:
        print(f"[DRY-RUN] {len(result.manifest)} files would be included:")
        for entry in result.manifest:
            print(f"  {entry}")
        print(f"[DRY-RUN] Output: {result.tarball}")
        return 0

    if result.success:
        print(f"[OK] {result.tarball}")
        print(f"[OK] {result.checksum_file}")
        print(f"[OK] sha256={result.sha256}")
        print(f"[OK] {len(result.manifest)} files")
    else:
        print(f"[FAIL] {result.error}", file=sys.stderr)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
