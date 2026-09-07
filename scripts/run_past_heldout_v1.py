#!/usr/bin/env python3
"""Portable entry point for the byte-frozen PAST held-out v1 runner."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory.past_heldout_v1_runner import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
