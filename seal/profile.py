"""SEAL Profile System — create, list, validate per-client profiles.

A profile is an isolated directory under ~/.seal/profiles/<name>/ with:
- config.toml: agent identity, OCEAN base, model selection
- db_url.env: SEAL_SCHEMA and SEAL_DB_URL for this profile
- agent.lock: runtime lock (created at agent start)
- logs/: agent and heartbeat logs
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


SEAL_HOME = Path(os.environ.get("SEAL_HOME", Path.home() / ".seal"))
PROFILES_DIR = SEAL_HOME / "profiles"

_SAFE_NAME = re.compile(r"^[a-z][a-z0-9_]{1,30}$")
_DB_DEFAULT = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"


def _sanitize_name(name: str) -> str:
    clean = re.sub(r"[^a-z0-9]", "_", name.lower())
    if not _SAFE_NAME.match(clean):
        raise ValueError(
            f"Invalid profile name '{name}'. Use lowercase letters, digits, underscores. "
            f"Min 2, max 31 chars. Must start with a letter."
        )
    return clean


def profile_dir(name: str) -> Path:
    return PROFILES_DIR / _sanitize_name(name)


def create(
    name: str,
    agent: str = "JARVIS",
    db_url: str | None = None,
    *,
    overwrite: bool = False,
) -> Path:
    """Create a new profile directory with default config.

    Returns the profile directory path.
    Raises FileExistsError if profile exists and overwrite=False.
    """
    safe = _sanitize_name(name)
    pdir = PROFILES_DIR / safe
    if pdir.exists() and not overwrite:
        raise FileExistsError(f"Profile '{safe}' already exists at {pdir}")

    (pdir / "logs").mkdir(parents=True, exist_ok=True)

    schema = f"soul_v3_{safe}"
    url = db_url or _DB_DEFAULT
    (pdir / "db_url.env").write_text(
        f"SEAL_SCHEMA={schema}\nSEAL_DB_URL={url}\n"
    )

    _write_default_config(pdir, safe, agent)
    return pdir


def _write_default_config(pdir: Path, name: str, agent: str) -> None:
    config = f"""[agent]
name = "{agent}"
display_name = "{agent} — SEAL"
profile = "{name}"
version = "1.0.0"

[ocean]
O = 0.83
C = 1.0
E = 0.4
A = 0.66
N = 0.12

[model]
primary = "claude-sonnet-4-6"
fallback = "claude-haiku-4-5-20251001"
local_endpoint = ""

[channels]
webchat_port = 8765

[rules]
language = "es"
timezone = "America/Lima"
"""
    (pdir / "config.toml").write_text(config)


def list_profiles() -> list[dict]:
    """Return list of existing profiles with status info."""
    if not PROFILES_DIR.exists():
        return []
    result = []
    for p in sorted(PROFILES_DIR.iterdir()):
        if not p.is_dir():
            continue
        env = _read_env(p / "db_url.env")
        lock_pids = _running_agents(p)
        result.append({
            "name": p.name,
            "path": str(p),
            "schema": env.get("SEAL_SCHEMA", "?"),
            "running": lock_pids,
        })
    return result


def get(name: str) -> dict:
    """Get profile info. Raises FileNotFoundError if missing."""
    safe = _sanitize_name(name)
    pdir = PROFILES_DIR / safe
    if not pdir.exists():
        raise FileNotFoundError(f"Profile '{safe}' not found")
    env = _read_env(pdir / "db_url.env")
    return {
        "name": safe,
        "path": str(pdir),
        "schema": env.get("SEAL_SCHEMA", "soul_v3"),
        "db_url": env.get("SEAL_DB_URL", _DB_DEFAULT),
        "running": _running_agents(pdir),
    }


def env_vars(name: str) -> dict[str, str]:
    """Return env vars dict for a profile (SEAL_SCHEMA, SEAL_DB_URL)."""
    info = get(name)
    return {"SEAL_SCHEMA": info["schema"], "SEAL_DB_URL": info["db_url"]}


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip()
    return result


def _is_alive(pid: int) -> bool:
    """Return True if pid exists and is not a zombie."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        stat = Path(f"/proc/{pid}/status").read_text()
        for line in stat.splitlines():
            if line.startswith("State:"):
                return "Z" not in line
    except OSError:
        pass
    return True


def _running_agents(pdir: Path) -> list[str]:
    """Return agent names with live (non-zombie) lock files."""
    running = []
    for lock in pdir.glob("*.lock"):
        try:
            pid = int(lock.read_text().strip())
            if _is_alive(pid):
                running.append(lock.stem)
            else:
                lock.unlink(missing_ok=True)  # stale or zombie lock
        except (ValueError, OSError):
            lock.unlink(missing_ok=True)
    return running
