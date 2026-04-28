"""SEAL installer — pure Python, no curl|bash.

Bootstraps a new SEAL installation on a client's machine:
  1. Detects Python version, OS, and PostgreSQL availability
  2. Creates ~/.seal/ directory structure (SEAL_HOME)
  3. Creates an initial profile via seal.profile
  4. Initialises the soul_v3 schema in PostgreSQL (if reachable)
  5. Writes a systemd service (Linux) or launchd plist (macOS)
  6. Symlinks the seal CLI to ~/.seal/bin/seal

Designed for client distribution — runs from an extracted tarball:
    python3 seal_install.py --profile acme_corp
    python3 -m seal.install --profile acme_corp --agent JARVIS
    python3 -m seal.install --profile acme_corp --non-interactive --skip-db

Output uses [OK]/[SKIP]/[WARN]/[FAIL] prefixes so it is human-readable
and CI-parseable without grep magic.
"""
from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Minimum requirements
# ---------------------------------------------------------------------------

_MIN_PYTHON: tuple[int, int] = (3, 10)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class EnvInfo:
    python_version: tuple[int, ...]
    os_name: str        # "linux" | "darwin" | "windows"
    arch: str           # "x86_64" | "arm64" | …
    has_systemd: bool
    has_psql: bool
    seal_home: Path


@dataclass
class InstallerConfig:
    profile_name: str
    agent_name: str = "JARVIS"
    db_url: str = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
    seal_home: Optional[Path] = None
    non_interactive: bool = False
    skip_db: bool = False
    skip_systemd: bool = False


@dataclass
class StepResult:
    step: str
    ok: bool
    detail: str = ""


@dataclass
class InstallResult:
    profile_dir: Optional[Path] = None
    steps: list[StepResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return all(s.ok for s in self.steps)

    @property
    def failed_steps(self) -> list[StepResult]:
        return [s for s in self.steps if not s.ok]


# ---------------------------------------------------------------------------
# Core installer
# ---------------------------------------------------------------------------


class SealInstaller:
    """Orchestrates the full install sequence for one profile."""

    def __init__(self, config: InstallerConfig) -> None:
        self.config = config

    def run(self) -> InstallResult:
        result = InstallResult()
        cfg = self.config

        # 1 — Detect environment
        env = detect_environment(seal_home_override=cfg.seal_home)
        if env.python_version[:2] < _MIN_PYTHON:
            result.steps.append(
                StepResult(
                    "env_check",
                    False,
                    f"Python {env.python_version} < {_MIN_PYTHON} required",
                )
            )
            return result
        result.steps.append(
            StepResult(
                "env_check",
                True,
                f"Python {'.'.join(str(v) for v in env.python_version)} "
                f"on {env.os_name}/{env.arch}",
            )
        )

        # 2 — Create SEAL_HOME skeleton
        try:
            _create_seal_home(env.seal_home)
            result.steps.append(StepResult("seal_home", True, str(env.seal_home)))
        except Exception as exc:
            result.steps.append(StepResult("seal_home", False, str(exc)))
            return result

        # 3 — Create profile (inline, respects seal_home from config)
        try:
            pdir = _create_profile(
                env.seal_home, cfg.profile_name, cfg.agent_name, cfg.db_url
            )
            result.profile_dir = pdir
            result.steps.append(StepResult("profile", True, str(pdir)))
        except Exception as exc:
            result.steps.append(StepResult("profile", False, str(exc)))
            return result

        # 4 — Init DB schema
        if cfg.skip_db:
            result.steps.append(StepResult("db_schema", True, "skipped"))
        elif not env.has_psql:
            result.steps.append(
                StepResult("db_schema", True, "psql binary not found — deferred")
            )
        else:
            ok, msg = _init_db_schema(cfg.profile_name, cfg.db_url)
            result.steps.append(StepResult("db_schema", ok, msg))

        # 5 — Systemd service (Linux only)
        if cfg.skip_systemd or env.os_name != "linux" or not env.has_systemd:
            result.steps.append(StepResult("systemd", True, "not applicable"))
        else:
            try:
                svc = _write_systemd_service(pdir, cfg.profile_name, cfg.agent_name)
                result.steps.append(StepResult("systemd", True, str(svc)))
            except Exception as exc:
                result.steps.append(StepResult("systemd", False, str(exc)))

        # 6 — CLI symlink
        try:
            link = _create_cli_symlink(env.seal_home)
            result.steps.append(StepResult("cli_symlink", True, str(link)))
        except Exception as exc:
            result.steps.append(StepResult("cli_symlink", False, str(exc)))

        return result


# ---------------------------------------------------------------------------
# Sub-steps
# ---------------------------------------------------------------------------


def detect_environment(
    seal_home_override: Optional[Path] = None,
) -> EnvInfo:
    """Probe the host OS and return an EnvInfo snapshot."""
    ver = sys.version_info[:3]
    system = platform.system().lower()
    # platform.machine() → "x86_64" / "AMD64" / "aarch64" / "arm64"
    arch = platform.machine().lower()
    if arch in ("amd64", "x86_64"):
        arch = "x86_64"
    elif arch in ("aarch64", "arm64"):
        arch = "arm64"

    has_systemd = system == "linux" and Path("/run/systemd/private").exists()
    has_psql = bool(_which("psql"))

    seal_home = seal_home_override or Path(
        os.environ.get("SEAL_HOME", Path.home() / ".seal")
    )
    return EnvInfo(
        python_version=ver,
        os_name=system,
        arch=arch,
        has_systemd=has_systemd,
        has_psql=has_psql,
        seal_home=seal_home,
    )


def _create_seal_home(seal_home: Path) -> None:
    """Create the ~/.seal/ skeleton if it does not exist."""
    for sub in ("profiles", "bin", "keys", "logs"):
        (seal_home / sub).mkdir(parents=True, exist_ok=True)


def _create_profile(
    seal_home: Path,
    profile_name: str,
    agent_name: str,
    db_url: str,
) -> Path:
    """Create a profile directory under seal_home/profiles/.

    Self-contained — does not use seal.profile so SEAL_HOME env var is
    irrelevant here; the caller's seal_home is always honoured.
    """
    safe = re.sub(r"[^a-z0-9]", "_", profile_name.lower())
    if not re.match(r"^[a-z][a-z0-9_]{1,30}$", safe):
        raise ValueError(f"Invalid profile name {profile_name!r}")
    pdir = seal_home / "profiles" / safe
    if pdir.exists():
        raise FileExistsError(f"Profile '{safe}' already exists at {pdir}")
    (pdir / "logs").mkdir(parents=True, exist_ok=True)
    schema = f"soul_v3_{safe}"
    (pdir / "db_url.env").write_text(
        f"SEAL_SCHEMA={schema}\nSEAL_DB_URL={db_url}\n"
    )
    _write_profile_config(pdir, safe, agent_name)
    return pdir


def _write_profile_config(pdir: Path, name: str, agent: str) -> None:
    config = f"""[agent]
name = "{agent}"
display_name = "{agent} — SEAL"
profile = "{name}"
version = "1.0.0"

[ocean]
O = 0.83
C = 1.0
E = 0.40
A = 0.66
N = 0.12

[model]
primary = "claude-opus-4-7"
fallback = "claude-sonnet-4-6"
local_endpoint = ""

[channels]
webchat_port = 8765

[rules]
language = "es"
timezone = "America/Lima"
"""
    (pdir / "config.toml").write_text(config)


def _init_db_schema(profile_name: str, db_url: str) -> tuple[bool, str]:
    """Create soul_v3_{profile_name} schema via psql if available."""
    safe = re.sub(r"[^a-z0-9]", "_", profile_name.lower())
    schema = f"soul_v3_{safe}"
    sql = f"CREATE SCHEMA IF NOT EXISTS {schema};"
    try:
        subprocess.run(
            ["psql", db_url, "-c", sql],
            check=True,
            capture_output=True,
            timeout=15,
        )
        return True, f"schema {schema} created"
    except subprocess.CalledProcessError as exc:
        return False, exc.stderr.decode(errors="replace").strip()
    except FileNotFoundError:
        return False, "psql not found"
    except Exception as exc:
        return False, str(exc)


def _write_systemd_service(
    profile_dir: Path,
    profile_name: str,
    agent_name: str,
) -> Path:
    """Write a systemd user service unit to the profile directory."""
    python = sys.executable
    unit = f"""[Unit]
Description=SEAL Agent — {agent_name} ({profile_name})
After=network.target

[Service]
Type=simple
Environment="SEAL_PROFILE={profile_name}"
Environment="SEAL_AGENT={agent_name}"
EnvironmentFile={profile_dir}/db_url.env
ExecStart={python} -m seal.agent --profile {profile_name}
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
"""
    service_path = profile_dir / f"seal-{profile_name}.service"
    service_path.write_text(unit)
    return service_path


def _create_cli_symlink(seal_home: Path) -> Path:
    """Symlink ~/.seal/bin/seal → seal/cli.py (or a no-op marker if cli not found)."""
    bin_dir = seal_home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    link = bin_dir / "seal"
    cli_target = Path(__file__).parent / "cli.py"

    if link.exists() or link.is_symlink():
        link.unlink()
    if cli_target.exists():
        link.symlink_to(cli_target)
    else:
        # cli.py not yet present — write a shim so the path is valid
        link.write_text(
            f"#!/usr/bin/env python3\n"
            f"# SEAL CLI shim — replace with seal/cli.py when available\n"
            f"import sys; print('SEAL CLI not yet installed'); sys.exit(1)\n"
        )
        link.chmod(0o755)
    return link


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

import re


def _which(binary: str) -> Optional[str]:
    """Return full path to *binary* or None (stdlib shutil.which wrapper)."""
    import shutil

    return shutil.which(binary)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_result(result: InstallResult) -> None:
    print()
    for step in result.steps:
        tag = "[OK]  " if step.ok else "[FAIL]"
        detail = f" — {step.detail}" if step.detail else ""
        print(f"  {tag} {step.step}{detail}")
    print()
    if result.success:
        print(f"  SEAL installed → {result.profile_dir}")
        print("  Add ~/.seal/bin to PATH, then run: seal start")
    else:
        failed = ", ".join(s.step for s in result.failed_steps)
        print(f"  Install incomplete — failed steps: {failed}")
    print()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="seal install",
        description="Bootstrap a new SEAL agent profile on this machine.",
    )
    parser.add_argument("--profile", required=True, help="Profile name (e.g. acme_corp)")
    parser.add_argument("--agent", default="JARVIS", help="Agent personality to deploy")
    parser.add_argument(
        "--db-url",
        default="postgresql://seal:seal_memory_2026@localhost:5433/seal_memory",
        help="PostgreSQL connection URL for soul_v3",
    )
    parser.add_argument(
        "--seal-home",
        type=Path,
        default=None,
        help="Override SEAL_HOME directory (default: ~/.seal)",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Never prompt for input — use defaults",
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Skip PostgreSQL schema initialisation",
    )
    parser.add_argument(
        "--skip-systemd",
        action="store_true",
        help="Skip systemd service generation",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    config = InstallerConfig(
        profile_name=args.profile,
        agent_name=args.agent,
        db_url=args.db_url,
        seal_home=args.seal_home,
        non_interactive=args.non_interactive,
        skip_db=args.skip_db,
        skip_systemd=args.skip_systemd,
    )
    installer = SealInstaller(config)
    result = installer.run()
    print_result(result)
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
