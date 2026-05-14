import pytest
from companion_core.db import init_db, close_db, _db_conn


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SEAL_DB_PATH", str(tmp_path / "test_companion.db"))
    monkeypatch.setenv("SEAL_TOML_PATH", str(tmp_path / "companion.toml"))
    yield


@pytest.fixture(autouse=True)
async def setup_db(isolated_db):
    import companion_core.db as db_mod
    import companion_core.main as main_mod
    db_mod._db_conn = None
    main_mod._config_cache.clear()
    await init_db()
    yield
    await close_db()
    db_mod._db_conn = None
    main_mod._config_cache.clear()
