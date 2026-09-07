#!/usr/bin/env python3
"""SEAL Bootstrap — instala el sistema en cualquier máquina con una línea.

Uso:
    python3 seal_bootstrap.py
    python3 seal_bootstrap.py --profile mi_laptop --agent JARVIS
    python3 seal_bootstrap.py --profile mi_laptop --db-url postgresql://...

Sin dependencias externas — puro Python stdlib.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_URL = "https://github.com/sknaider/proyecto-seal.git"
DEFAULT_DB = "postgresql://seal:REDACTADO@192.168.68.200:5433/seal_memory"
MIN_PYTHON = (3, 10)


def _ok(msg: str) -> None:
    print(f"  [OK]  {msg}")


def _info(msg: str) -> None:
    print(f"  ...   {msg}")


def _fail(msg: str) -> None:
    print(f"  [!!]  {msg}", file=sys.stderr)


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs)


def _check_python() -> None:
    v = sys.version_info[:2]
    if v < MIN_PYTHON:
        _fail(f"Python {v[0]}.{v[1]} detectado — se requiere {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+")
        sys.exit(1)
    _ok(f"Python {v[0]}.{v[1]}")


def _check_git() -> None:
    if not shutil.which("git"):
        _fail("git no encontrado. Instalar: sudo apt install git")
        sys.exit(1)
    _ok("git disponible")


def _check_claude() -> bool:
    if shutil.which("claude"):
        _ok("Claude Code CLI ya instalado")
        return True
    _info("Claude Code CLI no encontrado — instalar manualmente:")
    print("      npm install -g @anthropic-ai/claude-code")
    print("      claude auth login")
    return False


def _clone_or_update(target: Path) -> None:
    if (target / ".git").exists():
        _info("Repo ya existe — actualizando...")
        _run(["git", "pull", "--ff-only"], cwd=target)
        _ok("Repo actualizado")
    else:
        _info(f"Clonando SEAL en {target} ...")
        _run(["git", "clone", "--depth", "1", REPO_URL, str(target)])
        _ok("Repo clonado")


def _create_venv(repo: Path) -> Path:
    venv = repo / ".venv"
    if not (venv / "bin" / "python").exists() and not (venv / "Scripts" / "python.exe").exists():
        _info("Creando venv...")
        _run([sys.executable, "-m", "venv", str(venv)])
        _ok("venv creado")
    else:
        _ok("venv ya existe")
    return venv


def _venv_python(venv: Path) -> str:
    win = venv / "Scripts" / "python.exe"
    lin = venv / "bin" / "python"
    return str(win) if win.exists() else str(lin)


def _seal_install(repo: Path, venv: Path, profile: str, agent: str, db_url: str) -> None:
    python = _venv_python(venv)
    _info(f"Instalando perfil '{profile}' para agente {agent}...")
    cmd = [
        python, "-m", "seal.install",
        "--profile", profile,
        "--agent", agent,
        "--db-url", db_url,
        "--skip-systemd",
        "--non-interactive",
    ]
    result = subprocess.run(cmd, cwd=repo, capture_output=False)
    if result.returncode != 0:
        _fail("seal.install falló — ver output arriba")
        sys.exit(1)


def _write_launcher(repo: Path, profile: str, agent: str) -> Path:
    is_win = platform.system() == "Windows"
    if is_win:
        launcher = Path.home() / "seal_start.bat"
        launcher.write_text(
            f"@echo off\r\n"
            f"cd /d {repo}\r\n"
            f".venv\\Scripts\\python.exe -m seal.cli start --profile {profile} --agent {agent}\r\n"
        )
    else:
        launcher = Path.home() / "seal_start.sh"
        launcher.write_text(
            f"#!/bin/bash\n"
            f"source {repo}/.venv/bin/activate\n"
            f"python3 -m seal.cli start --profile {profile} --agent {agent}\n"
        )
        launcher.chmod(0o755)
    return launcher


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="SEAL Bootstrap — instala el sistema en esta máquina")
    parser.add_argument("--profile", default="mi_seal", help="Nombre del perfil (default: mi_seal)")
    parser.add_argument("--agent", default="JARVIS", help="Agente a desplegar (JARVIS/ADA/ALICE/NEXUS)")
    parser.add_argument("--db-url", default=DEFAULT_DB, help="URL PostgreSQL de soul_v3")
    parser.add_argument("--dir", type=Path, default=Path.home() / "seal", help="Directorio de instalación")
    args = parser.parse_args(argv)

    print()
    print("  ╔══════════════════════════════╗")
    print("  ║      SEAL Bootstrap v1.0     ║")
    print("  ╚══════════════════════════════╝")
    print()

    _check_python()
    _check_git()
    _check_claude()

    repo = args.dir.resolve()
    _clone_or_update(repo)
    venv = _create_venv(repo)
    _seal_install(repo, venv, args.profile, args.agent, args.db_url)

    launcher = _write_launcher(repo, args.profile, args.agent)
    _ok(f"Launcher creado: {launcher}")

    print()
    print("  ┌─ INSTALACIÓN COMPLETA ─────────────────────┐")
    print(f"  │  Perfil:   {args.profile:<32}│")
    print(f"  │  Agente:   {args.agent:<32}│")
    print(f"  │  Dir:      {str(repo)[:32]:<32}│")
    print(f"  │  Launcher: {str(launcher.name):<32}│")
    print("  └─────────────────────────────────────────────┘")
    print()
    print("  Para iniciar el agente:")
    if platform.system() == "Windows":
        print(f"      {launcher}")
    else:
        print(f"      bash {launcher}")
        print(f"      # o directamente:")
        print(f"      cd {repo} && source .venv/bin/activate")
        print(f"      seal start --profile {args.profile} --agent {args.agent}")
    print()


if __name__ == "__main__":
    main()
