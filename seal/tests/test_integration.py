"""End-to-end integration test for the SEAL CLI + profile + lock stack.

Flow:
    create-profile → list → status(stopped) → [start dummy] →
    status(running) → stop → status(stopped) → list(stopped)

Uses a real tmpdir and real fcntl locks.  The "start" step launches a genuine
dummy subprocess (python3 sleep) and wires its PID into the lock file — same
state the real CLI produces in --detach mode.

Run:
    python3 -m pytest seal/tests/test_integration.py -v
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import seal.profile as profile_mod
from seal.cli import main
from seal.profile import profile_dir


# ── helpers ───────────────────────────────────────────────────────────────────

def _run(argv: list[str], *, capture: bool = True) -> tuple[int, str]:
    """Invoke the CLI and return (exit_code, stdout_text)."""
    buf = io.StringIO() if capture else None
    try:
        if capture:
            with patch("sys.stdout", buf):
                rc = main(argv)
        else:
            rc = main(argv)
    except SystemExit as exc:
        rc = int(exc.code) if exc.code is not None else 0
    return rc, (buf.getvalue() if capture else "")


def _start_dummy() -> subprocess.Popen:
    """Launch a real long-lived process to act as the 'agent'."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _inject_pid(profile_name: str, agent: str, pid: int) -> Path:
    """Write pid into the agent lock file (simulates --detach start)."""
    lf = profile_dir(profile_name) / f"{agent.lower()}.lock"
    lf.write_text(str(pid))
    return lf


# ── integration test ──────────────────────────────────────────────────────────

class CliEndToEndTests(unittest.TestCase):

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        self._orig = (profile_mod.SEAL_HOME, profile_mod.PROFILES_DIR)
        profile_mod.SEAL_HOME = self._tmp
        profile_mod.PROFILES_DIR = self._tmp / "profiles"
        self._dummy = None

    def tearDown(self):
        profile_mod.SEAL_HOME = self._orig[0]
        profile_mod.PROFILES_DIR = self._orig[1]
        if self._dummy and self._dummy.poll() is None:
            self._dummy.kill()
            self._dummy.wait()

    # ── step 1: create-profile ────────────────────────────────────────────

    def test_01_create_profile(self):
        rc, out = _run(["create-profile", "--name", "e2e_test", "--agent", "JARVIS"])
        self.assertEqual(rc, 0)
        self.assertIn("e2e_test", out)
        pdir = profile_dir("e2e_test")
        self.assertTrue(pdir.is_dir())
        self.assertTrue((pdir / "config.toml").exists())
        self.assertTrue((pdir / "db_url.env").exists())
        self.assertTrue((pdir / "logs").is_dir())

    # ── step 2: list shows the new profile ───────────────────────────────

    def test_02_list_shows_profile(self):
        _run(["create-profile", "--name", "e2e_list", "--agent", "ADA"])
        rc, out = _run(["list"])
        self.assertEqual(rc, 0)
        self.assertIn("e2e_list", out)
        self.assertIn("soul_v3_e2e_list", out)
        self.assertIn("stopped", out)

    # ── step 3: status before start → stopped ────────────────────────────

    def test_03_status_before_start(self):
        _run(["create-profile", "--name", "e2e_status"])
        rc, out = _run(["status", "--profile", "e2e_status"])
        self.assertEqual(rc, 1)  # stopped → rc 1
        self.assertIn("stopped", out)
        self.assertIn("soul_v3_e2e_status", out)

    # ── step 4: inject live PID → status reports running ─────────────────

    def test_04_status_after_start(self):
        _run(["create-profile", "--name", "e2e_run"])
        self._dummy = _start_dummy()
        _inject_pid("e2e_run", "JARVIS", self._dummy.pid)

        rc, out = _run(["status", "--profile", "e2e_run"])
        self.assertEqual(rc, 0)  # running → rc 0
        self.assertIn("running", out)

    # ── step 5: stop kills the dummy process ─────────────────────────────

    def test_05_stop_terminates_process(self):
        _run(["create-profile", "--name", "e2e_stop"])
        self._dummy = _start_dummy()
        _inject_pid("e2e_stop", "JARVIS", self._dummy.pid)

        rc, out = _run(["stop", "--profile", "e2e_stop", "--agent", "JARVIS"])
        self.assertEqual(rc, 0)
        self.assertIn("stopped", out)

        # cmd_stop sends SIGTERM and waits up to 5s internally. After it returns,
        # the process should be dead. Use Popen.poll() — not os.kill(pid, 0) —
        # because os.kill succeeds on zombies (exited but not yet waited on).
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and self._dummy.poll() is None:
            time.sleep(0.05)
        self.assertIsNotNone(self._dummy.poll(), "process still alive after stop")

    # ── step 6: lock file removed after stop ─────────────────────────────

    def test_06_lock_removed_after_stop(self):
        _run(["create-profile", "--name", "e2e_lock"])
        self._dummy = _start_dummy()
        lf = _inject_pid("e2e_lock", "JARVIS", self._dummy.pid)

        _run(["stop", "--profile", "e2e_lock", "--agent", "JARVIS"])
        self.assertFalse(lf.exists())

    # ── step 7: status after stop → stopped ──────────────────────────────

    def test_07_status_after_stop(self):
        _run(["create-profile", "--name", "e2e_post_stop"])
        self._dummy = _start_dummy()
        _inject_pid("e2e_post_stop", "JARVIS", self._dummy.pid)
        _run(["stop", "--profile", "e2e_post_stop", "--agent", "JARVIS"])

        rc, out = _run(["status", "--profile", "e2e_post_stop"])
        self.assertEqual(rc, 1)
        self.assertIn("stopped", out)

    # ── step 8: list after stop → shows stopped ───────────────────────────

    def test_08_list_after_stop(self):
        _run(["create-profile", "--name", "e2e_final"])
        self._dummy = _start_dummy()
        _inject_pid("e2e_final", "JARVIS", self._dummy.pid)
        _run(["stop", "--profile", "e2e_final", "--agent", "JARVIS"])

        rc, out = _run(["list"])
        self.assertEqual(rc, 0)
        self.assertIn("stopped", out)

    # ── step 9: duplicate create-profile rejected ─────────────────────────

    def test_09_duplicate_profile_rejected(self):
        _run(["create-profile", "--name", "e2e_dup"])
        rc, _ = _run(["create-profile", "--name", "e2e_dup"])
        self.assertEqual(rc, 1)

    # ── step 10: stop on already-stopped profile is safe ─────────────────

    def test_10_stop_when_not_running_is_noop(self):
        _run(["create-profile", "--name", "e2e_noop"])
        rc, out = _run(["stop", "--profile", "e2e_noop", "--agent", "JARVIS"])
        self.assertEqual(rc, 0)

    # ── step 11: stale lock cleaned by status ────────────────────────────

    def test_11_stale_lock_cleaned_by_status(self):
        _run(["create-profile", "--name", "e2e_stale"])
        pdir = profile_dir("e2e_stale")
        (pdir / "jarvis.lock").write_text("999999999")

        rc, out = _run(["status", "--profile", "e2e_stale"])
        # After status, stale lock should be removed by list_profiles → _running_agents
        self.assertIn("stopped", out)

    # ── step 12: full flow in one test ────────────────────────────────────

    def test_12_full_flow(self):
        """Orchestrated create → list → status(stopped) → live → status(running) → stop → verify."""
        # create
        rc, _ = _run(["create-profile", "--name", "e2e_full", "--agent", "ADA"])
        self.assertEqual(rc, 0, "create-profile failed")

        # list shows it
        _, out = _run(["list"])
        self.assertIn("e2e_full", out)

        # status: stopped
        rc, _ = _run(["status", "--profile", "e2e_full"])
        self.assertEqual(rc, 1, "expected stopped (rc=1)")

        # inject live process
        self._dummy = _start_dummy()
        _inject_pid("e2e_full", "ADA", self._dummy.pid)

        # status: running
        rc, out = _run(["status", "--profile", "e2e_full"])
        self.assertEqual(rc, 0, "expected running (rc=0)")
        self.assertIn("running", out)

        # stop
        rc, _ = _run(["stop", "--profile", "e2e_full", "--agent", "ADA"])
        self.assertEqual(rc, 0, "stop failed")

        # status: stopped again
        rc, _ = _run(["status", "--profile", "e2e_full"])
        self.assertEqual(rc, 1, "expected stopped after stop")

        # list shows stopped
        _, out = _run(["list"])
        self.assertIn("stopped", out)


if __name__ == "__main__":
    unittest.main()
