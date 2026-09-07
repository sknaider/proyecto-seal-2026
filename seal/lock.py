"""Agent lock — prevents duplicate agent instances per profile.

Uses platform-appropriate exclusive file locking:
  - Linux/macOS: fcntl.LOCK_EX (OS releases on process death)
  - Windows:     msvcrt.locking (file-based, PID-stamped)

No manual cleanup needed on crash (both backends self-release).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_IS_WINDOWS = sys.platform == "win32"


class AgentLock:
    """Exclusive file lock for a single agent within a profile directory."""

    def __init__(self, profile_dir: Path, agent_name: str) -> None:
        self.lock_path = profile_dir / f"{agent_name.lower()}.lock"
        self._fd = None

    def acquire(self) -> bool:
        """Try to acquire lock. Returns False if another process holds it."""
        if _IS_WINDOWS:
            return self._acquire_windows()
        return self._acquire_posix()

    def _acquire_posix(self) -> bool:
        import fcntl
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

    def _acquire_windows(self) -> bool:
        import msvcrt
        try:
            self._fd = open(self.lock_path, "w")
            # Lock first byte exclusively, non-blocking
            msvcrt.locking(self._fd.fileno(), msvcrt.LK_NBLCK, 1)
            self._fd.write(str(os.getpid()))
            self._fd.flush()
            return True
        except (OSError, IOError):
            if self._fd:
                self._fd.close()
                self._fd = None
            return False

    def release(self) -> None:
        if self._fd:
            if _IS_WINDOWS:
                import msvcrt
                try:
                    self._fd.seek(0)
                    msvcrt.locking(self._fd.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
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
