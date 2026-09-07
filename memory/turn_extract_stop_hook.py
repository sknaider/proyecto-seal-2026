#!/usr/bin/env python3
"""SEAL Capa 4 — Turn Extractor Stop Hook (spec_seal_context_arch_v3 §7).

Fires from the Stop hook after each assistant turn. Reads the transcript of THIS
session (transcript_path del stdin) and runs turn_extractor.extract_and_store()
in a background subprocess. Non-blocking.

Registered in settings.json → Stop → matcher "" (all agents).

Cura 3-sep-2026 (veredicto FABLE #147281 "H7", caso «los dos cuerpos son uno»):
- El agente sale SOLO de SEAL_AGENT y solo si está en el roster. Antes, un SEAL_AGENT
  desconocido o vacío caía a un mapa por cwd que devolvía 'ADA': la terminal de FABLE
  (y cualquier sesión de este directorio) extraía memorias COMO ADA.
- El transcript es el de la sesión que disparó el hook (transcript_path / session_id
  del stdin). Antes se tomaba el .jsonl MÁS RECIENTE del directorio compartido: una
  sesión podía extraer el transcript de OTRA y guardarlo bajo su agente.
- El hook corre únicamente dentro de Claude Code, así que el cuerpo es '<AGENTE>_CLAUDE'
  salvo que el lanzador exporte SEAL_RUNTIME_INSTANCE.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

VENV_PY = "/home/dadito/IA/seal-spark/.venv/bin/python3"
MEMORY_DIR = Path(__file__).parent
RUNNER = MEMORY_DIR / "_turn_extract_runner.py"
ROSTER = ("ADA", "JARVIS", "ALICE", "NEXUS")


def resolve_agent(env: dict | None = None) -> str:
    """Agente = SEAL_AGENT exacto y en el roster; cualquier otra cosa -> '' (no se extrae)."""
    env = os.environ if env is None else env
    value = str(env.get("SEAL_AGENT", "")).strip().upper()
    return value if value in ROSTER else ""


def resolve_transcript(data: dict) -> str:
    """Solo el transcript que Claude Code declara para ESTA sesión. Sin adivinar por mtime."""
    path = str(data.get("transcript_path") or "").strip()
    if not path or not os.path.isfile(path):
        return ""
    return path


def runtime_instance_for(agent: str, env: dict | None = None) -> str:
    env = os.environ if env is None else env
    explicit = str(env.get("SEAL_RUNTIME_INSTANCE", "")).strip()
    return explicit or f"{agent}_CLAUDE"


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        data = {}

    agent = resolve_agent()
    if not agent:
        sys.exit(0)

    transcript = resolve_transcript(data)
    if not transcript:
        sys.exit(0)

    env = os.environ.copy()
    env["SEAL_AGENT"] = agent
    env["SEAL_TRANSCRIPT"] = transcript
    env["SEAL_RUNTIME_INSTANCE"] = runtime_instance_for(agent, env)
    if data.get("session_id"):
        env["SEAL_SESSION_ID_HOOK"] = str(data["session_id"])
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
