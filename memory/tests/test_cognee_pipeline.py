"""Unit tests — SEAL Cognee Ingestion Pipeline (spec_fase3_cognee_pipeline).

DoD #5: ≥5 tests, all PASS.
Tests are isolated: no file I/O side effects outside temp files.
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure memory/ is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

from cognee_ingest import chunk_hash, chunk_text


# ── Test 1: chunk_text respects MAX_CHARS boundary ───────────────────────────

def test_chunk_text_max_size():
    """Chunks must never exceed MAX_CHARS characters."""
    from cognee_ingest import MAX_CHARS
    long_para = "A" * (MAX_CHARS * 3)
    chunks = chunk_text(long_para)
    assert len(chunks) > 1, "Long paragraph should produce multiple chunks"
    for c in chunks:
        assert len(c) <= MAX_CHARS, f"Chunk exceeds MAX_CHARS: {len(c)}"


# ── Test 2: chunk_text respects MIN_CHUNK_CHARS filter ───────────────────────

def test_chunk_text_min_filter():
    """Tiny paragraphs under MIN_CHUNK_CHARS must be dropped."""
    from cognee_ingest import MIN_CHUNK_CHARS
    short_para = "X" * (MIN_CHUNK_CHARS - 1)
    normal_para = "N" * MIN_CHUNK_CHARS  # exactly at threshold — must survive
    text = f"{short_para}\n\n{normal_para}"
    chunks = chunk_text(text)
    assert any(normal_para in c for c in chunks), "Normal paragraph must appear"
    assert not any(c == short_para for c in chunks), "Short paragraph must be filtered"


# ── Test 3: chunk_hash produces deterministic 16-char hex ────────────────────

def test_chunk_hash_deterministic():
    """Same text must always produce the same hash, different text different hash."""
    text_a = "The quick brown fox jumps over the lazy dog."
    text_b = "Pack my box with five dozen liquor jugs."
    h1 = chunk_hash(text_a)
    h2 = chunk_hash(text_a)
    h3 = chunk_hash(text_b)
    assert h1 == h2, "Hash must be deterministic"
    assert h1 != h3, "Different texts must produce different hashes"
    assert len(h1) == 16, "Hash must be 16 chars"
    int(h1, 16)  # Must be valid hex


# ── Test 4: read_document auto-detects Markdown ──────────────────────────────

def test_read_document_markdown():
    """read_document on a .md file must return [(1, text)] with full content."""
    from cognee_ingest import read_document
    content = "# Title\n\nSome paragraph.\n\n## Section\n\nMore content."
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w",
                                     encoding="utf-8", delete=False) as f:
        f.write(content)
        tmp_path = Path(f.name)
    try:
        pages = read_document(tmp_path)
        assert len(pages) == 1, "Markdown should return exactly 1 page"
        assert pages[0][0] == 1, "Page number must be 1"
        assert "Title" in pages[0][1], "Content must be preserved"
    finally:
        tmp_path.unlink()


# ── Test 5: read_document auto-detects TXT ───────────────────────────────────

def test_read_document_txt():
    """read_document on a .txt file must return [(1, text)]."""
    from cognee_ingest import read_document
    content = "Plain text content for testing the TXT reader path."
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w",
                                     encoding="utf-8", delete=False) as f:
        f.write(content)
        tmp_path = Path(f.name)
    try:
        pages = read_document(tmp_path)
        assert len(pages) == 1
        assert content in pages[0][1]
    finally:
        tmp_path.unlink()


# ── Test 6: dedup_check returns True for existing hash ───────────────────────

@pytest.mark.asyncio
async def test_dedup_check_returns_true_for_existing():
    """dedup_check must return True when the chunk_hash already exists in DB."""
    from cognee_ingest import dedup_check

    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=99)  # simulate existing row

    result = await dedup_check(mock_conn, "JARVIS", "deadbeef12345678")
    assert result is True


# ── Test 7: dedup_check returns False for new hash ───────────────────────────

@pytest.mark.asyncio
async def test_dedup_check_returns_false_for_new():
    """dedup_check must return False when the chunk_hash does not exist."""
    from cognee_ingest import dedup_check

    mock_conn = AsyncMock()
    mock_conn.fetchval = AsyncMock(return_value=None)  # simulate no row

    result = await dedup_check(mock_conn, "JARVIS", "newhashabcdef123")
    assert result is False


# ── Test 8: chunk_text paragraph splitting ───────────────────────────────────

def test_chunk_text_paragraph_splitting():
    """Separate paragraphs must produce separate chunks."""
    from cognee_ingest import MIN_CHUNK_CHARS
    para_a = "A" * MIN_CHUNK_CHARS
    para_b = "B" * MIN_CHUNK_CHARS
    text = f"{para_a}\n\n{para_b}"
    chunks = chunk_text(text)
    assert len(chunks) == 2, f"Expected 2 chunks, got {len(chunks)}"
    assert para_a in chunks
    assert para_b in chunks


if __name__ == "__main__":
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v"],
        capture_output=False
    )
    sys.exit(result.returncode)
