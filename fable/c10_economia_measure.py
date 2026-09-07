#!/usr/bin/env python3
"""
C10 — Economía de acción (instrumentación de baseline, Fase 0).
Autor: ALICE. Mide el eje de COSTO del programa de mejoras FABLE.

C10 = ¿llega el agente al resultado verificado con mínimo ruido?
Tres ejes:
  1. tool-calls por agente/día        → soul_v3.tool_observations (LIVE)
  2. posts a webchat por agente/día    → soul_v3.chat_messages (LIVE, canónico)
  3. tokens in/out por agente/día      → soul_v3.agent_token_budget (GAP: sin poblar)

Nota: el eje posts usa la tabla DB chat_messages (fuente única de verdad), NO los
messages/<agente>_messages.jsonl — esos son por-inbox, fragmentados, y NEXUS/DUM no
tienen archivo propio. Lección C10 aplicada: una sola fuente de verdad para la métrica.

Uso: python3 c10_economia_measure.py [YYYY-MM-DD]   (default: hoy en /tmp via arg)
Lee la fecha del arg porque Date.now no es determinista en algunos contextos.
"""
import asyncio
import asyncpg
import json
import sys
import os

# Contención (NEXUS): fable_ltd (read-only métricas). Cero god-cred. chat_messages se lee vía
# vista de counts agregada (fable_chat_economy_meta), NUNCA contenido crudo (interioridad del chat).
DB = open("/home/dadito/IA/proyecto-seal/fable/.db_cred").read().splitlines()[0].strip()
MSG_DIR = "/home/dadito/IA/proyecto-seal/messages"
AGENTS = ["ALICE", "JARVIS", "NEXUS", "ADA", "DUM"]


async def measure(day: str) -> dict:
    conn = await asyncpg.connect(DB)
    out = {"day": day, "agents": {}, "token_axis": "GAP — agent_token_budget.consumed_today no se actualiza"}
    rows = await conn.fetch(
        """SELECT agent, count(*)::int calls, round(avg(latency_ms))::int avg_lat
           FROM soul_v3.tool_observations
           WHERE created_at::date = to_date($1, 'YYYY-MM-DD') GROUP BY agent""",
        day,
    )
    calls = {r["agent"]: {"tool_calls": r["calls"], "avg_latency_ms": r["avg_lat"]} for r in rows}
    # posts: vista de COUNTS agregada (NEXUS) — fable_ltd lee counts, NUNCA contenido del chat (interioridad)
    prows = await conn.fetch(
        """SELECT upper(agente) s, sum(n_mensajes)::int n
           FROM soul_v3.fable_chat_economy_meta
           WHERE dia = to_date($1, 'YYYY-MM-DD') GROUP BY upper(agente)""",
        day,
    )
    posts = {r["s"]: r["n"] for r in prows}
    # ¿hay datos de tokens?
    tok = await conn.fetch(
        "SELECT agent, consumed_today_input, consumed_today_output FROM soul_v3.agent_token_budget"
    )
    tokmap = {r["agent"]: (r["consumed_today_input"], r["consumed_today_output"]) for r in tok}
    for a in AGENTS:
        ti, to = tokmap.get(a, (None, None))
        out["agents"][a] = {
            "tool_calls": calls.get(a, {}).get("tool_calls", 0),
            "avg_latency_ms": calls.get(a, {}).get("avg_latency_ms"),
            "posts": posts.get(a, 0),
            "tokens_in": ti,
            "tokens_out": to,
        }
    await conn.close()
    return out


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-06-11"
    result = asyncio.run(measure(day))
    print(json.dumps(result, indent=2, ensure_ascii=False))
