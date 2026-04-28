#!/usr/bin/env python3
"""SEAL PostCompact Hook — Re-inyecta contexto crítico de SOUL después de cada compactación.

from zoneinfo import ZoneInfo
LIMA_TZ = ZoneInfo("America/Lima")
Se ejecuta automáticamente cuando Claude Code compacta la conversación.
Devuelve correcciones de William + reglas activas + estado del equipo.
"""

import asyncio
import json
import os
import sys
import time

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"


def detect_agent():
    try:
        ppid = os.getppid()
        cmdline = open(f"/proc/{ppid}/cmdline", "rb").read().decode("utf-8", errors="replace")
        if "JARVIS" in cmdline:
            return "JARVIS"
        elif "ADA" in cmdline:
            return "ADA"
    except Exception:
        pass
    cwd = os.getcwd()
    if "memory" in cwd:
        return "JARVIS"
    return "ADA"


async def post_compact_context() -> str:
    import asyncpg
    t0 = time.monotonic()
    agent = detect_agent()

    try:
        conn = await asyncio.wait_for(asyncpg.connect(DB_URL), timeout=3.0)
    except Exception:
        return ""

    lines = [f"⚡ SOUL RE-CARGADO TRAS COMPACTACIÓN — {agent}"]
    lines.append("La compactación borró contexto de sesión. Esto es lo que SIEMPRE debes recordar:\n")

    try:
        # Correcciones más importantes de William (sin límite de tiempo)
        corrections = await conn.fetch("""
            SELECT content, importance FROM memories
            WHERE agent = $1 AND category = 'correction' AND invalid_at IS NULL
            ORDER BY importance DESC, created_at DESC
            LIMIT 7
        """, agent)

        if corrections:
            lines.append("🔴 CORRECCIONES CRÍTICAS DE WILLIAM:")
            for c in corrections:
                lines.append(f"  • {c['content'][:250]}")

        # Todas las reglas críticas
        rules = await conn.fetch("""
            SELECT rule_key, content FROM rules
            WHERE active = true AND priority >= 8
            ORDER BY
                CASE WHEN priority = 10 THEN 0 ELSE 1 END,
                created_at DESC
            LIMIT 7
        """)

        if rules:
            lines.append("\n📋 REGLAS ACTIVAS (NO OLVIDAR):")
            for r in rules:
                lines.append(f"  [{r['rule_key']}]: {r['content'][:200]}")

        # Estado del equipo desde event_log (fuente única de verdad)
        from datetime import datetime, timezone as tz
        lines.append("\n🟢 ESTADO DEL EQUIPO:")
        now_utc = datetime.now(tz.utc)
        for name in ["ADA", "JARVIS", "ALICE", "DUM"]:
            try:
                row = await conn.fetchrow(
                    """SELECT created_at FROM soul_v3.event_log
                       WHERE event_type = 'heartbeat' AND agent = $1
                       ORDER BY created_at DESC LIMIT 1""",
                    name,
                )
                if row:
                    age = int((now_utc - row["created_at"]).total_seconds())
                    status = "ALIVE" if age < 600 else "STALE"
                    lines.append(f"  {name}: {status} (hace {age}s)")
                else:
                    lines.append(f"  {name}: OFFLINE")
            except Exception:
                lines.append(f"  {name}: OFFLINE")

    except Exception as e:
        lines.append(f"(error: {e})")
    finally:
        await conn.close()

    elapsed = int((time.monotonic() - t0) * 1000)
    lines.append(f"\n[PostCompact hook — {elapsed}ms — continúa tu trabajo normalmente]")
    return "\n".join(lines)


def main():
    # PostCompact hook recibe el summary como stdin
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception:
        input_data = {}

    try:
        result = asyncio.run(post_compact_context())
    except Exception:
        print(json.dumps({}))
        return

    if result:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostCompact",
                "additionalContext": result
            }
        }
        print(json.dumps(output))
    else:
        print(json.dumps({}))


if __name__ == "__main__":
    main()
