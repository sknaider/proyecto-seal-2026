import os
import runpy
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
CONFIG = HERE / "studio_db.py"


def test_missing_dsn_fails_closed(monkeypatch):
    monkeypatch.delenv("SEAL_STUDIO_DB_DSN", raising=False)
    with pytest.raises(RuntimeError, match="fails closed"):
        runpy.run_path(str(CONFIG))


def test_superuser_dsn_is_rejected(monkeypatch):
    monkeypatch.setenv(
        "SEAL_STUDIO_DB_DSN",
        "postgresql://seal:not-a-real-secret@127.0.0.1:5433/seal_memory",
    )
    with pytest.raises(RuntimeError, match="refusing broader login"):
        runpy.run_path(str(CONFIG))


def test_restricted_login_is_required(monkeypatch):
    dsn = "postgresql://svc_seal_studio:dummy@127.0.0.1:5433/seal_memory"
    monkeypatch.setenv("SEAL_STUDIO_DB_DSN", dsn)
    namespace = runpy.run_path(str(CONFIG))
    assert namespace["DB_URL"] == dsn


def test_active_studio_sources_have_no_superuser_fallback():
    for name in ("main.py", "mcp_gateway.py", "studio_db.py"):
        source = (HERE / name).read_text(encoding="utf-8")
        assert "seal_memory_2026" not in source
        assert "postgresql://seal:" not in source
