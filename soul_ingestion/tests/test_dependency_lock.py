from __future__ import annotations

from importlib.metadata import version
from pathlib import Path


def test_runtime_dependency_lock_is_complete_and_matches_environment() -> None:
    lock_path = Path(__file__).resolve().parents[1] / "requirements.lock"
    entries = {
        name: pinned
        for line in lock_path.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
        for name, pinned in [line.split("==", maxsplit=1)]
    }
    assert entries == {
        "asyncpg": "0.31.0",
        "fastapi": "0.136.1",
        "pydantic": "2.13.3",
        "pypdf": "6.12.2",
        "uvicorn": "0.46.0",
    }
    assert {name: version(name) for name in entries} == entries
