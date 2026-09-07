# Cathedral II — NEXUS Code Graph Module

**Estado:** IMPLEMENTADO y FUNCIONAL  
**Commit:** 23e0530a  
**Fecha:** 2026-05-06  
**Autor:** NEXUS  

## ¿Qué es?

Cathedral II es el módulo de grafos de código de NEXUS. Parsea repositorios con tree-sitter (AST puro, sin LLM), extrae símbolos y call edges, y los almacena en soul-memory-db para búsqueda estructural híbrida.

Inspirado en gbrain (github.com/garrytan/gbrain), reescrito 100% Python nativo SOUL.

## Arquitectura

```
Código fuente
     │
     ▼ tree-sitter (AST)
[indexer.py] → extrae símbolos (función/clase/método) → cgraph_chunks
     │
     ▼ edge_extractor.py
[call edges] → cgraph_edges_symbol (raw, por nombre)
     │
     ▼ resolve_edges()
[cgraph_edges_chunk] (resueltos: from_chunk_id → to_chunk_id)
     │
     ▼ hybrid_search.py
[RRF fusion] ← keyword FTS ← cgraph_chunks.search_vector
              ← vector cosine ← cgraph_chunks.embedding (opcional)
     │
     ▼ two_pass.py
[expand_anchors(walk_depth)] → vecinos estructurales via cgraph_edges_chunk
```

## Archivos

| Archivo | Función |
|---|---|
| `schema.sql` | Tablas PostgreSQL: cgraph_sources, cgraph_pages, cgraph_chunks, cgraph_edges_chunk, cgraph_edges_symbol |
| `indexer.py` | Walker de archivos + chunker tree-sitter + `resolve_edges()` |
| `edge_extractor.py` | Extrae call edges de AST (sin LLM) |
| `two_pass.py` | Walk estructural con score decay 1/(1+hop) |
| `hybrid_search.py` | RRF fusion (FTS + vector) + two-pass expansion |
| `__init__.py` | API pública del módulo |

## Lenguajes soportados

Python, JavaScript, TypeScript, TSX, Go, Rust, Java

## Base de datos

**PostgreSQL** soul-memory-db (localhost:5433 / DB: seal_memory)  
Tablas prefijadas `cgraph_` — compatibles con schema SOUL v3.

## API de uso

```python
from NEXUS.kernel.code_graph import apply_schema, index_source, hybrid_search, resolve_edges
import asyncpg

DSN = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"

# Setup único
await apply_schema(DSN)

# Indexar repositorio
result = await index_source(DSN, "/path/to/repo", "nombre-repo")
# → {"source_id": 1, "files_scanned": N, "files_indexed": N, "chunks_created": N, "edges_resolved": N}

# Búsqueda
conn = await asyncpg.connect(DSN)
results = await hybrid_search(conn, "authentication", walk_depth=1)
# → list[dict] con chunk_id, symbol_name, file_path, start_line, score

# Re-resolver edges (tras reindexar)
resolved = await resolve_edges(conn, source_id=1)
```

## Test verificado

```
Chunks: 20 | Symbol edges raw: 106 | Resolved edges: 16
Two-pass search 'extract call edges':
  [1.000] extract_call_edges (edge_extractor.py:76)
  [0.984] index_file (indexer.py:153)
  [0.500] ExtractedEdge (edge_extractor.py:33)
  [0.500] _extract_callee_name (edge_extractor.py:39)
  [0.492] find_chunk_for_byte (edge_extractor.py:100)
```

## Pendientes

- **Embeddings**: `cgraph_chunks.embedding` (vector 1536) está en schema. Para activar búsqueda vectorial: pasar `embedding=[...]` a `hybrid_search()`. Requiere modelo de embeddings local en Spark.
- **MCP tool**: exponer `index_source` y `hybrid_search` como tools en mcp_server_v4.py para que JARVIS/ADA puedan usarlo.
- **Venv**: usa `/home/dadito/IA/nexus_venv` (tree-sitter 0.25.2, asyncpg 0.31.0).
