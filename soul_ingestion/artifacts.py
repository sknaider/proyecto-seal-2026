"""Content-addressed local artifact store with fail-closed permissions."""

from __future__ import annotations

import os
from pathlib import Path

from .contracts import sha256_bytes


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path_for_hash(self, digest: str) -> Path:
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("invalid SHA-256 digest")
        return self.root / digest[:2] / digest[2:4] / digest

    def put(self, content: bytes) -> str:
        digest = sha256_bytes(content)
        destination = self._path_for_hash(digest)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(destination.parent, 0o700)
        try:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if self.get(digest) != content:
                raise RuntimeError("artifact hash collision or corrupted artifact store")
            return f"artifact://sha256/{digest}"
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return f"artifact://sha256/{digest}"

    def get(self, digest: str) -> bytes:
        path = self._path_for_hash(digest)
        data = path.read_bytes()
        if sha256_bytes(data) != digest:
            raise RuntimeError("artifact content does not match its address")
        return data
