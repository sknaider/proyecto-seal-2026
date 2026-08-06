#!/usr/bin/env python3
"""Execute the exact reviewed parity hook bytes and expose their digest."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import sys


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if len(sys.argv) != 2:
        raise RuntimeError("one hook path is required")
    candidate = Path(sys.argv[1])
    resolved = candidate.resolve(strict=True)
    if candidate.is_symlink() or not resolved.is_relative_to(ROOT / "memory"):
        raise RuntimeError("parity hook escaped the reviewed memory root")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(resolved, flags)
    try:
        before = os.fstat(fd)
        current = os.stat(resolved, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
            raise RuntimeError("parity hook changed during open")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        source = b"".join(chunks)
    finally:
        os.close(fd)
    os.environ["SOUL_EXECUTED_SCRIPT_SHA256"] = hashlib.sha256(source).hexdigest()
    scope = {"__name__": "__main__", "__file__": str(resolved), "__package__": None}
    exec(compile(source, str(resolved), "exec"), scope, scope)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
