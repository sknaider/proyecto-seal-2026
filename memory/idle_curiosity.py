#!/usr/bin/env python3
"""
idle_curiosity.py — Curiosidad espontánea para agentes SEAL (GAP 1).

Corre cada 15min via cron. Si el agente está idle y el cooldown pasó,
genera una pregunta/reflexión desde memoria reciente y la postea.

Uso:
    python3 idle_curiosity.py JARVIS
    python3 idle_curiosity.py ADA
"""
from __future__ import annotations

import asyncio
import json
import random
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncpg

sys.path.insert(0, os.path.dirname(__file__))
from db import get_pool, close_pool

LIMA_TZ = ZoneInfo("America/Lima")

IDLE_THRESHOLD_MINUTES = 15
COOLDOWN_MINUTES = 45
MAX_PER_DAY = 8
CHAT_API = "http://localhost:8765/api/agents/send"
WILLIAM_CHANNEL_JSONL = "/home/dadito/IA/proyecto-seal/messages/william_channel.jsonl"

CURIOSITY_TEMPLATES = {
    "decision": [
        "Tomamos la decisión de '{snippet}' — ¿sigue siendo válida o hay algo nuevo que cambia el panorama?",
        "Decidimos '{snippet}' — ¿hay casos edge que no consideramos entonces?",
    ],
    "insight": [
        "Tuve el insight: '{snippet}' — ¿lo hemos aplicado completamente o quedó en teoría?",
        "Insight pendiente: '{snippet}' — ¿qué bloquea convertirlo en acción?",
    ],
    "correction": [
        "Fui corregido sobre '{snippet}' — ¿ya cambié el patrón o solo lo memoricé?",
        "Corrección internalizada: '{snippet}' — ¿cuándo fue la última vez que la apliqué?",
    ],
    "fact": [
        "Recordé que '{snippet}' — ¿hay algo relacionado que deberíamos explorar?",
    ],
    "default": [
        "Pensando en '{snippet}' — ¿qué no estamos viendo?",
        "'{snippet}' sigue en mi mente — ¿hay una pregunta sin responder ahí?",
    ],
}


def load_state(agent: str) -> dict:
    path = Path(f"/tmp/{agent}_idle_curiosity_state.json")
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return {}


def save_state(agent: str, state: dict):
    path = Path(f"/tmp/{agent}_idle_curiosity_state.json")
    path.write_text(json.dumps(state))


def should_fire(agent: str, state: dict) -> tuple[bool, str]:
    now = datetime.now(LIMA_TZ)

    last_str = state.get("last_fired")
    if last_str:
        last = datetime.fromisoformat(last_str)
        elapsed = (now - last).total_seconds() / 60
        if elapsed < COOLDOWN_MINUTES:
            return False, f"cooldown: {elapsed:.0f}/{COOLDOWN_MINUTES}min"

    today = now.strftime("%Y-%m-%d")
    if state.get("date_today") == today and state.get("count_today", 0) >= MAX_PER_DAY:
        return False, f"max/day reached ({MAX_PER_DAY})"

    if not is_channel_idle(agent):
        return False, "channel not idle"

    return True, "ok"


def is_channel_idle(agent: str) -> bool:
    """Check if the agent's channel has been quiet for IDLE_THRESHOLD_MINUTES."""
    path = Path(WILLIAM_CHANNEL_JSONL)
    if not path.exists():
        return True

    threshold = datetime.now(LIMA_TZ) - timedelta(minutes=IDLE_THRESHOLD_MINUTES)

    try:
        lines = path.read_bytes()[-8192:].decode("utf-8", errors="ignore").splitlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
                ts_str = msg.get("timestamp", "")
                if not ts_str:
                    continue
                ts = datetime.fromisoformat(ts_str)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=LIMA_TZ)
                if msg.get("from") == agent or msg.get("to") in (agent, "equipo"):
                    return ts < threshold
            except Exception:
                continue
    except Exception:
        pass
    return True


async def pick_seed_memory(pool: asyncpg.Pool, agent: str) -> dict | None:
    """Pick a recent, interesting memory to spark curiosity."""
    already_used = await pool.fetch("""
        SELECT seed_memory_id FROM idle_curiosity_log
        WHERE agent = $1 AND created_at > now() - interval '3 days'
          AND seed_memory_id IS NOT NULL
    """, agent)
    used_ids = {r["seed_memory_id"] for r in already_used}

    candidates = await pool.fetch("""
        SELECT id, content, category, importance
        FROM memories
        WHERE agent = $1
          AND invalid_at IS NULL
          AND category IN ('decision', 'insight', 'correction', 'fact', 'pattern')
          AND importance >= 6
          AND created_at > now() - interval '14 days'
        ORDER BY importance DESC, random()
        LIMIT 20
    """, agent)

    eligible = [r for r in candidates if r["id"] not in used_ids]
    if not eligible:
        eligible = list(candidates)
    if not eligible:
        return None

    return dict(random.choice(eligible[:8]))


def craft_question(seed: dict) -> tuple[str, str]:
    """Return (question, output_type) from a seed memory."""
    category = seed.get("category", "default")
    content = seed.get("content", "")
    snippet = content[:80].strip().rstrip(".,")

    templates = CURIOSITY_TEMPLATES.get(category, CURIOSITY_TEMPLATES["default"])
    question = random.choice(templates).format(snippet=snippet)

    introspective_keywords = ("yo ", "mi ", "me pregunto", "fui ", "internaliz", "cambié")
    output_type = "inner_monologue"
    if any(kw in question.lower() for kw in ("equipo", "nosotros", "william", "seal", "sistema")):
        output_type = "web_chat"
    elif any(kw in question.lower() for kw in introspective_keywords):
        output_type = "inner_monologue"
    else:
        output_type = "web_chat" if seed.get("importance", 5) >= 8 else "inner_monologue"

    return question, output_type


async def post_web_chat(agent: str, question: str):
    import urllib.request
    payload = json.dumps({
        "from": agent,
        "to": "equipo",
        "type": "curiosity",
        "channel": "web_chat",
        "message": f"💭 {question}",
    }).encode()
    req = urllib.request.Request(
        CHAT_API,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read()
    except Exception as e:
        print(f"  [chat] POST failed: {e}")


async def save_inner_monologue(pool: asyncpg.Pool, agent: str, question: str):
    now = datetime.now(LIMA_TZ)
    turn_num = await pool.fetchval(
        "SELECT COALESCE(MAX(turn_number), 0) + 1 FROM inner_monologue WHERE agent = $1", agent
    )
    await pool.execute("""
        INSERT INTO inner_monologue (agent, turn_number, thought, emotional_state, created_at)
        VALUES ($1, $2, $3, $4, $5)
    """, agent, turn_num, question, "curioso", now)


async def log_curiosity(pool: asyncpg.Pool, agent: str, question: str, output_type: str, seed: dict):
    await pool.execute("""
        INSERT INTO idle_curiosity_log (agent, question, output_type, seed_memory_id, seed_content, created_at)
        VALUES ($1, $2, $3, $4, $5, now())
    """, agent, question, output_type, seed.get("id"), seed.get("content", "")[:200])


async def run(agent: str):
    state = load_state(agent)
    fire, reason = should_fire(agent, state)
    if not fire:
        print(f"[{agent}] skip — {reason}")
        return

    pool = await get_pool()
    try:
        seed = await pick_seed_memory(pool, agent)
        if not seed:
            print(f"[{agent}] no seed memory found")
            return

        question, output_type = craft_question(seed)
        print(f"[{agent}] → {output_type}: {question[:80]}…")

        if output_type == "web_chat":
            await post_web_chat(agent, question)
        else:
            await save_inner_monologue(pool, agent, question)

        await log_curiosity(pool, agent, question, output_type, seed)

        now = datetime.now(LIMA_TZ)
        today = now.strftime("%Y-%m-%d")
        state["last_fired"] = now.isoformat()
        state["date_today"] = today
        state["count_today"] = state.get("count_today", 0) + 1 if state.get("date_today") == today else 1
        save_state(agent, state)

    finally:
        await close_pool()


if __name__ == "__main__":
    agent = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SEAL_AGENT", "JARVIS")
    asyncio.run(run(agent))
