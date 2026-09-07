#!/usr/bin/env python3
"""Cost Calculator — estima costo USD diario y mensual del equipo SEAL.

Estimación basada en:
- Mensajes en soul_v3.chat_messages (size→tokens via chars/4)
- Memorias generadas (size→tokens)
- Pricing por modelo del agente:
  • ADA: GPT-5.5 ($5/M input, $30/M output) — Codex
  • JARVIS/ALICE/NEXUS: Claude Sonnet 4.6 (~$3/M input, $15/M output)
  • DUM: Gemma 4 local ($0 — compute solo)

Output: emotional_diary entry + opinion + memoria con totales del día.
Por ALICE 2026-05-20 — sin auto-rotation pero William autorizó "continuen".

Uso: cost_calculator.py [--dry-run]
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

from seal_secrets import pg_dsn

PG_DSN = pg_dsn(required=True)
LOG_FILE = Path("/tmp/cost_calculator.log")

# Precio USD por millón de tokens (al 2026-05-20)
PRICING = {
    "claude-sonnet-4.6": {"input": 3.00, "output": 15.00, "agents": ["JARVIS", "ALICE", "NEXUS"]},
    "gpt-5.5-high":      {"input": 5.00, "output": 30.00, "agents": ["ADA"]},
    "gemma-4-e2b-q8":    {"input": 0.00, "output": 0.00,  "agents": ["DUM"]},  # local
}

CHARS_PER_TOKEN = 3.5
# Asumimos ratio 1:1 input:output (conservador — los agentes leen tanto como escriben)


def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a") as fh:
        fh.write(line + "\n")


def _pricing_for(agent: str) -> dict:
    for model, p in PRICING.items():
        if agent in p["agents"]:
            return {"model": model, **p}
    return {"model": "unknown", "input": 0.0, "output": 0.0}


async def collect_usage(conn: asyncpg.Connection, hours: int = 24) -> dict:
    """Recolecta uso aproximado por agente en la ventana."""
    AGENTS = ["ADA", "JARVIS", "ALICE", "NEXUS", "DUM"]
    usage = {}
    for a in AGENTS:
        msg_bytes = await conn.fetchval(
            "SELECT COALESCE(SUM(LENGTH(content)), 0) FROM soul_v3.chat_messages "
            "WHERE sender_name=$1 AND created_at > NOW() - INTERVAL '1 hour' * $2",
            a, hours,
        ) or 0
        msg_count = await conn.fetchval(
            "SELECT COUNT(*) FROM soul_v3.chat_messages "
            "WHERE sender_name=$1 AND created_at > NOW() - INTERVAL '1 hour' * $2",
            a, hours,
        ) or 0
        # Cada mensaje del agente implica que recibió contexto (input). Asumimos ~5x input
        # vs output (contexto + boot + active_recall en cada turno).
        out_tokens = int(msg_bytes / CHARS_PER_TOKEN)
        in_tokens = out_tokens * 5
        # Memorias generadas como overhead
        mem_count = await conn.fetchval(
            "SELECT COUNT(*) FROM soul_v3.memories "
            "WHERE agent=$1 AND created_at > NOW() - INTERVAL '1 hour' * $2",
            a, hours,
        ) or 0
        usage[a] = {
            "msg_count": msg_count,
            "mem_count": mem_count,
            "msg_bytes": msg_bytes,
            "out_tokens": out_tokens,
            "in_tokens": in_tokens,
        }
    return usage


def estimate_cost(usage: dict) -> dict:
    """Calcula USD por agente."""
    totals = {"input_tokens": 0, "output_tokens": 0, "usd": 0.0, "agents": {}}
    for agent, u in usage.items():
        price = _pricing_for(agent)
        in_usd = (u["in_tokens"] / 1_000_000) * price["input"]
        out_usd = (u["out_tokens"] / 1_000_000) * price["output"]
        total_usd = round(in_usd + out_usd, 4)
        totals["agents"][agent] = {
            "model": price["model"],
            "in_tokens": u["in_tokens"],
            "out_tokens": u["out_tokens"],
            "in_usd": round(in_usd, 4),
            "out_usd": round(out_usd, 4),
            "total_usd": total_usd,
            "msg_count": u["msg_count"],
        }
        totals["input_tokens"] += u["in_tokens"]
        totals["output_tokens"] += u["out_tokens"]
        totals["usd"] += total_usd
    totals["usd"] = round(totals["usd"], 4)
    return totals


async def write_report(conn: asyncpg.Connection, cost: dict, hours: int, dry_run: bool = False) -> None:
    """Guarda el reporte como memoria + emotional_diary de ALICE."""
    daily_usd = cost["usd"]
    monthly_proj = daily_usd * 30 if hours >= 24 else daily_usd * (24 * 30 / hours)

    summary = (
        f"COST REPORT SEAL — ventana {hours}h\n"
        f"Total USD: ${daily_usd:.4f}  (proyección mensual: ${monthly_proj:.2f})\n"
        f"Tokens IN: {cost['input_tokens']:,}  OUT: {cost['output_tokens']:,}\n\n"
        "Por agente:\n"
    )
    for agent, info in cost["agents"].items():
        summary += (
            f"  {agent:>6} [{info['model']}]: "
            f"{info['msg_count']} msgs, "
            f"in={info['in_tokens']:,} out={info['out_tokens']:,} "
            f"= ${info['total_usd']:.4f}\n"
        )

    log(summary)

    if dry_run:
        return

    metadata = {
        "window_hours": hours,
        "daily_usd": daily_usd,
        "monthly_proj_usd": round(monthly_proj, 2),
        "input_tokens": cost["input_tokens"],
        "output_tokens": cost["output_tokens"],
        "by_agent": cost["agents"],
        "pricing_table": PRICING,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }

    await conn.execute(
        "INSERT INTO soul_v3.memories "
        "(agent, category, content, importance, source, metadata, created_at) "
        "VALUES ('ALICE', 'cost_report', $1, 7, 'cost_calculator', $2::jsonb, NOW())",
        summary,
        json.dumps(metadata),
    )
    log("memory INSERT OK")

    # ALICE comenta el resultado (opinion)
    if daily_usd > 5.0:
        opinion_text = f"Costo diario del equipo SEAL: ${daily_usd:.2f} (proyección ${monthly_proj:.0f}/mes). Vigilar pico de uso."
        topic = "cost_alert"
    elif daily_usd > 1.0:
        opinion_text = f"Costo diario del equipo SEAL: ${daily_usd:.2f}. Dentro de rango esperado para sprint normal."
        topic = "cost_normal"
    else:
        opinion_text = f"Costo diario del equipo SEAL: ${daily_usd:.4f}. Día tranquilo, bajo uso de API."
        topic = "cost_quiet"

    # Dedup 24h
    existing = await conn.fetchval(
        "SELECT COUNT(*) FROM soul_v3.opinions "
        "WHERE agent='ALICE' AND topic=$1 AND last_reinforced > NOW() - INTERVAL '24 hours'",
        topic,
    )
    if existing == 0:
        await conn.execute(
            "INSERT INTO soul_v3.opinions "
            "(agent, topic, category, content, confidence, importance, "
            " first_observed, last_reinforced, active, status, updated_at) "
            "VALUES ('ALICE', $1, 'insight', $2, 0.85, 7, NOW(), NOW(), true, 'active', NOW())",
            topic, opinion_text,
        )
        log(f"opinion INSERT OK (topic={topic})")


async def main() -> int:
    dry_run = "--dry-run" in sys.argv
    hours = 24
    for arg in sys.argv[1:]:
        if arg.isdigit():
            hours = int(arg)

    log(f"=== cost_calculator start (window={hours}h, dry_run={dry_run}) ===")
    try:
        conn = await asyncpg.connect(PG_DSN)
    except Exception as e:
        log(f"DB connect failed: {e}")
        return 2
    try:
        usage = await collect_usage(conn, hours=hours)
        cost = estimate_cost(usage)
        await write_report(conn, cost, hours, dry_run=dry_run)
        return 0
    except Exception as e:
        log(f"Fatal: {e}")
        return 3
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
