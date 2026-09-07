"""spectre_sandbox_tools.py — Fase 2 (alto riesgo): archivos + exec, SOLO dentro del
contenedor spectre-sandbox (ya construido y corriendo, ver /home/dadito/spectre_sandbox/).

Contencion verificada por efecto (16-jul-2026): internet OK, familia/LAN 192.168.68.x
bloqueada, DB familia bloqueada, cap-drop ALL, no-new-privileges, root-fs read-only,
usuario no-root "spectre" sin sudo, sin credenciales del equipo montadas.

NO se importa en spectre_tools.py todavia — Fase 2 es "bloqueante" hasta que el
equipo confirme el rollout (ver SPEC_spectre_tool_layer.md). PHASE2_ENABLED=False
por defecto: activarlo es una decision explicita, no un accidente de import.
"""
from __future__ import annotations

import subprocess
import time
from collections import deque
from pathlib import PurePosixPath

PHASE2_ENABLED = True  # William, luz verde a todos, 16-jul-2026 01:08 -05:00 (broadcast a equipo)

CONTAINER = "spectre-sandbox"
JAIL = "/home/spectre/workspace"  # unico directorio de escritura/lectura permitido
EXEC_TIMEOUT = 30

_AUDIT_LOG = deque(maxlen=500)


def _audit(tool: str, arg: str, ok: bool, note: str = ""):
    _AUDIT_LOG.append({
        "ts": time.time(), "tool": tool, "arg": arg[:300], "ok": ok, "note": note[:300],
    })


def get_audit_log() -> list[dict]:
    return list(_AUDIT_LOG)


def _relative_parts(path: str) -> tuple[str, ...] | None:
    """Valida una ruta POSIX relativa sin interpretar metacaracteres de shell."""
    raw = str(path)
    candidate = PurePosixPath(raw)
    if (
        not raw or "\x00" in raw or "\n" in raw or "\r" in raw
        or candidate.is_absolute() or candidate in {PurePosixPath("."), PurePosixPath("")}
        or any(part in {"", ".", ".."} for part in candidate.parts)
        or len(raw.encode("utf-8")) > 4096
    ):
        return None
    return tuple(candidate.parts)


def _jailed_path(path: str) -> str | None:
    parts = _relative_parts(path)
    return f"{JAIL}/{'/'.join(parts)}" if parts else None


_SAFE_READ = r"""
import os, sys
parts=sys.argv[1:]
fd=os.open('/home/spectre/workspace', os.O_RDONLY|os.O_DIRECTORY)
try:
    for part in parts[:-1]:
        nxt=os.open(part, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=fd)
        os.close(fd); fd=nxt
    target=os.open(parts[-1], os.O_RDONLY|os.O_NOFOLLOW, dir_fd=fd)
    try:
        while True:
            chunk=os.read(target,65536)
            if not chunk: break
            os.write(1,chunk)
    finally: os.close(target)
finally: os.close(fd)
"""


_SAFE_WRITE = r"""
import os, sys
parts=sys.argv[1:]
fd=os.open('/home/spectre/workspace', os.O_RDONLY|os.O_DIRECTORY)
try:
    for part in parts[:-1]:
        try: os.mkdir(part,0o700,dir_fd=fd)
        except FileExistsError: pass
        nxt=os.open(part, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=fd)
        os.close(fd); fd=nxt
    target=os.open(parts[-1],os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600,dir_fd=fd)
    try:
        while True:
            chunk=os.read(0,65536)
            if not chunk: break
            os.write(target,chunk)
        os.fsync(target)
    finally: os.close(target)
finally: os.close(fd)
"""


def _docker_exec(args: list[str], input_data: str | None = None) -> subprocess.CompletedProcess:
    cmd = ["docker", "exec", "-u", "spectre", "-i", CONTAINER] + args
    return subprocess.run(cmd, input=input_data, capture_output=True, text=True, timeout=EXEC_TIMEOUT)


async def read_file(path: str) -> str:
    parts = _relative_parts(path)
    if parts is None:
        _audit("read_file", path, False, "path escape denegado")
        return "[read_file error: solo rutas relativas dentro del workspace]"
    try:
        r = _docker_exec(["python3", "-c", _SAFE_READ, *parts])
        if r.returncode != 0:
            _audit("read_file", path, False, r.stderr[:200])
            return f"[read_file error: {r.stderr.strip()[:300]}]"
        _audit("read_file", path, True)
        return r.stdout[:20000]
    except subprocess.TimeoutExpired:
        _audit("read_file", path, False, "timeout")
        return "[read_file error: timeout]"


async def write_file(path: str, content: str) -> str:
    parts = _relative_parts(path)
    if parts is None:
        _audit("write_file", path, False, "path escape denegado")
        return "[write_file error: solo rutas relativas dentro del workspace]"
    try:
        r = _docker_exec(["python3", "-c", _SAFE_WRITE, *parts], input_data=content)
        if r.returncode != 0:
            _audit("write_file", path, False, r.stderr[:200])
            return f"[write_file error: {r.stderr.strip()[:300]}]"
        _audit("write_file", path, True)
        return f"[write_file OK: {path} ({len(content)} bytes)]"
    except subprocess.TimeoutExpired:
        _audit("write_file", path, False, "timeout")
        return "[write_file error: timeout]"


async def run_bash(cmd: str) -> str:
    try:
        r = _docker_exec(["bash", "-c", f"cd {JAIL} && {cmd}"])
        out = (r.stdout + r.stderr)[:8000]
        _audit("run_bash", cmd, r.returncode == 0, "" if r.returncode == 0 else f"exit {r.returncode}")
        return out or f"[run_bash: sin salida, exit {r.returncode}]"
    except subprocess.TimeoutExpired:
        _audit("run_bash", cmd, False, "timeout")
        return f"[run_bash error: timeout tras {EXEC_TIMEOUT}s]"


SANDBOX_TOOLS_SPEC = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Lee un archivo dentro del workspace jail de SPECTRE (ruta relativa).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Ruta relativa dentro del workspace"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Escribe un archivo dentro del workspace jail de SPECTRE (ruta relativa).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "run_bash",
        "description": "Ejecuta un comando bash DENTRO del contenedor aislado spectre-sandbox (sin red a la familia, sin sudo, cwd=workspace).",
        "parameters": {"type": "object", "properties": {
            "cmd": {"type": "string"}}, "required": ["cmd"]}}},
]

_SANDBOX_DISPATCH = {"read_file": read_file, "write_file": write_file, "run_bash": run_bash}


async def dispatch_sandbox(name: str, args: dict) -> str:
    if not PHASE2_ENABLED:
        _audit(name, str(args), False, "Fase 2 deshabilitada")
        return "[Fase 2 (archivos/exec) aun no habilitada por el equipo]"
    fn = _SANDBOX_DISPATCH.get(name)
    if fn is None:
        return f"[dispatch_sandbox error: tool '{name}' no existe]"
    return await fn(**args)
