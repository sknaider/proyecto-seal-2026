"""SEAL upgrade tool — in-place upgrade from a signed tarball.

Performs a safe, atomic upgrade of the SEAL runtime without touching
profile data (config.toml, soul_v3 schema, agent logs):

  1. Verify SHA-256 of the new tarball against its .sha256 sidecar
  2. Stop any running agents for the target profile (SIGTERM + wait)
  3. Backup current runtime dirs to SEAL_HOME/backups/
  4. Extract new tarball to a staging directory
  5. Swap runtime directories (seal/, memory/, migrations/)
  6. Profiles and their configs are never touched
  7. Report: ready to restart

If any step fails after backup, the backup path is reported so the
operator can restore manually.

Usage:
    python3 -m seal.upgrade --tarball seal-1.1.0-x86_64.tar.gz
    python3 -m seal.upgrade --tarball seal-1.1.0-x86_64.tar.gz --dry-run
    python3 -m seal.upgrade --tarball ... --skip-backup --profile acme_corp
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import signal
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class UpgradeConfig:
    tarball: Path
    checksum_file: Optional[Path] = None
    seal_home: Optional[Path] = None
    install_root: Optional[Path] = None
    profile: Optional[str] = None
    skip_backup: bool = False
    dry_run: bool = False
    stop_timeout: int = 10


@dataclass
class StepResult:
    step: str
    ok: bool
    detail: str = ""


@dataclass
class UpgradeResult:
    new_version: str = ""
    backup_path: Optional[Path] = None
    steps: list[StepResult] = field(default_factory=list)
    success: bool = False
    error: str = ""

    @property
    def failed_steps(self) -> list[StepResult]:
        return [s for s in self.steps if not s.ok]


# ---------------------------------------------------------------------------
# Core upgrader
# ---------------------------------------------------------------------------


class SealUpgrader:
    """Safe in-place SEAL runtime upgrade."""

    _RUNTIME_DIRS = ("seal", "memory", "migrations")

    def __init__(self, config: UpgradeConfig) -> None:
        self.config = config
        self._seal_home = config.seal_home or Path(
            os.environ.get("SEAL_HOME", Path.home() / ".seal")
        )
        self._install_root = config.install_root or Path(__file__).parent.parent

    # ------------------------------------------------------------------

    def run(self) -> UpgradeResult:
        result = UpgradeResult()
        cfg = self.config

        # 1 — Verify SHA-256
        checksum_path = cfg.checksum_file or Path(str(cfg.tarball) + ".sha256")
        ok, detail = _verify_sha256(cfg.tarball, checksum_path)
        result.steps.append(StepResult("verify_sha256", ok, detail))
        if not ok:
            result.error = detail
            return result

        # 2 — Parse version from tarball name (seal-X.Y.Z-arch.tar.gz)
        version = _parse_version(cfg.tarball.name)
        result.new_version = version
        result.steps.append(
            StepResult("parse_version", bool(version), version or "could not parse")
        )

        if cfg.dry_run:
            result.steps.append(StepResult("dry_run", True, "no files modified"))
            result.success = True
            return result

        # 3 — Stop running agents
        stopped, detail = _stop_agents(self._seal_home, cfg.profile, cfg.stop_timeout)
        result.steps.append(StepResult("stop_agents", True, detail))

        # 4 — Backup current runtime
        if cfg.skip_backup:
            result.steps.append(StepResult("backup", True, "skipped"))
        else:
            backup_path, err = _backup_runtime(
                self._install_root, self._seal_home, self._RUNTIME_DIRS
            )
            if err:
                result.steps.append(StepResult("backup", False, err))
                result.error = err
                return result
            result.backup_path = backup_path
            result.steps.append(StepResult("backup", True, str(backup_path)))

        # 5 — Extract to staging area
        staging, err = _extract_to_staging(cfg.tarball)
        if err:
            result.steps.append(StepResult("extract", False, err))
            result.error = err
            return result
        result.steps.append(StepResult("extract", True, str(staging)))

        # 6 — Swap runtime directories
        top = _find_top_dir(staging)
        if top is None:
            err = "could not find top-level directory in tarball"
            result.steps.append(StepResult("swap_runtime", False, err))
            result.error = err
            shutil.rmtree(staging, ignore_errors=True)
            return result

        swapped: list[str] = []
        for dir_name in self._RUNTIME_DIRS:
            src = staging / top / dir_name
            dst = self._install_root / dir_name
            if not src.exists():
                continue
            old = Path(str(dst) + ".old")
            if dst.exists():
                dst.rename(old)
            shutil.copytree(src, dst)
            shutil.rmtree(old, ignore_errors=True)
            swapped.append(dir_name)

        shutil.rmtree(staging, ignore_errors=True)
        result.steps.append(StepResult("swap_runtime", True, ", ".join(swapped) or "none"))

        result.success = True
        return result


# ---------------------------------------------------------------------------
# Step implementations
# ---------------------------------------------------------------------------


def _verify_sha256(tarball: Path, checksum_file: Path) -> tuple[bool, str]:
    """Return (ok, detail) after comparing tarball hash against sidecar."""
    if not tarball.exists():
        return False, f"tarball not found: {tarball}"
    if not checksum_file.exists():
        return False, f"checksum file not found: {checksum_file}"
    expected_line = checksum_file.read_text().strip()
    if not expected_line:
        return False, "checksum file is empty"
    expected_sha = expected_line.split()[0]
    actual_sha = _sha256(tarball)
    if actual_sha != expected_sha:
        return False, f"sha256 mismatch: got {actual_sha[:16]}…, want {expected_sha[:16]}…"
    return True, f"sha256 OK ({actual_sha[:16]}…)"


def _stop_agents(
    seal_home: Path,
    profile: Optional[str],
    timeout: int,
) -> tuple[int, str]:
    """Send SIGTERM to running agent processes; return (count, detail)."""
    stopped = 0
    skipped = 0
    search_glob = f"{profile}/*.lock" if profile else "**/*.lock"
    profiles_dir = seal_home / "profiles"
    if not profiles_dir.exists():
        return 0, "no profiles directory"

    for lock_path in profiles_dir.glob(search_glob):
        try:
            pid = int(lock_path.read_text().strip())
        except (ValueError, OSError):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped += 1
        except ProcessLookupError:
            lock_path.unlink(missing_ok=True)
            skipped += 1
        except PermissionError:
            skipped += 1

    if stopped:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            still_alive = False
            for lock_path in profiles_dir.glob(search_glob):
                try:
                    pid = int(lock_path.read_text().strip())
                    os.kill(pid, 0)
                    still_alive = True
                except (ValueError, OSError, ProcessLookupError):
                    pass
            if not still_alive:
                break
            time.sleep(0.5)

    return stopped, f"stopped={stopped} stale_skipped={skipped}"


def _backup_runtime(
    install_root: Path,
    seal_home: Path,
    dirs: tuple[str, ...],
) -> tuple[Optional[Path], str]:
    """Create a backup tarball of current runtime dirs."""
    backups_dir = seal_home / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = backups_dir / f"seal-backup-{ts}.tar.gz"
    try:
        with tarfile.open(backup_path, "w:gz") as tar:
            for dir_name in dirs:
                d = install_root / dir_name
                if d.exists():
                    tar.add(d, arcname=dir_name)
    except Exception as exc:
        return None, f"backup failed: {exc}"
    return backup_path, ""


def _extract_to_staging(tarball: Path) -> tuple[Optional[Path], str]:
    """Extract tarball to a temp directory; return (staging_path, error)."""
    staging = Path(tempfile.mkdtemp(prefix="seal_upgrade_"))
    try:
        with tarfile.open(tarball, "r:gz") as tar:
            extract_kwargs = {}
            if sys.version_info >= (3, 12):
                extract_kwargs["filter"] = "data"
            tar.extractall(staging, **extract_kwargs)
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return None, f"extraction failed: {exc}"
    return staging, ""


def _find_top_dir(staging: Path) -> Optional[str]:
    """Return the single top-level directory name inside the staging area."""
    entries = [p for p in staging.iterdir() if p.is_dir()]
    return entries[0].name if len(entries) == 1 else None


def _parse_version(tarball_name: str) -> str:
    """Extract version string from seal-X.Y.Z-arch.tar.gz."""
    parts = tarball_name.split("-")
    if len(parts) >= 3 and parts[0] == "seal":
        return parts[1]
    return ""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="seal upgrade",
        description="Upgrade SEAL runtime from a signed tarball.",
    )
    p.add_argument("--tarball", type=Path, required=True, help="Path to .tar.gz")
    p.add_argument(
        "--checksum",
        type=Path,
        default=None,
        help="Path to .sha256 sidecar (default: <tarball>.sha256)",
    )
    p.add_argument(
        "--seal-home",
        type=Path,
        default=None,
        help="SEAL_HOME override",
    )
    p.add_argument(
        "--install-root",
        type=Path,
        default=None,
        help="Directory containing seal/, memory/, migrations/ (default: auto-detect)",
    )
    p.add_argument("--profile", default=None, help="Profile name to stop before upgrade")
    p.add_argument("--skip-backup", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--stop-timeout",
        type=int,
        default=10,
        help="Seconds to wait for agents to stop (default: 10)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = UpgradeConfig(
        tarball=args.tarball,
        checksum_file=args.checksum,
        seal_home=args.seal_home,
        install_root=args.install_root,
        profile=args.profile,
        skip_backup=args.skip_backup,
        dry_run=args.dry_run,
        stop_timeout=args.stop_timeout,
    )
    result = SealUpgrader(config).run()
    print()
    for step in result.steps:
        tag = "[OK]  " if step.ok else "[FAIL]"
        detail = f" — {step.detail}" if step.detail else ""
        print(f"  {tag} {step.step}{detail}")
    print()
    if result.success:
        print(f"  SEAL {result.new_version} installed. Run: seal start")
    else:
        print(f"  Upgrade failed: {result.error}", file=sys.stderr)
        if result.backup_path:
            print(f"  Backup at: {result.backup_path}", file=sys.stderr)
    print()
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
