"""Load the absorbed SOUL API v1 contract inside the canonical SDK process.

The compatibility implementation remains a single source file under
``seal-agent/api`` for installer packaging, but its production runtime is the
native, supervised SDK API.  Secrets and the database URL are mandatory and
the legacy admin surface is blocked by the parent application.
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import ModuleType


ROOT = pathlib.Path(__file__).resolve().parents[1]
LEGACY_SOURCE = ROOT / "seal-agent" / "api" / "main.py"


def load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("seal_absorbed_api_v1", LEGACY_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot_load_soul_api_v1:{LEGACY_SOURCE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
