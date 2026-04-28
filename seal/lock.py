"""Agent lock — prevents duplicate agent instances per profile.

Uses fcntl.LOCK_EX: OS releases lock automatically on process death.
No manual cleanup needed on crash.
"""
from __future__ import annotations

import fcntl
import os
from pathlib import Path


class AgentLock:
    """Exclusive file lock for a single agent within a profile directory."""

    def __init__(self, profile_dir: Path, agent_name: str) -> None:
        self.lock_path = profile_dir / f"{agent_name.lower()}.lock"
        self._fd = None

    def acquire(self) -> bool:
        """Try to acquire lock. Returns False if another process holds it."""
        self._fd = open(self.lock_path, "w")
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd.write(str(os.getpid()))
            self._fd.flush()
            return True
        except BlockingIOError:
            self._fd.close()
            self._fd = None
            return False

    def release(self) -> None:
        if self._fd:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None
            self.lock_path.unlink(missing_ok=True)

    def holder_pid(self) -> int | None:
        """Return PID of current lock holder, or None if no one holds it."""
        try:
            return int(self.lock_path.read_text().strip())
        except (FileNotFoundError, ValueError):
            return None

    def __enter__(self) -> "AgentLock":
        if not self.acquire():
            pid = self.holder_pid()
            raise RuntimeError(
                f"Agent already running in this profile (PID {pid}). "
                f"Stop it first: seal stop --profile <name>"
            )
        return self

    def __exit__(self, *_) -> None:
        self.release()
