"""SEAL Procedure Evolution (GAP 4) — Sandbox implementation by NEXUS

Los procedures exitosos suben en prioridad; los que fallan se degradan.
Cuando un procedure falla 3 veces consecutivas, el agente propone una
nueva version y solicita aprobacion de William.

Evolucion supervisada: William aprueba cambios. Historial completo,
nada se borra nunca. Rollback = activar la version anterior.

Schema en Soul DB (procedural_memories) — columnas nuevas F4:
    version               INT        version del procedure (empieza en 1)
    parent_version_id     BIGINT     id del procedure que este reemplaza
    success_rate          FLOAT      tasa de exito EMA (alpha=0.2)
    consecutive_fail_count INT       fallos consecutivos sin exito entre medio
    last_improved_at      TIMESTAMPTZ fecha de ultima mejora
    pending_revision      BOOLEAN    True = esperando aprobacion de William
"""

from __future__ import annotations
import asyncio
import asyncpg
import json
from datetime import datetime, timezone
from typing import Optional

DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
EMA_ALPHA = 0.2  # factor de suavizamiento: mas bajo = mas inercial
FAIL_THRESHOLD = 3  # fallos consecutivos que disparan propuesta de revision


class ProcedureEvolution:
    """Gestor de evolucion de procedures."""

    def __init__(self, agent: str):
        self.agent = agent
        self._conn: Optional[asyncpg.Connection] = None

    async def connect(self):
        if not self._conn or self._conn.is_closed():
            self._conn = await asyncpg.connect(DSN)

    async def close(self):
        if self._conn and not self._conn.is_closed():
            await self._conn.close()

    async def record_outcome(
        self,
        procedure_id: int,
        success: bool,
        context: Optional[str] = None,
    ) -> dict:
        """Registra el resultado de usar un procedure.
        Actualiza metricas via EMA. Si falla 3 veces consecutivas,
        retorna {propose_revision: True} para que el agente notifique a William.
        """
        await self.connect()
        proc = await self._conn.fetchrow(
            "SELECT * FROM procedural_memories WHERE id=$1",
            procedure_id,
        )
        if not proc:
            raise ValueError(f"Procedure {procedure_id} no encontrado")

        # Calcular nuevas metricas
        old_rate = float(proc["success_rate"] or 0.5)
        new_rate = EMA_ALPHA * (1.0 if success else 0.0) + (1 - EMA_ALPHA) * old_rate
        new_success = proc["success_count"] + (1 if success else 0)
        new_fail = proc["fail_count"] + (0 if success else 1)
        new_hit = proc["hit_count"] + 1
        new_consec = 0 if success else (proc["consecutive_fail_count"] or 0) + 1
        propose_revision = False

        await self._conn.execute(
            """
            UPDATE procedural_memories SET
                hit_count = $1,
                success_count = $2,
                fail_count = $3,
                success_rate = $4,
                consecutive_fail_count = $5,
                updated_at = NOW()
            WHERE id = $6
            """,
            new_hit, new_success, new_fail, new_rate, new_consec, procedure_id,
        )

        result = {
            "procedure_id": procedure_id,
            "success": success,
            "new_success_rate": round(new_rate, 3),
            "consecutive_fails": new_consec,
            "propose_revision": False,
        }

        if new_consec >= FAIL_THRESHOLD:
            await self._conn.execute(
                "UPDATE procedural_memories SET pending_revision=TRUE WHERE id=$1",
                procedure_id,
            )
            propose_revision = True
            result["propose_revision"] = True
            result["revision_message"] = (
                f"[PROCEDURE EVOLUTION] El procedure id={procedure_id} "
                f"('{proc['query'][:60]}') fallo {new_consec} veces consecutivas. "
                f"success_rate actual: {new_rate:.1%}. "
                f"Propongo crear version {(proc['version'] or 1) + 1}. "
                f"Contexto del ultimo fallo: {context or 'no especificado'}. "
                f"William, apruebas la revision? Responde 'si' o 'no'."
            )

        label = "EXITO" if success else "FALLO"
        print(f"[PROC EVOLUTION] id={procedure_id} {label} | rate={new_rate:.1%} | consec_fails={new_consec}")
        if propose_revision:
            print(f"[PROC EVOLUTION] REVISION PROPUESTA (umbral={FAIL_THRESHOLD} alcanzado)")
        return result

    async def create_revision(
        self,
        parent_id: int,
        new_workflow: str,
        improvement_notes: str,
        approved_by: str = "William",
    ) -> int:
        """Crea una nueva version de un procedure (post-aprobacion de William).
        La version vieja se desactiva, la nueva queda activa.
        """
        await self.connect()
        parent = await self._conn.fetchrow(
            "SELECT * FROM procedural_memories WHERE id=$1",
            parent_id,
        )
        if not parent:
            raise ValueError(f"Procedure padre {parent_id} no encontrado")

        new_version = (parent["version"] or 1) + 1
        new_id = await self._conn.fetchval(
            """
            INSERT INTO procedural_memories
                (agent, task_type, query, workflow, facts, version,
                 parent_version_id, active, success_rate, hit_count,
                 success_count, fail_count, last_improved_at, source_task, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE, 0.5, 0, 0, 0, NOW(), $8, NOW(), NOW())
            RETURNING id
            """,
            self.agent,
            parent["task_type"],
            parent["query"],
            new_workflow,
            json.dumps({"improvement_notes": improvement_notes, "approved_by": approved_by}),
            new_version,
            parent_id,
            f"Revision aprobada por {approved_by} - mejora de v{parent['version'] or 1}",
        )

        # Desactivar la version vieja
        await self._conn.execute(
            "UPDATE procedural_memories SET active=FALSE, pending_revision=FALSE WHERE id=$1",
            parent_id,
        )

        print(f"[PROC EVOLUTION] Nueva version v{new_version} creada: id={new_id} (padre: id={parent_id})")
        return new_id

    async def get_procedure_history(self, procedure_id: int) -> list[dict]:
        """Retorna todas las versiones de un procedure (cadena completa)."""
        await self.connect()
        versions = []
        current_id = procedure_id
        visited = set()

        while current_id and current_id not in visited:
            visited.add(current_id)
            row = await self._conn.fetchrow(
                "SELECT id, version, query, success_rate, active, hit_count, fail_count, parent_version_id, created_at FROM procedural_memories WHERE id=$1",
                current_id,
            )
            if row:
                versions.append(dict(row))
                current_id = row["parent_version_id"]
            else:
                break

        return list(reversed(versions))

    async def top_procedures(
        self, task_type: Optional[str] = None, limit: int = 5
    ) -> list[dict]:
        """Devuelve los procedures mejor rankeados por success_rate."""
        await self.connect()
        base_filter = "agent=$1 AND active=TRUE"
        args = [self.agent]
        if task_type:
            base_filter += " AND task_type=$2"
            args.append(task_type)
            args.append(limit)
        else:
            args.append(limit)

        rows = await self._conn.fetch(
            f"SELECT id, version, query, success_rate, hit_count FROM procedural_memories "
            f"WHERE {base_filter} ORDER BY success_rate DESC, hit_count DESC LIMIT ${len(args)}",
            *args,
        )
        return [dict(r) for r in rows]


async def demo_evolution_cycle():
    """Demo: procedure exitoso x3, luego 3 fallos consecutivos -> propuesta revision."""
    pe = ProcedureEvolution(agent="NEXUS")
    print("[PROC EVOLUTION] --- Demo: ciclo de evolucion ---")

    # Crear un procedure de prueba
    conn = await asyncpg.connect(DSN)
    proc_id = await conn.fetchval(
        """
        INSERT INTO procedural_memories
            (agent, task_type, query, workflow, active, version,
             success_rate, hit_count, success_count, fail_count,
             consecutive_fail_count, created_at, updated_at)
        VALUES ($1, 'test_evolution', 'procedimiento de prueba F4',
                'workflow de prueba para demo evolucion', TRUE, 1,
                0.5, 0, 0, 0, 0, NOW(), NOW())
        RETURNING id
        """,
        "NEXUS",
    )
    await conn.close()
    print(f"[PROC EVOLUTION] Procedure de prueba creado: id={proc_id}")

    # 3 exitos
    print("\n[PROC EVOLUTION] Ejecutando 3 veces con exito...")
    for i in range(3):
        r = await pe.record_outcome(proc_id, success=True)
    print(f"  success_rate tras 3 exitos: {r['new_success_rate']:.1%}")

    # 3 fallos consecutivos
    print("\n[PROC EVOLUTION] Ejecutando 3 veces con fallo (simula bug inesperado)...")
    revision_msg = None
    for i in range(3):
        r = await pe.record_outcome(
            proc_id, success=False,
            context=f"Error en paso {i+1}: conexion rechazada"
        )
        if r["propose_revision"]:
            revision_msg = r["revision_message"]

    print(f"  success_rate tras 3 fallos: {r['new_success_rate']:.1%}")
    if revision_msg:
        print(f"\n  PROPUESTA PARA WILLIAM:\n  {revision_msg}")

    # Simular aprobacion de William -> crear v2
    print("\n[PROC EVOLUTION] William aprueba. Creando version 2...")
    new_id = await pe.create_revision(
        parent_id=proc_id,
        new_workflow="workflow mejorado: agrega retry con backoff exponencial + circuit breaker",
        improvement_notes="La version 1 fallaba sin retry. V2 agrega resiliencia.",
    )

    # Mostrar historial completo
    print("\n[PROC EVOLUTION] Historial de versiones:")
    history = await pe.get_procedure_history(new_id)
    for h in history:
        status = "ACTIVO" if h["active"] else "OBSOLETO"
        print(f"  v{h['version']} [{status}] id={h['id']} | rate={h['success_rate']:.1%} | hits={h['hit_count']}")

    await pe.close()
    print("\n[PROC EVOLUTION] --- Demo completado ---")
    return {"original_id": proc_id, "revised_id": new_id}


if __name__ == "__main__":
    result = asyncio.run(demo_evolution_cycle())
    print(f"\nResultado: {result}")
