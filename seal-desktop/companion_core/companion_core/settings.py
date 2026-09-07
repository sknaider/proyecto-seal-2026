import os
import pathlib

from companion_core.platform_paths import db_path as _platform_db_path
from companion_core.platform_paths import toml_path as _platform_toml_path


def db_path() -> pathlib.Path:
    env = os.environ.get("SEAL_DB_PATH")
    return pathlib.Path(env) if env else _platform_db_path()


def toml_path() -> pathlib.Path:
    env = os.environ.get("SEAL_TOML_PATH")
    return pathlib.Path(env) if env else _platform_toml_path()


def load_config() -> dict:
    path = toml_path()
    if not path.exists():
        return {}
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            return {}
    with open(path, "rb") as f:
        return tomllib.load(f)
