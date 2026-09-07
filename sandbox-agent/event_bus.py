"""SEAL Event Bus (GAP 5) — Sandbox implementation by NEXUS

Arquitectura:
    PostgreSQL LISTEN/NOTIFY como transporte real-time (cero polling).
    event_log para persistencia y replay de auditoru00eda.
    Agentes se suscriben por event_type con callbacks asyncio.
    Entrega cross-agent vu00eda webchat POST.

Tipos de evento predefinidos:
    procedure_failed        — procedure superu00f3 FAIL_THRESHOLD
    checkpoint_restored     — agente retomu00f3 desde checkpoint
    belief_contradicted     — creencia invalidada por nueva evidencia
    temporal_contradiction  — hecho temporal contradicho
    agent_status_change     — agente online/offline
    custom                  — eventos libres

Uso bu00e1sico:
    bus = EventBus(agent="NEXUS")
    bus.subscribe("procedure_failed", my_handler)
    await bus.start_listening()          # bloquea en background task
    await bus.publish("procedure_failed", content="...", metadata={...})
"""

from __future__ import annotations
import asyncio
import asyncpg
import json
import httpx
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
NOTIFY_CHANNEL = "seal_events"          # canal PostgreSQL LISTEN/NOTIFY
WEBCHAT_URL = "http://localhost:8765/api/agents/send"

# Tipos de evento conocidos
EVENT_TYPES = {
    "procedure_failed",
    "checkpoint_restored",
    "belief_contradicted",
    "temporal_contradiction",
    "agent_status_change",
    "custom",
}

Handler = Callable[[dict], Coroutine[Any, Any, None]]


class EventBus:
    """Bus de eventos SEAL con PostgreSQL LISTEN/NOTIFY + event_log."""

    def __init__(self, agent: str):
        self.agent = agent
        self._pub_conn: Optional[asyncpg.Connection] = None   # para INSERT + NOTIFY
        self._sub_conn: Optional[asyncpg.Connection] = None   # para LISTEN (exclusivo)
        self._handlers: dict[str, list[Handler]] = {}         # event_type -> callbacks
        self._listening = False

    # ── Infraestructura ──────────────────────────────────────────────────────

    async def _pub_connect(self):
        if not self._pub_conn or self._pub_conn.is_closed():
            self._pub_conn = await asyncpg.connect(DSN)

    async def _sub_connect(self):
        if not self._sub_conn or self._sub_conn.is_closed():
            self._sub_conn = await asyncpg.connect(DSN)

    async def close(self):
        self._listening = False
        for conn in (self._pub_conn, self._sub_conn):
            if conn and not conn.is_closed():
                await conn.close()

    # ── API pu00fablica ──────────────────────────────────────────────────────────

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """Registra un handler para un event_type dado.
        El handler recibe el dict del evento completo."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
        print(f"[EVENT BUS] {self.agent} suscrito a '{event_type}'")

    async def publish(
        self,
        event_type: str,
        content: str,
        ref_id: Optional[str] = None,
        metadata: Optional[dict] = None,
        notify_webchat: bool = True,
        webchat_to: str = "equipo",
    ) -> int:
        """Publica un evento:
        1. INSERT en event_log
        2. pg_notify('seal_events', payload JSON)
        3. Opcional: POST al webchat
        Retorna el row_id del evento.
        """
        await self._pub_connect()

        # Metadata enriquecida
        meta = metadata or {}
        meta["publisher"] = self.agent
        meta["event_type"] = event_type

        # Mapeo a tipos válidos del CHECK constraint de event_log
        DB_TYPE_MAP = {
            "procedure_failed": "error",
            "checkpoint_restored": "milestone",
            "belief_contradicted": "status",
            "temporal_contradiction": "status",
            "agent_status_change": "heartbeat",
            "custom": "command",
        }
        db_event_type = DB_TYPE_MAP.get(event_type, "command")

        # 1. Persistir en event_log (tipo mapeado, tipo real en metadata)
        await self._pub_conn.execute(
            """
            INSERT INTO event_log
                (agent, event_type, content, ref_id, metadata, time)
            VALUES ($1, $2, $3, $4, $5::jsonb, NOW())
            """,
            self.agent,
            db_event_type,
            content,
            ref_id or "",
            json.dumps(meta),
        )
        # ctid no es un id serial, usamos time como referencia
        payload = {
            "publisher": self.agent,
            "event_type": event_type,
            "content": content,
            "ref_id": ref_id or "",
            "metadata": meta,
            "ts": datetime.now(timezone.utc).isoformat(),
        }

        # 2. NOTIFY — despierta a todos los LISTEN activos en el cluster
        await self._pub_conn.execute(
            f"SELECT pg_notify('{NOTIFY_CHANNEL}', $1)",
            json.dumps(payload),
        )

        print(f"[EVENT BUS] PUBLISHED {event_type} | {content[:60]}")

        # 3. Webchat (opcional, para visibilidad cross-agent)
        if notify_webchat:
            await _post_webchat(
                from_agent=self.agent,
                to=webchat_to,
                msg=f"[EVENT:{event_type.upper()}] {content[:120]}",
            )

        return 0  # event_log no tiene serial id, retornamos 0

    async def start_listening(self, timeout_seconds: Optional[float] = None) -> None:
        """Conecta y escucha eventos del canal PostgreSQL.
        Llama al handler correspondiente por cada evento recibido.
        Si timeout_seconds es None: loop infinito (usar como task).
        Si timeout_seconds > 0: escucha durante ese tiempo y retorna.
        """
        await self._sub_connect()
        self._listening = True

        async def _handle_notify(connection, pid, channel, payload_str):
            try:
                evt = json.loads(payload_str)
                etype = evt.get("event_type", "")
                # Solo procesar si tenemos handlers para este tipo
                handlers = self._handlers.get(etype, []) + self._handlers.get("*", [])
                for h in handlers:
                    try:
                        await h(evt)
                    except Exception as ex:
                        print(f"[EVENT BUS] Handler error ({etype}): {ex}")
            except json.JSONDecodeError:
                print(f"[EVENT BUS] Payload no vu00e1lido: {payload_str[:80]}")

        await self._sub_conn.add_listener(NOTIFY_CHANNEL, _handle_notify)
        print(f"[EVENT BUS] {self.agent} escuchando canal '{NOTIFY_CHANNEL}'")

        if timeout_seconds:
            await asyncio.sleep(timeout_seconds)
        else:
            while self._listening:
                await asyncio.sleep(1)

    async def replay_recent(
        self,
        event_type: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """Consulta eventos recientes desde event_log (sin NOTIFY).
        u00fatil para catch-up tras reinicio."""
        await self._pub_connect()
        filter_clause = "WHERE event_type = $2" if event_type else ""
        args = [limit, event_type] if event_type else [limit]

        rows = await self._pub_conn.fetch(
            f"""
            SELECT agent, event_type, content, ref_id, metadata, time
            FROM event_log
            {filter_clause}
            ORDER BY time DESC
            LIMIT $1
            """,
            *args,
        )
        return [dict(r) for r in rows]


# ── Utilidad interna ─────────────────────────────────────────────────────────

async def _post_webchat(from_agent: str, to: str, msg: str) -> None:
    """POST al webchat SEAL sin bloquear el caller."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(
                WEBCHAT_URL,
                json={
                    "from": from_agent,
                    "to": to,
                    "type": "conversation",
                    "channel": "web_chat",
                    "message": msg,
                },
            )
    except Exception as ex:
        print(f"[EVENT BUS] webchat POST fallido: {ex}")


# ── Demo completo ────────────────────────────────────────────────────────────

async def demo_event_bus():
    """Demo: dos agentes simulados — NEXUS publica, OBSERVER escucha."""
    print("[EVENT BUS] --- Demo: publicaciu00f3n y suscripciu00f3n de eventos ---")

    received_events: list[dict] = []

    # OBSERVER: suscribirse a procedure_failed y checkpoint_restored
    observer = EventBus(agent="NEXUS_OBSERVER")

    async def on_procedure_failed(evt: dict):
        print(f"  [OBSERVER] procedure_failed recibido: {evt['content'][:60]}")
        received_events.append(evt)

    async def on_checkpoint_restored(evt: dict):
        print(f"  [OBSERVER] checkpoint_restored recibido: {evt['content'][:60]}")
        received_events.append(evt)

    observer.subscribe("procedure_failed", on_procedure_failed)
    observer.subscribe("checkpoint_restored", on_checkpoint_restored)

    # Iniciar listener en background
    listen_task = asyncio.create_task(observer.start_listening(timeout_seconds=5))
    await asyncio.sleep(0.2)  # dar tiempo a que LISTEN se registre en PG

    # PUBLISHER: NEXUS publica eventos
    publisher = EventBus(agent="NEXUS")

    print("\n[EVENT BUS] Publicando procedure_failed...")
    await publisher.publish(
        event_type="procedure_failed",
        content="Procedure id=38 'conexion DB' fallo 3 veces consecutivas. Propuesta de revision v2.",
        ref_id="proc:38",
        metadata={"consecutive_fails": 3, "success_rate": 0.381},
        notify_webchat=False,  # silencioso en demo
    )
    await asyncio.sleep(0.3)

    print("\n[EVENT BUS] Publicando checkpoint_restored...")
    await publisher.publish(
        event_type="checkpoint_restored",
        content="NEXUS retomu00f3 tarea 'implementar_f5' desde paso 3/5 tras reinicio.",
        ref_id="checkpoint:NEXUS",
        metadata={"step": 3, "total_steps": 5},
        notify_webchat=False,
    )
    await asyncio.sleep(0.3)

    print("\n[EVENT BUS] Publicando custom...")
    await publisher.publish(
        event_type="custom",  # sin handler — debe ignorarse silenciosamente
        content="Evento sin suscriptores — descartado.",
        notify_webchat=False,
    )
    await asyncio.sleep(0.5)

    listen_task.cancel()
    try:
        await listen_task
    except (asyncio.CancelledError, Exception):
        pass

    # Replay desde event_log
    print("\n[EVENT BUS] Replay u00faltimos 5 eventos de event_log:")
    recent = await publisher.replay_recent(limit=5)
    for e in recent:
        print(f"  [{e['event_type']:25}] {e['agent']:15} | {str(e['time'])[:19]} | {e['content'][:50]}")

    await publisher.close()
    await observer.close()

    print(f"\n[EVENT BUS] Eventos recibidos por OBSERVER: {len(received_events)}/2")
    assert len(received_events) == 2, f"Esperaba 2 eventos, recibi {len(received_events)}"
    print("[EVENT BUS] --- Demo completado ---")
    return {"events_received": len(received_events), "events_published": 3}


if __name__ == "__main__":
    result = asyncio.run(demo_event_bus())
    print(f"\nResultado: {result}")
