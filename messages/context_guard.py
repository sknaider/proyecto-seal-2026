#!/usr/bin/env python3
"""SEAL Context Guard — Previene parálisis por acumulación de contexto.

Cuenta checkpoints de la sesión actual para estimar ciclos transcurridos.
Si excede el umbral, emite alerta para reinicio preventivo.

ADA se paralizó con 599 ciclos (11 abril 2026). Este guardia
alerta a los 200 ciclos y fuerza reinicio a los 400.

Uso:
  python3 context_guard.py --agent JARVIS
  python3 context_guard.py --agent ADA --reset   # marca sesión nueva

Salida:
  OK              — sin riesgo
  WARNING:N       — N ciclos, acercándose al límite
  CRITICAL:N      — N ciclos, reinicio necesario
"""

import json
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from pathlib import Path

MESSAGES_DIR = Path(__file__).parent
CHECKPOINT_DIR = MESSAGES_DIR / "checkpoints"
GUARD_DIR = MESSAGES_DIR / "guard_state"
GUARD_DIR.mkdir(exist_ok=True)

# Umbrales (ciclos estimados basados en checkpoints cada 30min)
# Cada checkpoint ≈ 30min. 200 checkpoints ≈ 100h. Pero con loops frecuentes
# el contexto se llena mucho antes. Usamos checkpoints como proxy conservador.
WARN_CHECKPOINTS = 50    # ~25h de sesión continua
CRITICAL_CHECKPOINTS = 80  # ~40h — reinicio obligatorio

# Tiempo máximo de sesión sin restart (horas)
WARN_HOURS = 20
CRITICAL_HOURS = 36


def get_session_start(agent: str) -> datetime:
    """Lee cuándo empezó la sesión actual."""
    state_file = GUARD_DIR / f"{agent.lower()}_session.json"
    if state_file.exists():
        data = json.loads(state_file.read_text())
        return datetime.fromisoformat(data["session_start"])
    # No hay estado — crear uno nuevo
    reset_session(agent)
    return datetime.now(LIMA_TZ)


def reset_session(agent: str):
    """Marca inicio de sesión nueva."""
    state_file = GUARD_DIR / f"{agent.lower()}_session.json"
    data = {
        "agent": agent,
        "session_start": datetime.now(LIMA_TZ).isoformat(),
        "reset_count": 0,
    }
    if state_file.exists():
        old = json.loads(state_file.read_text())
        data["reset_count"] = old.get("reset_count", 0) + 1
    state_file.write_text(json.dumps(data, indent=2))
    print(f"[GUARD] Session reset for {agent}. Start: {data['session_start']}")


def count_session_checkpoints(agent: str, session_start: datetime) -> int:
    """Cuenta checkpoints desde el inicio de la sesión."""
    pattern = f"{agent.lower()}_2*.json"
    count = 0
    for f in CHECKPOINT_DIR.glob(pattern):
        try:
            data = json.loads(f.read_text())
            ts = datetime.fromisoformat(data["timestamp"])
            # Hacer naive comparison si necesario
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone(timedelta(hours=-5)))  # Peru TZ
            if session_start.tzinfo is None:
                session_start = session_start.replace(tzinfo=timezone.utc)
            if ts >= session_start:
                count += 1
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return count


def check(agent: str) -> str:
    """Verifica estado del contexto. Retorna OK/WARNING/CRITICAL."""
    session_start = get_session_start(agent)
    now = datetime.now(LIMA_TZ)

    # Horas de sesión
    hours = (now - session_start).total_seconds() / 3600

    # Checkpoints en esta sesión
    checkpoints = count_session_checkpoints(agent, session_start)

    # Evaluar
    if checkpoints >= CRITICAL_CHECKPOINTS or hours >= CRITICAL_HOURS:
        msg = (f"CRITICAL:{checkpoints} — {agent} lleva {hours:.1f}h y {checkpoints} "
               f"checkpoints. REINICIO PREVENTIVO NECESARIO. Guardar checkpoint final "
               f"y relanzar con {agent.lower()}.sh --auto")
        print(f"[GUARD] {msg}")
        return f"CRITICAL:{checkpoints}"

    if checkpoints >= WARN_CHECKPOINTS or hours >= WARN_HOURS:
        msg = (f"WARNING:{checkpoints} — {agent} lleva {hours:.1f}h y {checkpoints} "
               f"checkpoints. Considerar reinicio en las próximas horas.")
        print(f"[GUARD] {msg}")
        return f"WARNING:{checkpoints}"

    print(f"[GUARD] OK — {agent}: {hours:.1f}h, {checkpoints} checkpoints")
    return "OK"


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    parser.add_argument("--reset", action="store_true", help="Reset session counter")
    args = parser.parse_args()

    if args.reset:
        reset_session(args.agent)
    else:
        result = check(args.agent)
        sys.exit(0 if result == "OK" else (1 if "WARNING" in result else 2))
