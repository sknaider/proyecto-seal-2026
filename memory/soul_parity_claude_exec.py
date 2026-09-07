#!/usr/bin/env python3
"""Pin the OS PID that is about to become the Claude Code process."""
from __future__ import annotations

import os
import sys


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] != "claude":
        raise RuntimeError("expected claude executable")
    os.environ["SOUL_PARITY_RUNTIME_PID"] = str(os.getpid())
    os.execvp("claude", sys.argv[1:])


if __name__ == "__main__":
    main()
