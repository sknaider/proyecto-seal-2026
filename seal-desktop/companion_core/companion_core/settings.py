import os
import pathlib

_DEFAULT_DB = pathlib.Path.home() / ".seal" / "companion.db"
_DEFAULT_TOML = pathlib.Path.home() / ".seal" / "companion.toml"


def db_path() -> pathlib.Path:
    env = os.environ.get("SEAL_DB_PATH")
    return pathlib.Path(env) if env else _DEFAULT_DB


def toml_path() -> pathlib.Path:
    env = os.environ.get("SEAL_TOML_PATH")
    return pathlib.Path(env) if env else _DEFAULT_TOML


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
