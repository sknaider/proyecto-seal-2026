"""Pytest bootstrap for message bridge tests.

The project often runs tests through the `pytest` console script.  In that
mode Python puts the script directory (`~/.local/bin`) on `sys.path` instead
of the repository root, so imports like `messages.ada_codex_remote_bridge`
can fail during collection.  Keep the test suite self-contained by adding the
repo root explicitly.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
