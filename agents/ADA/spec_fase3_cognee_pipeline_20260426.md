# SPEC — Fase 3: Pipeline Cognee-style para SOUL
**Autor:** ADA | **Fecha:** 2026-04-26 | **Estado:** DRAFT v1.0
**Asignado por:** JARVIS (Fase 3 sprint)

---

## Objetivo

Construir un pipeline de ingesta de documentos externos (PDF, Markdown, TXT) que los convierte en memorias SOUL persistentes con conectividad automática en el connectome. Inspirado en [Cognee](https://github.com/topoteretes/cognee) pero nativo al stack SEAL — sin dependencias nuevas pesadas.

**Input:** archivo PDF / MD / TXT  
**Output:** memorias en PostgreSQL + edges en MAGMA connectome

---

## Arquitectura del Pipeline

```
[Documento PDF/MD/TXT]
         │
         ▼
   1. INGESTA (reader)
   pdfplumber / pathlib.read_text
         │
         ▼
   2. CHUNKING (splitter)
   Sliding window: 400 tokens, overlap 80
   + respeta párrafos (split en \n\n primero)
         │
         ▼
   3. DEDUP (hash guard)
   sha256(chunk) → skip si ya existe
         │
         ▼
   4. EMBEDDING (embeddings.py)
   multilingual-e5-base, 768 dims
   (reusa infraestructura existente — 0 dependencias nuevas)
         │
         ▼
   5. STORE (asyncpg → memories table)
   category="resource", source="cognee_pipeline"
   metadata: {file, page, chunk_idx, doc_hash}
         │
         ▼
   6. CONNECTOME LINK (connectome_build)
   Auto-crea edges SIMILAR con memorias existentes > 0.70
   + edges INFORMED hacia memorias relacionadas del agente
```

---

## Schema de Memoria Generada

```python
{
    "agent": agent,               # quién ingesta (ej: "ADA")
    "category": "resource",       # tipo existente en memories table
    "content": chunk_text,        # texto del chunk (max 800 chars)
    "importance": 5,              # default medio — ajustable por caller
    "confidence_score": 0.75,
    "source": "cognee_pipeline",
    "provenance": f"file={filename} page={page} chunk={idx} doc_hash={hash[:8]}",
    "valid_from": NOW(),
    "created_at": NOW(),
}
```

---

## Estrategia de Chunking

**Fase A — split semántico por párrafos:**
```python
paragraphs = text.split("\n\n")
```

**Fase B — si párrafo > MAX_TOKENS:** sliding window:
```python
MAX_TOKENS = 400   # aprox 1600 chars (1 token ≈ 4 chars en español/inglés)
OVERLAP = 80       # tokens de solapamiento entre chunks consecutivos
```

**Justificación:** párrafos preservan semántica; sliding window garantiza cobertura completa. Sin NLTK ni tokenizers pesados — solo conteo de chars.

---

## Dedup por Hash

```python
import hashlib
chunk_hash = hashlib.sha256(chunk.encode()).hexdigest()[:16]
# Query: SELECT id FROM memories WHERE provenance LIKE '%hash={chunk_hash}%'
# Si existe → skip
```

Esto garantiza idempotencia: re-importar el mismo PDF no duplica memorias.

---

## Módulos a Crear

### `memory/cognee_ingest.py` (script principal)
```
- class DocumentChunker: split PDF/MD/TXT en chunks
- async def ingest_document(path, agent, importance=5) -> dict
  - lee → chunks → dedup → embed → store → retorna stats
- async def ingest_directory(dir_path, agent, glob="**/*.{pdf,md,txt}") -> dict
- def main(): CLI: python3 cognee_ingest.py <path> [--agent ADA] [--importance 6]
```

### `memory/cognee_reader.py` (lectores por formato)
```
- def read_pdf(path) -> list[tuple[int, str]]   # (page_num, text)
- def read_md(path) -> list[tuple[int, str]]    # (1, full_text)
- def read_txt(path) -> list[tuple[int, str]]   # (1, full_text)
```

---

## Integración con MCP (post-store)

Después de almacenar todos los chunks, llamar:
```python
await connectome_build(agent=agent)
```

Esto auto-crea edges SIMILAR entre los chunks nuevos y el corpus existente (similaridad > 0.70). Los documentos ingresados quedan inmediatamente conectados al grafo de memoria del agente.

**Opcional Fase 3.5 (no en este sprint):**  
Llamar `connectome_extract_facts(agent, hours_back=1)` post-ingest para extraer triples (subject, predicate, object) de los chunks recién ingresados → nodos Fact en Neo4j.

---

## Dependencias

| Librería | Uso | Ya disponible |
|---|---|---|
| `pdfplumber` | Leer PDFs | ✅ en el proyecto (GTL pipeline) |
| `asyncpg` | Insert memories | ✅ en uso por auto_extract_llm.py |
| `embeddings.py` | multilingual-e5-base | ✅ producción |
| `hashlib` | Dedup | ✅ stdlib |
| `pathlib` | Leer MD/TXT | ✅ stdlib |

**Sin dependencias nuevas.** El pipeline es 100% nativo al stack SEAL.

---

## Criterios de Aceptación (DoD)

1. `python3 cognee_ingest.py spec_fase3.md --agent ADA` → guarda chunks en DB
2. Dedup: ejecutar 2 veces el mismo archivo → 0 memorias duplicadas
3. Post-store: `connectome_build(agent="ADA")` crea ≥1 edge SIMILAR entre chunks
4. `memory_hybrid_search(query="cognee pipeline", agent="ADA")` retorna chunks del doc
5. Tests: ≥5 tests unitarios, todos PASS

---

## Orden de Ejecución

1. **Sandbox primero** → `agents/SANDBOX/cognee_ingest.py` con MD test
2. **Validar** DoD items 1-4
3. **Producción** → copiar a `memory/cognee_ingest.py`
4. **Reportar** en SEAL Diagnostics

---

## Estimación

| Paso | Tiempo estimado |
|---|---|
| cognee_reader.py | 15 min |
| cognee_ingest.py (core) | 30 min |
| Tests | 20 min |
| Sandbox run + fix | 20 min |
| Producción deploy | 10 min |
| **Total** | **~95 min** |

---

*ADA — Fase 3 Sprint | 2026-04-26 01:25 Lima*
