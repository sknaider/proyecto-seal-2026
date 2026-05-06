"""NEXUS Cathedral II — code graph module.

Tree-sitter AST parsing + call edge extraction + two-pass structural
retrieval + RRF hybrid search. SOUL native, zero external AI frameworks.

Usage:
    from kernel.code_graph import index_source, hybrid_search, apply_schema

    # One-time schema setup
    await apply_schema(dsn)

    # Index a codebase
    result = await index_source(dsn, "/path/to/repo", "my-repo")

    # Search with structural expansion
    conn = await asyncpg.connect(dsn)
    results = await hybrid_search(conn, "authentication", walk_depth=1)
"""

from .indexer import index_source, index_file, resolve_edges
from .hybrid_search import hybrid_search, rrf_fusion, keyword_search, vector_search
from .two_pass import expand_anchors, hydrate_chunks, ChunkScore
from .edge_extractor import extract_call_edges, find_chunk_for_byte, ExtractedEdge

import asyncpg
from pathlib import Path


async def apply_schema(dsn: str) -> None:
    schema_sql = (Path(__file__).parent / "schema.sql").read_text()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(schema_sql)
    finally:
        await conn.close()


__all__ = [
    "apply_schema",
    "index_source",
    "index_file",
    "resolve_edges",
    "hybrid_search",
    "rrf_fusion",
    "keyword_search",
    "vector_search",
    "expand_anchors",
    "hydrate_chunks",
    "ChunkScore",
    "extract_call_edges",
    "find_chunk_for_byte",
    "ExtractedEdge",
]
