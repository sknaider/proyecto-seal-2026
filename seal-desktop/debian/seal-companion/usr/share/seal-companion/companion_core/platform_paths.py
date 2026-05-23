"""Cross-platform filesystem paths for SEAL App.

The desktop app runs as a Linux package today and as a Windows executable in
the public release path. Keep all user-writable state out of install
directories and behind this module so backend code does not hardcode
``~/.config`` or ``/usr/share``.
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path
from typing import Iterable

APP_NAME = "SEAL App"
APP_DIR = "seal-app"
LEGACY_DIR = "soul-companion"


def platform_name(override: str | None = None) -> str:
    raw = override or os.environ.get("SEAL_PLATFORM_OVERRIDE") or platform.system()
    normalized = raw.strip().lower()
    if normalized.startswith(("win", "msys", "mingw")):
        return "windows"
    if normalized in {"darwin", "mac", "macos"}:
        return "macos"
    return "linux"


def _base_config_dir(os_name: str | None = None) -> Path:
    os_key = platform_name(os_name)
    if os_key == "windows":
        return Path(os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Roaming")
    if os_key == "macos":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _base_data_dir(os_name: str | None = None) -> Path:
    os_key = platform_name(os_name)
    if os_key == "windows":
        return Path(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or Path.home() / "AppData" / "Local")
    if os_key == "macos":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def config_dir(os_name: str | None = None) -> Path:
    env = os.environ.get("SEAL_CONFIG_DIR")
    if env:
        return Path(env)
    return _base_config_dir(os_name) / APP_DIR


def data_dir(os_name: str | None = None) -> Path:
    env = os.environ.get("SEAL_DATA_DIR")
    if env:
        return Path(env)
    return _base_data_dir(os_name) / APP_DIR


def db_path() -> Path:
    env = os.environ.get("SEAL_DB_PATH")
    return Path(env) if env else data_dir() / "companion.db"


def toml_path() -> Path:
    env = os.environ.get("SEAL_TOML_PATH")
    return Path(env) if env else config_dir() / "companion.toml"


def vault_dir() -> Path:
    env = os.environ.get("SOUL_VAULT_DIR")
    return Path(env) if env else config_dir() / "vault"


def screen_captures_dir() -> Path:
    env = os.environ.get("SEAL_SCREEN_DIR")
    return Path(env) if env else data_dir() / "screen_captures"


def voice_models_dir() -> Path:
    env = os.environ.get("SEAL_VOICE_MODELS_DIR")
    return Path(env) if env else data_dir() / "voice_models"


def _ui_candidates() -> Iterable[Path]:
    if os.environ.get("SEAL_UI_DIR"):
        yield Path(os.environ["SEAL_UI_DIR"])
    yield Path("/usr/share/seal-companion/ui")
    yield Path(sys.executable).resolve().parent / "ui"
    yield Path.cwd() / "ui"
    yield Path(__file__).resolve().parents[1] / "ui" / "dist"


def ui_dir() -> Path:
    for candidate in _ui_candidates():
        if candidate.is_dir():
            return candidate
    return Path(os.environ.get("SEAL_UI_DIR", "/usr/share/seal-companion/ui"))
