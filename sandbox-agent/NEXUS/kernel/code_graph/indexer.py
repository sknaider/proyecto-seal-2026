"""Cathedral II — file walker + tree-sitter chunker + edge builder.

Walks a source directory, parses each supported code file with tree-sitter,
extracts symbol-level chunks and call edges, and stores them in soul-memory-db.
"""

from __future__ import annotations
import asyncio
import hashlib
import os
import re
from pathlib import Path
from typing import Optional

import asyncpg

from .edge_extractor import extract_call_edges, find_chunk_for_byte

SUPPORTED = {
    ".py":   "python",
    ".js":   "javascript",
    ".ts":   "typescript",
    ".tsx":  "tsx",
    ".go":   "go",
    ".rs":   "rust",
    ".java": "java",
}

SYMBOL_NODE_TYPES: dict[str, set[str]] = {
    "python":     {"function_definition", "class_definition", "decorated_definition"},
    "javascript": {"function_declaration", "class_declaration", "arrow_function", "method_definition"},
    "typescript": {"function_declaration", "class_declaration", "method_definition", "interface_declaration"},
    "tsx":        {"function_declaration", "class_declaration", "method_definition"},
    "go":         {"function_declaration", "method_declaration"},
    "rust":       {"function_item", "impl_item", "struct_item"},
    "java":       {"class_declaration", "method_declaration", "interface_declaration"},
}

SYMBOL_NAME_FIELDS = {
    "python":     "name",
    "javascript": "name",
    "typescript": "name",
    "tsx":        "name",
    "go":         "name",
    "rust":       "name",
    "java":       "name",
}


def _get_parser(language: str):
    """Lazy-load tree-sitter parser for the given language."""
    if language == "python":
        import tree_sitter_python as m
    elif language in ("javascript",):
        import tree_sitter_javascript as m
    elif language in ("typescript", "tsx"):
        import tree_sitter_typescript as m
        if language == "tsx":
            from tree_sitter import Language, Parser
            return Parser(Language(m.language_tsx()))
    elif language == "go":
        import tree_sitter_go as m
    elif language == "rust":
        import tree_sitter_rust as m
    elif language == "java":
        import tree_sitter_java as m
    else:
        return None
    from tree_sitter import Language, Parser
    return Parser(Language(m.language()))


def _extract_doc_comment(node) -> Optional[str]:
    """Extract leading string literal or comment as doc comment."""
    if node.type == "function_definition":
        body = node.child_by_field_name("body")
        if body and body.children:
            first = body.children[0]
            if first.type == "expression_statement" and first.children:
                inner = first.children[0]
                if inner.type in ("string", "string_literal"):
                    return inner.text.decode("utf-8", errors="replace").strip("\"' \t\n")
    return None


def _extract_symbols(root_node, source: bytes, language: str,
                     file_path: str) -> list[dict]:
    """Walk AST and extract symbol-level chunks with metadata."""
    symbol_types = SYMBOL_NODE_TYPES.get(language, set())
    name_field = SYMBOL_NAME_FIELDS.get(language, "name")
    chunks = []
    idx = 0

    stack = [(root_node, [])]
    while stack:
        node, parent_path = stack.pop()
        if node.type in symbol_types:
            name_node = node.child_by_field_name(name_field)
            sym_name = None
            if name_node:
                sym_name = name_node.text.decode("utf-8", errors="replace")

            text = source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            doc = _extract_doc_comment(node)

            qualified = ".".join(parent_path + [sym_name]) if sym_name else None

            chunks.append({
                "chunk_index":          idx,
                "chunk_text":           text,
                "language":             language,
                "symbol_name":          sym_name,
                "symbol_type":          node.type,
                "symbol_name_qualified": qualified,
                "parent_symbol_path":   parent_path[:],
                "doc_comment":          doc,
                "start_line":           start_line,
                "end_line":             end_line,
                "start_byte":           node.start_byte,
                "end_byte":             node.end_byte,
            })
            idx += 1

            next_parent = parent_path + ([sym_name] if sym_name else [])
            for child in reversed(node.named_children):
                stack.append((child, next_parent))
        else:
            for child in reversed(node.named_children):
                stack.append((child, parent_path))

    if not chunks:
        text = source.decode("utf-8", errors="replace")
        chunks.append({
            "chunk_index":           0,
            "chunk_text":            text[:8000],
            "language":              language,
            "symbol_name":           None,
            "symbol_type":           "module",
            "symbol_name_qualified": None,
            "parent_symbol_path":    [],
            "doc_comment":           None,
            "start_line":            1,
            "end_line":              text.count("\n") + 1,
            "start_byte":            0,
            "end_byte":              len(source),
        })

    return chunks


async def index_file(conn: asyncpg.Connection, source_id: int,
                     file_path: str, root_path: str) -> int:
    """Parse one file and upsert chunks + edges. Returns chunk count."""
    root = Path(root_path).expanduser().resolve(strict=True)
    abs_path = (root / file_path).resolve(strict=True)
    try:
        abs_path.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"source file escapes repository root: {file_path}") from exc
    ext = Path(file_path).suffix.lower()
    language = SUPPORTED.get(ext)
    if language is None:
        return 0

    source = abs_path.read_bytes()
    content_hash = hashlib.sha256(source).hexdigest()

    existing = await conn.fetchrow(
        "SELECT id, content_hash FROM cgraph_pages WHERE source_id=$1 AND file_path=$2",
        source_id, file_path,
    )
    if existing and existing["content_hash"] == content_hash:
        return 0

    parser = _get_parser(language)
    if parser is None:
        return 0
    tree = parser.parse(source)

    chunks = _extract_symbols(tree.root_node, source, language, file_path)
    edges_raw = extract_call_edges(tree.root_node, language)

    if existing:
        page_id = existing["id"]
        await conn.execute(
            "UPDATE cgraph_pages SET content_hash=$1, indexed_at=now() WHERE id=$2",
            content_hash, page_id,
        )
        await conn.execute("DELETE FROM cgraph_chunks WHERE page_id=$1", page_id)
    else:
        page_id = await conn.fetchval(
            """
            INSERT INTO cgraph_pages (source_id, file_path, language, content_hash)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (source_id, file_path)
            DO UPDATE SET content_hash=EXCLUDED.content_hash, indexed_at=now()
            RETURNING id
            """,
            source_id, file_path, language, content_hash,
        )

    chunk_ids: list[int] = []
    for c in chunks:
        cid = await conn.fetchval(
            """
            INSERT INTO cgraph_chunks
              (page_id, chunk_index, chunk_text, language, symbol_name, symbol_type,
               symbol_name_qualified, parent_symbol_path, doc_comment, start_line, end_line)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            RETURNING id
            """,
            page_id, c["chunk_index"], c["chunk_text"], c["language"],
            c["symbol_name"], c["symbol_type"], c["symbol_name_qualified"],
            c["parent_symbol_path"], c["doc_comment"],
            c["start_line"], c["end_line"],
        )
        chunk_ids.append(cid)

    # Map call edges to chunks via byte offset
    for edge in edges_raw:
        ci = find_chunk_for_byte(edge.call_site_start_byte, source, chunks)
        if ci is None:
            continue
        from_chunk_id = chunk_ids[ci]
        from_sym = chunks[ci].get("symbol_name_qualified") or chunks[ci].get("symbol_name") or ""

        await conn.execute(
            """
            INSERT INTO cgraph_edges_symbol (from_chunk_id, from_symbol, to_symbol, edge_type)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (from_chunk_id, to_symbol, edge_type) DO NOTHING
            """,
            from_chunk_id, from_sym, edge.to_symbol, edge.edge_type,
        )

    return len(chunks)


async def index_source(dsn: str, root_path: str, name: str,
                       extensions: Optional[set[str]] = None,
                       skip_dirs: Optional[set[str]] = None) -> dict:
    """Compatibility wrapper for trusted CLI callers with an explicit DSN."""
    conn = await asyncpg.connect(dsn)
    try:
        return await index_source_with_connection(
            conn,
            root_path,
            name,
            extensions=extensions,
            skip_dirs=skip_dirs,
        )
    finally:
        await conn.close()


async def index_source_with_connection(
    conn: asyncpg.Connection,
    root_path: str,
    name: str,
    extensions: Optional[set[str]] = None,
    skip_dirs: Optional[set[str]] = None,
) -> dict:
    """Index a repository using a caller-supplied scoped DB connection.

    The MCP route uses this entrypoint so credentials never enter argv, a
    temporary file, subprocess environment, or a second unscoped connection.
    """
    skip_dirs = skip_dirs or {"__pycache__", ".git", "node_modules", ".venv",
                              "venv", "dist", "build", ".mypy_cache"}
    extensions = extensions or set(SUPPORTED.keys())
    root = Path(root_path).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"repository root is not a directory: {root}")

    source_id = await conn.fetchval(
            """
            INSERT INTO cgraph_sources (root_path, name)
            VALUES ($1, $2)
            ON CONFLICT (root_path) DO UPDATE SET name=EXCLUDED.name
            RETURNING id
            """,
            str(root), name,
        )

    files = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fn in filenames:
            ext = Path(fn).suffix.lower()
            if ext in extensions:
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                files.append(rel)

    total_chunks = 0
    indexed_files = 0
    skipped_files = 0
    for fp in files:
        try:
            n = await index_file(conn, source_id, fp, str(root))
            if n > 0:
                indexed_files += 1
                total_chunks += n
        except (OSError, PermissionError, ValueError):
            skipped_files += 1

    edges_resolved = await resolve_edges(conn, source_id)

    return {
        "source_id": source_id,
        "files_scanned": len(files),
        "files_indexed": indexed_files,
        "files_skipped": skipped_files,
        "chunks_created": total_chunks,
        "edges_resolved": edges_resolved,
    }


async def resolve_edges(conn: asyncpg.Connection, source_id: int) -> int:
    """Resolve cgraph_edges_symbol → cgraph_edges_chunk within source_id.

    Looks up each unresolved edge's to_symbol in cgraph_chunks for the same
    source and inserts into cgraph_edges_chunk. Returns count of new edges.
    """
    rows = await conn.fetch(
        """
        SELECT es.id, es.from_chunk_id, es.from_symbol, es.to_symbol, es.edge_type
        FROM cgraph_edges_symbol es
        JOIN cgraph_chunks fc ON fc.id = es.from_chunk_id
        JOIN cgraph_pages fp ON fp.id = fc.page_id
        WHERE fp.source_id = $1
        """,
        source_id,
    )

    count = 0
    for row in rows:
        targets = await conn.fetch(
            """
            SELECT cc.id
            FROM cgraph_chunks cc
            JOIN cgraph_pages p ON p.id = cc.page_id
            WHERE p.source_id = $1
              AND (cc.symbol_name = $2 OR cc.symbol_name_qualified = $2
                   OR cc.symbol_name_qualified LIKE '%.' || $2)
            LIMIT 5
            """,
            source_id, row["to_symbol"],
        )
        for t in targets:
            try:
                await conn.execute(
                    """
                    INSERT INTO cgraph_edges_chunk
                      (from_chunk_id, to_chunk_id, from_symbol, to_symbol, edge_type)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (from_chunk_id, to_chunk_id, edge_type) DO NOTHING
                    """,
                    row["from_chunk_id"], t["id"],
                    row["from_symbol"], row["to_symbol"], row["edge_type"],
                )
                count += 1
            except Exception:
                pass

    return count
