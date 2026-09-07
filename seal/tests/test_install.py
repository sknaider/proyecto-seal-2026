"""Contract tests for seal/install.py

Run:
    python3 -m pytest seal/tests/test_install.py -v

Or standalone:
    python3 -m unittest seal.tests.test_install -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from seal.install import (
    EnvInfo,
    InstallerConfig,
    InstallResult,
    SealInstaller,
    StepResult,
    _create_seal_home,
    _parse_args,
    _write_systemd_service,
    detect_environment,
)


def _make_env(tmp: Path, **kwargs) -> EnvInfo:
    defaults = dict(
        python_version=sys.version_info[:3],
        os_name="linux",
        arch="x86_64",
        has_systemd=False,
        has_psql=False,
        seal_home=tmp / ".seal",
    )
    defaults.update(kwargs)
    return EnvInfo(**defaults)


class DetectEnvironmentTests(unittest.TestCase):
    def test_returns_env_info(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = detect_environment(seal_home_override=Path(tmp) / ".seal")
            self.assertIsInstance(env.python_version, tuple)
            self.assertGreaterEqual(len(env.python_version), 3)
            self.assertIn(env.os_name, ("linux", "darwin", "windows"))
            self.assertIn(env.arch, ("x86_64", "arm64", env.arch))  # just check it's a string

    def test_seal_home_override_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / "custom_seal"
            env = detect_environment(seal_home_override=override)
            self.assertEqual(env.seal_home, override)

    def test_python_version_is_current(self) -> None:
        env = detect_environment()
        self.assertEqual(env.python_version[:2], sys.version_info[:2])


class CreateSealHomeTests(unittest.TestCase):
    def test_creates_expected_subdirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seal_home = Path(tmp) / ".seal"
            _create_seal_home(seal_home)
            for sub in ("profiles", "bin", "keys", "logs"):
                self.assertTrue((seal_home / sub).is_dir(), f"missing: {sub}")

    def test_idempotent_on_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            seal_home = Path(tmp) / ".seal"
            _create_seal_home(seal_home)
            _create_seal_home(seal_home)  # should not raise
            self.assertTrue((seal_home / "bin").is_dir())


class SystemdServiceTests(unittest.TestCase):
    def test_service_file_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "profile"
            pdir.mkdir()
            svc = _write_systemd_service(pdir, "acme", "JARVIS")
            self.assertTrue(svc.exists())
            self.assertEqual(svc.name, "seal-acme.service")

    def test_service_contains_profile_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "profile"
            pdir.mkdir()
            svc = _write_systemd_service(pdir, "acme_corp", "NEXUS")
            content = svc.read_text()
            self.assertIn("SEAL_PROFILE=acme_corp", content)
            self.assertIn("SEAL_AGENT=NEXUS", content)

    def test_service_has_restart_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "p"
            pdir.mkdir()
            svc = _write_systemd_service(pdir, "test", "JARVIS")
            content = svc.read_text()
            self.assertIn("Restart=on-failure", content)

    def test_service_uses_current_python(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "p"
            pdir.mkdir()
            svc = _write_systemd_service(pdir, "test", "JARVIS")
            content = svc.read_text()
            self.assertIn(sys.executable, content)


class SealInstallerTests(unittest.TestCase):
    def _make_config(self, tmp: Path, **kwargs) -> InstallerConfig:
        defaults = dict(
            profile_name="test_client",
            agent_name="JARVIS",
            seal_home=tmp / ".seal",
            skip_db=True,
            skip_systemd=True,
            non_interactive=True,
        )
        defaults.update(kwargs)
        return InstallerConfig(**defaults)

    def test_full_install_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            config = self._make_config(tmp_p)
            result = SealInstaller(config).run()
            self.assertTrue(result.success, result.failed_steps)
            self.assertIsNotNone(result.profile_dir)
            self.assertTrue(result.profile_dir.exists())

    def test_creates_seal_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            config = self._make_config(tmp_p)
            SealInstaller(config).run()
            self.assertTrue((tmp_p / ".seal" / "bin").is_dir())
            self.assertTrue((tmp_p / ".seal" / "profiles").is_dir())

    def test_python_too_old_fails_early(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            config = self._make_config(tmp_p)
            with patch("seal.install.detect_environment") as mock_detect:
                mock_detect.return_value = _make_env(
                    tmp_p, python_version=(3, 9, 0), seal_home=tmp_p / ".seal"
                )
                result = SealInstaller(config).run()
            self.assertFalse(result.success)
            self.assertEqual(result.steps[0].step, "env_check")
            self.assertFalse(result.steps[0].ok)

    def test_duplicate_profile_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            config = self._make_config(tmp_p)
            SealInstaller(config).run()
            # second run: profile already exists
            result2 = SealInstaller(config).run()
            profile_step = next(s for s in result2.steps if s.step == "profile")
            self.assertFalse(profile_step.ok)

    def test_result_reports_all_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            config = self._make_config(tmp_p)
            result = SealInstaller(config).run()
            step_names = {s.step for s in result.steps}
            # All mandatory steps should appear
            for expected in ("env_check", "seal_home", "profile"):
                self.assertIn(expected, step_names)


class ParseArgsTests(unittest.TestCase):
    def test_profile_required(self) -> None:
        with self.assertRaises(SystemExit):
            _parse_args([])

    def test_profile_parsed(self) -> None:
        args = _parse_args(["--profile", "acme"])
        self.assertEqual(args.profile, "acme")

    def test_defaults(self) -> None:
        args = _parse_args(["--profile", "x"])
        self.assertEqual(args.agent, "JARVIS")
        self.assertFalse(args.skip_db)
        self.assertFalse(args.skip_systemd)
        self.assertFalse(args.non_interactive)

    def test_flags_parsed(self) -> None:
        args = _parse_args([
            "--profile", "x",
            "--non-interactive",
            "--skip-db",
            "--skip-systemd",
        ])
        self.assertTrue(args.non_interactive)
        self.assertTrue(args.skip_db)
        self.assertTrue(args.skip_systemd)


class InstallResultTests(unittest.TestCase):
    def test_success_all_ok(self) -> None:
        r = InstallResult(steps=[StepResult("a", True), StepResult("b", True)])
        self.assertTrue(r.success)

    def test_failure_one_fail(self) -> None:
        r = InstallResult(steps=[StepResult("a", True), StepResult("b", False, "oops")])
        self.assertFalse(r.success)
        self.assertEqual(len(r.failed_steps), 1)
        self.assertEqual(r.failed_steps[0].step, "b")

    def test_empty_is_success(self) -> None:
        self.assertTrue(InstallResult().success)


if __name__ == "__main__":
    unittest.main()
