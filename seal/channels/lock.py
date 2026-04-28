"""Credential lock — prevents two profiles from binding the same channel token.

When a profile starts a channel adapter, it claims an exclusive lock keyed
by the credential identity (bot token, API key fingerprint, etc.). A
second profile attempting the same credential will fail to acquire the
lock and abort cleanly instead of producing duplicate replies.

OS-level fcntl.LOCK_EX is used so the lock is automatically released when
the holding process dies — no orphan cleanup required.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
from typing import Optional


class CredentialLock:
    """Exclusive lock keyed by credential fingerprint."""

    def __init__(self, lock_dir: Path, channel: str, credential: str) -> None:
        self._lock_dir = lock_dir
        self._channel = channel
        self._fp = self._fingerprint(credential)
        self._fd: Optional[int] = None

    @staticmethod
    def _fingerprint(credential: str) -> str:
        return hashlib.sha256(credential.encode("utf-8")).hexdigest()[:16]

    @property
    def lock_path(self) -> Path:
        return self._lock_dir / f"{self._channel}.{self._fp}.lock"

    def acquire(self) -> bool:
        """Try to acquire the lock. Returns False if another holder exists."""
        self._lock_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("ascii"))
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> "CredentialLock":
        if not self.acquire():
            raise RuntimeError(
                f"credential for {self._channel} already in use by another profile"
            )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
