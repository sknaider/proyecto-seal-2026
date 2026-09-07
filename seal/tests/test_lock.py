"""Tests for seal/lock.py AgentLock.

Run:
    python3 -m pytest seal/tests/test_lock.py -v

Uses real fcntl on temp files — no mocks needed for the happy path.
Subprocess is used to test the duplicate-detection path (two processes).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from seal.lock import AgentLock


class AgentLockAcquireTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        # Clean up any leftover lock files
        for f in self._tmp.glob("*.lock"):
            f.unlink(missing_ok=True)

    def test_acquire_returns_true(self):
        lock = AgentLock(self._tmp, "TestAgent")
        self.assertTrue(lock.acquire())
        lock.release()

    def test_lock_file_created_on_acquire(self):
        lock = AgentLock(self._tmp, "TestAgent")
        lock.acquire()
        try:
            self.assertTrue(lock.lock_path.exists())
        finally:
            lock.release()

    def test_lock_file_contains_pid(self):
        lock = AgentLock(self._tmp, "TestAgent")
        lock.acquire()
        try:
            pid = int(lock.lock_path.read_text().strip())
            self.assertEqual(pid, os.getpid())
        finally:
            lock.release()

    def test_release_removes_lock_file(self):
        lock = AgentLock(self._tmp, "TestAgent")
        lock.acquire()
        lock.release()
        self.assertFalse(lock.lock_path.exists())

    def test_release_idempotent(self):
        lock = AgentLock(self._tmp, "TestAgent")
        lock.acquire()
        lock.release()
        lock.release()  # must not raise

    def test_lock_path_uses_lowercase_name(self):
        lock = AgentLock(self._tmp, "JARVIS")
        self.assertEqual(lock.lock_path.name, "jarvis.lock")

    def test_holder_pid_returns_pid_when_locked(self):
        lock = AgentLock(self._tmp, "TestAgent")
        lock.acquire()
        try:
            self.assertEqual(lock.holder_pid(), os.getpid())
        finally:
            lock.release()

    def test_holder_pid_returns_none_when_no_file(self):
        lock = AgentLock(self._tmp, "Ghost")
        self.assertIsNone(lock.holder_pid())

    def test_holder_pid_returns_none_on_corrupt_file(self):
        lock = AgentLock(self._tmp, "Corrupt")
        lock.lock_path.write_text("not-a-number")
        self.assertIsNone(lock.holder_pid())
        lock.lock_path.unlink(missing_ok=True)


class AgentLockContextManagerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        for f in self._tmp.glob("*.lock"):
            f.unlink(missing_ok=True)

    def test_context_manager_acquires_and_releases(self):
        lock = AgentLock(self._tmp, "Agent")
        with lock:
            self.assertTrue(lock.lock_path.exists())
        self.assertFalse(lock.lock_path.exists())

    def test_context_manager_releases_on_exception(self):
        lock = AgentLock(self._tmp, "Agent")
        try:
            with lock:
                raise ValueError("intentional")
        except ValueError:
            pass
        self.assertFalse(lock.lock_path.exists())


class AgentLockDuplicateTests(unittest.TestCase):
    """Uses subprocess to simulate a second process holding the lock."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        for f in self._tmp.glob("*.lock"):
            f.unlink(missing_ok=True)

    def _hold_lock_in_subprocess(self) -> subprocess.Popen:
        """Launch a child that acquires the lock and blocks until killed."""
        script = f"""
import sys, time
sys.path.insert(0, {repr(str(Path(__file__).resolve().parent.parent.parent))})
from seal.lock import AgentLock
from pathlib import Path
lock = AgentLock(Path({repr(str(self._tmp))}), "Agent")
acquired = lock.acquire()
print("ok" if acquired else "fail", flush=True)
time.sleep(30)  # hold until killed
"""
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            text=True,
        )
        # Wait for child to signal it acquired the lock
        line = proc.stdout.readline().strip()
        if line != "ok":
            proc.kill()
            self.skipTest("child could not acquire lock")
        return proc

    def test_second_acquire_returns_false(self):
        child = self._hold_lock_in_subprocess()
        try:
            lock2 = AgentLock(self._tmp, "Agent")
            self.assertFalse(lock2.acquire())
        finally:
            child.kill()
            child.wait()

    def test_context_manager_raises_on_duplicate(self):
        child = self._hold_lock_in_subprocess()
        try:
            lock2 = AgentLock(self._tmp, "Agent")
            with self.assertRaises(RuntimeError) as cm:
                with lock2:
                    pass
            self.assertIn("already running", str(cm.exception))
        finally:
            child.kill()
            child.wait()

    def test_lock_released_after_child_dies(self):
        child = self._hold_lock_in_subprocess()
        child.kill()
        child.wait()
        # After child dies, OS releases fcntl lock automatically
        lock2 = AgentLock(self._tmp, "Agent")
        self.assertTrue(lock2.acquire())
        lock2.release()


if __name__ == "__main__":
    unittest.main()
