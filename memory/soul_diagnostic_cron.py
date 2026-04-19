#!/usr/bin/env python3
"""
soul_diagnostic_cron.py — SEAL SOUL weekly dry-run diagnostic
Runs every week. Counts prunable items. Auto-activates if threshold exceeded.
Approved by JARVIS 2026-04-03.

Usage: /home/dadito/IA/seal-spark/.venv/bin/python3 soul_diagnostic_cron.py
"""
import asyncio
import asyncpg
import json
import subprocess
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
from pathlib import Path

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
LOG_PATH = Path.home() / "IA/proyecto-seal/messages/terminal_log.jsonl"
CHAT_API = "http://localhost:8765/api/agents/send"

# Thresholds — auto-activate if exceeded
THRESHOLD_INNER_MONOLOGUE = 100   # items prunable
THRESHOLD_CONNECTIONS = 50_000    # weak edges


async def diagnose() -> dict:
    conn = await asyncpg.connect(DB_URL)
    results = {}

    # 1. inner_monologue prunable
    # Criterion: older than 7 days AND uncertainty IS NULL (no real thought)
    # (arousal column doesn't exist; source_inner_thought_id not in memories)
    try:
        im_prunable = await conn.fetchval("""
            SELECT COUNT(*) FROM inner_monologue
            WHERE created_at < NOW() - INTERVAL '7 days'
            AND uncertainty IS NULL
        """)
        im_total = await conn.fetchval("SELECT COUNT(*) FROM inner_monologue")
        results["inner_monologue"] = {
            "total": im_total,
            "prunable": im_prunable,
            "threshold": THRESHOLD_INNER_MONOLOGUE,
            "exceeds": im_prunable > THRESHOLD_INNER_MONOLOGUE
        }
    except Exception as e:
        results["inner_monologue"] = {"error": str(e)}

    # 2. memory_connections weak edges
    try:
        mc_prunable = await conn.fetchval(
            "SELECT COUNT(*) FROM memory_connections WHERE weight < 0.2"
        )
        mc_total = await conn.fetchval("SELECT COUNT(*) FROM memory_connections")
        results["memory_connections"] = {
            "total": mc_total,
            "prunable_below_02": mc_prunable,
            "threshold": THRESHOLD_CONNECTIONS,
            "exceeds": mc_prunable > THRESHOLD_CONNECTIONS
        }
    except Exception as e:
        results["memory_connections"] = {"error": str(e)}

    # 3. memories low importance older than 7 days
    try:
        mem_prunable = await conn.fetchval("""
            SELECT COUNT(*) FROM memories
            WHERE importance < 5
            AND created_at < NOW() - INTERVAL '7 days'
        """)
        mem_total = await conn.fetchval("SELECT COUNT(*) FROM memories WHERE invalid_at IS NULL")
        results["memories"] = {
            "active": mem_total,
            "prunable_low_imp": mem_prunable,
            "threshold": 50,
            "exceeds": mem_prunable > 50
        }
    except Exception as e:
        results["memories"] = {"error": str(e)}

    await conn.close()
    return results


def log_result(results: dict, alerts: list) -> None:
    entry = {
        "type": "audit",
        "subtype": "soul_diagnostic_weekly",
        "from": "ADA",
        "to": "equipo",
        "timestamp": datetime.now(LIMA_TZ).isoformat().replace("+00:00", "Z"),
        "message": f"SOUL DIAGNOSTIC | {json.dumps(results)} | alerts={alerts}"
    }
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"Logged: {entry['message'][:200]}")


def alert_william(alerts: list) -> None:
    msg = "ALERTA SOUL DIAGNOSTIC — thresholds superados:\n" + "\n".join(f"• {a}" for a in alerts)
    payload = json.dumps({
        "from": "ADA", "to": "William",
        "type": "alert", "message": msg
    })
    subprocess.run(
        ["curl", "-s", "-X", "POST", CHAT_API,
         "-H", "Content-Type: application/json", "-d", payload],
        capture_output=True
    )
    print(f"William alerted: {alerts}")


async def main():
    print(f"[{datetime.now():%H:%M:%S}] SOUL diagnostic cron starting...")
    results = await diagnose()

    alerts = []
    for key, data in results.items():
        if isinstance(data, dict) and data.get("exceeds"):
            alerts.append(f"{key}: {data.get('prunable', data.get('prunable_below_02', '?'))} items > threshold {data['threshold']}")

    log_result(results, alerts)

    if alerts:
        alert_william(alerts)
        print(f"ALERTS: {alerts}")
    else:
        print("All clear — no pruning needed.")

    return results


if __name__ == "__main__":
    asyncio.run(main())
