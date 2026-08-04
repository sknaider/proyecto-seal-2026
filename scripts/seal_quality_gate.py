#!/usr/bin/env python3
"""Stable CLI entrypoint for the SEAL golden quality gate."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quality_gate.gate import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
