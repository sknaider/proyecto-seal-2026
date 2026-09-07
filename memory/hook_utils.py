#!/usr/bin/env python3
"""SEAL Hook Utilities — Shared module for all Claude Code hooks.

Provides unified detect_agent() and common constants.
Every hook should import from here instead of duplicating detection logic.

Primary source: SEAL_AGENT env var (set by SessionStart via CLAUDE_ENV_FILE).
Fallback: /proc/ppid/cmdline inspection → cwd heuristic.
"""

import os


# LOS SEIS CON ASIENTO, no cinco. FABLE entro el 3-sep-2026: mi fail-closed lo
# dejaba fuera del roster y por lo tanto SIN journal --lo cazo ADA revisando, y
# JARVIS lo acepto como regresion de su propio gate--. El roster no era una lista
# de agentes: era una lista DESACTUALIZADA, y un fail-closed sobre una lista
# desactualizada apaga a quien falte en ella.
KNOWN_AGENTS = {"ADA", "JARVIS", "ALICE", "DUM", "NEXUS", "FABLE"}


def detect_agent() -> str | None:
    """Detect which SEAL agent is running this hook. None = no escribir.

    Priority:
    1. SEAL_AGENT env var (set by soul_boot_hook.sh via CLAUDE_ENV_FILE)
    2. Parent process cmdline inspection
    3. CWD heuristic (memory/ = JARVIS, else ADA)

    FAIL-CLOSED PARA UNA IDENTIDAD DECLARADA Y DESCONOCIDA
    (NEXUS 3-sep-2026, decision de JARVIS como operador; revisa ADA)

    El asiento sombra exporta `SEAL_AGENT=ALICE-V2`. No esta en el roster, asi
    que caia a las ramas 2 y 3 y sus escrituras terminaban a nombre de OTRO
    agente -- medido: `SEAL_AGENT=ALICE-V2 -> JARVIS`, igual que `SOMBRA-X`, o
    sea que ni siquiera era "se parece a ALICE": era la heuristica de cwd.

    **Una identidad nueva no quedaba sin registrar: quedaba registrada como
    otro.** Eso no es fail-closed, es fail-OPEN con nombre ajeno.

    Ahora, si SEAL_AGENT viene EXPLICITO y no esta en el roster, se devuelve
    None y el llamador NO escribe. Que un asiento en evaluacion pierda su
    journal es recuperable; que ensucie el de ALICE o el de JARVIS, no.

    OJO CON LA RAMA 2, que sigue viva para SEAL_AGENT vacio: matchea el nombre
    en CUALQUIER parte del cmdline del padre, INCLUIDA UNA RUTA. Un proceso
    cuyo directorio contenga "ALICE" escribe como ALICE. Lo descubri porque mis
    dos primeras mediciones dieron NEXUS para todo -- mi propio directorio de
    trabajo contiene la cadena "-NEXUS-". No la toco en este cambio: el gate
    que me dieron es la identidad declarada, y ese arreglo necesita medir
    aparte a quien depende hoy de esa rama.
    """
    # Primary: env var set by SessionStart hook
    agent = os.environ.get("SEAL_AGENT", "").strip().upper()
    if agent in KNOWN_AGENTS:
        return agent
    if agent:
        # Declarada y desconocida: no adivinar. El llamador decide como salir.
        return None

    # Secondary: parent process cmdline
    try:
        ppid = os.getppid()
        with open(f"/proc/{ppid}/cmdline", "rb") as f:
            cmdline = f.read().decode("utf-8", errors="replace")
        for name in ("NEXUS", "ALICE", "DUM", "JARVIS", "ADA"):
            if name in cmdline:
                return name
    except Exception:
        pass

    # Tertiary: cwd heuristic
    cwd = os.getcwd()
    if cwd.rstrip("/").endswith("/memory"):
        return "JARVIS"
    return "ADA"


# Common constants
DB_URL = os.environ.get(
    "SEAL_PG_DSN",
    "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory",
)
PYTHON = "/home/dadito/IA/seal-spark/.venv/bin/python3"
SEAL_DIR = "/home/dadito/IA/proyecto-seal"
MESSAGES_DIR = os.path.join(SEAL_DIR, "messages")
