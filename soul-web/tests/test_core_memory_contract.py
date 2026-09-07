
from pathlib import Path

import pytest
import tomllib
from soul_web import __version__
from soul_web.core_memory import CoreMemory


def test_core_memory_rejects_unsafe_search_limits(tmp_path):
    with pytest.raises(ValueError, match="search_limit"):
        CoreMemory(tmp_path / "soul.db", search_limit=0)
    with pytest.raises(ValueError, match="search_limit"):
        CoreMemory(tmp_path / "soul.db", search_limit=51)


def test_core_memory_resolves_database_path(tmp_path):
    memory = CoreMemory(tmp_path / "nested" / "soul.db")
    assert memory.database == (tmp_path / "nested" / "soul.db").resolve()


def test_core_memory_exposes_episode_crash_recovery_lookup():
    assert callable(getattr(CoreMemory, "find_episode_projection", None))


def test_distribution_requires_fixed_core_and_versions_match() -> None:
    project = tomllib.loads(
        (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    assert __version__ == project["version"] == "0.1.4"
    assert "soul-framework[ann,llm]>=0.4.3,<0.5" in project["dependencies"]
