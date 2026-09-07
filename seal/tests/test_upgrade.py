"""Contract tests for seal/upgrade.py

Run:
    python3 -m pytest seal/tests/test_upgrade.py -v

Or standalone:
    python3 -m unittest seal.tests.test_upgrade -v
"""
from __future__ import annotations

import hashlib
import os
import shutil
import signal
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from seal.upgrade import (
    UpgradeConfig,
    UpgradeResult,
    SealUpgrader,
    StepResult,
    _backup_runtime,
    _extract_to_staging,
    _find_top_dir,
    _parse_args,
    _parse_version,
    _stop_agents,
    _verify_sha256,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_runtime(root: Path) -> None:
    """Populate a minimal install_root with runtime dirs."""
    (root / "seal").mkdir(parents=True, exist_ok=True)
    (root / "seal" / "install.py").write_text("# v1")
    (root / "seal" / "upgrade.py").write_text("# v1")
    (root / "memory").mkdir(exist_ok=True)
    (root / "memory" / "mcp_server.py").write_text("# v1")
    (root / "migrations").mkdir(exist_ok=True)
    (root / "migrations" / "schema.sql").write_text("-- v1")


def _make_tarball(dist: Path, version: str = "1.1.0", arch: str = "x86_64") -> tuple[Path, Path]:
    """Build a minimal seal-{version}-{arch}.tar.gz + .sha256 in dist/."""
    dist.mkdir(parents=True, exist_ok=True)
    name = f"seal-{version}-{arch}.tar.gz"
    tarball = dist / name

    with tarfile.open(tarball, "w:gz") as tar:
        top = f"seal-{version}"
        for subdir, files in [
            ("seal", [("install.py", "# v2"), ("upgrade.py", "# v2")]),
            ("memory", [("mcp_server.py", "# v2")]),
            ("migrations", [("schema.sql", "-- v2")]),
        ]:
            with tempfile.TemporaryDirectory() as tmp:
                d = Path(tmp) / subdir
                d.mkdir()
                for fname, content in files:
                    (d / fname).write_text(content)
                for f in d.iterdir():
                    tar.add(f, arcname=f"{top}/{subdir}/{f.name}")

    sha = hashlib.sha256(tarball.read_bytes()).hexdigest()
    checksum = Path(str(tarball) + ".sha256")
    checksum.write_text(f"{sha}  {name}\n")
    return tarball, checksum


def _cfg(tarball: Path, checksum: Path, install_root: Path, seal_home: Path, **kwargs) -> UpgradeConfig:
    defaults = dict(
        tarball=tarball,
        checksum_file=checksum,
        install_root=install_root,
        seal_home=seal_home,
        skip_backup=True,
        dry_run=False,
    )
    defaults.update(kwargs)
    return UpgradeConfig(**defaults)


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


class VerifySha256Tests(unittest.TestCase):
    def test_valid_checksum_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tb, cs = _make_tarball(Path(tmp))
            ok, detail = _verify_sha256(tb, cs)
            self.assertTrue(ok)
            self.assertIn("OK", detail)

    def test_tampered_tarball_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tb, cs = _make_tarball(Path(tmp))
            tb.write_bytes(b"corrupted")
            ok, detail = _verify_sha256(tb, cs)
            self.assertFalse(ok)
            self.assertIn("mismatch", detail)

    def test_missing_tarball_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, cs = _make_tarball(Path(tmp))
            ok, _ = _verify_sha256(Path(tmp) / "nonexistent.tar.gz", cs)
            self.assertFalse(ok)

    def test_missing_checksum_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tb, _ = _make_tarball(Path(tmp))
            ok, _ = _verify_sha256(tb, Path(tmp) / "nonexistent.sha256")
            self.assertFalse(ok)


class ParseVersionTests(unittest.TestCase):
    def test_standard_name(self) -> None:
        self.assertEqual(_parse_version("seal-1.2.3-x86_64.tar.gz"), "1.2.3")

    def test_arm_name(self) -> None:
        self.assertEqual(_parse_version("seal-2.0.0-arm64.tar.gz"), "2.0.0")

    def test_unparseable_returns_empty(self) -> None:
        self.assertEqual(_parse_version("random.tar.gz"), "")


class FindTopDirTests(unittest.TestCase):
    def test_single_top_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            (staging / "seal-1.0.0").mkdir()
            self.assertEqual(_find_top_dir(staging), "seal-1.0.0")

    def test_multiple_dirs_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            (staging / "a").mkdir()
            (staging / "b").mkdir()
            self.assertIsNone(_find_top_dir(staging))

    def test_empty_dir_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(_find_top_dir(Path(tmp)))


class BackupRuntimeTests(unittest.TestCase):
    def test_creates_backup_tarball(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "runtime"
            _make_runtime(install)
            seal_home = Path(tmp) / ".seal"
            backup, err = _backup_runtime(install, seal_home, ("seal", "memory", "migrations"))
            self.assertEqual(err, "")
            self.assertTrue(backup.exists())
            self.assertTrue(backup.name.startswith("seal-backup-"))

    def test_backup_contains_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "runtime"
            _make_runtime(install)
            seal_home = Path(tmp) / ".seal"
            backup, _ = _backup_runtime(install, seal_home, ("seal",))
            with tarfile.open(backup, "r:gz") as tar:
                names = tar.getnames()
            self.assertTrue(any("seal" in n for n in names))

    def test_skips_missing_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "runtime"
            install.mkdir()
            seal_home = Path(tmp) / ".seal"
            backup, err = _backup_runtime(install, seal_home, ("nonexistent",))
            self.assertEqual(err, "")
            self.assertTrue(backup.exists())


class ExtractStagingTests(unittest.TestCase):
    def test_extracts_tarball(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tb, _ = _make_tarball(Path(tmp))
            staging, err = _extract_to_staging(tb)
            self.assertEqual(err, "")
            self.assertTrue(staging.exists())
            self.assertTrue(any(staging.iterdir()))
            shutil.rmtree(staging)

    def test_corrupt_tarball_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.tar.gz"
            bad.write_bytes(b"not a tarball")
            staging, err = _extract_to_staging(bad)
            self.assertIsNone(staging)
            self.assertTrue(err)


class StopAgentsTests(unittest.TestCase):
    def test_no_profiles_dir_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            count, detail = _stop_agents(Path(tmp) / ".seal", None, timeout=1)
            self.assertEqual(count, 0)

    def test_stale_lock_cleaned_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seal_home = Path(tmp) / ".seal"
            profile_dir = seal_home / "profiles" / "test_p"
            profile_dir.mkdir(parents=True)
            lock = profile_dir / "JARVIS.lock"
            lock.write_text("999999999")  # non-existent PID
            count, _ = _stop_agents(seal_home, None, timeout=1)
            self.assertEqual(count, 0)
            # stale lock should be removed
            self.assertFalse(lock.exists())


class SealUpgraderTests(unittest.TestCase):
    def test_full_upgrade_swaps_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "runtime"
            _make_runtime(install)
            seal_home = Path(tmp) / ".seal"
            dist = Path(tmp) / "dist"
            tb, cs = _make_tarball(dist)

            config = _cfg(tb, cs, install, seal_home)
            result = SealUpgrader(config).run()

            self.assertTrue(result.success, result.failed_steps)
            # New content should be v2
            upgraded = (install / "seal" / "install.py").read_text()
            self.assertEqual(upgraded, "# v2")

    def test_dry_run_does_not_modify_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "runtime"
            _make_runtime(install)
            seal_home = Path(tmp) / ".seal"
            dist = Path(tmp) / "dist"
            tb, cs = _make_tarball(dist)

            config = _cfg(tb, cs, install, seal_home, dry_run=True)
            result = SealUpgrader(config).run()
            self.assertTrue(result.success)
            # Original content untouched
            self.assertEqual((install / "seal" / "install.py").read_text(), "# v1")

    def test_sha256_mismatch_fails_early(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "runtime"
            _make_runtime(install)
            seal_home = Path(tmp) / ".seal"
            dist = Path(tmp) / "dist"
            tb, cs = _make_tarball(dist)
            tb.write_bytes(b"corrupted")  # tamper after building checksum

            config = _cfg(tb, cs, install, seal_home)
            result = SealUpgrader(config).run()
            self.assertFalse(result.success)
            self.assertEqual(result.steps[0].step, "verify_sha256")
            self.assertFalse(result.steps[0].ok)

    def test_backup_created_when_not_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "runtime"
            _make_runtime(install)
            seal_home = Path(tmp) / ".seal"
            dist = Path(tmp) / "dist"
            tb, cs = _make_tarball(dist)

            config = _cfg(tb, cs, install, seal_home, skip_backup=False)
            result = SealUpgrader(config).run()
            self.assertTrue(result.success, result.failed_steps)
            self.assertIsNotNone(result.backup_path)
            self.assertTrue(result.backup_path.exists())

    def test_version_detected_from_tarball_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "runtime"
            _make_runtime(install)
            seal_home = Path(tmp) / ".seal"
            dist = Path(tmp) / "dist"
            tb, cs = _make_tarball(dist, version="2.5.1")

            config = _cfg(tb, cs, install, seal_home)
            result = SealUpgrader(config).run()
            self.assertEqual(result.new_version, "2.5.1")

    def test_result_has_all_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "runtime"
            _make_runtime(install)
            seal_home = Path(tmp) / ".seal"
            dist = Path(tmp) / "dist"
            tb, cs = _make_tarball(dist)

            config = _cfg(tb, cs, install, seal_home)
            result = SealUpgrader(config).run()
            step_names = {s.step for s in result.steps}
            for expected in ("verify_sha256", "stop_agents", "extract", "swap_runtime"):
                self.assertIn(expected, step_names)


class ParseArgsTests(unittest.TestCase):
    def test_tarball_required(self) -> None:
        with self.assertRaises(SystemExit):
            _parse_args([])

    def test_tarball_parsed(self) -> None:
        args = _parse_args(["--tarball", "/tmp/seal-1.0.0-x86_64.tar.gz"])
        self.assertEqual(args.tarball, Path("/tmp/seal-1.0.0-x86_64.tar.gz"))

    def test_defaults(self) -> None:
        args = _parse_args(["--tarball", "/tmp/x.tar.gz"])
        self.assertFalse(args.skip_backup)
        self.assertFalse(args.dry_run)
        self.assertEqual(args.stop_timeout, 10)


if __name__ == "__main__":
    unittest.main()
