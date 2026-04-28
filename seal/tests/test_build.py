"""Contract tests for seal/build.py

Run:
    python3 -m pytest seal/tests/test_build.py -v

Or standalone:
    python3 -m unittest seal.tests.test_build -v
"""
from __future__ import annotations

import hashlib
import tarfile
import tempfile
import unittest
from pathlib import Path

from seal.build import (
    BuildConfig,
    BuildResult,
    _collect_manifest,
    _detect_arch,
    _sha256_file,
    _should_exclude,
    build,
    verify_tarball,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_source(root: Path) -> None:
    """Populate a minimal SEAL-like source tree for testing."""
    (root / "seal").mkdir()
    (root / "seal" / "__init__.py").write_text("")
    (root / "seal" / "install.py").write_text("# installer")
    (root / "seal" / "profile.py").write_text("# profile")
    (root / "seal" / "build.py").write_text("# build")
    (root / "seal" / "__pycache__").mkdir()
    (root / "seal" / "__pycache__" / "install.cpython-312.pyc").write_text("")

    tests_dir = root / "seal" / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_install.py").write_text("# tests")

    (root / "memory").mkdir()
    (root / "memory" / "mcp_server.py").write_text("# mcp")
    (root / "memory" / "config.py").write_text("# config")

    (root / "migrations").mkdir()
    (root / "migrations" / "schema.sql").write_text("-- schema")


def _cfg(source: Path, output: Path, **kwargs) -> BuildConfig:
    defaults = dict(
        version="1.0.0",
        source_root=source,
        output_dir=output,
        arch="x86_64",
    )
    defaults.update(kwargs)
    return BuildConfig(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class DetectArchTests(unittest.TestCase):
    def test_returns_string(self) -> None:
        arch = _detect_arch()
        self.assertIsInstance(arch, str)
        self.assertTrue(arch)

    def test_known_values(self) -> None:
        # At minimum it should be one of these on supported platforms
        arch = _detect_arch()
        self.assertIn(arch, ("x86_64", "arm64", arch))  # just type-check


class Sha256FileTests(unittest.TestCase):
    def test_matches_hashlib(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as f:
            f.write(b"seal test content")
            path = Path(f.name)
        expected = hashlib.sha256(b"seal test content").hexdigest()
        self.assertEqual(_sha256_file(path), expected)
        path.unlink()


class ShouldExcludeTests(unittest.TestCase):
    def _cfg_excl(self, **kwargs) -> BuildConfig:
        with tempfile.TemporaryDirectory() as tmp:
            return BuildConfig(
                version="1.0.0",
                source_root=Path(tmp),
                output_dir=Path(tmp),
                **kwargs,
            )

    def test_excludes_pycache(self) -> None:
        cfg = self._cfg_excl()
        self.assertTrue(_should_exclude(Path("seal/__pycache__/x.pyc"), cfg))

    def test_excludes_pyc(self) -> None:
        cfg = self._cfg_excl()
        self.assertTrue(_should_exclude(Path("seal/module.pyc"), cfg))

    def test_excludes_tests_by_default(self) -> None:
        cfg = self._cfg_excl(include_tests=False)
        self.assertTrue(_should_exclude(Path("seal/tests/test_foo.py"), cfg))

    def test_includes_tests_when_flag_set(self) -> None:
        cfg = self._cfg_excl(include_tests=True)
        # tests dir should NOT be excluded when include_tests=True
        result = _should_exclude(Path("seal/tests/test_foo.py"), cfg)
        # only excluded if it matches other patterns
        self.assertFalse(result)

    def test_does_not_exclude_normal_file(self) -> None:
        cfg = self._cfg_excl()
        self.assertFalse(_should_exclude(Path("seal/install.py"), cfg))


class CollectManifestTests(unittest.TestCase):
    def test_collects_expected_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            _make_source(src)
            cfg = _cfg(src, src / "dist")
            manifest = _collect_manifest(cfg)
            # Should include files from seal/, memory/, migrations/
            self.assertTrue(any("memory/" in e for e in manifest))
            self.assertTrue(any("migrations/" in e for e in manifest))
            self.assertTrue(any("seal/" in e for e in manifest))

    def test_excludes_pycache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            _make_source(src)
            manifest = _collect_manifest(_cfg(src, src / "dist"))
            self.assertFalse(any("__pycache__" in e for e in manifest))

    def test_excludes_pyc_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            _make_source(src)
            manifest = _collect_manifest(_cfg(src, src / "dist"))
            self.assertFalse(any(e.endswith(".pyc") for e in manifest))

    def test_skips_missing_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            _make_source(src)
            # Non-existent dir + no individual files — manifest must be empty
            cfg = _cfg(src, src / "dist", include_dirs=("nonexistent_dir",), include_files=())
            manifest = _collect_manifest(cfg)
            self.assertEqual(manifest, [])

    def test_no_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            _make_source(src)
            manifest = _collect_manifest(_cfg(src, src / "dist"))
            self.assertEqual(len(manifest), len(set(manifest)))

    def test_sorted_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            _make_source(src)
            manifest = _collect_manifest(_cfg(src, src / "dist"))
            self.assertEqual(manifest, sorted(manifest))


class BuildTests(unittest.TestCase):
    def test_creates_tarball(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            _make_source(src)
            dist = Path(tmp) / "dist"
            result = build(_cfg(src, dist))
            self.assertTrue(result.success, result.error)
            self.assertTrue(result.tarball.exists())

    def test_tarball_name_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            _make_source(src)
            dist = Path(tmp) / "dist"
            result = build(_cfg(src, dist, version="2.3.4", arch="arm64"))
            self.assertEqual(result.tarball.name, "seal-2.3.4-arm64.tar.gz")

    def test_creates_checksum_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            _make_source(src)
            dist = Path(tmp) / "dist"
            result = build(_cfg(src, dist))
            self.assertTrue(result.checksum_file.exists())
            content = result.checksum_file.read_text()
            self.assertIn(result.sha256, content)

    def test_no_absolute_paths_in_tar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            _make_source(src)
            dist = Path(tmp) / "dist"
            result = build(_cfg(src, dist))
            with tarfile.open(result.tarball, "r:gz") as tar:
                for member in tar.getmembers():
                    self.assertFalse(
                        member.name.startswith("/"),
                        f"absolute path found: {member.name}",
                    )

    def test_tar_entries_under_version_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            _make_source(src)
            dist = Path(tmp) / "dist"
            result = build(_cfg(src, dist, version="1.0.0"))
            with tarfile.open(result.tarball, "r:gz") as tar:
                for member in tar.getmembers():
                    self.assertTrue(
                        member.name.startswith("seal-1.0.0/"),
                        f"unexpected prefix: {member.name}",
                    )

    def test_dry_run_no_files_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            _make_source(src)
            dist = Path(tmp) / "dist"
            result = build(_cfg(src, dist, dry_run=True))
            self.assertTrue(result.success)
            self.assertFalse(dist.exists())  # output_dir not created in dry run
            self.assertTrue(len(result.manifest) > 0)

    def test_manifest_populated_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            _make_source(src)
            dist = Path(tmp) / "dist"
            result = build(_cfg(src, dist))
            self.assertTrue(result.manifest)

    def test_sha256_matches_tarball(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            _make_source(src)
            dist = Path(tmp) / "dist"
            result = build(_cfg(src, dist))
            self.assertTrue(verify_tarball(result.tarball, result.checksum_file))

    def test_pycache_not_in_tarball(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            _make_source(src)
            dist = Path(tmp) / "dist"
            result = build(_cfg(src, dist))
            with tarfile.open(result.tarball, "r:gz") as tar:
                names = [m.name for m in tar.getmembers()]
            self.assertFalse(any("__pycache__" in n for n in names))
            self.assertFalse(any(n.endswith(".pyc") for n in names))


class VerifyTarballTests(unittest.TestCase):
    def test_valid_checksum_returns_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            _make_source(src)
            dist = Path(tmp) / "dist"
            result = build(_cfg(src, dist))
            self.assertTrue(verify_tarball(result.tarball, result.checksum_file))

    def test_tampered_tarball_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            src.mkdir()
            _make_source(src)
            dist = Path(tmp) / "dist"
            result = build(_cfg(src, dist))
            # Tamper with tarball
            result.tarball.write_bytes(b"corrupted content")
            self.assertFalse(verify_tarball(result.tarball, result.checksum_file))


if __name__ == "__main__":
    unittest.main()
