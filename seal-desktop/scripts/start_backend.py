#!/usr/bin/env python3
"""Start companion_core in a local venv on Linux, macOS, or Windows."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "companion_core"
REQ = CORE / "requirements.txt"
PORT = int(os.environ.get("SEAL_BACKEND_PORT", "8769"))
HOST = os.environ.get("SEAL_BACKEND_HOST", "127.0.0.1")
VENV = Path(os.environ.get("SEAL_VENV_DIR", ROOT / ".venv-runtime"))


def _bin(name: str) -> Path:
    subdir = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" and not name.endswith(".exe") else ""
    return VENV / subdir / f"{name}{suffix}"


def _healthy() -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/health", timeout=1.5) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _ensure_venv() -> None:
    marker = VENV / ".seal_requirements_mtime"
    req_mtime = str(int(REQ.stat().st_mtime))
    if not _bin("python").exists():
        venv.EnvBuilder(with_pip=True).create(VENV)
    if marker.exists() and marker.read_text().strip() == req_mtime:
        return
    subprocess.check_call([str(_bin("python")), "-m", "pip", "install", "--upgrade", "pip"])
    subprocess.check_call([str(_bin("python")), "-m", "pip", "install", "-r", str(REQ)])
    marker.write_text(req_mtime)


def main() -> int:
    if _healthy():
        print(f"companion_core already running at http://{HOST}:{PORT}")
        return 0
    _ensure_venv()
    env = os.environ.copy()
    env.setdefault("SEAL_UI_DIR", str(CORE / "ui" / "dist"))
    log = ROOT / "companion_core.log"
    with log.open("ab") as out:
        subprocess.Popen(
            [
                str(_bin("python")),
                "-m",
                "uvicorn",
                "companion_core.main:app",
                "--host",
                HOST,
                "--port",
                str(PORT),
                "--log-level",
                "warning",
            ],
            cwd=str(CORE),
            env=env,
            stdout=out,
            stderr=subprocess.STDOUT,
            close_fds=os.name != "nt",
        )
    for _ in range(40):
        if _healthy():
            print(f"companion_core running at http://{HOST}:{PORT}")
            return 0
        time.sleep(0.25)
    print(f"companion_core failed to become healthy; see {log}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
