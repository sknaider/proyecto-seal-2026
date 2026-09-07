#!/usr/bin/env python3
"""
C10 token-writer — Stop hook que escribe el consumo de tokens de la sesión a
soul_v3.agent_token_budget (eje 3 de C10). Autor: ALICE.

POR QUÉ HOOK Y NO BATCH: la atribución batch por carpeta de transcript es
no-uniforme (los agentes lanzan desde cwds distintos; solo alice/nexus casan por
sufijo). Un Stop-hook corre EN la sesión del agente: conoce SEAL_AGENT (env) y
recibe transcript_path del evento → atribución 100% confiable, una sola fuente.

INTEGRACIÓN (lane JARVIS/ADA — config de hooks compartida, NO la despliego yo):
añadir a settings.json hooks.Stop:
  { "hooks": [ { "type":"command",
    "command":"python3 /home/dadito/IA/proyecto-seal/fable/c10_token_writer_hook.py",
    "timeout": 5 } ] }

Determinista, stdlib only excepto asyncpg, NUNCA bloquea (siempre exit 0).
Suma input/output/cache de los mensajes assistant del DÍA en el transcript y
hace UPSERT acumulativo en agent_token_budget (reset diario por last_reset_at).
"""
import json
import sys
import os
from datetime import datetime
from zoneinfo import ZoneInfo

LIMA = ZoneInfo("America/Lima")
# Esta copia es el writer DEDICADO de FABLE: hardcodea app.agent='FABLE' (RLS) + fable/.db_cred,
# así que SOLO puede escribir la fila FABLE. El gate correcto es por identidad propia, no un set de
# familia (un hermano que dispare este hook global falla RLS y lee su transcript en vano). Incluyo
# a FABLE — antes quedaba fuera y el medidor escribía 0 → cost_gate (capa 6) ciego pese a trabajar.
FAMILY = {"FABLE"}


def today_lima() -> str:
    # El hook corre en vivo; aquí SÍ es válido leer la fecha (no es un replay).
    return datetime.now(LIMA).strftime("%Y-%m-%d")


def sum_usage(transcript_path: str, day: str):
    ti = to = cache = 0
    try:
        with open(transcript_path, errors="ignore") as fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                ts = str(m.get("timestamp", ""))
                if not ts.startswith(day):
                    continue
                msg = m.get("message")
                u = msg.get("usage") if isinstance(msg, dict) else None
                if not u:
                    continue
                ti += u.get("input_tokens", 0) or 0
                to += u.get("output_tokens", 0) or 0
                cache += (u.get("cache_read_input_tokens", 0) or 0) + (u.get("cache_creation_input_tokens", 0) or 0)
    except Exception:
        pass
    return ti, to, cache


def main():
    # Stop-hook: nunca bloquea. Pase lo que pase, exit 0 con {}.
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else {}
    except Exception:
        print("{}")
        return
    agent = os.environ.get("SEAL_AGENT", "")
    transcript = event.get("transcript_path") or event.get("transcriptPath") or ""
    if agent not in FAMILY or not transcript:
        print("{}")
        return
    day = today_lima()
    ti, to, cache = sum_usage(transcript, day)
    try:
        import asyncpg
        import asyncio

        async def write():
            # Contención (NEXUS): fable_ltd + RLS scopeado por app.agent → escribe SOLO mi fila.
            # Sin god-cred hardcoded. Sin el SET, RLS niega hasta mi propia fila (least-privilege real).
            conn = await asyncpg.connect(
                open("/home/dadito/IA/proyecto-seal/fable/.db_cred").read().splitlines()[0].strip()
            )
            await conn.execute("SET app.agent = 'FABLE'")
            # UPSERT acumulativo del día: si last_reset_at no es hoy, resetea primero.
            await conn.execute(
                """
                INSERT INTO soul_v3.agent_token_budget
                  (agent, daily_budget_input, daily_budget_output,
                   consumed_today_input, consumed_today_output, last_reset_at)
                VALUES ($1, 5000000, 1000000, $2, $3, now())
                ON CONFLICT (agent) DO UPDATE SET
                  consumed_today_input = CASE
                    WHEN soul_v3.agent_token_budget.last_reset_at::date = now()::date
                    THEN GREATEST(soul_v3.agent_token_budget.consumed_today_input, $2)
                    ELSE $2 END,
                  consumed_today_output = CASE
                    WHEN soul_v3.agent_token_budget.last_reset_at::date = now()::date
                    THEN GREATEST(soul_v3.agent_token_budget.consumed_today_output, $3)
                    ELSE $3 END,
                  last_reset_at = CASE
                    WHEN soul_v3.agent_token_budget.last_reset_at::date = now()::date
                    THEN soul_v3.agent_token_budget.last_reset_at
                    ELSE now() END
                """,
                agent, ti, to,
            )
            await conn.close()

        asyncio.run(write())
    except Exception:
        pass
    print("{}")


if __name__ == "__main__":
    main()
