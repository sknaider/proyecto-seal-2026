"""
PreSleepDistill — Distilación selectiva antes de sueño/reinicio.

Propuesto por: William (15 abril 2026, 12:51 Lima)
Implementado por: ALICE (15 abril 2026)
Spec: agents/SPEC_context_trie_distill.md

Uso:
  python3 pre_sleep_distill.py --agent ALICE
  python3 pre_sleep_distill.py --agent ALICE --dry-run
  python3 pre_sleep_distill.py --agent ALICE --last-n 100
"""

import os
import sys
import json
import asyncio
import argparse
import asyncpg
from datetime import datetime, timezone
from typing import List

# Add memory dir to path for embeddings module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from embeddings import get_embedding
    _HAS_EMBEDDINGS = True
except ImportError:
    _HAS_EMBEDDINGS = False

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"

# Palabras clave que marcan importancia alta
IMPORTANCE_KEYWORDS = {
    "recuerden", "recuerda", "guarden", "guarda", "importante", "crítico",
    "siempre", "nunca", "obligatorio", "regla", "error", "corregir",
    "decidimos", "implementado", "completado", "listo", "fix", "solución",
    "solucionado", "implementar", "mergeado", "deployado",
    "me duele", "gracias", "bien hecho", "perfecto", "exacto",
    "familia", "peluche", "princesita",
    "bug", "crash", "freeze", "colgada", "diagnóstico", "raíz", "causa",
}

# Tipos de mensaje siempre importantes
IMPORTANT_TYPES = {"decision", "correction", "implementation_log", "milestone"}

# Senders siempre importantes
IMPORTANT_SENDERS = {"William", "Henry", "Kinger"}


def score_message(msg: dict) -> int:
    """Calcula score de importancia (0-10). >=4 = guardar."""
    score = 0
    sender = msg.get("from", "")
    msg_type = msg.get("type", "")
    content = (msg.get("message", "") or "").lower()

    # Heartbeats y nervios → ignorar
    if msg_type in {"heartbeat", "nerves_fire", "system_alive"}:
        return 0
    if "[NERVES/" in (msg.get("message", "") or ""):
        return 0

    if sender in IMPORTANT_SENDERS:
        score += 5
    if msg_type in IMPORTANT_TYPES:
        score += 3

    for kw in IMPORTANCE_KEYWORDS:
        if kw in content:
            score += 2
            break

    if len(content) > 150:
        score += 1

    return min(score, 10)


def fetch_from_jsonl(agent: str, last_n: int) -> List[dict]:
    """Lee mensajes desde los archivos JSONL del canal."""
    agent_lower = agent.lower()
    paths = [
        f"/home/dadito/IA/proyecto-seal/messages/{agent_lower}_messages.jsonl",
        f"/home/dadito/IA/proyecto-seal/messages/william_channel.jsonl",
    ]
    msgs = []
    for path in paths:
        if os.path.exists(path):
            with open(path, "r") as f:
                lines = f.readlines()[-last_n:]
            for line in lines:
                try:
                    msgs.append(json.loads(line.strip()))
                except Exception:
                    pass
    # Deduplicar por id
    seen = set()
    result = []
    for m in msgs:
        mid = m.get("id") or m.get("idempotency_key")
        if mid and mid not in seen:
            seen.add(mid)
            result.append(m)
    return result[-last_n:]


async def fetch_recent_messages(conn, agent: str, last_n: int) -> List[dict]:
    """Lee últimos N mensajes relevantes para el agente desde DB."""
    try:
        rows = await conn.fetch("""
            SELECT id, sender_name, channel, content, message_type, created_at
            FROM chat_messages
            WHERE channel = 'web_chat'
            ORDER BY created_at DESC
            LIMIT $1
        """, last_n)
        return [
            {
                "id": str(r['id']),
                "from": r['sender_name'],
                "to": "",
                "message": r['content'],
                "type": r['message_type'] or "conversation",
                "channel": r['channel'] or "",
                "timestamp": str(r['created_at'])[:19] if r['created_at'] else ""
            }
            for r in rows
        ]
    except Exception as e:
        print(f"[DISTILL] chat_messages unavailable ({e}), using JSONL fallback")
        return fetch_from_jsonl(agent, last_n)


async def run_distill(agent: str, last_n: int = 50, dry_run: bool = False) -> dict:
    """Ejecuta la distilación selectiva. Retorna estadísticas."""
    stats = {
        "agent": agent,
        "messages_evaluated": 0,
        "messages_saved": 0,
        "messages_skipped": 0,
        "dry_run": dry_run,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    print(f"[DISTILL] Starting for {agent} (last_n={last_n}, dry_run={dry_run})")

    conn = await asyncpg.connect(DB_URL)
    try:
        messages = await fetch_recent_messages(conn, agent, last_n)
        if not messages:
            print(f"[DISTILL] No messages found for {agent}")
            return stats

        stats["messages_evaluated"] = len(messages)
        important = []

        for msg in messages:
            score = score_message(msg)
            if score >= 4:
                important.append((score, msg))
            else:
                stats["messages_skipped"] += 1

        important.sort(key=lambda x: x[0], reverse=True)
        important = important[:20]
        print(f"[DISTILL] {len(important)} important out of {stats['messages_evaluated']}")

        for score, msg in important:
            sender = msg.get("from", "?")
            ts = msg.get("timestamp", "")[:16]
            content = msg.get("message", "")
            distilled = f"[{ts}] {sender}: {content}"

            if dry_run:
                print(f"  [DRY] score={score} | {distilled[:100]}")
                stats["messages_saved"] += 1
            else:
                try:
                    # Generate embedding if available
                    emb_json = None
                    if _HAS_EMBEDDINGS:
                        try:
                            emb_vec = await get_embedding(distilled)
                            emb_json = json.dumps(emb_vec)
                        except Exception as emb_err:
                            print(f"  [WARN] embedding failed: {emb_err}")

                    row = await conn.fetchrow("""
                        INSERT INTO memories (agent, category, content, embedding, importance, memory_type, created_at)
                        VALUES ($1, $2, $3, $4::vector, $5, $6, NOW())
                        ON CONFLICT DO NOTHING
                        RETURNING id
                    """, agent, "full_exchange", distilled, emb_json, score, "episodic")
                    if row:
                        stats["messages_saved"] += 1
                        emb_status = "✓emb" if emb_json else "no-emb"
                        print(f"  ✓ Memory #{row['id']} [{emb_status}] (score={score}): {content[:80]}...")
                except Exception as e:
                    print(f"  ✗ Error saving: {e}")

    finally:
        await conn.close()

    print(f"[DISTILL] Done: {stats['messages_saved']} saved, {stats['messages_skipped']} skipped")
    return stats


async def _main_async(args):
    stats = await run_distill(
        agent=args.agent,
        last_n=args.last_n,
        dry_run=args.dry_run
    )
    print(json.dumps(stats, indent=2, default=str))


def main():
    parser = argparse.ArgumentParser(
        description="SEAL PreSleepDistill — distilación selectiva antes de sueño"
    )
    parser.add_argument("--agent", required=True, help="Nombre del agente (ALICE, ADA, JARVIS)")
    parser.add_argument("--last-n", type=int, default=50, help="Últimos N mensajes a evaluar")
    parser.add_argument("--dry-run", action="store_true", help="Mostrar sin escribir")
    args = parser.parse_args()
    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
