#!/usr/bin/env python3
"""SEAL Memory Extractor Agent — H2.5

Extrae memorias del transcript de Claude Code periódicamente.
Diseñado para correr como cron cada 20 min — complementario a
la extracción built-in de Claude Code.

Principio clave: SIN MUTEX. Claude Code no libera locks para nosotros.
  - No file locks, no mutex checks
  - Watermark JSON en /tmp para saber dónde quedamos
  - Qdrant similarity (0.85) deduplicará lo que Claude ya extrajo

Uso (cron o manual):
    python3 memory_extractor_agent.py ADA
    python3 memory_extractor_agent.py JARVIS
    python3 memory_extractor_agent.py --dry-run ADA

Fallback LLM:
    Primary: gemma4-31b @ localhost:8899
    Fallback: qwen2.5:7b @ localhost:11434/api/chat (Ollama)

Owner: ADA (H2.5) | Ahorro proyectado: $8-12/mes (memorias más frecuentes → menos repeats)
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

LOG = logging.getLogger("memory-extractor")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

# ── LLM endpoints ──
LLM_PRIMARY_URL = "http://localhost:8899/v1/chat/completions"
LLM_PRIMARY_MODEL = "gemma4-31b"
LLM_OLLAMA_URL = "http://localhost:11434/api/chat"
LLM_OLLAMA_MODEL = "qwen2.5:7b"

# ── DB ──
DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"

# ── Transcript dirs per agent ──
TRANSCRIPT_DIRS: dict[str, str] = {
    "ADA":    os.path.expanduser("~/.claude/projects/-home-dadito-IA-proyecto-seal"),
    "JARVIS": os.path.expanduser("~/.claude/projects/-home-dadito-IA"),
    "ALICE":  os.path.expanduser("~/.claude/projects/-home-dadito-IA-proyecto-seal"),
}

# ── Watermark dir ──
WATERMARK_DIR = Path("/tmp")

# Minimum new messages before triggering extraction — avoids tiny batches
MIN_NEW_MESSAGES = 8

# Importance threshold — don't store noise
MIN_IMPORTANCE = 6


def watermark_path(agent: str, transcript_path: str) -> Path:
    """Per-agent, per-file watermark so changing transcript resets cursor."""
    # Use last 20 chars of path as suffix to avoid collisions
    suffix = transcript_path.replace("/", "_")[-20:]
    return WATERMARK_DIR / f".seal_memextract_{agent}_{suffix}.json"


def load_watermark(agent: str, transcript_path: str) -> int:
    """Returns last processed line number (0 = unprocessed)."""
    wpath = watermark_path(agent, transcript_path)
    try:
        data = json.loads(wpath.read_text())
        if data.get("file") == transcript_path:
            return int(data.get("line", 0))
    except Exception:
        pass
    return 0


def save_watermark(agent: str, transcript_path: str, line: int) -> None:
    wpath = watermark_path(agent, transcript_path)
    try:
        wpath.write_text(json.dumps({"file": transcript_path, "line": line, "ts": time.time()}))
    except Exception as e:
        LOG.warning("Could not save watermark: %s", e)


def find_latest_transcript(agent: str) -> str | None:
    """Find most recent Claude Code transcript JSONL for agent."""
    transcript_dir = TRANSCRIPT_DIRS.get(agent)
    if not transcript_dir:
        return None
    files = glob.glob(os.path.join(transcript_dir, "*.jsonl"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def read_new_messages(filepath: str, from_line: int) -> tuple[list[dict], int]:
    """Read messages from transcript starting at from_line.
    Returns (messages, last_line_read).
    """
    messages = []
    last_line = from_line
    try:
        with open(filepath, "r") as f:
            for i, raw in enumerate(f):
                if i < from_line:
                    continue
                last_line = i + 1
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                msg_type = entry.get("type")
                if msg_type not in ("user", "assistant"):
                    continue

                message = entry.get("message", {})
                content_parts = message.get("content", "")

                if isinstance(content_parts, str):
                    text = content_parts
                elif isinstance(content_parts, list):
                    text = "\n".join(
                        p.get("text", "") for p in content_parts
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                else:
                    continue

                if not text or len(text) < 10:
                    continue

                messages.append({
                    "role": message.get("role", msg_type),
                    "text": text[:2000],
                    "timestamp": entry.get("timestamp", ""),
                })
    except Exception as e:
        LOG.warning("Error reading transcript: %s", e)

    return messages, last_line


async def llm_generate(prompt: str, max_tokens: int = 600) -> str:
    """Try primary LLM first, fallback to Ollama."""
    # Try gemma4-31b
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(LLM_PRIMARY_URL, json={
                "model": LLM_PRIMARY_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.3,
                "stream": False,
            })
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        LOG.debug("Primary LLM unavailable (%s), trying Ollama fallback", e)

    # Fallback: Ollama qwen2.5:7b
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(LLM_OLLAMA_URL, json={
                "model": LLM_OLLAMA_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            })
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()
    except Exception as e:
        LOG.warning("Ollama fallback also failed: %s", e)
        return ""


async def extract_and_store(
    messages: list[dict],
    agent: str,
    dry_run: bool = False,
) -> list[str]:
    """Extract memories from messages and store them. Returns action log."""
    actions: list[str] = []

    # Build conversation text — last 40 messages, user gets more space
    conv_text = ""
    for m in messages[-40:]:
        role = "William" if m["role"] == "user" else agent
        max_len = 400 if m["role"] == "user" else 150
        line = f"[{role}]: {m['text'][:max_len]}\n"
        if len(conv_text) + len(line) > 6000:
            break
        conv_text += line

    prompt = f"""Lee esta conversación entre William y {agent}. Extrae SOLO las memorias más importantes.

REGLAS:
- MÁXIMO 8 memorias
- Importancia mínima {MIN_IMPORTANCE}/10 — si no es importante, no la incluyas
- SÍ guardar: decisiones de William, correcciones de comportamiento, hitos, cambios de confianza, emociones
- NO guardar: bugs arreglados, archivos modificados, pasos técnicos rutinarios
- Español siempre

Formato exacto (una línea por memoria):
1. [CATEGORÍA] (IMPORTANCIA/10) Texto

Categorías: decision, insight, emotion, pattern, milestone, trust, correction, preference

Conversación:
{conv_text}

Memorias (máximo 8):"""

    result = await llm_generate(prompt, max_tokens=600)
    if not result:
        actions.append("LLM unavailable — skipping extraction")
        return actions

    # Parse — handles both "[category] (N/10) text" and "category (N/10) text"
    memories: list[dict] = []
    pattern = re.compile(
        r'^\d+\.\s*\[?(\w+)\]?\s*[\(\[](\d+)(?:/10)?[\)\]]\s*(.+)',
        re.IGNORECASE,
    )
    for line in result.split("\n"):
        line = line.strip()
        match = pattern.match(line)
        if match:
            cat = match.group(1).lower()
            imp = min(max(int(match.group(2)), 1), 10)
            content = match.group(3).strip()
            if len(content) > 20:
                memories.append({"category": cat, "importance": imp, "content": content})

    memories = [m for m in memories if m["importance"] >= MIN_IMPORTANCE]
    if not memories:
        actions.append("No memories above threshold — nothing to store")
        return actions

    if dry_run:
        for m in memories:
            actions.append(f"[DRY RUN] [{m['category']}, imp={m['importance']}] {m['content'][:100]}")
        return actions

    # Store — Qdrant similarity dedup + PG write
    try:
        import asyncpg
    except ImportError:
        actions.append("asyncpg not available — cannot store")
        return actions

    qdrant_client = None
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.models import Filter, FieldCondition, MatchValue, PointStruct
        qdrant_client = AsyncQdrantClient(url="http://localhost:6333")
    except Exception:
        LOG.debug("Qdrant not available — PG-only writes")

    # Embedding via Ollama nomic-embed-text
    async def get_embedding(text: str) -> list | None:
        try:
            import urllib.request
            payload = json.dumps({
                "model": "nomic-embed-text",
                "input": text,
            }).encode()
            req = urllib.request.Request(
                "http://localhost:11434/api/embed",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                return data["embeddings"][0]
        except Exception:
            return None

    try:
        conn = await asyncio.wait_for(asyncpg.connect(DB_URL), timeout=5.0)
    except Exception as e:
        actions.append(f"DB connection failed: {e}")
        return actions

    try:
        valid_cats = {"decision", "insight", "emotion", "pattern", "milestone", "trust", "correction", "preference", "fact", "humor", "dynamic"}

        for m in memories:
            content = m["content"]
            category = m["category"] if m["category"] in valid_cats else "insight"
            importance = m["importance"]

            emb = await get_embedding(content)

            # Qdrant similarity check — skip if already stored (>0.85 sim)
            if emb and qdrant_client:
                try:
                    similar = await qdrant_client.query_points(
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
                        actions.append(f"SKIP (dup sim={old.score:.2f}): {content[:60]}…")
                        continue
                except Exception as e:
                    LOG.debug("Qdrant check failed: %s", e)

            # Write to PG
            try:
                row = await conn.fetchrow(
                    """INSERT INTO memories
                       (agent, category, content, embedding, importance, source, valid_from, metadata)
                       VALUES ($1, $2, $3, $4, $5, 'memory_extractor_agent', NOW(), '{}')
                       RETURNING id, created_at, COALESCE(confidence_score, 0.7) AS confidence_score""",
                    agent, category, content,
                    json.dumps(emb) if emb else None,
                    importance,
                )
                mem_id = row["id"]
                confidence = float(row["confidence_score"])

                # Write to Qdrant — include confidence to keep PG/Qdrant in sync
                if emb and qdrant_client:
                    try:
                        from qdrant_client.models import PointStruct
                        await qdrant_client.upsert(
                            collection_name="soul_memories",
                            points=[PointStruct(
                                id=mem_id,
                                vector=emb,
                                payload={
                                    "pg_id": mem_id, "agent": agent,
                                    "category": category, "content": content,
                                    "importance": importance,
                                    "confidence": confidence,
                                    "source": "memory_extractor_agent",
                                },
                            )],
                        )
                    except Exception as e:
                        LOG.debug("Qdrant upsert failed for #%d: %s", mem_id, e)

                actions.append(f"#{mem_id} [stored] (imp={importance}) [{category}] {content[:80]}…")
                LOG.info("Extracted: #%d [%s] %s", mem_id, category, content[:60])

            except Exception as e:
                actions.append(f"PG write failed: {e} — content: {content[:60]}")
                LOG.warning("PG write failed: %s", e)

    finally:
        await conn.close()
        if qdrant_client:
            await qdrant_client.close()

    return actions


async def run(agent: str, dry_run: bool = False) -> None:
    filepath = find_latest_transcript(agent)
    if not filepath:
        LOG.warning("No transcript found for %s", agent)
        return

    watermark = load_watermark(agent, filepath)
    new_messages, last_line = read_new_messages(filepath, watermark)

    LOG.info(
        "Agent=%s | transcript=%s | from_line=%d | new_messages=%d",
        agent, os.path.basename(filepath), watermark, len(new_messages),
    )

    if len(new_messages) < MIN_NEW_MESSAGES:
        LOG.info("Not enough new messages (%d < %d) — skipping", len(new_messages), MIN_NEW_MESSAGES)
        return

    actions = await extract_and_store(new_messages, agent, dry_run)

    print(f"\n{'='*50}")
    print(f"  Memory Extractor — {agent} | {len(new_messages)} msgs → {len(actions)} actions")
    print(f"{'='*50}")
    for a in actions:
        print(f"  {a}")
    print(f"{'='*50}\n")

    if not dry_run:
        save_watermark(agent, filepath, last_line)
        LOG.info("Watermark updated → line %d", last_line)


def main() -> None:
    parser = argparse.ArgumentParser(description="SEAL Memory Extractor Agent (H2.5)")
    parser.add_argument("agent", nargs="?", default="ADA",
                        help="Agent: ADA, JARVIS, ALICE (default: ADA)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be extracted without storing")
    args = parser.parse_args()

    asyncio.run(run(args.agent.upper(), args.dry_run))


if __name__ == "__main__":
    main()
