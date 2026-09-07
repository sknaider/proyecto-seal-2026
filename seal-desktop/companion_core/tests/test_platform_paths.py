from pathlib import Path

from companion_core import platform_paths
from companion_core import settings


def test_linux_paths_use_xdg_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("SEAL_PLATFORM_OVERRIDE", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.delenv("SEAL_CONFIG_DIR", raising=False)
    monkeypatch.delenv("SEAL_DATA_DIR", raising=False)
    monkeypatch.delenv("SEAL_DB_PATH", raising=False)
    monkeypatch.delenv("SEAL_TOML_PATH", raising=False)

    assert platform_paths.config_dir() == tmp_path / "config" / "seal-app"
    assert platform_paths.data_dir() == tmp_path / "data" / "seal-app"
    assert settings.db_path() == tmp_path / "data" / "seal-app" / "companion.db"
    assert settings.toml_path() == tmp_path / "config" / "seal-app" / "companion.toml"


def test_windows_paths_use_localappdata_and_appdata(monkeypatch, tmp_path):
    local = tmp_path / "LocalAppData"
    roaming = tmp_path / "RoamingAppData"
    monkeypatch.setenv("SEAL_PLATFORM_OVERRIDE", "windows")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(roaming))
    monkeypatch.delenv("SEAL_CONFIG_DIR", raising=False)
    monkeypatch.delenv("SEAL_DATA_DIR", raising=False)
    monkeypatch.delenv("SEAL_DB_PATH", raising=False)
    monkeypatch.delenv("SEAL_TOML_PATH", raising=False)

    assert platform_paths.platform_name() == "windows"
    assert platform_paths.config_dir() == roaming / "seal-app"
    assert platform_paths.data_dir() == local / "seal-app"
    assert platform_paths.voice_models_dir() == local / "seal-app" / "voice_models"
    assert platform_paths.screen_captures_dir() == local / "seal-app" / "screen_captures"


def test_explicit_path_overrides_win_on_any_os(monkeypatch, tmp_path):
    monkeypatch.setenv("SEAL_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("SEAL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SEAL_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("SEAL_TOML_PATH", str(tmp_path / "app.toml"))
    monkeypatch.setenv("SOUL_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("SEAL_VOICE_MODELS_DIR", str(tmp_path / "voices"))

    assert platform_paths.config_dir() == tmp_path / "cfg"
    assert platform_paths.data_dir() == tmp_path / "data"
    assert settings.db_path() == tmp_path / "db.sqlite"
    assert settings.toml_path() == tmp_path / "app.toml"
    assert platform_paths.vault_dir() == tmp_path / "vault"
    assert platform_paths.voice_models_dir() == tmp_path / "voices"


def test_ui_dir_prefers_existing_override(monkeypatch, tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    monkeypatch.setenv("SEAL_UI_DIR", str(ui))

    assert platform_paths.ui_dir() == Path(ui)
