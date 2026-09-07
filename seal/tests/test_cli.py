"""Tests for seal/cli.py.

Run:
    python3 -m pytest seal/tests/test_cli.py -v
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import seal.profile as profile_mod
from seal.cli import (
    _build_parser,
    _find_claude,
    _read_agent_from_config,
    cmd_create_profile,
    cmd_list,
    cmd_status,
    cmd_stop,
    main,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _tmp_profiles(tmp: str) -> None:
    """Redirect SEAL profile storage to a temp dir."""
    profile_mod.SEAL_HOME = Path(tmp)
    profile_mod.PROFILES_DIR = Path(tmp) / "profiles"


def _restore_profiles(orig_home, orig_profiles) -> None:
    profile_mod.SEAL_HOME = orig_home
    profile_mod.PROFILES_DIR = orig_profiles


def _make_args(**kwargs):
    """Build a simple namespace from kwargs."""
    from argparse import Namespace
    return Namespace(**kwargs)


# ── create-profile ────────────────────────────────────────────────────────────

class CreateProfileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = (profile_mod.SEAL_HOME, profile_mod.PROFILES_DIR)
        _tmp_profiles(self._tmp)

    def tearDown(self):
        _restore_profiles(*self._orig)

    def test_create_success(self):
        args = _make_args(name="acme_corp", agent="JARVIS", overwrite=False)
        rc = cmd_create_profile(args)
        self.assertEqual(rc, 0)
        pdir = profile_mod.profile_dir("acme_corp")
        self.assertTrue((pdir / "config.toml").exists())
        self.assertTrue((pdir / "db_url.env").exists())
        self.assertTrue((pdir / "logs").is_dir())

    def test_create_duplicate_fails(self):
        args = _make_args(name="acme_corp", agent="JARVIS", overwrite=False)
        cmd_create_profile(args)
        rc = cmd_create_profile(args)
        self.assertEqual(rc, 1)

    def test_create_invalid_name_fails(self):
        args = _make_args(name="!!BAD!!", agent="JARVIS", overwrite=False)
        rc = cmd_create_profile(args)
        self.assertEqual(rc, 1)

    def test_schema_name_in_env(self):
        args = _make_args(name="my_client", agent="ADA", overwrite=False)
        cmd_create_profile(args)
        env = (profile_mod.profile_dir("my_client") / "db_url.env").read_text()
        self.assertIn("SEAL_SCHEMA=soul_v3_my_client", env)


# ── list ──────────────────────────────────────────────────────────────────────

class ListTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = (profile_mod.SEAL_HOME, profile_mod.PROFILES_DIR)
        _tmp_profiles(self._tmp)

    def tearDown(self):
        _restore_profiles(*self._orig)

    def test_list_empty(self):
        args = _make_args()
        rc = cmd_list(args)
        self.assertEqual(rc, 0)

    def test_list_shows_profiles(self):
        for name in ("alpha", "beta"):
            profile_mod.create(name)
        import io
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            cmd_list(_make_args())
        output = buf.getvalue()
        self.assertIn("alpha", output)
        self.assertIn("beta", output)


# ── status ────────────────────────────────────────────────────────────────────

class StatusTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = (profile_mod.SEAL_HOME, profile_mod.PROFILES_DIR)
        _tmp_profiles(self._tmp)

    def tearDown(self):
        _restore_profiles(*self._orig)

    def test_status_missing_profile_exits_1(self):
        with self.assertRaises(SystemExit) as cm:
            cmd_status(_make_args(profile="ghost", agent=None))
        self.assertEqual(cm.exception.code, 1)

    def test_status_stopped_returns_1(self):
        profile_mod.create("demo")
        rc = cmd_status(_make_args(profile="demo", agent=None))
        self.assertEqual(rc, 1)

    def test_status_shows_schema(self):
        profile_mod.create("demo")
        import io
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            cmd_status(_make_args(profile="demo", agent=None))
        self.assertIn("soul_v3_demo", buf.getvalue())


# ── stop ──────────────────────────────────────────────────────────────────────

class StopTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = (profile_mod.SEAL_HOME, profile_mod.PROFILES_DIR)
        _tmp_profiles(self._tmp)

    def tearDown(self):
        _restore_profiles(*self._orig)

    def test_stop_not_running_is_ok(self):
        profile_mod.create("client")
        rc = cmd_stop(_make_args(profile="client", agent="JARVIS"))
        self.assertEqual(rc, 0)

    def test_stop_stale_lock_cleaned(self):
        profile_mod.create("client")
        pdir = profile_mod.profile_dir("client")
        lock_file = pdir / "jarvis.lock"
        lock_file.write_text("999999999")  # non-existent PID
        rc = cmd_stop(_make_args(profile="client", agent="JARVIS"))
        self.assertEqual(rc, 0)
        self.assertFalse(lock_file.exists())


# ── start (subprocess mock) ───────────────────────────────────────────────────

class StartTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = (profile_mod.SEAL_HOME, profile_mod.PROFILES_DIR)
        _tmp_profiles(self._tmp)
        profile_mod.create("testclient", agent="JARVIS")

    def tearDown(self):
        _restore_profiles(*self._orig)

    def test_start_detach_launches_subprocess(self):
        from seal.cli import cmd_start
        mock_proc = MagicMock()
        mock_proc.pid = 12345

        # lock_path is an instance attribute set in __init__ — no need to mock it.
        # The tmpdir profile exists, so write_text on the real path works fine.
        with patch("seal.cli._find_claude", return_value="/usr/bin/claude"), \
             patch("seal.lock.AgentLock.acquire", return_value=True), \
             patch("seal.lock.AgentLock.release"), \
             patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            args = _make_args(profile="testclient", agent="JARVIS", detach=True)
            rc = cmd_start(args)
        self.assertEqual(rc, 0)
        mock_popen.assert_called_once()
        call_cmd = mock_popen.call_args[0][0]
        self.assertIn("--name", call_cmd)
        self.assertIn("JARVIS", call_cmd)

    def test_start_no_claude_binary_fails(self):
        from seal.cli import cmd_start
        with patch("seal.cli._find_claude", return_value=None), \
             patch("seal.lock.AgentLock.acquire", return_value=True), \
             patch("seal.lock.AgentLock.release"):
            args = _make_args(profile="testclient", agent="JARVIS", detach=True)
            rc = cmd_start(args)
        self.assertEqual(rc, 1)

    def test_start_already_running_fails(self):
        from seal.cli import cmd_start
        with patch("seal.lock.AgentLock.acquire", return_value=False), \
             patch("seal.lock.AgentLock.holder_pid", return_value=9999):
            args = _make_args(profile="testclient", agent="JARVIS", detach=True)
            rc = cmd_start(args)
        self.assertEqual(rc, 1)


# ── parser ────────────────────────────────────────────────────────────────────

class ParserTests(unittest.TestCase):
    def _parse(self, argv):
        return _build_parser().parse_args(argv)

    def test_create_profile_parses(self):
        a = self._parse(["create-profile", "--name", "foo"])
        self.assertEqual(a.command, "create-profile")
        self.assertEqual(a.name, "foo")
        self.assertEqual(a.agent, "JARVIS")

    def test_start_detach_flag(self):
        a = self._parse(["start", "--profile", "foo", "--detach"])
        self.assertTrue(a.detach)

    def test_logs_follow_flag(self):
        a = self._parse(["logs", "--profile", "foo", "--follow"])
        self.assertTrue(a.follow)
        self.assertEqual(a.lines, 50)

    def test_logs_custom_lines(self):
        a = self._parse(["logs", "--profile", "foo", "-n", "100"])
        self.assertEqual(a.lines, 100)

    def test_missing_subcommand_exits(self):
        with self.assertRaises(SystemExit):
            _build_parser().parse_args([])


# ── utils ─────────────────────────────────────────────────────────────────────

class UtilsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = (profile_mod.SEAL_HOME, profile_mod.PROFILES_DIR)
        _tmp_profiles(self._tmp)

    def tearDown(self):
        _restore_profiles(*self._orig)

    def test_read_agent_from_config(self):
        profile_mod.create("cli_test", agent="ADA")
        agent = _read_agent_from_config("cli_test")
        self.assertEqual(agent, "ADA")

    def test_read_agent_default_when_no_config(self):
        profile_mod.create("empty_cfg")
        pdir = profile_mod.profile_dir("empty_cfg")
        (pdir / "config.toml").unlink()
        agent = _read_agent_from_config("empty_cfg")
        self.assertEqual(agent, "JARVIS")

    def test_find_claude_with_which(self):
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = _find_claude()
        self.assertEqual(result, "/usr/local/bin/claude")

    def test_find_claude_returns_none_when_missing(self):
        with patch("shutil.which", return_value=None), \
             patch("pathlib.Path.exists", return_value=False):
            result = _find_claude()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
