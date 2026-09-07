from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


MESSAGES = Path(__file__).parents[1]
if str(MESSAGES) not in sys.path:
    sys.path.insert(0, str(MESSAGES))


def test_chat_db_requires_explicit_runtime_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEAL_PG_DSN", raising=False)
    sys.modules.pop("chat_db", None)
    chat_db = importlib.import_module("chat_db")
    with pytest.raises(RuntimeError, match="SEAL_PG_DSN is required"):
        chat_db.ChatDB()


def test_chat_db_accepts_explicit_least_privilege_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEAL_PG_DSN", raising=False)
    sys.modules.pop("chat_db", None)
    chat_db = importlib.import_module("chat_db")
    dsn = "postgresql://runtime:test@127.0.0.1:5433/seal_memory"
    assert chat_db.ChatDB(dsn).dsn == dsn
