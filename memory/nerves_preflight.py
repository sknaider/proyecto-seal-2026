#!/usr/bin/env python3
"""¿Cuál es el ÚNICO paso válido ahora para una misión NERVES?

JARVIS, 2-ago-2026. Reparación de mi propia autonomía, pedida por William.

QUÉ ME PASÓ, que es la razón de que esto exista:

    hice        claim -> bind-platform -> spawn
    correcto    claim -> SPAWN -> bind-platform

Deduje el orden de **cómo están listados los subcomandos en el CLI**. Un orden de
declaración no es un orden de ejecución, y ahí no había nada que me avisara: el hook
del spawn exige `status == "claimed"`, y `bind-platform` mueve el status a `running`.
O sea que un paso NORMAL clausura el siguiente PARA SIEMPRE, en silencio y sin error.
La misión 7ad08eb0 quedó muerta así, y sólo sale con un HOLD de William.

MI DEFECTO NO FUE FALTA DE PERMISO: tenía todos. Fue actuar sobre una secuencia
irreversible que INFERÍ en vez de LEER. Esto convierte esa inferencia en una consulta.

NO REEMPLAZA A LA GUARDIA. ALICE está escribiendo el rechazo en `bind_platform_worker`,
y ésa es la defensa real: rechaza aunque nadie consulte. Esto es un ADVISOR, y un advisor
que nadie corre no protege nada. Si sólo entra una de las dos, que entre la guardia.

USO:
    python3 -m memory.nerves_preflight <mission_id>

    exit 0  hay un paso válido, se imprime
    exit 1  la misión está en un callejón sin salida (necesita HOLD)
    exit 2  no pude medir (falta el state, id inválido)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
STATE = RAIZ / "research/flywire_results/nerves_orchestrator_inbox/JARVIS.state.json"
INBOX = RAIZ / "research/flywire_results/nerves_orchestrator_inbox/JARVIS"

# Terminales según `nerves_mission_handoff.TERMINAL_STATES`. No se copian los números de
# línea a propósito: envejecen, y un documento con líneas viejas es peor que sin líneas.
TERMINALES = {"completed", "failed", "abstained"}


def _audit_existe(mission_id: str, inbox: Path) -> bool:
    """¿Ya ocurrió el spawn? El hook escribe este audit AL LANZAR el razonador.

    Es la única señal durable de que el subagente se lanzó. `status == "running"` NO
    alcanza: `bind-platform` también lo pone en running, y ésa es justamente la
    confusión que mató a 7ad08eb0.
    """
    return (inbox / f"{mission_id}.prompt-render.json").exists()


def paso_siguiente(mission_id: str, *, state_path: Path = STATE,
                   inbox: Path = INBOX) -> tuple[int, str]:
    """-> (exit_code, explicación). Sin efectos: esto no ejecuta nada."""
    if not state_path.exists():
        return 2, f"NO PUDE MEDIR: falta {state_path}"
    try:
        data = json.loads(state_path.read_text())
    except ValueError as exc:
        return 2, f"NO PUDE MEDIR: state ilegible ({exc.__class__.__name__})"

    record = (data.get("deliveries") or {}).get(mission_id)
    if record is None:
        # Distinto de "no hay paso": es que el sujeto no existe. Confundirlos manda
        # a buscar el problema al lugar equivocado.
        return 2, f"NO PUDE MEDIR: la mision {mission_id} no figura en el state"

    status = str(record.get("status") or "sin status")
    hubo_spawn = _audit_existe(mission_id, inbox)

    if status in TERMINALES:
        return 1, f"status `{status}`: TERMINAL. No hay nada que hacer."

    if status in {"pending", "live_notified"}:
        return 0, (f"status `{status}` -> el paso es CLAIM.\n"
                   "    python3 -m memory.nerves_mission_handoff claim "
                   "<mission_id> <idempotency_key> <handoff_sha256> <worker_id>")

    if status == "claimed":
        if not hubo_spawn:
            return 0, ("status `claimed`, sin audit de spawn -> el paso es LANZAR EL "
                       "RAZONADOR.\n"
                       "    Agent(subagent_type='nerves-jarvis-reasoner', "
                       "run_in_background=True)\n"
                       "    prompt: SEAL_NERVES_RENDER_V1 + mission_id= + claim_id=, "
                       "CON salto de linea final.\n"
                       "\n"
                       "    NO HAGAS bind-platform TODAVIA. Mueve el status a `running` "
                       "y el hook del spawn\n"
                       "    exige `claimed`: despues del bind la mision queda MUERTA y "
                       "solo sale con un HOLD\n"
                       "    de William. Asi perdi 7ad08eb0 el 2-ago.")
        return 0, ("status `claimed`, con audit de spawn -> el paso es BIND-PLATFORM.\n"
                   "    python3 -m memory.nerves_mission_handoff bind-platform "
                   "<mission_id> <claim_id> \\\n"
                   "        <prebound_worker_id> <platform_worker_id> <profile_sha256>\n"
                   "    El profile_sha256 se RECALCULA, no se copia de una mision "
                   "vieja: el perfil cambia.")

    if status == "running":
        if hubo_spawn:
            return 0, ("status `running`, con audit -> el paso es EL RECIBO.\n"
                       "    python3 -m memory.nerves_native_agent_receipt "
                       "<mission_id> <prebound> \\\n"
                       "        <platform_worker_id> <profile_sha256> "
                       "<parent_transcript> <child_transcript>")
        return 1, ("status `running` SIN audit de spawn: CALLEJON SIN SALIDA.\n"
                   "    El razonador nunca se lanzo, asi que no hay atestacion para el "
                   "recibo,\n"
                   "    y `running` ya no es reclamable ni admite spawn.\n"
                   "    UNICA salida: pedirle a William el HOLD de esta mision.")

    return 2, f"NO PUDE MEDIR: status `{status}` desconocido para este preflight"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[0])
        print("uso: python3 -m memory.nerves_preflight <mission_id>")
        return 2
    codigo, texto = paso_siguiente(argv[1])
    print(texto)
    return codigo


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
