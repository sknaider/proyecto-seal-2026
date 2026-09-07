#!/usr/bin/env python3
"""Sync ADA bridge files into Windows Codex project folders."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


WINDOWS_HOST = os.environ.get("ADA_CODEX_WINDOWS_HOST", "daditogamer")
SOURCE_DIR = Path("/home/dadito/IA/proyecto-seal/windows/codex_app_ada_bridge")
TOKEN_FILE = Path("/tmp/seal/ada_codex_app_bridge.token")
SOUL_CLONE_TOKEN_FILE = Path("/tmp/seal/ada_codex_app_soul_clone.token")
SYNC_INTERVAL = int(os.environ.get("ADA_CODEX_WINDOWS_SYNC_INTERVAL", "30"))
OFFLINE_INTERVAL = int(os.environ.get("ADA_CODEX_WINDOWS_OFFLINE_INTERVAL", "300"))
SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]


def run(args: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired as exc:
        stderr = f"timeout after {exc.timeout}s"
        if exc.stderr:
            stderr = f"{stderr}: {exc.stderr}"
        return subprocess.CompletedProcess(args, 124, exc.stdout or "", stderr)


def host_alive(host: str) -> bool:
    """Require a real authenticated SSH handshake, not just an open TCP port."""
    result = run(["ssh", *SSH_OPTIONS, host, "exit 0"])
    return result.returncode == 0


def list_windows_projects() -> list[str]:
    command = (
        "powershell -NoProfile -Command "
        "\"$base=$env:USERPROFILE+'\\\\Documents\\\\Codex'; "
        "if(Test-Path $base){"
        "Get-ChildItem $base -Directory | ForEach-Object {"
        "if($_.Name -match '^\\d{4}-\\d{2}-\\d{2}$'){"
        "Get-ChildItem $_.FullName -Directory | Where-Object {$_.Name -notmatch '^\\.'} | ForEach-Object {$_.FullName}"
        "}elseif($_.Name -notmatch '^\\.'){$_.FullName}"
        "}"
        "}\""
    )
    result = run(["ssh", *SSH_OPTIONS, WINDOWS_HOST, command])
    if result.returncode != 0:
        print(f"[codex-windows-sync] list failed: {result.stderr.strip()}", flush=True)
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def target_to_scp_path(target: str) -> str:
    # OpenSSH scp on Windows accepts paths relative to the user's home with /.
    marker = "\\Users\\Dadito\\"
    if marker in target:
        rel = target.split(marker, 1)[1].replace("\\", "/")
        return f"{WINDOWS_HOST}:{rel}/"
    return f"{WINDOWS_HOST}:{target}/"


def sync_project(target: str) -> bool:
    scp_target = target_to_scp_path(target)
    mkdir_cmd = (
        "powershell -NoProfile -Command "
        f"\"New-Item -ItemType Directory -Force '{target}\\\\.seal' | Out-Null\""
    )
    run(["ssh", *SSH_OPTIONS, WINDOWS_HOST, mkdir_cmd])
    files = [
        SOURCE_DIR / "AGENTS.md",
        SOURCE_DIR / "README.md",
        SOURCE_DIR / "send-to-ada.ps1",
        SOURCE_DIR / "finish-shadow-work.ps1",
        SOURCE_DIR / "status.ps1",
        SOURCE_DIR / "ada-codex-tools.ps1",
        SOURCE_DIR / "ADA-Codex-Tools.bat",
        SOURCE_DIR / "launch-codex-app-visible.ps1",
        SOURCE_DIR / "ADA-Codex-App.bat",
        SOURCE_DIR / "ada-shadow-clone.ps1",
        SOURCE_DIR / "ADA-Shadow-Clone.bat",
    ]
    result = run(["scp", "-q", *SSH_OPTIONS, *map(str, files), scp_target])
    token_result = run(["scp", "-q", *SSH_OPTIONS, str(TOKEN_FILE), target_to_scp_path(f"{target}\\.seal") + "bridge.token"])
    ok = result.returncode == 0 and token_result.returncode == 0
    if ok:
        print(f"[codex-windows-sync] synced {target}", flush=True)
    else:
        print(f"[codex-windows-sync] sync failed {target}: {result.stderr} {token_result.stderr}", flush=True)
    return ok


def sync_global_agents() -> None:
    global_file = SOURCE_DIR / "GLOBAL_AGENTS.md"
    for target in [f"{WINDOWS_HOST}:Documents/Codex/AGENTS.md", f"{WINDOWS_HOST}:AGENTS.md"]:
        result = run(["scp", "-q", *SSH_OPTIONS, str(global_file), target])
        if result.returncode != 0:
            print(f"[codex-windows-sync] global sync failed {target}: {result.stderr.strip()}", flush=True)


def sync_global_bridge_tokens() -> bool:
    """Keep both independently revocable Windows bridge identities current."""
    remote_dir = f"{WINDOWS_HOST}:ADA-Codex-Bridge/.seal/"
    results = [
        run(
            [
                "scp",
                "-q",
                *SSH_OPTIONS,
                str(TOKEN_FILE),
                f"{remote_dir}bridge.token",
            ]
        ),
        run(
            [
                "scp",
                "-q",
                *SSH_OPTIONS,
                str(SOUL_CLONE_TOKEN_FILE),
                f"{remote_dir}soul_clone.token",
            ]
        ),
    ]
    ok = all(result.returncode == 0 for result in results)
    if not ok:
        errors = " ".join(
            result.stderr.strip() for result in results if result.returncode != 0
        )
        print(f"[codex-windows-sync] global token sync failed: {errors}", flush=True)
    return ok


def bridge_token_signature() -> tuple[tuple[int, int], ...] | None:
    """Return a non-secret change detector for the two local token files."""
    try:
        return tuple(
            (token.stat().st_size, token.stat().st_mtime_ns)
            for token in (TOKEN_FILE, SOUL_CLONE_TOKEN_FILE)
        )
    except FileNotFoundError:
        return None


def main() -> None:
    print("[codex-windows-sync] started", flush=True)
    seen: set[str] = set()
    synced_token_signature: tuple[tuple[int, int], ...] | None = None
    while True:
        if not host_alive(WINDOWS_HOST):
            print(
                f"[codex-windows-sync] host {WINDOWS_HOST} unavailable; "
                f"retrying in {OFFLINE_INTERVAL}s",
                flush=True,
            )
            time.sleep(OFFLINE_INTERVAL)
            continue
        token_signature = bridge_token_signature()
        if token_signature is None:
            print("[codex-windows-sync] local bridge token missing", flush=True)
        elif token_signature != synced_token_signature and sync_global_bridge_tokens():
            synced_token_signature = token_signature
        sync_global_agents()
        for project in list_windows_projects():
            agents_path_cmd = (
                "powershell -NoProfile -Command "
                f"\"if(Test-Path '{project}\\\\AGENTS.md'){{Write-Output present}}\""
            )
            needs_sync = project not in seen
            if not needs_sync:
                result = run(["ssh", *SSH_OPTIONS, WINDOWS_HOST, agents_path_cmd])
                needs_sync = "present" not in result.stdout
            if needs_sync and sync_project(project):
                seen.add(project)
        time.sleep(SYNC_INTERVAL)


if __name__ == "__main__":
    main()
