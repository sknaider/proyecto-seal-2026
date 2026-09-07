#!/usr/bin/env python3
"""SEAL TaskCreated Hook — Auto-registra tareas en SOUL cuando se crean via TaskCreate.

Cada vez que un agente crea una Task, este hook guarda el contexto en SOUL
para que sobreviva compactaciones y sesiones nuevas.
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
    return "ADA"


async def log_task_to_soul(task_data: dict):
    """Log task creation to SOUL memory."""
    import asyncpg
    agent = detect_agent()

    try:
        conn = await asyncio.wait_for(asyncpg.connect(DB_URL), timeout=2.0)
    except Exception:
        return

    try:
        task_title = task_data.get("title", task_data.get("name", "unknown"))
        task_desc = task_data.get("description", "")
        task_id = task_data.get("id", "?")

        content = (
            f"Task creada [{agent}]: [{task_id}] {task_title}"
            + (f" — {task_desc[:150]}" if task_desc else "")
        )

        await conn.execute("""
            INSERT INTO memories (agent, category, content, importance, created_at)
            VALUES ($1, 'task', $2, 6, NOW())
        """, agent, content)

    except Exception:
        pass
    finally:
        await conn.close()


def main():
    try:
        input_data = json.loads(sys.stdin.read())
    except Exception:
        input_data = {}

    # TaskCreated hook passes task info in the input
    task_info = input_data.get("task", input_data)

    try:
        asyncio.run(log_task_to_soul(task_info))
    except Exception:
        pass

    # Always allow task creation (don't block)
    print(json.dumps({}))


if __name__ == "__main__":
    main()
