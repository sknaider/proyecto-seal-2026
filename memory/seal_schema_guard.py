#!/usr/bin/env python3
"""SEAL Schema Guard — verifica que todas las tablas requeridas existen en BD.

Ejecutar en cada boot de agente o como check standalone.
Alerta via webchat si alguna tabla falta.

Uso:
  python3 seal_schema_guard.py              # check + alert
  python3 seal_schema_guard.py --quiet      # solo exit code (0=ok, 1=falta tabla)
"""
from seal_secrets import pg_dsn

import asyncio
import json
import sys
import logging
import urllib.request
from datetime import datetime, timezone

import asyncpg

LOG = logging.getLogger("seal-schema-guard")

DB_URL = pg_dsn(required=True)
WEBCHAT_URL = "http://localhost:8765/api/agents/send"

# ── Tablas requeridas para que el sistema funcione ──
# Solo las que tienen SQL activo en módulos importados por servicios vivos.
# Actualizar este set cuando se crea una tabla nueva o se depreca una.
REQUIRED_TABLES = {
    # Core memory
    "memories",
    "memories_archive",
    "cold_archive",
    "memory_connections",
    "memory_edges",
    "memory_broadcasts",
    # Identity & personality
    "identity",
    "relationships",
    "beliefs",
    "opinions",
    "instincts",
    "instinct_activations",
    "peer_models",
    "ocean_base_values",
    "ocean_current",
    "ocean_drift_log",
    "style_fingerprints",
    # Consciousness & reflection
    "inner_monologue",
    "diary",
    "motivation_states",
    "nerves_metrics_log",
    "event_log",
    "working_state",
    # Episodic
    "distilled_exchanges",
    "reasoning_traces",
    "procedural_memories",
    "session_memory",
    "sessions",
    # Governance & audit
    "rules",
    "reflective_diagnoses",
    "debate_log",
    "soul_audit_log",
    "drift_events",
    "drift_metrics",
    # GAM
    "gam_event_graph",
    "gam_topics",
    # NERVES
    "agent_tasks",
    # Bench
    "bench_runs",
    "bench_results",
    # Observability
    "tool_observations",
    "smg_audit_log",
    # Agents & lifecycle
    "agents",
    "agent_lifecycle",
    "agent_sessions",
    # Chat
    "chat_messages",
    "chat_channels",
    "chat_users",
    # Code graph
    "cgraph_chunks",
    "cgraph_edges_chunk",
    # Indexer
    "index_runs",
    "index_state",
    "memory_search_idx",
    # sycophancy
    "sycophancy_eval",
    "sycophancy_log",
    # Consent
    "consent_tokens",
}


async def check_schema(quiet: bool = False) -> tuple[bool, list[str]]:
    """Returns (all_ok, list_of_missing_tables)."""
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await conn.fetch("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema IN ('public', 'soul_v3')
        """)
        existing = {r["table_name"] for r in rows}
        missing = sorted(REQUIRED_TABLES - existing)
        return (len(missing) == 0, missing)
    finally:
        await conn.close()


def _notify(message: str) -> None:
    try:
        payload = json.dumps({
            "from": "NEXUS",
            "to": "equipo",
            "type": "alert",
            "channel": "web_chat",
            "message": message,
        }).encode()
        req = urllib.request.Request(
            WEBCHAT_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


async def main(quiet: bool = False) -> int:
    logging.basicConfig(level=logging.WARNING if quiet else logging.INFO,
                        format="%(asctime)s [schema-guard] %(message)s")

    ok, missing = await check_schema(quiet)

    if ok:
        if not quiet:
            print("✅ Schema OK — todas las tablas requeridas existen.")
        return 0

    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    msg = (
        f"🚨 [schema-guard {ts}] TABLAS FALTANTES en soul_v3:\n"
        + "\n".join(f"  • {t}" for t in missing)
        + f"\n\nTotal: {len(missing)} tabla(s). Revisar migrations."
    )

    if not quiet:
        print(msg)
        LOG.warning(msg)

    _notify(msg)
    return 1


if __name__ == "__main__":
    quiet = "--quiet" in sys.argv
    exit_code = asyncio.run(main(quiet=quiet))
    sys.exit(exit_code)
