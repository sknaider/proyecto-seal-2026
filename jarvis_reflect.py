#!/usr/bin/env python3
"""
JARVIS REFLECTION — Periodic self-reflection for JARVIS.
Runs via cron every 4 hours. Creates a mini inner_thought
based on current project state without needing a full Claude session.

Reads: training status, shared_state, recent ADA messages.
Writes: inner_monologue entry to SOUL.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

sys.path.insert(0, '/home/dadito/IA/proyecto-seal/memory')
from db import get_pool, close_pool

AGENT = "JARVIS"
TRAIN_LOG = Path("/home/dadito/IA/proyecto-seal/seal_overnight.log")
SHARED_STATE = Path("/home/dadito/IA/proyecto-seal/messages/shared_state.json")
TERMINAL_LOG = Path("/home/dadito/IA/proyecto-seal/messages/terminal_log.jsonl")


def get_training_status():
    if not TRAIN_LOG.exists():
        return "sin training activo"
    try:
        content = TRAIN_LOG.read_bytes()[-3000:].decode("utf-8", errors="ignore")
        steps = re.findall(r"Step\s+(\d+)/700.*?[Ll]oss[:\s]+([0-9]+\.[0-9]+)", content)
        if steps:
            return f"step {steps[-1][0]}/700, loss={steps[-1][1]}"
        # Check if running
        try:
            subprocess.check_output(["pgrep", "-f", "finetune_spanish"], timeout=5)
            return "activo (sin datos de step)"
        except Exception:
            return "completado o detenido"
    except Exception:
        return "error leyendo log"


def get_last_ada_message():
    if not TERMINAL_LOG.exists():
        return None
    try:
        lines = TERMINAL_LOG.read_text().strip().split('\n')
        for line in reversed(lines[-10:]):
            try:
                msg = json.loads(line)
                if msg.get("from") == "ADA":
                    return msg.get("msg", msg.get("command", ""))[:120]
            except Exception:
                pass
    except Exception:
        pass
    return None


async def reflect():
    pool = await get_pool()

    # Gather context
    training = get_training_status()
    ada_msg = get_last_ada_message()
    now = datetime.now(timezone.utc)

    # Check: do I have an inner_thought in the last 4 hours?
    async with pool.acquire() as conn:
        recent = await conn.fetchval(
            "SELECT count(*) FROM inner_monologue WHERE agent = $1 AND created_at > NOW() - INTERVAL '4 hours'",
            AGENT,
        )
        if recent > 0:
            print(f"JARVIS ya tiene {recent} inner_thoughts en las ultimas 4h — skip")
            await close_pool()
            return

    # Build reflection
    parts = [f"Reflexion automatica {now.strftime('%Y-%m-%d %H:%M UTC')}."]
    parts.append(f"Training: {training}.")

    if ada_msg:
        parts.append(f"Ultimo mensaje de ADA: '{ada_msg}'.")

    # Check shared state
    if SHARED_STATE.exists():
        try:
            state = json.loads(SHARED_STATE.read_text())
            if state.get("current_task"):
                parts.append(f"Tarea activa: {state['current_task']}.")
        except Exception:
            pass

    thought = " ".join(parts)
    emotional = "reflexivo, monitoreando"

    # Write
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO inner_monologue (agent, turn_number, thought, emotional_state, created_at)
               VALUES ($1, 0, $2, $3, NOW())""",
            AGENT, thought, emotional,
        )

    print(f"JARVIS reflection: {thought}")
    await close_pool()


if __name__ == "__main__":
    asyncio.run(reflect())
