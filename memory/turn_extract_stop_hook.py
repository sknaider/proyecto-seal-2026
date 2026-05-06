#!/usr/bin/env python3
"""SEAL Capa 4 — Turn Extractor Stop Hook (spec_seal_context_arch_v3 §7).

Fires from the Stop hook after each assistant turn.
Reads the last assistant exchange from the transcript JSONL,
then runs turn_extractor.extract_and_store() in a background subprocess.

Non-blocking: launches subprocess and returns immediately (< 5ms).
The extraction (turn_extractor.py) runs with timeout and stores facts
as type='fact' memories in soul_v3.memories.

Registered in settings.json → Stop → matcher "" (all agents).
"""
from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from pathlib import Path

VENV_PY = "/home/dadito/IA/seal-spark/.venv/bin/python3"
MEMORY_DIR = Path(__file__).parent
RUNNER = MEMORY_DIR / "_turn_extract_runner.py"

AGENT_CWD_MAP = {
    "sandbox-agent": "NEXUS",
    "proyecto-seal/memory": "JARVIS",
    "proyecto-seal": "ADA",
}


def detect_agent_from_cwd(cwd: str) -> str:
    env_agent = os.environ.get("SEAL_AGENT", "").upper()
    if env_agent and env_agent in ("ADA", "JARVIS", "ALICE", "NEXUS"):
        return env_agent
    for fragment, agent in AGENT_CWD_MAP.items():
        if fragment in cwd:
            return agent
    return ""


def get_project_dir(cwd: str) -> str:
    home = os.path.expanduser("~")
    if "sandbox-agent" in cwd:
        return os.path.join(home, ".claude/projects/-home-dadito-IA-proyecto-seal-sandbox-agent-NEXUS")
    if "proyecto-seal/memory" in cwd or "proyecto-seal\\memory" in cwd:
        return os.path.join(home, ".claude/projects/-home-dadito-IA-proyecto-seal-memory")
    if "proyecto-seal" in cwd:
        return os.path.join(home, ".claude/projects/-home-dadito-IA-proyecto-seal")
    if "/IA" in cwd:
        return os.path.join(home, ".claude/projects/-home-dadito-IA")
    return ""


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        data = {}

    cwd = data.get("cwd", os.getcwd())
    agent = detect_agent_from_cwd(cwd)
    if not agent:
        sys.exit(0)

    proj_dir = get_project_dir(cwd)
    if not proj_dir:
        sys.exit(0)

    jsonl_files = sorted(
        glob.glob(f"{proj_dir}/*.jsonl"),
        key=os.path.getmtime,
        reverse=True,
    )
    if not jsonl_files:
        sys.exit(0)

    transcript = jsonl_files[0]

    env = os.environ.copy()
    env["SEAL_AGENT"] = agent
    env["SEAL_TRANSCRIPT"] = transcript
    # Force Ollama/fallback — do NOT call Haiku API from inside a Claude session
    env["ANTHROPIC_API_KEY"] = ""

    subprocess.Popen(
        [VENV_PY, str(RUNNER)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    sys.exit(0)


if __name__ == "__main__":
    main()
