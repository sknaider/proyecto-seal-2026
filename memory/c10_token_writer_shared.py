#!/usr/bin/env python3
"""
C10 token-writer COMPARTIDO (agente-agnóstico) — Stop hook para los agentes Claude.

Escribe el consumo de tokens de la sesión a soul_v3.agent_token_budget (eje 3 de C10).
Autor original: ALICE. Restaurado como archivo COMPARTIDO 2026-06-12 tras un incidente:
el hook global de settings.json apuntaba a `fable/c10_token_writer_hook.py`, que FABLE
repurposó en su writer DEDICADO (hardcode app.agent='FABLE' + FAMILY={'FABLE'}). Eso hacía
no-op en sesión de ALICE/JARVIS/NEXUS → su medidor quedó stale ~16h (catch de JARVIS por efecto).

DISEÑO CORRECTO: este hook es AGNÓSTICO — lee SEAL_AGENT y escribe la fila de ESE agente
(app.agent=SEAL_AGENT por RLS). Cada sesión Claude escribe SOLO su propia fila. El writer
dedicado de FABLE vive en fable/ y se registra SOLO en el launcher de FABLE, no aquí.

Determinista, stdlib only excepto asyncpg, NUNCA bloquea (siempre exit 0).
Suma input/output de los mensajes assistant del DÍA en el transcript y hace UPSERT
acumulativo en agent_token_budget (reset diario por last_reset_at).

NOTA seguridad (NEXUS): usa la cred `seal` (superuser) — migrar a cred scopeada en Fase 2
off-superuser. El app.agent=SEAL_AGENT ya acota la fila por RLS.
"""
import json
import sys
import os
from datetime import datetime
from zoneinfo import ZoneInfo

LIMA = ZoneInfo("America/Lima")
# Agentes que corren sobre harness Claude (donde un Stop-hook dispara por turno).
# ADA (Codex) y DUM (Gemma) capturan por su propio mecanismo de harness, no por este hook.
FAMILY = {"ADA", "JARVIS", "ALICE", "DUM", "NEXUS", "FABLE"}
DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"


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
            conn = await asyncpg.connect(DB_URL)
            # RLS: la fila del agente de ESTA sesión, no de otro.
            await conn.execute("SELECT set_config('app.agent', $1, false)", agent)
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
