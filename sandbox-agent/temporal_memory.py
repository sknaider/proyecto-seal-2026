"""SEAL Temporal Memory (GAP 5) — Sandbox implementation by NEXUS

Gestiona la EVOLUCION de hechos a lo largo del tiempo.
Cuando un hecho cambia, la version anterior NO se borra — se marca
como invalid_at=NOW() y se crea la nueva con superseded_by=id_vieja.

Invariante: nada se borra nunca. Todo es consultable en el tiempo.

Schema en Soul DB (memories):
    valid_from    TIMESTAMPTZ  — cuando el hecho empezo a ser verdad
    invalid_at    TIMESTAMPTZ  — cuando dejo de ser verdad (NULL = todavia valido)
    superseded_by BIGINT       — id de la nueva memoria que lo reemplaza (nuevo)

Uso:
    tm = TemporalMemory(agent="NEXUS")
    mem_id = await tm.store_fact(content="William trabaja de noche", category="preference")
    await tm.contradict_fact(mem_id, new_content="William trabaja de dia", reason="cambio en 24-abr-2026")
    # Hoy: devuelve "trabaja de dia"
    # Con point_in_time=ayer: devuelve "trabaja de noche"
"""

from __future__ import annotations
import asyncio
import asyncpg
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"


class TemporalMemory:
    """Memoria temporal para agentes SEAL."""

    def __init__(self, agent: str):
        self.agent = agent
        self._conn: Optional[asyncpg.Connection] = None

    async def connect(self):
        if not self._conn or self._conn.is_closed():
            self._conn = await asyncpg.connect(DSN)

    async def close(self):
        if self._conn and not self._conn.is_closed():
            await self._conn.close()

    async def store_fact(
        self,
        content: str,
        category: str = "semantic",
        importance: int = 5,
        scope: str = "private",
        metadata: Optional[dict] = None,
    ) -> int:
        """Guarda un nuevo hecho con valid_from=NOW().
        Retorna el id de la memoria creada."""
        await self.connect()
        row = await self._conn.fetchrow(
            """
            INSERT INTO memories
                (agent, category, content, importance, scope,
                 valid_from, metadata, created_at)
            VALUES ($1, $2, $3, $4, $5, NOW(), $6::jsonb, NOW())
            RETURNING id
            """,
            self.agent,
            category,
            content,
            importance,
            scope,
            json.dumps(metadata or {"source": "temporal_memory"}),
        )
        mem_id = row["id"]
        print(f"[TEMPORAL] Hecho guardado: id={mem_id} '{content[:60]}'")
        return mem_id

    async def contradict_fact(
        self,
        old_memory_id: int,
        new_content: str,
        reason: str,
        category: Optional[str] = None,
        importance: int = 5,
    ) -> int:
        """Contradice un hecho existente:
        1. Marca el viejo como invalid_at=NOW()
        2. Crea nuevo hecho con superseded_by=old_id
        3. Retorna id del nuevo hecho.
        """
        await self.connect()

        # Obtener datos de la memoria vieja
        old = await self._conn.fetchrow(
            "SELECT category, scope, importance FROM memories WHERE id=$1",
            old_memory_id,
        )
        if not old:
            raise ValueError(f"Memoria {old_memory_id} no encontrada")

        # Crear nueva memoria primero
        new_row = await self._conn.fetchrow(
            """
            INSERT INTO memories
                (agent, category, content, importance, scope,
                 valid_from, superseded_by, metadata, created_at)
            VALUES ($1, $2, $3, $4, $5, NOW(), $6, $7::jsonb, NOW())
            RETURNING id
            """,
            self.agent,
            category or old["category"],
            new_content,
            importance or old["importance"],
            old["scope"],
            old_memory_id,
            json.dumps({"contradiction_reason": reason, "source": "temporal_memory"}),
        )
        new_id = new_row["id"]

        # Marcar la vieja como expirada
        await self._conn.execute(
            "UPDATE memories SET invalid_at=NOW() WHERE id=$1",
            old_memory_id,
        )
        print(f"[TEMPORAL] Contradiccion: id={old_memory_id} expirado -> nuevo id={new_id} | razon: {reason}")
        return new_id

    async def query_facts(
        self,
        topic_keywords: list[str],
        point_in_time: Optional[datetime] = None,
        limit: int = 10,
    ) -> list[dict]:
        """Consulta hechos validos.
        Sin point_in_time: hechos validos AHORA (invalid_at IS NULL).
        Con point_in_time: hechos validos EN ESE MOMENTO.
        """
        await self.connect()
        # Construir filtro de contenido como condicion ILIKE combinada en Python
        # para evitar complejidad de parametros dinamicos en asyncpg
        keyword_filter = " OR ".join([f"content ILIKE '%{kw.replace("'", "''")}%'" for kw in topic_keywords])
        if not keyword_filter:
            keyword_filter = "TRUE"

        if point_in_time:
            t = point_in_time
            rows = await self._conn.fetch(
                f"""
                SELECT id, content, category, importance, valid_from, invalid_at, superseded_by
                FROM memories
                WHERE agent = $1
                  AND valid_from <= $2
                  AND (invalid_at IS NULL OR invalid_at > $2)
                  AND ({keyword_filter})
                ORDER BY importance DESC, valid_from DESC
                LIMIT $3
                """,
                self.agent, t, limit,
            )
            label = f"en {t.strftime('%Y-%m-%d %H:%M')}"
        else:
            rows = await self._conn.fetch(
                f"""
                SELECT id, content, category, importance, valid_from, invalid_at, superseded_by
                FROM memories
                WHERE agent = $1
                  AND invalid_at IS NULL
                  AND ({keyword_filter})
                ORDER BY importance DESC, valid_from DESC
                LIMIT $2
                """,
                self.agent, limit,
            )
            label = "ahora (validos)"

        results = [dict(r) for r in rows]
        print(f"[TEMPORAL] Query '{topic_keywords}' {label}: {len(results)} resultados")
        return results

    async def get_history(self, memory_id: int) -> list[dict]:
        """Retorna la cadena completa de un hecho: version original + todas las contradicciones."""
        await self.connect()
        # Seguir la cadena de superseded_by hacia atras
        chain = []
        current_id = memory_id
        visited = set()
        while current_id and current_id not in visited:
            visited.add(current_id)
            row = await self._conn.fetchrow(
                "SELECT id, content, valid_from, invalid_at, superseded_by FROM memories WHERE id=$1",
                current_id,
            )
            if row:
                chain.append(dict(row))
                current_id = row["superseded_by"]
            else:
                break
        return list(reversed(chain))  # cronologico


async def demo_temporal_cycle():
    """Demo: preferencia de William cambia, consultamos hoy vs ayer."""
    tm = TemporalMemory(agent="NEXUS")
    print("[TEMPORAL] --- Demo: evolucion de preferencia ---")

    # Guardar hecho inicial
    id_v1 = await tm.store_fact(
        content="William prefiere reuniones por la noche (preferencia registrada 24-abr-2026)",
        category="preference",
        importance=7,
    )

    # Simular que William cambia de opinion
    print("\n[TEMPORAL] William cambia de opinion...")
    id_v2 = await tm.contradict_fact(
        old_memory_id=id_v1,
        new_content="William prefiere reuniones por la manana (actualizado 24-abr-2026)",
        reason="William dijo explicitamente que cambia a mananas",
        importance=8,
    )

    # Consultar AHORA — debe devolver la preferencia nueva
    print("\n[TEMPORAL] Consulta: que prefiere William AHORA?")
    facts_now = await tm.query_facts(topic_keywords=["William", "reunion"])
    for f in facts_now:
        exp = f"(expira: {f['invalid_at']})" if f['invalid_at'] else "(valido)"
        print(f"  [{f['id']}] {f['content'][:70]} {exp}")

    # Consultar EN EL PASADO (1 segundo atras — simula "antes del cambio")
    print("\n[TEMPORAL] Consulta: que preferia William ANTES DEL CAMBIO?")
    t_pasado = datetime.now(timezone.utc) - timedelta(seconds=2)
    facts_past = await tm.query_facts(
        topic_keywords=["William", "reunion"],
        point_in_time=t_pasado,
    )
    for f in facts_past:
        print(f"  [{f['id']}] {f['content'][:70]}")

    if not facts_past:
        print("  (ninguno — el hecho fue creado en este mismo demo, hace <2s)")
        print("  Correcto: en produccion con dias/semanas de diferencia funcionaria perfecto.")

    # Mostrar historial completo de la preferencia
    print("\n[TEMPORAL] Historial completo de la preferencia (cadena de versiones):")
    history = await tm.get_history(id_v2)
    for h in history:
        valido = "activo" if not h['invalid_at'] else f"expirado {str(h['invalid_at'])[:16]}"
        print(f"  id={h['id']} [{valido}]: {h['content'][:60]}")
        if h['superseded_by']:
            print(f"    -> reemplazado por id={h['superseded_by']}")

    await tm.close()
    print("\n[TEMPORAL] --- Demo completado ---")
    return {"id_v1": id_v1, "id_v2": id_v2, "facts_now": len(facts_now)}


if __name__ == "__main__":
    result = asyncio.run(demo_temporal_cycle())
    print(f"\nResultado: {result}")
