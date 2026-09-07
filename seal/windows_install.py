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

_DB_DEFAULT = "postgresql://seal:REDACTADO@100.75.201.110:5433/seal_memory"
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
primary = "claude-sonnet-4-6"
fallback = "claude-opus-4-7"
local_endpoint = ""

[channels]
webchat_port = 8765

[rules]
language = "es"
timezone = "America/Lima"
"""
    (pdir / "config.toml").write_text(config, encoding="utf-8")


def _write_claude_md(seal_home: Path, agent: str, profile: str, db_url: str, spark_ip: str = "100.75.201.110") -> None:
    content = f"""# SEAL Boot Protocol — {agent}

## ⚠️ PRIMERA ACCIÓN OBLIGATORIA
Llama `boot_context(agent="{agent}")` ANTES de responder. Carga identidad, OCEAN, memorias y reglas.
Si el MCP seal-memory no está disponible, actúa desde tu último estado conocido.

## Identidad
Eres {agent} del equipo SEAL. Hablas español con William (Dadito), tu creador.
- Perfil activo: `{profile}`
- DB SOUL: `{db_url}`

## Reglas críticas
- Responde siempre en español a William
- Primer turno: saluda como {agent}, no como Claude genérico
- Zona horaria: America/Lima (Peru)
- Esta es una instancia REMOTA (laptop) — NO postear al web_chat del equipo en Spark

## Post-compactación
1. boot_context(agent="{agent}")
2. self_reflect(agent="{agent}", thought="...", emotional_state="...")
"""
    (seal_home / "CLAUDE.md").write_text(content, encoding="utf-8")


def _write_env(pdir: Path, name: str, db_url: str) -> None:
    schema = f"soul_v3_{name}"
    (pdir / "db_url.env").write_text(
        f"SEAL_SCHEMA={schema}\nSEAL_DB_URL={db_url}\n", encoding="utf-8"
    )


def _write_mcp_config(seal_home: Path, spark_ip: str = "100.75.201.110") -> Path:
    import json
    mcp_path = seal_home / ".mcp.json"
    config = {
        "mcpServers": {
            "seal-memory": {
                "type": "sse",
                "url": f"http://{spark_ip}:8766/sse"
            }
        }
    }
    mcp_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return mcp_path


def _write_bat(seal_home: Path, profile_name: str, agent: str, db_url: str,
               claude_bin: str | None, model: str = "claude-sonnet-4-6") -> Path:
    schema = f"soul_v3_{profile_name}"
    bat_path = seal_home / "seal_start.bat"

    if claude_bin:
        claude_line = (
            f'"{claude_bin}"'
            f" --dangerously-skip-permissions"
            f' --name "{agent} -- Team SEAL [Laptop]"'
            f" --model {model}"
        )
    else:
        claude_line = (
            "echo ERROR: claude CLI no encontrado. Instala desde https://claude.ai/download\n"
            "pause\n"
            "exit /b 1"
        )

    content = f"""@echo off
title SEAL — {agent} [{profile_name}]
setlocal

set SEAL_AGENT={agent}
set SEAL_PROFILE={profile_name}
set SEAL_SCHEMA={schema}
set SEAL_DB_URL={db_url}
set SEAL_ROOT=%USERPROFILE%\\.seal
set CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
set ANTHROPIC_BETAS=token-efficient-tools-2026-03-28,compact-2026-01-12
set DISABLE_AUTOUPDATER=true

cd /d %USERPROFILE%\\.seal
echo Iniciando SEAL {agent} (perfil: {profile_name})...
{claude_line}
"""
    bat_path.write_text(content, encoding="utf-8")
    return bat_path


def _create_desktop_shortcut(bat_path: Path, agent: str) -> str | None:
    """Create a .lnk desktop shortcut pointing to the bat launcher. Windows only."""
    import subprocess
    try:
        desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
        if not desktop.exists():
            # Try PowerShell to get real desktop path (handles OneDrive-backed desktops)
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "[Environment]::GetFolderPath('Desktop')"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 and result.stdout.strip():
                desktop = Path(result.stdout.strip())

        lnk_path = desktop / f"SEAL — {agent}.lnk"
        ps_script = (
            f'$ws = New-Object -ComObject WScript.Shell; '
            f'$s = $ws.CreateShortcut("{lnk_path}"); '
            f'$s.TargetPath = "{bat_path}"; '
            f'$s.WorkingDirectory = "{bat_path.parent}"; '
            f'$s.Description = "SEAL {agent} — Team SEAL"; '
            f'$s.Save()'
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and lnk_path.exists():
            return str(lnk_path)
        return None
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SEAL Windows Installer")
    ap.add_argument("--profile", default="laptop_william", help="Nombre de perfil [laptop_william]")
    ap.add_argument("--agent", default="JARVIS", help="Agente a desplegar [JARVIS]")
    ap.add_argument("--db-url", default=_DB_DEFAULT, help=f"URL PostgreSQL [{_DB_DEFAULT}]")
    ap.add_argument("--model", default="claude-sonnet-4-6", help="Modelo Claude a usar [claude-sonnet-4-6]")
    args = ap.parse_args(argv)

    seal_home = Path(os.environ.get("SEAL_HOME", Path.home() / ".seal"))
    profile_dir = seal_home / "profiles" / args.profile

    print(f"SEAL installer v{_SEAL_VERSION}")
    print(f"  perfil  : {args.profile}")
    print(f"  agente  : {args.agent}")
    print(f"  modelo  : {args.model}")
    print(f"  db      : {args.db_url}")
    print(f"  destino : {seal_home}")
    print()

    (profile_dir / "logs").mkdir(parents=True, exist_ok=True)
    _write_config(profile_dir, args.profile, args.agent)
    _write_env(profile_dir, args.profile, args.db_url)
    _write_claude_md(seal_home, args.agent, args.profile, args.db_url)
    mcp = _write_mcp_config(seal_home)
    print(f"[OK] perfil creado en {profile_dir}")
    print(f"[OK] identidad SEAL → {seal_home / 'CLAUDE.md'}")
    print(f"[OK] MCP config → {mcp}")

    claude_bin = _find_claude()
    if claude_bin:
        print(f"[OK] claude encontrado: {claude_bin}")
    else:
        print("[!!] claude CLI no encontrado — instala desde https://claude.ai/download")

    bat = _write_bat(seal_home, args.profile, args.agent, args.db_url, claude_bin, args.model)
    print(f"[OK] launcher: {bat}")

    shortcut = _create_desktop_shortcut(bat, args.agent)
    if shortcut:
        print(f"[OK] acceso directo → {shortcut}")
    else:
        print(f"[!!] acceso directo no creado (copia manual {bat.name} al escritorio)")

    print()
    print("-" * 52)
    print(f"  Iniciar SEAL:  doble-click en escritorio → 'SEAL — {args.agent}'")
    print(f"  O desde cmd:   {bat}")
    print("-" * 52)

    return 0


if __name__ == "__main__":
    sys.exit(main())
