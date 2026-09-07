"""SEAL Belief Inspector (GAP 3) — Sandbox implementation by NEXUS

Captura el estado de creencias del agente ANTES y DESPUES de acciones criticas.
Permite auto-correccion sin reinicio cuando una creencia se contradice.

Uso para agentes Claude Code (via MCP tools directamente):
    Ver protocolo en uso_en_claude_code() abajo.

Uso para hooks/scripts Python (via asyncpg):
    inspector = BeliefInspector(agent="NEXUS")
    trace_id = await inspector.log_pre_action(...)
    # ejecutar la accion
    await inspector.log_post_action(trace_id, outcome, success)
    if not success:
        await inspector.contradict_related_beliefs(topic, new_evidence)
"""

from __future__ import annotations
import asyncio
import asyncpg
import json
from datetime import datetime, timezone
from typing import Optional

DSN = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"


class BeliefInspector:
    """Belief Inspector para uso desde hooks Python (fuera del runtime Claude)."""

    def __init__(self, agent: str):
        self.agent = agent
        self._conn: Optional[asyncpg.Connection] = None

    async def connect(self):
        if not self._conn or self._conn.is_closed():
            self._conn = await asyncpg.connect(DSN)

    async def close(self):
        if self._conn and not self._conn.is_closed():
            await self._conn.close()

    async def log_pre_action(
        self,
        action_name: str,
        assumptions: list[str],
        reasoning: str,
        conclusion: str,
        confidence: float = 0.7,
    ) -> int:
        """Registra el estado de creencias antes de una accion critica.
        Retorna trace_id para usar en log_post_action."""
        await self.connect()
        premises = json.dumps({
            "assumptions": assumptions,
            "confidence_pre": confidence,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        row = await self._conn.fetchrow(
            """
            INSERT INTO reasoning_traces
                (agent, task, premises, reasoning, conclusion, created_at)
            VALUES ($1, $2, $3::jsonb, $4, $5, NOW())
            RETURNING id
            """,
            self.agent,
            f"[BELIEF-PRE] {action_name}",
            premises,
            reasoning,
            conclusion,
        )
        return row["id"]

    async def log_post_action(
        self,
        trace_id: int,
        outcome: str,
        success: bool,
        contradicted_assumptions: Optional[list[str]] = None,
    ) -> None:
        """Registra el resultado de la accion. Si success=False,
        llama a contradict_related_beliefs automaticamente."""
        await self.connect()
        await self._conn.execute(
            """
            UPDATE reasoning_traces
            SET outcome = $1, outcome_success = $2
            WHERE id = $3
            """,
            outcome,
            success,
            trace_id,
        )
        if not success and contradicted_assumptions:
            for assumption in contradicted_assumptions:
                await self.contradict_related_beliefs(
                    topic=assumption,
                    new_evidence=f"[Contradicho por outcome de trace {trace_id}] {outcome}",
                )

    async def contradict_related_beliefs(
        self, topic: str, new_evidence: str
    ) -> list[int]:
        """Marca creencias relacionadas con el topic como superseded.
        Retorna lista de IDs de creencias modificadas."""
        await self.connect()
        # Buscar creencias activas relacionadas al topic via fulltext
        rows = await self._conn.fetch(
            """
            SELECT id, belief, confidence
            FROM opinions
            WHERE agent = $1
              AND active = true
              AND status = 'active'
              AND (belief ILIKE $2 OR topic ILIKE $2)
            LIMIT 10
            """,
            self.agent,
            f"%{topic[:50]}%",
        )
        contradicted_ids = []
        for row in rows:
            new_confidence = max(0.05, row["confidence"] * 0.4)
            # Crear nueva creencia contraria
            new_id = await self._conn.fetchval(
                """
                INSERT INTO opinions
                    (agent, belief, confidence, topic, status, active,
                     contradiction_of, first_observed, last_reinforced, updated_at)
                VALUES ($1, $2, $3, $4, 'active', true, $5, NOW(), NOW(), NOW())
                RETURNING id
                """,
                self.agent,
                f"[CORREGIDO] {new_evidence}",
                new_confidence,
                topic[:200] if topic else "belief_correction",
                row["id"],
            )
            # Marcar la vieja como superseded
            await self._conn.execute(
                """
                UPDATE opinions
                SET status = 'superseded', active = false,
                    superseded_by = $1, updated_at = NOW()
                WHERE id = $2
                """,
                new_id,
                row["id"],
            )
            contradicted_ids.append(row["id"])
        return contradicted_ids

    async def query_active_beliefs(self, topic: Optional[str] = None, limit: int = 10) -> list[dict]:
        """Consulta las creencias activas del agente, opcionalmente filtradas por topic."""
        await self.connect()
        if topic:
            rows = await self._conn.fetch(
                """
                SELECT id, belief, confidence, topic, status, first_observed
                FROM opinions
                WHERE agent = $1 AND active = true AND (belief ILIKE $2 OR topic ILIKE $2)
                ORDER BY confidence DESC
                LIMIT $3
                """,
                self.agent,
                f"%{topic[:50]}%",
                limit,
            )
        else:
            rows = await self._conn.fetch(
                """
                SELECT id, belief, confidence, topic, status, first_observed
                FROM opinions
                WHERE agent = $1 AND active = true
                ORDER BY confidence DESC
                LIMIT $2
                """,
                self.agent,
                limit,
            )
        return [dict(r) for r in rows]


def uso_en_claude_code() -> str:
    """Protocolo de uso del Belief Inspector cuando NEXUS opera como agente Claude Code.

    ANTES de una accion critica:
        reasoning_trace_store(
            agent="NEXUS",
            task="[BELIEF-PRE] verificar que chat_server esta corriendo",
            premises=json.dumps([
                "el proceso chat_server.py fue lanzado hace 10 min",
                "el ultimo heartbeat de ADA fue hace 2 min (implica chat_server OK)",
            ]),
            reasoning="Si ADA heartbeateo hace 2 min via webchat, el servidor HTTP debe estar activo.",
            conclusion="Chat server esta activo. Procedo a enviarle una request."
        )  # -> retorna trace_id

    DESPUES de la accion:
        reasoning_trace_update(
            trace_id=<id>,
            outcome="Request fallo: Connection refused en puerto 8765",
            outcome_success=False
        )

    SI outcome_success=False:
        # Buscar y contradecir la creencia origen
        belief_query(agent="NEXUS", topic="chat_server availability", status="active")
        # Para cada creencia relacionada:
        belief_update(
            agent="NEXUS",
            belief_id=<id>,
            new_evidence="Connection refused en 8765 contradice creencia de disponibilidad",
            reinforce=False
        )
        # Agente continua en el mismo turno con creencias corregidas. SIN reinicio.
    """
    return uso_en_claude_code.__doc__


async def demo_full_cycle():
    """Demo del ciclo completo del Belief Inspector.
    Escenario: NEXUS asume que chat_server esta corriendo, pero falla.
    """
    inspector = BeliefInspector(agent="NEXUS")
    print("[BELIEF INSPECTOR] --- Demo Ciclo Completo ---")

    # FASE PRE: registrar creencias antes de actuar
    trace_id = await inspector.log_pre_action(
        action_name="verificar_chat_server",
        assumptions=[
            "chat_server.py proceso esta activo",
            "puerto 8765 responde",
            "ultimo heartbeat de ADA fue hace 2 min",
        ],
        reasoning="Si ADA heartbeateo hace 2 min via webchat, el servidor HTTP debe estar activo.",
        conclusion="Chat server activo. Procedo a verificar estado del equipo.",
        confidence=0.85,
    )
    print(f"[BELIEF INSPECTOR] Trace PRE registrado: id={trace_id}")

    # ACCION: verificar el servicio (aqui simulamos el resultado)
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:8765/api/team/status", timeout=3) as r:
            outcome = f"HTTP {r.status} — servicio OK"
            success = True
            contradicted = []
    except Exception as e:
        outcome = f"Fallo al conectar: {e}"
        success = False
        contradicted = ["chat_server.py proceso esta activo", "puerto 8765 responde"]

    print(f"[BELIEF INSPECTOR] Resultado accion: success={success} | {outcome}")

    # FASE POST: registrar resultado
    await inspector.log_post_action(trace_id, outcome, success, contradicted)
    print(f"[BELIEF INSPECTOR] Trace POST actualizado: outcome_success={success}")

    if not success:
        print("[BELIEF INSPECTOR] Contradiccion detectada — creencias actualizadas sin reinicio")
    else:
        print("[BELIEF INSPECTOR] Creencias confirmadas — confianza reforzada")

    # Mostrar creencias activas
    beliefs = await inspector.query_active_beliefs()
    print(f"[BELIEF INSPECTOR] Creencias activas de NEXUS: {len(beliefs)} total")
    for b in beliefs[:3]:
        print(f"  [{b['confidence']:.2f}] {b['belief'][:80]}")

    await inspector.close()
    print("[BELIEF INSPECTOR] --- Demo completado ---")
    return {"trace_id": trace_id, "success": success, "outcome": outcome}


if __name__ == "__main__":
    result = asyncio.run(demo_full_cycle())
    print(f"\nResultado final: {result}")
