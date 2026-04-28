#!/usr/bin/env python3
"""SEAL Windows Installer — one script, one command.

Usage (cmd o PowerShell, desde cualquier red con Tailscale activo):
    python -c "import urllib.request; exec(urllib.request.urlopen('http://100.75.201.110:9001/seal/windows_install.py').read())"

    # Con opciones (guardar primero):
    python si.py --profile laptop_william --agent JARVIS

Creates:
    %USERPROFILE%\\.seal\\profiles\\<name>\\config.toml
    %USERPROFILE%\\.seal\\profiles\\<name>\\db_url.env
    %USERPROFILE%\\.seal\\seal_start.bat   (doble-click para iniciar)

No external dependencies — stdlib only.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_DB_DEFAULT = "postgresql://seal:seal_memory_2026@100.75.201.110:5433/seal_memory"
_SEAL_VERSION = "0.1.0"


def _find_claude() -> str | None:
    import shutil

    found = shutil.which("claude")
    if found:
        return found
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "claude" / "claude.exe",
        Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd",
        Path(os.environ.get("APPDATA", "")) / "npm" / "claude",
        Path.home() / ".local" / "bin" / "claude",
        Path("C:/Program Files/claude/claude.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _write_config(pdir: Path, name: str, agent: str) -> None:
    config = f"""[agent]
name = "{agent}"
display_name = "{agent} — SEAL"
profile = "{name}"
version = "{_SEAL_VERSION}"

[ocean]
O = 0.83
C = 1.0
E = 0.4
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
    (pdir / "config.toml").write_text(config, encoding="utf-8")


def _write_env(pdir: Path, name: str, db_url: str) -> None:
    schema = f"soul_v3_{name}"
    (pdir / "db_url.env").write_text(
        f"SEAL_SCHEMA={schema}\nSEAL_DB_URL={db_url}\n", encoding="utf-8"
    )


def _write_bat(seal_home: Path, profile_name: str, agent: str, db_url: str,
               claude_bin: str | None) -> Path:
    schema = f"soul_v3_{profile_name}"
    bat_path = seal_home / "seal_start.bat"

    claude_line = f'"{claude_bin}" --name {agent}' if claude_bin else (
        "echo ERROR: claude CLI no encontrado. Instala desde https://claude.ai/download\npause\nexit /b 1"
    )

    content = f"""@echo off
title SEAL — {agent} [{profile_name}]
setlocal

set SEAL_AGENT={agent}
set SEAL_PROFILE={profile_name}
set SEAL_SCHEMA={schema}
set SEAL_DB_URL={db_url}
set SEAL_ROOT=%USERPROFILE%\\.seal

echo Iniciando SEAL {agent} (perfil: {profile_name})...
{claude_line}
"""
    bat_path.write_text(content, encoding="utf-8")
    return bat_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SEAL Windows Installer")
    ap.add_argument("--profile", default="laptop_william", help="Nombre de perfil [laptop_william]")
    ap.add_argument("--agent", default="JARVIS", help="Agente a desplegar [JARVIS]")
    ap.add_argument("--db-url", default=_DB_DEFAULT, help=f"URL PostgreSQL [{_DB_DEFAULT}]")
    args = ap.parse_args(argv)

    seal_home = Path(os.environ.get("SEAL_HOME", Path.home() / ".seal"))
    profile_dir = seal_home / "profiles" / args.profile

    print(f"SEAL installer v{_SEAL_VERSION}")
    print(f"  perfil  : {args.profile}")
    print(f"  agente  : {args.agent}")
    print(f"  db      : {args.db_url}")
    print(f"  destino : {seal_home}")
    print()

    (profile_dir / "logs").mkdir(parents=True, exist_ok=True)
    _write_config(profile_dir, args.profile, args.agent)
    _write_env(profile_dir, args.profile, args.db_url)
    print(f"[OK] perfil creado en {profile_dir}")

    claude_bin = _find_claude()
    if claude_bin:
        print(f"[OK] claude encontrado: {claude_bin}")
    else:
        print("[!!] claude CLI no encontrado — instala desde https://claude.ai/download")

    bat = _write_bat(seal_home, args.profile, args.agent, args.db_url, claude_bin)
    print(f"[OK] launcher: {bat}")
    print()
    print("-" * 52)
    print(f"  Iniciar SEAL:  doble-click en  {bat.name}")
    print(f"  O desde cmd:   {bat}")
    print("-" * 52)

    return 0


if __name__ == "__main__":
    sys.exit(main())
