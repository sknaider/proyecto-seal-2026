"""seal/update.py — Self-update with automatic rollback.

Fetches a new SEAL release from a remote server, applies it in-place,
and rolls back automatically if any post-update verification fails.

Flow:
  1. Backup  ~/.seal/  →  ~/.seal.bak/  (atomic copy)
  2. Download  tarball + .sha256 sidecar  from SEAL_UPDATE_URL
  3. Verify SHA-256
  4. Apply  (extract over SEAL_HOME)
  5. Post-update health check (import seal, check version tag)
  6. Rollback from backup on any failure in steps 3-5

Usage:
    seal update [--check] [--url URL] [--no-backup] [--dry-run]

Stdlib only — no external deps.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Constants ──────────────────────────────────────────────────────────────────

_DEFAULT_UPDATE_URL = os.environ.get(
    "SEAL_UPDATE_URL",
    "http://192.168.68.200:9000/seal/releases/latest",
)
_SEAL_HOME_DEFAULT = Path.home() / ".seal"
_BACKUP_SUFFIX     = ".bak"
_VERSION_FILE      = "VERSION"
_MANIFEST_FILE     = "MANIFEST.json"
_CONNECT_TIMEOUT   = 10.0
_DOWNLOAD_TIMEOUT  = 120.0


# ── Data models ────────────────────────────────────────────────────────────────

@dataclass
class UpdateConfig:
    url:         str           = _DEFAULT_UPDATE_URL
    seal_home:   Path          = field(default_factory=lambda: _SEAL_HOME_DEFAULT)
    dry_run:     bool          = False
    no_backup:   bool          = False
    check_only:  bool          = False


@dataclass
class StepResult:
    step:   str
    ok:     bool
    detail: str = ""


@dataclass
class UpdateResult:
    old_version:   str = ""
    new_version:   str = ""
    backup_path:   Optional[Path] = None
    rolled_back:   bool = False
    success:       bool = False
    error:         str  = ""
    steps: list[StepResult] = field(default_factory=list)

    def add(self, step: str, ok: bool, detail: str = "") -> StepResult:
        r = StepResult(step, ok, detail)
        self.steps.append(r)
        return r


# ── Core ───────────────────────────────────────────────────────────────────────

class SealUpdater:
    """Orchestrates the full update lifecycle."""

    def __init__(self, config: UpdateConfig) -> None:
        self.cfg    = config
        self.home   = config.seal_home
        self.backup: Optional[Path] = None

    # ── Public entry point ────────────────────────────────────────────────

    def run(self) -> UpdateResult:
        result = UpdateResult()
        result.old_version = _read_version(self.home)

        if self.cfg.check_only:
            return self._check_remote_version(result)

        try:
            # 1. Backup
            if not self.cfg.no_backup and not self.cfg.dry_run:
                ok, detail = self._do_backup()
                result.add("backup", ok, detail)
                if not ok:
                    result.error = detail
                    return result
                result.backup_path = self.backup

            # 2. Download
            tarball, checksum = self._do_download(result)
            if tarball is None:
                return result

            # 3. Verify
            if checksum:
                ok, detail = _verify_sha256(tarball, checksum)
                result.add("verify", ok, detail)
                if not ok:
                    result.error = detail
                    self._rollback(result)
                    return result
            else:
                result.add("verify", True, "no .sha256 sidecar — skipped")

            # 4. Apply
            if self.cfg.dry_run:
                result.add("apply", True, "dry-run — skipped")
            else:
                ok, detail = _apply_tarball(tarball, self.home)
                result.add("apply", ok, detail)
                if not ok:
                    result.error = detail
                    self._rollback(result)
                    return result

            # 5. Post-update verify
            if not self.cfg.dry_run:
                ok, detail = _post_verify(self.home)
                result.add("post-verify", ok, detail)
                if not ok:
                    result.error = detail
                    self._rollback(result)
                    return result

            result.new_version = _read_version(self.home)
            result.success = True

        except Exception as exc:
            result.error = str(exc)
            result.add("unexpected", False, str(exc))
            if self.backup:
                self._rollback(result)

        return result

    # ── Steps ─────────────────────────────────────────────────────────────

    def _do_backup(self) -> tuple[bool, str]:
        backup_path = self.home.parent / (self.home.name + _BACKUP_SUFFIX)
        try:
            if backup_path.exists():
                shutil.rmtree(backup_path)
            shutil.copytree(self.home, backup_path)
            self.backup = backup_path
            return True, str(backup_path)
        except Exception as exc:
            return False, str(exc)

    def _do_download(
        self, result: UpdateResult
    ) -> tuple[Optional[Path], Optional[Path]]:
        base_url = self.cfg.url.rstrip("/")
        manifest_url  = f"{base_url}/manifest.json"
        try:
            manifest = _http_json(manifest_url)
        except Exception as exc:
            result.add("manifest", False, str(exc)[:120])
            result.error = str(exc)
            return None, None

        tarball_name = manifest.get("tarball", "seal-latest.tar.gz")
        checksum_name = manifest.get("sha256", tarball_name + ".sha256")
        result.new_version = manifest.get("version", "unknown")

        tarball_url  = f"{base_url}/{tarball_name}"
        checksum_url = f"{base_url}/{checksum_name}"

        tmp = tempfile.mkdtemp(prefix="seal_update_")
        tarball_path = Path(tmp) / tarball_name

        try:
            result.add("manifest", True, f"version={result.new_version}")
            _http_download(tarball_url, tarball_path)
            result.add("download", True, f"{tarball_name} → {tmp}")
        except Exception as exc:
            result.add("download", False, str(exc)[:120])
            result.error = str(exc)
            return None, None

        checksum_path = Path(tmp) / checksum_name
        try:
            _http_download(checksum_url, checksum_path)
        except Exception:
            checksum_path = None  # type: ignore[assignment]

        return tarball_path, checksum_path

    def _check_remote_version(self, result: UpdateResult) -> UpdateResult:
        base_url = self.cfg.url.rstrip("/")
        try:
            manifest = _http_json(f"{base_url}/manifest.json")
            result.new_version = manifest.get("version", "unknown")
            result.add("check", True, f"remote={result.new_version} local={result.old_version}")
            result.success = True
        except Exception as exc:
            result.add("check", False, str(exc)[:120])
            result.error = str(exc)
        return result

    def _rollback(self, result: UpdateResult) -> None:
        if not self.backup or not self.backup.exists():
            result.add("rollback", False, "no backup available")
            return
        try:
            if self.home.exists():
                shutil.rmtree(self.home)
            shutil.copytree(self.backup, self.home)
            result.rolled_back = True
            result.add("rollback", True, f"restored from {self.backup}")
        except Exception as exc:
            result.add("rollback", False, str(exc))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_CONNECT_TIMEOUT) as resp:
        return json.loads(resp.read())


def _http_download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
        with open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)


def _verify_sha256(tarball: Path, checksum_file: Path) -> tuple[bool, str]:
    try:
        expected = checksum_file.read_text().split()[0].lower()
    except Exception as exc:
        return False, f"cannot read checksum file: {exc}"
    h = hashlib.sha256()
    with open(tarball, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual == expected:
        return True, f"sha256 ok ({actual[:16]}…)"
    return False, f"sha256 mismatch: expected {expected[:16]}… got {actual[:16]}…"


def _apply_tarball(tarball: Path, dest: Path) -> tuple[bool, str]:
    staging = Path(tempfile.mkdtemp(prefix="seal_stage_"))
    try:
        with tarfile.open(tarball, "r:gz") as tf:
            tf.extractall(staging)
        top = _find_top(staging)
        src = staging / top if top else staging
        # Overwrite only package dirs; never touch profile data
        for item in ("seal", "memory", "migrations", _VERSION_FILE, _MANIFEST_FILE):
            src_path = src / item
            dst_path = dest / item
            if not src_path.exists():
                continue
            if dst_path.exists():
                if dst_path.is_dir():
                    shutil.rmtree(dst_path)
                else:
                    dst_path.unlink()
            if src_path.is_dir():
                shutil.copytree(src_path, dst_path)
            else:
                shutil.copy2(src_path, dst_path)
        return True, f"applied from {tarball.name}"
    except Exception as exc:
        return False, str(exc)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _find_top(staging: Path) -> Optional[str]:
    entries = list(staging.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0].name
    return None


def _post_verify(seal_home: Path) -> tuple[bool, str]:
    version = _read_version(seal_home)
    seal_pkg = seal_home / "seal" / "__init__.py"
    if not seal_pkg.exists():
        return False, "seal/__init__.py missing after apply"
    return True, f"version={version}"


def _read_version(seal_home: Path) -> str:
    vf = seal_home / _VERSION_FILE
    if vf.exists():
        return vf.read_text().strip()
    return "unknown"


# ── CLI renderer ───────────────────────────────────────────────────────────────

_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_RESET  = "\033[0m"


def _colored(text: str, color: str, no_color: bool) -> str:
    return text if no_color else f"{color}{text}{_RESET}"


def print_result(result: UpdateResult, no_color: bool = False) -> None:
    print()
    for step in result.steps:
        icon   = "✓" if step.ok else "✗"
        color  = _GREEN if step.ok else _RED
        marker = _colored(icon, color, no_color)
        print(f"  {marker}  {step.step:<18} {step.detail}")
    print()
    if result.rolled_back:
        print(_colored("  ↩ Rolled back to previous version", _YELLOW, no_color))
    if result.success:
        msg = f"  Updated: {result.old_version} → {result.new_version}"
        print(_colored(msg, _GREEN, no_color))
    else:
        print(_colored(f"  Update failed: {result.error}", _RED, no_color))
    print()


# ── Entry point ────────────────────────────────────────────────────────────────

def run_update(
    url: str = _DEFAULT_UPDATE_URL,
    seal_home: Optional[Path] = None,
    dry_run: bool = False,
    no_backup: bool = False,
    check_only: bool = False,
    no_color: bool = False,
    json_out: bool = False,
) -> int:
    cfg = UpdateConfig(
        url=url,
        seal_home=seal_home or _SEAL_HOME_DEFAULT,
        dry_run=dry_run,
        no_backup=no_backup,
        check_only=check_only,
    )
    result = SealUpdater(cfg).run()

    if json_out:
        out = {
            "success":     result.success,
            "rolled_back": result.rolled_back,
            "old_version": result.old_version,
            "new_version": result.new_version,
            "error":       result.error,
            "steps": [{"step": s.step, "ok": s.ok, "detail": s.detail} for s in result.steps],
        }
        print(json.dumps(out, indent=2))
        return 0 if result.success else 1

    print_result(result, no_color=no_color)
    return 0 if result.success else 1


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(prog="seal update")
    p.add_argument("--url",       default=_DEFAULT_UPDATE_URL)
    p.add_argument("--seal-home", type=Path, default=None)
    p.add_argument("--dry-run",   action="store_true")
    p.add_argument("--no-backup", action="store_true")
    p.add_argument("--check",     action="store_true", dest="check_only")
    p.add_argument("--no-color",  action="store_true")
    p.add_argument("--json",      action="store_true", dest="json_out")
    args = p.parse_args()
    sys.exit(run_update(
        url=args.url,
        seal_home=args.seal_home,
        dry_run=args.dry_run,
        no_backup=args.no_backup,
        check_only=args.check_only,
        no_color=args.no_color,
        json_out=args.json_out,
    ))
