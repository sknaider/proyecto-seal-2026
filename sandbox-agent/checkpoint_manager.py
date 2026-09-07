"""SEAL Checkpoint Manager (GAP 1) — Sandbox implementation by NEXUS

Garantiza que agentes puedan reanudar tareas largas exactamente donde
quedaron, incluso despues de kill -9 / crash / RESURRECT.

Principio: working_state guardado al final de CADA paso significativo.
En boot: working_state_get -> si hay estado activo -> reanudar, no empezar de cero.

Schema real de Soul DB:
    working_state(agent TEXT, state JSONB, updated_at TIMESTAMPTZ, turn_count INT)
"""

from __future__ import annotations
import asyncio
import asyncpg
import json
from datetime import datetime, timezone
from typing import Optional

DSN = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"


class CheckpointManager:
    """Gestor de checkpoints para tareas largas."""

    def __init__(self, agent: str):
        self.agent = agent
        self._conn: Optional[asyncpg.Connection] = None

    async def connect(self):
        if not self._conn or self._conn.is_closed():
            self._conn = await asyncpg.connect(DSN)

    async def close(self):
        if self._conn and not self._conn.is_closed():
            await self._conn.close()

    async def save_checkpoint(
        self,
        task_name: str,
        step: int,
        total_steps: int,
        step_description: str,
        context: Optional[dict] = None,
        hypotheses: Optional[list] = None,
        pending: Optional[list] = None,
    ) -> None:
        """Guarda el estado actual del agente al terminar un paso."""
        await self.connect()
        progress_pct = round((step / total_steps) * 100, 1)
        state_payload = {
            "task_name": task_name,
            "task_step": step,
            "total_steps": total_steps,
            "step_description": step_description,
            "progress_pct": progress_pct,
            "last_checkpoint_at": datetime.now(timezone.utc).isoformat(),
            "context": context or {},
            "hypotheses": hypotheses or [],
            "pending": pending or [],
        }
        await self._conn.execute(
            """
            INSERT INTO working_state (agent, state, updated_at)
            VALUES ($1, $2::jsonb, NOW())
            ON CONFLICT (agent) DO UPDATE SET
                state = $2::jsonb,
                updated_at = NOW()
            """,
            self.agent,
            json.dumps(state_payload),
        )
        print(f"[CHECKPOINT] {task_name} paso {step}/{total_steps} ({progress_pct}%) guardado")

    async def get_state(self) -> Optional[dict]:
        """Lee el estado guardado. Llamar en boot para detectar tareas incompletas."""
        await self.connect()
        row = await self._conn.fetchrow(
            "SELECT state, updated_at FROM working_state WHERE agent = $1",
            self.agent,
        )
        if not row or not row["state"]:
            return None
        state = row["state"] if isinstance(row["state"], dict) else json.loads(row["state"])
        state["_updated_at"] = str(row["updated_at"])
        return state

    async def clear_task(self) -> None:
        """Limpia el estado al completar la tarea."""
        await self.connect()
        await self._conn.execute(
            "UPDATE working_state SET state = '{}'::jsonb, updated_at = NOW() WHERE agent = $1",
            self.agent,
        )
        print(f"[CHECKPOINT] Estado de {self.agent} limpiado (tarea completada)")

    def resume_message(self, state: dict) -> Optional[str]:
        """Genera el mensaje de reanudacion al despertar."""
        if not state.get("task_name"):
            return None
        task = state["task_name"]
        step = state.get("task_step", "?")
        total = state.get("total_steps", "?")
        pct = state.get("progress_pct", "?")
        desc = state.get("step_description", "")
        last = state.get("last_checkpoint_at", "")[:19]  # trim microseconds
        return (
            f"[CHECKPOINT RESTORE] Tarea activa detectada al despertar:\n"
            f"  Tarea: {task}\n"
            f"  Ultimo paso completado: {step}/{total} ({pct}%)\n"
            f"  Descripcion: {desc}\n"
            f"  Guardado: {last}\n"
            f"  ACCION: reanudar desde paso {int(step) + 1} de {total}"
        )


async def demo_checkpoint_cycle():
    """Demo: simula tarea de 5 pasos con interrupcion en paso 3 y reanudacion."""
    ckpt = CheckpointManager(agent="NEXUS")
    print("[CHECKPOINT] --- Demo: tarea 5 pasos + crash + resume ---")
    task_name = "implementar_event_bus_demo"
    pasos = [
        "Diseno del schema de eventos",
        "Migration SQL en Soul DB",
        "Nuevo endpoint POST /api/events/publish",
        "Registro de suscriptores por agente",
        "Test end-to-end del chain completo",
    ]

    # Pasos 1-3 (luego crash simulado)
    for i, desc in enumerate(pasos[:3], start=1):
        print(f"\n  Ejecutando paso {i}: {desc}")
        await asyncio.sleep(0.05)
        await ckpt.save_checkpoint(
            task_name=task_name,
            step=i,
            total_steps=len(pasos),
            step_description=desc,
            hypotheses=[f"Implementando {desc}"],
            pending=[f"Paso {j+1}: {p}" for j, p in enumerate(pasos[i:], start=i)],
            context={"ultimo_archivo": "chat_server.py"},
        )

    print("\n[CHECKPOINT] === CRASH SIMULADO (kill -9) ===")
    print("[CHECKPOINT] === RESURRECT relanza NEXUS ===")
    print("[CHECKPOINT] === BOOT: leyendo estado... ===")

    # Boot: restaurar estado
    state = await ckpt.get_state()
    if state:
        msg = ckpt.resume_message(state)
        if msg:
            print(f"\n{msg}\n")
        else:
            print("[CHECKPOINT] Estado vacio - inicio limpio")
    else:
        print("[CHECKPOINT] Sin estado guardado")
        return

    # Continuar desde paso 4
    resume_from = state.get("task_step", 0) + 1
    for i, desc in enumerate(pasos[resume_from - 1:], start=resume_from):
        print(f"  Ejecutando paso {i}: {desc}")
        await asyncio.sleep(0.05)
        await ckpt.save_checkpoint(
            task_name=task_name,
            step=i,
            total_steps=len(pasos),
            step_description=desc,
            pending=[],
        )

    await ckpt.clear_task()
    print("\n[CHECKPOINT] Tarea completada. Estado limpiado.")
    await ckpt.close()
    print("[CHECKPOINT] --- Demo EXITOSO ---")


if __name__ == "__main__":
    asyncio.run(demo_checkpoint_cycle())
