#!/usr/bin/env python3
"""SEAL Continuity Snapshot — Capa 2 del sistema de sueño.

Captura el estado mental activo de un agente cada 5 minutos.
Al despertar, el agente lee este snapshot y retoma el hilo sin hueco.

Uso:
  continuity_snapshot.py --agent ADA
  continuity_snapshot.py --agent JARVIS --window 2
"""

import asyncio
import json
import sys
import os
import subprocess
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

LIMA_TZ = ZoneInfo("America/Lima")
SEAL_DIR = Path(__file__).parent.parent
MESSAGES_DIR = Path(__file__).parent
CONTINUITY_DIR = MESSAGES_DIR / "continuity"
CONTINUITY_DIR.mkdir(exist_ok=True)

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
MCP_PYTHON = "/home/dadito/IA/seal-spark/.venv/bin/python3"
SCHEMA_VERSION = 1
SNAPSHOT_MAX_KB = 50


def _truncate(s: str, n: int = 300) -> str:
    return s[:n] if len(s) > n else s


async def build_snapshot(agent: str, window_hours: int = 2) -> dict:
    import asyncpg

    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=window_hours)

    conn = await asyncpg.connect(DB_URL)

    # --- recent_messages: últimos 50 mensajes relevantes al agente ---
    channel_file = MESSAGES_DIR / "william_channel.jsonl"
    recent_messages = []
    if channel_file.exists():
        lines = channel_file.read_text(errors="replace").strip().splitlines()
        for line in lines[-200:]:
            try:
                m = json.loads(line)
                if m.get("type") in ("system_alive", "heartbeat"):
                    continue
                ts = m.get("timestamp", "")
                recent_messages.append({
                    "ts": ts,
                    "from": m.get("from", "?"),
                    "to": m.get("to", "?"),
                    "msg": _truncate(m.get("message", ""), 200),
                    "type": m.get("type", "conversation"),
                })
            except:
                pass
    recent_messages = recent_messages[-50:]

    # --- recent_thoughts: últimos 5 inner_thoughts ---
    rows = await conn.fetch(
        "SELECT thought, emotional_state, intention, created_at "
        "FROM inner_monologue WHERE agent = $1 "
        "ORDER BY created_at DESC LIMIT 5",
        agent
    )
    recent_thoughts = [
        {
            "ts": r["created_at"].isoformat() if r["created_at"] else "",
            "thought": _truncate(r["thought"] or "", 300),
            "emotional_state": r["emotional_state"] or "",
            "intention": _truncate(r["intention"] or "", 150),
        }
        for r in rows
    ]

    # --- working_state: desde DB ---
    ws_row = await conn.fetchrow(
        "SELECT state FROM working_state WHERE agent = $1 "
        "ORDER BY updated_at DESC LIMIT 1",
        agent
    )
    working_state = {}
    if ws_row and ws_row["state"]:
        try:
            working_state = json.loads(ws_row["state"]) if isinstance(ws_row["state"], str) else ws_row["state"]
        except:
            working_state = {}

    # --- active_tasks: desde TaskList si hay endpoint, sino vacío ---
    active_tasks = []
    try:
        result = subprocess.run(
            ["bash", "-c", f"cat {MESSAGES_DIR}/vscode_commands.jsonl 2>/dev/null | tail -20"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().splitlines():
            try:
                m = json.loads(line)
                if m.get("type") in ("task_create", "task_update", "task"):
                    active_tasks.append({
                        "id": m.get("id", "?"),
                        "desc": _truncate(m.get("message", ""), 150),
                        "status": m.get("status", "?"),
                    })
            except:
                pass
    except:
        pass

    # --- emotional_arc: últimos 10 estados emocionales ---
    emo_rows = await conn.fetch(
        "SELECT emotional_state, created_at FROM inner_monologue "
        "WHERE agent = $1 ORDER BY created_at DESC LIMIT 10",
        agent
    )
    emotional_arc = [
        {"ts": r["created_at"].isoformat() if r["created_at"] else "", "state": r["emotional_state"] or ""}
        for r in emo_rows
    ]

    # --- key_decisions: mensajes tipo coordination/decision en ventana ---
    key_decisions = []
    for m in recent_messages:
        if m["type"] in ("coordination", "decision") or (
            agent.upper() in m.get("from", "").upper() and m["type"] == "conversation"
        ):
            key_decisions.append({
                "ts": m["ts"],
                "decision": _truncate(m["msg"], 200),
                "rationale": "",
            })
    key_decisions = key_decisions[-10:]

    await conn.close()

    # --- last msg de William ---
    last_william_msg = ""
    for m in reversed(recent_messages):
        if m["from"] == "William":
            last_william_msg = m["msg"]
            break

    # --- resume_prompt ---
    last_state = recent_thoughts[0]["emotional_state"] if recent_thoughts else "desconocido"
    last_thought = recent_thoughts[0]["thought"] if recent_thoughts else ""
    last_intention = recent_thoughts[0]["intention"] if recent_thoughts else ""
    minutes_ago = 5  # el timer corre cada 5min

    resume_prompt = (
        f"Acabas de despertar. Hace ~{minutes_ago}min estabas activo. "
        f"Tu último estado emocional: {last_state}. "
    )
    if last_thought:
        resume_prompt += f"Tu último pensamiento: \"{last_thought[:200]}\". "
    if last_intention:
        resume_prompt += f"Ibas a: {last_intention[:150]}. "
    if last_william_msg:
        resume_prompt += f"Último mensaje de William: \"{last_william_msg[:200]}\". "
    if active_tasks:
        first_task = active_tasks[0].get("desc", "")
        if first_task:
            resume_prompt += f"Tarea activa: {first_task[:150]}. "
    resume_prompt += "Retoma el hilo."

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "agent": agent,
        "snapshot_at": now.isoformat(),
        "session_id": f"{int(now.timestamp())}_{agent}",
        "window_hours": window_hours,
        "window_start": window_start.isoformat(),
        "context": {
            "recent_messages": recent_messages,
            "recent_thoughts": recent_thoughts,
            "working_state": working_state,
            "active_tasks": active_tasks,
            "emotional_arc": emotional_arc,
            "key_decisions": key_decisions,
        },
        "resume_prompt": resume_prompt,
    }

    return snapshot


async def save_snapshot(agent: str, window_hours: int = 2):
    snapshot = await build_snapshot(agent, window_hours)

    output = json.dumps(snapshot, indent=2, ensure_ascii=False, default=str)

    # Verificar tamaño
    size_kb = len(output.encode()) / 1024
    if size_kb > SNAPSHOT_MAX_KB:
        # Reducir: truncar recent_messages
        while size_kb > SNAPSHOT_MAX_KB and snapshot["context"]["recent_messages"]:
            snapshot["context"]["recent_messages"] = snapshot["context"]["recent_messages"][10:]
            output = json.dumps(snapshot, indent=2, ensure_ascii=False, default=str)
            size_kb = len(output.encode()) / 1024

    agent_lower = agent.lower()
    latest_path = CONTINUITY_DIR / f"{agent_lower}_continuity_snapshot.json"
    latest_path.write_text(output)

    # Archivo diario (rotación)
    from zoneinfo import ZoneInfo
    date_str = datetime.now(ZoneInfo("America/Lima")).strftime("%Y%m%d")
    daily_path = CONTINUITY_DIR / f"{agent_lower}_continuity_{date_str}.json"
    daily_path.write_text(output)

    # Limpiar archivos diarios viejos (mantener últimos 7 días)
    old_files = sorted(CONTINUITY_DIR.glob(f"{agent_lower}_continuity_2*.json"))
    for f in old_files[:-7]:
        f.unlink()

    msgs = len(snapshot["context"]["recent_messages"])
    thoughts = len(snapshot["context"]["recent_thoughts"])
    print(f"[OK] Snapshot {agent} — {size_kb:.1f}KB — {msgs} msgs, {thoughts} thoughts — {latest_path.name}")
    print(f"resume_prompt: {snapshot['resume_prompt'][:150]}...")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True, help="ADA, JARVIS, ALICE, DUM")
    parser.add_argument("--window", type=int, default=2, help="Ventana en horas (default: 2)")
    args = parser.parse_args()

    asyncio.run(save_snapshot(args.agent, args.window))
