import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("SEAL_DB_URL", "postgresql://test:test@127.0.0.1:1/test")
sys.path.insert(0, str(Path(__file__).parent))

import agent_self_reflect
import soul_reflect


def test_legacy_cli_routes_to_current_agent_scoped_engine(monkeypatch):
    observed = {}

    async def fake_main():
        observed["argv"] = list(sys.argv)
        return 0

    monkeypatch.setattr(agent_self_reflect, "main", fake_main)
    monkeypatch.setattr(sys, "argv", ["soul_reflect.py", "full", "NEXUS"])

    soul_reflect.main()

    assert observed["argv"] == ["agent_self_reflect.py", "NEXUS", "24"]
    assert sys.argv == ["soul_reflect.py", "full", "NEXUS"]


def test_all_legacy_actions_use_one_maintained_path(monkeypatch):
    calls = []

    async def fake_main():
        calls.append(tuple(sys.argv))
        return 0

    monkeypatch.setattr(agent_self_reflect, "main", fake_main)
    for action in ("diary", "opinions", "relationships", "style", "distill", "full"):
        monkeypatch.setattr(sys, "argv", ["soul_reflect.py", action, "ADA"])
        soul_reflect.main()

    assert calls == [("agent_self_reflect.py", "ADA", "24")] * 6


def test_nonzero_current_engine_result_is_propagated(monkeypatch):
    async def fake_main():
        return 3

    monkeypatch.setattr(agent_self_reflect, "main", fake_main)
    monkeypatch.setattr(sys, "argv", ["soul_reflect.py", "full", "NEXUS"])

    try:
        soul_reflect.main()
    except SystemExit as exc:
        assert exc.code == 3
    else:
        raise AssertionError("legacy CLI must propagate current engine failure")


def test_importable_legacy_function_also_routes_to_current_engine(monkeypatch):
    calls = []

    async def fake_main():
        calls.append(tuple(sys.argv))
        return 0

    monkeypatch.setattr(agent_self_reflect, "main", fake_main)
    asyncio.run(soul_reflect.write_diary(None, "NEXUS"))
    assert calls == [("agent_self_reflect.py", "NEXUS", "24")]
