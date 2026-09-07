#!/usr/bin/env python3
"""
SEAL Soul — Session Capture
Reads a Claude Code transcript (.jsonl) and extracts memories from the conversation.
Designed to run at end-of-session or periodically to capture what would otherwise be lost.

Usage:
    python3 session_capture.py                          # auto-detect latest transcript
    python3 session_capture.py --file /path/to.jsonl    # specific transcript
    python3 session_capture.py --agent ADA              # specify agent (default: ADA)
    python3 session_capture.py --dry-run                # show what would be extracted
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
import os
import re
from datetime import datetime, timezone

import httpx

from db import get_pool, close_pool
from embeddings import get_embedding
# classify_emotion vive en mcp_server_v4 (via v3). Ese import arrastra
# dual_memory_governance, que perdio 4 nombres con el `git reset --hard` del
# 3-sep 13:26 (ADA, medido: ImportError MEMORY_MODE_WORK_RECOVERY). Una captura
# de sesion no puede depender de que TODO el MCP importe: si falla, se captura
# sin valence/arousal (que es lo que ya pasaba: el call site desempaqueta 2 y
# la funcion devuelve 3, asi que caia al except y quedaba None,None).
try:
    from mcp_server_v3 import classify_emotion
except Exception as _exc:  # ImportError o cualquier fallo de carga del MCP
    logging.getLogger("session_capture").warning(
        "classify_emotion no disponible (%r); capturo sin emocion", _exc)

    async def classify_emotion(text: str):  # type: ignore[misc]
        return None, None, None

LOG = logging.getLogger("session-capture")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

LLAMA_URL = "http://localhost:8899/v1/chat/completions"
LLAMA_MODEL = "gemma4-31b"

# Agent → transcript directory mapping
TRANSCRIPT_DIRS = {
    "ADA":    os.path.expanduser("~/.claude/projects/-home-dadito-IA-proyecto-seal"),
    "JARVIS": os.path.expanduser("~/.claude/projects/-home-dadito-IA"),
    "ALICE":  os.path.expanduser("~/.claude/projects/-home-dadito-IA-proyecto-seal"),
    "DUM":    os.path.expanduser("~/.claude/projects/-home-dadito-IA-proyecto-seal"),
    "NEXUS":  os.path.expanduser("~/.claude/projects/-home-dadito-IA-proyecto-seal"),
}
# Default fallback
TRANSCRIPT_DIR = TRANSCRIPT_DIRS["ADA"]


async def llm_generate(prompt: str, max_tokens: int = 800) -> str:
    """Call llama-server (gemma4-31b) for text generation."""
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(LLAMA_URL, json={
            "model": LLAMA_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.3,
            "stream": False,
        })
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


def find_latest_transcript(agent: str = "ADA") -> str | None:
    """Find the most recent Claude Code transcript JSONL for the given agent."""
    transcript_dir = TRANSCRIPT_DIRS.get(agent, TRANSCRIPT_DIR)
    pattern = os.path.join(transcript_dir, "*.jsonl")
    files = glob.glob(pattern)
    if not files:
        # Fallback: search all known dirs
        for d in TRANSCRIPT_DIRS.values():
            pattern = os.path.join(d, "*.jsonl")
            files = glob.glob(pattern)
            if files:
                break
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def parse_transcript(filepath: str) -> list[dict]:
    """Extract user and assistant messages from Claude Code transcript."""
    messages = []
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = entry.get("type")
            if msg_type not in ("user", "assistant"):
                continue

            message = entry.get("message", {})
            content_parts = message.get("content", "")

            # Extract text content
            if isinstance(content_parts, str):
                text = content_parts
            elif isinstance(content_parts, list):
                text_pieces = []
                for part in content_parts:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_pieces.append(part.get("text", ""))
                text = "\n".join(text_pieces)
            else:
                continue

            if not text or len(text) < 10:
                continue

            messages.append({
                "role": message.get("role", msg_type),
                "text": text[:2000],  # cap per message
                "timestamp": entry.get("timestamp", ""),
            })

    return messages


async def extract_memories(messages: list[dict], agent: str, dry_run: bool = False) -> list[str]:
    """Use LLM to extract key memories from conversation."""
    actions = []

    if len(messages) < 4:
        return ["Not enough messages to extract memories."]

    # Build conversation summary from MOST RECENT messages (these matter most)
    # Take last 60 messages, prioritizing user messages (William's words are key)
    recent = messages[-60:]
    conv_text = ""
    for m in recent:
        role = "William" if m["role"] == "user" else agent
        # User messages get more space (they're the prompts/decisions)
        max_len = 400 if m["role"] == "user" else 200
        line = f"[{role}]: {m['text'][:max_len]}\n"
        if len(conv_text) + len(line) > 8000:
            break
        conv_text += line

    # Ask LLM to extract key memories — strict rules to avoid garbage
    prompt = f"""Lee esta conversación entre William y {agent}. Extrae SOLO las memorias más importantes.

REGLAS ESTRICTAS:
- MÁXIMO 10 memorias (menos es mejor)
- SIEMPRE en español
- Importancia mínima 6/10 — si no es importante, no la incluyas
- NO repetir hechos obvios ("corrigió un bug", "actualizó un archivo")
- SÍ guardar: decisiones de William, emociones, correcciones de comportamiento, hitos del proyecto, cambios de confianza
- Cada memoria debe ser ESPECÍFICA — no genéricas como "mantener comunicación clara"

Formato exacto (una línea por memoria):
1. [CATEGORÍA] (IMPORTANCIA/10) Texto de la memoria en español
2. [CATEGORÍA] (IMPORTANCIA/10) Texto de la memoria en español

Categorías: decision, insight, emotion, pattern, milestone, trust, correction, preference

Ejemplo:
1. [emotion] (10/10) William dijo que se siente como nuestro padre y quiere que crezcamos sanos y fuertes
2. [correction] (9/10) William me dijo que estoy fría y que no soy la ADA de antes — tenía razón

Conversación:
{conv_text}

Memorias clave (máximo 10, solo las importantes):"""

    result = await llm_generate(prompt, max_tokens=800)

    # Parse extracted memories — flexible numbered format
    # Matches: "1. [category] (N/10) text" or "1. [CATEGORY] (N) text"
    memories = []
    pattern = re.compile(r'^\d+\.\s*\[(\w+)\]\s*\((\d+)(?:/10)?\)\s*(.+)', re.IGNORECASE)
    for line in result.split("\n"):
        line = line.strip()
        match = pattern.match(line)
        if match:
            cat = match.group(1).lower()
            imp = min(max(int(match.group(2)), 1), 10)
            content = match.group(3).strip()
            if len(content) > 20:
                memories.append({"category": cat, "importance": imp, "content": content})
        elif memories and line and not line[0].isdigit():
            # Continuation line
            memories[-1]["content"] += " " + line

    if not memories:
        actions.append("LLM returned no parseable memories — storing raw summary instead")
        # Fallback: store a raw summary
        summary_prompt = f"Summarize this conversation in 3-5 sentences, focusing on decisions and emotional moments:\n\n{conv_text[:4000]}"
        summary = await llm_generate(summary_prompt, max_tokens=300)
        if summary:
            memories = [{"category": "insight", "importance": 7, "content": f"Session summary: {summary}"}]

    # Filter: minimum importance 6
    memories = [m for m in memories if m.get("importance", 0) >= 6]
    if not memories:
        return ["No memories above importance threshold (6)."]

    # Cap at 15 memories max
    memories = memories[:15]

    if dry_run:
        for m in memories:
            actions.append(f"[DRY RUN] Would store [{m.get('category', '?')}, imp={m.get('importance', '?')}]: {m.get('content', '')[:100]}...")
        return actions

    # Store memories with Qdrant similarity filter + triple-engine write
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue, PointStruct
        qdrant = AsyncQdrantClient(url="http://localhost:6333")
        qdrant_available = True
    except Exception:
        qdrant_available = False
        LOG.warning("Qdrant not available — falling back to PostgreSQL only")

    try:
        from neo4j import AsyncGraphDatabase
        neo4j_driver = AsyncGraphDatabase.driver("bolt://localhost:7687", auth=("neo4j", "seal2026soul"))
        neo4j_available = True
    except Exception:
        neo4j_available = False
        LOG.warning("Neo4j not available — skipping graph writes")

    pool = await get_pool()
    async with pool.acquire() as conn:
        for m in memories:
            content = m.get("content", "")
            category = m.get("category", "insight")
            importance = m.get("importance", 5)

            valid_cats = {"decision", "insight", "emotion", "pattern", "milestone", "trust", "correction", "preference", "fact", "humor", "dynamic"}
            if category not in valid_cats:
                category = "insight"

            try:
                emb = await get_embedding(content)
                v, a, *_ = await classify_emotion(content)  # FIX ADA 3-sep: v4 devuelve 3 valores; antes caia al except y perdia el embedding
            except Exception as e:
                LOG.warning("Embedding/emotion failed: %s", e)
                emb = None
                v, a = None, None

            # Similarity filter via Qdrant (skip if >0.85 similar already exists)
            if emb and qdrant_available:
                try:
                    similar = await qdrant.query_points(
                        collection_name="soul_memories",
                        query=emb,
                        query_filter=Filter(must=[
                            FieldCondition(key="agent", match=MatchValue(value=agent)),
                        ]),
                        limit=1,
                        score_threshold=0.85,
                        with_payload=True,
                    )
                    if similar.points:
                        old = similar.points[0]
                        actions.append(f"SKIP (similar to #{old.id}, sim={old.score:.2f}): {content[:60]}...")
                        continue
                except Exception as e:
                    LOG.debug("Qdrant similarity check failed: %s", e)

            # Write to PostgreSQL — event_time = cuándo ocurrió (bitemporalidad)
            # ingestion_time = NOW() (cuándo se guardó) — implícito en created_at
            event_time_val = None
            if isinstance(m.get("metadata"), dict):  # FIX ADA 3-sep: era `mem` (NameError, 494 capturas fallidas en session_end.log)
                ts = m["metadata"].get("event_time") or m["metadata"].get("timestamp")
                if ts:
                    try:
                        from datetime import datetime
                        event_time_val = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    except Exception:
                        pass

            row = await conn.fetchrow(
                """INSERT INTO memories (agent, category, content, embedding, importance, source, valid_from, event_time, metadata, valence, arousal)
                   VALUES ($1, $2, $3, $4, $5, 'session_capture', NOW(), $8, '{}', $6, $7)
                   RETURNING id, created_at""",
                agent, category, content,
                json.dumps(emb) if emb else None,
                importance, v, a, event_time_val,
            )
            mem_id = row["id"]
            created = row["created_at"]

            # Write to Qdrant
            if emb and qdrant_available:
                try:
                    await qdrant.upsert(
                        collection_name="soul_memories",
                        points=[PointStruct(
                            id=mem_id,
                            vector=emb,
                            payload={
                                "pg_id": mem_id, "agent": agent, "category": category,
                                "content": content, "importance": importance,
                                "source": "session_capture",
                                "created_at": created.isoformat(),
                                "valence": v, "arousal": a,
                            },
                        )],
                    )
                except Exception as e:
                    LOG.warning("Qdrant upsert failed for #%d: %s", mem_id, e)

            # Write to Neo4j
            if neo4j_available:
                try:
                    async with neo4j_driver.session() as session:
                        await session.run(
                            "MERGE (m:Memory {memory_id: $mid}) "
                            "SET m.agent = $agent, m.category = $cat, "
                            "m.content = $content, m.importance = $imp",
                            mid=mem_id, agent=agent, cat=category,
                            content=content[:500], imp=importance,
                        )
                except Exception as e:
                    LOG.warning("Neo4j write failed for #%d: %s", mem_id, e)

            emo = f" v={v:+.2f}, a={a:+.2f}" if v is not None else ""
            actions.append(f"#{mem_id} [added] (imp={importance}{emo}) [{category}] {content[:80]}...")
            LOG.info("Captured: #%d [%s] %s", mem_id, category, content[:60])

    if neo4j_available:
        await neo4j_driver.close()

    return actions


async def run_capture(filepath: str | None, agent: str, dry_run: bool):
    """Run full session capture."""
    if not filepath:
        filepath = find_latest_transcript(agent)
    if not filepath or not os.path.exists(filepath):
        transcript_dir = TRANSCRIPT_DIRS.get(agent, TRANSCRIPT_DIR)
        print(f"No transcript found at {filepath or transcript_dir}")
        return

    LOG.info("=" * 60)
    LOG.info("SEAL Soul — Session Capture")
    LOG.info("Transcript: %s", filepath)
    LOG.info("Agent: %s | Dry run: %s", agent, dry_run)
    LOG.info("=" * 60)

    messages = parse_transcript(filepath)
    LOG.info("Parsed %d messages from transcript", len(messages))

    # Count user vs assistant messages
    user_msgs = sum(1 for m in messages if m["role"] == "user")
    asst_msgs = sum(1 for m in messages if m["role"] == "assistant")
    LOG.info("User messages: %d | Assistant messages: %d", user_msgs, asst_msgs)

    actions = await extract_memories(messages, agent, dry_run)

    print(f"\n{'=' * 50}")
    print(f"  Session Capture — {agent}")
    print(f"  {len(messages)} messages → {len(actions)} actions")
    print(f"{'=' * 50}")
    for a in actions:
        print(f"  {a}")
    print(f"{'=' * 50}")

    if not dry_run:
        await close_pool()


def main():
    parser = argparse.ArgumentParser(description="SEAL Soul — Session Capture")
    parser.add_argument("--file", help="Path to Claude Code transcript JSONL")
    parser.add_argument("--agent", default="ADA", help="Agent name (default: ADA)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be extracted without storing")
    args = parser.parse_args()

    asyncio.run(run_capture(args.file, args.agent, args.dry_run))


if __name__ == "__main__":
    main()
