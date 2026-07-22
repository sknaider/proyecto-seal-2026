from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("fable_nerves.py")
SPEC = importlib.util.spec_from_file_location("fable_nerves_under_test", MODULE_PATH)
fable_nerves = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(fable_nerves)


def test_live_rigor_action_writes_0600_artifact(monkeypatch, tmp_path):
    class Proc:
        returncode = 0

        async def communicate(self):
            return b"instrumentation green", b""

    async def fake_spawn(*_args, **_kwargs):
        return Proc()

    report_path = tmp_path / "fable_action.json"
    monkeypatch.setattr(fable_nerves, "ACTION_REPORT", report_path)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)
    result = asyncio.run(fable_nerves._execute_live_action("verificar_por_efecto"))
    assert result["status"] == "clean"
    assert report_path.exists()
    assert report_path.stat().st_mode & 0o777 == 0o600


def test_non_allowlisted_fable_action_fails_closed():
    with pytest.raises(RuntimeError, match="not allowlisted"):
        asyncio.run(fable_nerves._execute_live_action("crear_gold_example"))


def test_fable_full_tick_lock_is_single_flight(monkeypatch, tmp_path):
    monkeypatch.setattr(fable_nerves, "LOCK_FILE", tmp_path / "fable.lock")
    with fable_nerves._tick_lock() as first:
        with fable_nerves._tick_lock() as second:
            assert first is True
            assert second is False
    with fable_nerves._tick_lock() as after_release:
        assert after_release is True


def test_every_drive_has_positive_cooldown():
    assert fable_nerves.DRIVES
    assert all(cfg.get("cooldown_s", 0) > 0 for cfg in fable_nerves.DRIVES.values())
