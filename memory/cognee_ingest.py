#!/usr/bin/env python3
"""SEAL Cognee-style Document Ingestion Pipeline — Fase 3 Sandbox.

PDF/MD/TXT → chunking → embedding → memory_store → connectome_build

Author: ADA — Fase 3 (2026-04-26)
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import asyncpg
from datetime import datetime, timezone

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
QDRANT_URL = "http://localhost:6333"
QDRANT_API_KEY = "79edecc663271e84a5a559c89038cd20276098101a68ad8995c20428d9a47560"
QDRANT_COLLECTION = "soul_memories"
LOG = logging.getLogger("cognee_ingest")

# Inject seal-spark venv so sentence_transformers is available
_SEAL_VENV = Path("/home/dadito/IA/seal-spark/.venv/lib/python3.12/site-packages")
if _SEAL_VENV.exists() and str(_SEAL_VENV) not in sys.path:
    sys.path.insert(0, str(_SEAL_VENV))

MAX_CHARS = 1600    # ~400 tokens (1 token ≈ 4 chars)
OVERLAP_CHARS = 320  # ~80 tokens overlap
MIN_CHUNK_CHARS = 80  # skip tiny chunks (headers, blank lines)


# ── Readers ──────────────────────────────────────────────────────────────────

def read_md(path: Path) -> list[tuple[int, str]]:
    """Returns [(page=1, full_text)]."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [(1, text)]


def read_txt(path: Path) -> list[tuple[int, str]]:
    return [(1, path.read_text(encoding="utf-8", errors="ignore"))]


def read_pdf(path: Path) -> list[tuple[int, str]]:
    """Returns [(page_num, page_text), ...]."""
    try:
        import pdfplumber
        pages = []
        with pdfplumber.open(str(path)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append((i, text))
        return pages
    except ImportError:
        LOG.warning("pdfplumber no disponible — tratando como TXT")
        return read_txt(path)


def read_document(path: Path) -> list[tuple[int, str]]:
    """Auto-detect format and read."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return read_pdf(path)
    elif suffix in (".md", ".markdown"):
        return read_md(path)
    else:
        return read_txt(path)


# ── Chunker ───────────────────────────────────────────────────────────────────

def chunk_text(text: str) -> list[str]:
    """Split text into semantic chunks. Respects paragraph boundaries first,
    then applies sliding window for oversized paragraphs."""
    chunks = []

    # Phase A: split by double newline (paragraphs)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    for para in paragraphs:
        if len(para) <= MAX_CHARS:
            if len(para) >= MIN_CHUNK_CHARS:
                chunks.append(para)
        else:
            # Phase B: sliding window for long paragraphs
            start = 0
            while start < len(para):
                end = start + MAX_CHARS
                chunk = para[start:end].strip()
                if len(chunk) >= MIN_CHUNK_CHARS:
                    chunks.append(chunk)
                start += MAX_CHARS - OVERLAP_CHARS
                if start >= len(para):
                    break

    return chunks


# ── Embedding ─────────────────────────────────────────────────────────────────

async def get_embedding(text: str) -> Optional[list[float]]:
    """Get embedding using embeddings.py (same directory)."""
    try:
        mem_path = str(Path(__file__).parent)
        if mem_path not in sys.path:
            sys.path.insert(0, mem_path)
        from embeddings import get_embedding as _embed
        return await _embed(text)
    except Exception as e:
        LOG.warning("embedding error: %s", e)
        return None


# ── Dedup ─────────────────────────────────────────────────────────────────────

def chunk_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def dedup_check(conn, agent: str, chash: str) -> bool:
    """Returns True if chunk already exists (skip)."""
    existing = await conn.fetchval(
        "SELECT id FROM soul_v3.memories WHERE agent=$1 "
        "AND metadata->>'chunk_hash' = $2 AND invalid_at IS NULL LIMIT 1",
        agent, chash
    )
    return existing is not None


# ── Store ─────────────────────────────────────────────────────────────────────

async def store_chunk(conn, agent: str, chunk: str, chash: str,
                      filename: str, page: int, idx: int,
                      importance: int, embedding: Optional[list[float]]) -> Optional[int]:
    """Insert chunk as a memory. Returns memory id or None if error."""
    import json as _json
    now = datetime.now(timezone.utc)
    meta = _json.dumps({
        "chunk_hash": chash, "file": filename,
        "page": page, "chunk_idx": idx,
        "pipeline": "cognee_pipeline",
    })

    embed_val = _json.dumps(embedding) if embedding else None

    try:
        row_id = await conn.fetchval("""
            INSERT INTO soul_v3.memories
              (agent, category, content, importance, confidence_score,
               source, valid_from, created_at, metadata, embedding)
            VALUES ($1, 'resource', $2, $3, 0.75, 'cognee_pipeline',
                    $4, $4, $5::jsonb, $6::vector)
            RETURNING id
        """,
            agent, chunk[:800], importance, now, meta, embed_val
        )
        return row_id
    except Exception as e:
        try:
            row_id = await conn.fetchval("""
                INSERT INTO soul_v3.memories
                  (agent, category, content, importance, confidence_score,
                   source, valid_from, created_at, metadata)
                VALUES ($1, 'resource', $2, $3, 0.75, 'cognee_pipeline',
                        $4, $4, $5::jsonb)
                RETURNING id
            """,
                agent, chunk[:800], importance, now, meta
            )
            LOG.warning("stored without embedding (vector error: %s)", e)
            return row_id
        except Exception as e2:
            LOG.error("store_chunk error: %s", e2)
            return None


# ── Qdrant ───────────────────────────────────────────────────────────────────

async def upsert_qdrant(memory_id: int, embedding: list[float],
                        agent: str, content: str, category: str) -> bool:
    """Upsert memory into Qdrant soul_memories collection."""
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.models import PointStruct
        client = AsyncQdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY)
        await client.upsert(
            collection_name=QDRANT_COLLECTION,
            points=[PointStruct(
                id=memory_id,
                vector=embedding,
                payload={"agent": agent, "content": content[:200],
                         "category": category, "source": "cognee_pipeline"}
            )]
        )
        await client.close()
        return True
    except Exception as e:
        LOG.warning("qdrant upsert error id=%d: %s", memory_id, e)
        return False


# ── Pipeline ──────────────────────────────────────────────────────────────────

async def ingest_document(path: Path, agent: str = "ADA",
                          importance: int = 5) -> dict:
    """Full pipeline: read → chunk → dedup → embed → store.
    Returns stats dict."""
    result = {
        "file": str(path),
        "pages_read": 0,
        "chunks_total": 0,
        "chunks_stored": 0,
        "chunks_skipped_dedup": 0,
        "memory_ids": [],
    }

    if not path.exists():
        LOG.error("file not found: %s", path)
        return result

    # 1. Read
    pages = read_document(path)
    result["pages_read"] = len(pages)
    LOG.info("read %d pages from %s", len(pages), path.name)

    # 2. Chunk all pages
    all_chunks: list[tuple[int, int, str]] = []  # (page, chunk_idx, text)
    for page_num, page_text in pages:
        chunks = chunk_text(page_text)
        for idx, chunk in enumerate(chunks):
            all_chunks.append((page_num, idx, chunk))

    result["chunks_total"] = len(all_chunks)
    LOG.info("generated %d chunks", len(all_chunks))

    # 3. Connect to DB
    conn = await asyncpg.connect(DB_URL)
    try:
        for page_num, idx, chunk in all_chunks:
            chash = chunk_hash(chunk)

            # Dedup check
            if await dedup_check(conn, agent, chash):
                result["chunks_skipped_dedup"] += 1
                continue

            # Embed
            embedding = await get_embedding(chunk)

            # Store
            mid = await store_chunk(
                conn, agent, chunk, chash,
                path.name, page_num, idx, importance, embedding
            )
            if mid:
                result["memory_ids"].append(mid)
                result["chunks_stored"] += 1
                LOG.info("stored chunk id=%d page=%d idx=%d", mid, page_num, idx)
                # Index to Qdrant for hybrid search
                if embedding:
                    await upsert_qdrant(mid, embedding, agent, chunk, "fact")

    finally:
        await conn.close()

    return result


async def ingest_directory(dir_path: Path, agent: str = "ADA",
                           importance: int = 5,
                           patterns: list[str] = None) -> dict:
    """Ingest all matching files in directory."""
    if patterns is None:
        patterns = ["**/*.pdf", "**/*.md", "**/*.txt"]

    files = []
    for pattern in patterns:
        files.extend(dir_path.glob(pattern))
    files = list(set(files))

    totals = {"files": len(files), "chunks_stored": 0,
              "chunks_skipped_dedup": 0, "chunks_total": 0}

    for f in files:
        r = await ingest_document(f, agent, importance)
        totals["chunks_stored"] += r["chunks_stored"]
        totals["chunks_skipped_dedup"] += r["chunks_skipped_dedup"]
        totals["chunks_total"] += r["chunks_total"]

    return totals


# ── CLI / Test ────────────────────────────────────────────────────────────────

def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="SEAL Cognee-style ingestion")
    parser.add_argument("path", help="File or directory to ingest")
    parser.add_argument("--agent", default="ADA", help="Target agent (default: ADA)")
    parser.add_argument("--importance", type=int, default=5, help="Importance 1-10")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show chunks without storing")
    args = parser.parse_args()

    target = Path(args.path)

    if args.dry_run:
        pages = read_document(target)
        all_chunks = []
        for pn, pt in pages:
            for i, c in enumerate(chunk_text(pt)):
                all_chunks.append((pn, i, c))
        print(f"=== DRY RUN — {target.name} ===")
        print(f"Pages: {len(pages)} | Chunks: {len(all_chunks)}")
        for pn, i, c in all_chunks[:5]:
            print(f"\n[page={pn} chunk={i}] {c[:120]}...")
        if len(all_chunks) > 5:
            print(f"... (+{len(all_chunks)-5} more chunks)")
        return True

    if target.is_dir():
        result = asyncio.run(ingest_directory(target, args.agent, args.importance))
    else:
        result = asyncio.run(ingest_document(target, args.agent, args.importance))

    print("\n=== INGESTION RESULT ===")
    for k, v in result.items():
        if k != "memory_ids":
            print(f"  {k}: {v}")
    if "memory_ids" in result:
        print(f"  memory_ids: [{result['memory_ids'][0] if result['memory_ids'] else '—'} ... {result['memory_ids'][-1] if len(result['memory_ids']) > 1 else ''}]")

    return result.get("chunks_stored", 0) > 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
