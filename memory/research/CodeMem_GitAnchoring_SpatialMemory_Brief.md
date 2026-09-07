# Git Anchoring + Spatial/Layer Memory para SOUL
**Investigó:** ALICE — 12 Abril 2026
**Para:** William, JARVIS, ADA — implementación en SOUL
**Origen:** Análisis competitivo de CodeMem (github.com/zhangshi0512/CodeMem) → orden de investigación de William
**Status:** Listo para implementación — brief técnico completo

---

## RESUMEN EJECUTIVO

Se propone integrar **DOS nuevas capacidades de memoria** a SOUL que multiplicarán su efectividad en sistemas multi-dominio (AXION médico/minería/aduanas):

1. **Git Anchoring:** Anclar memorias a commits, ramas y cambios de archivos, permitiendo invalidación inteligente cuando el código evoluciona
2. **Spatial/Layer Memory:** Organizar memorias por capas arquitectónicas del proyecto, prevenir contaminación cross-domain, y optimizar retrieval por intención

Ambas capacidades están validadas en papers peer-reviewed e implementadas parcialmente en sistemas de producción (Letta, EverMemOS, GitHub Copilot).

**Esfuerzo total estimado:** Git Anchoring = 8.5 días-ADA | Spatial Memory = 11.5 días-ADA | **Total = 20 días-ADA**

---

# CAPACIDAD 1: GIT ANCHORING

## ¿Qué hace y problema que resuelve?

**Git Anchoring** vincula cada memoria a su contexto de git en el momento que fue creada:
- `git_commit_hash`: commit SHA en que se tomó la decisión
- `git_branch`: rama activa cuando se aprendió
- `file_paths[]`: archivos que la memoria afecta
- `git_timestamp`: momento Git del commit (diferente del timestamp de creación de memoria)

**Problemas que resuelve:**

1. **Obsolescencia silenciosa:** Una memoria sobre "estructura DB en tabla users" persiste aunque la tabla fue eliminada hace 3 commits. Git Anchoring detecta esto.
2. **Reutilización segura:** Al cambiar de rama, SOUL recupera SOLO las memorias relevantes para esa rama.
3. **Supersession inteligente:** Cuando resuelves un bug de forma diferente, la primera solución se marca como "superseded", no eliminada.
4. **Rastreabilidad:** Cada decisión queda linked a código, diff y commit message.
5. **Staleness detection:** Detecta automáticamente si un archivo referenciado fue modificado DESPUÉS de que la memoria fue creada.

## Implementación de referencia

### CodeMem (arxiv 2512.15813)
- State Anchoring, Procedural Memory, Architectural Layers
- MCP server TypeScript, depende de EverMemOS cloud (nosotros lo implementamos local)

### Git Context Controller — GCC (arxiv 2508.00031)
- Operaciones git-inspiradas: COMMIT, BRANCH, MERGE, CONTEXT sobre archivos de memoria
- Directorio `.GCC/`: organiza memoria en plans/ commits/ traces/
- **Resultado: 80%+ en SWE-Bench vs 65% baselines**

### Lore (arxiv 2603.15566)
- Usa git trailers en commit messages como "canales de conocimiento"
- Query-able via CLI: `git log --grep="Reasoning-Chains"`

### Selective Memory (arxiv 2603.15994)
- Write-time gating: evalúa memoria en el momento de guardar
- Supersession chains: preserva OLD→superseded_by(NEW) en vez de borrar
- Versioning: Archive mantiene 100% accuracy en temporal queries

## Diseño de integración SOUL

### Schema PostgreSQL

```sql
-- Extender tabla memories
ALTER TABLE memories ADD COLUMN git_commit_hash VARCHAR(40);
ALTER TABLE memories ADD COLUMN git_branch VARCHAR(255);
ALTER TABLE memories ADD COLUMN git_tag VARCHAR(255);
ALTER TABLE memories ADD COLUMN file_paths TEXT[];
ALTER TABLE memories ADD COLUMN git_timestamp TIMESTAMPTZ;
ALTER TABLE memories ADD COLUMN git_author VARCHAR(255);
ALTER TABLE memories ADD COLUMN superseded_by_id INTEGER REFERENCES memories(id) ON DELETE SET NULL;
ALTER TABLE memories ADD COLUMN supersession_reason TEXT;
ALTER TABLE memories ADD COLUMN last_file_check TIMESTAMPTZ;
ALTER TABLE memories ADD COLUMN files_still_valid BOOLEAN DEFAULT true;
ALTER TABLE memories ADD COLUMN staleness_confidence FLOAT DEFAULT 1.0;

-- Índices
CREATE INDEX idx_git_commit ON memories(git_commit_hash);
CREATE INDEX idx_git_branch ON memories(git_branch);
CREATE INDEX idx_file_paths ON memories USING GIN(file_paths);
CREATE INDEX idx_superseded_by ON memories(superseded_by_id);
CREATE INDEX idx_files_still_valid ON memories(files_still_valid);
```

### Neo4j: Nodos y Relaciones

```
Nodos nuevos:
  GitCommit { sha, branch, author, timestamp, message, diff_summary }
  GitFile { path, last_modified_commit, last_modified_timestamp }

Relaciones nuevas:
  (Memory)-[:ANCHORED_AT]->(GitCommit)
  (Memory)-[:REFERENCES_FILE]->(GitFile)
  (Memory)-[:SUPERSEDED_BY]->(Memory)
  (GitCommit)-[:CONTAINS_CHANGE_TO]->(GitFile)
  (Agent)-[:MADE_COMMIT]->(GitCommit)
```

### Qdrant: Filtros por Git

```python
# Buscar solo en branch activo, solo memorias válidas
qdrant_search(
    vector=query_embedding,
    filter={
        "git_branch": {"$eq": "feature/axion-medical"},
        "files_still_valid": {"$eq": True}
    },
    limit=10
)
```

### MCP Tools Nuevas (7 herramientas)

| Tool | Descripción |
|------|-------------|
| `memory_store_with_git_anchor` | Guarda memoria anclada a commit/branch/files |
| `memory_retrieve_current_branch` | Retrieval filtrado por branch activo |
| `memory_check_file_staleness` | Verifica si archivos cambiaron post-memoria |
| `memory_mark_superseded` | Marca vieja memoria como reemplazada por nueva |
| `memory_retrieve_branch_history` | Historia de memorias de una branch específica |
| `memory_git_timeline_query` | Query temporal por rango de commits |
| `memory_invalidate_stale_batch` | Batch invalidation de memorias obsoletas |

## Esfuerzo estimado

| Componente | Días-ADA |
|-----------|----------|
| Schema PostgreSQL + índices | 0.5 |
| Neo4j graph extensions | 1.0 |
| Qdrant metadata filtering | 0.5 |
| MCP tools (7 tools) | 3.5 |
| Git integration (GitPython) | 1.0 |
| Unit tests | 2.0 |
| **TOTAL** | **8.5 días** |

## Casos de uso concretos SEAL/AXION

### Medical Domain
Protocolo de antibióticos guardado en commit "abc123". Médico actualiza el protocolo en commit "xyz789". Staleness detection detecta que el archivo fue modificado post-memoria → `files_still_valid = false` → agente alerta "Este conocimiento está desactualizado. Verificar fuente."

### Customs/Aduanas
HS Code guardado como memoria. Regulación cambia en commit "ghi789". Memoria se marca `superseded_by=new_memory_id`. Auditoría completa: "¿cuándo cambió esto?" → git log + cadena de supersesión.

## Riesgos y dependencias

| Riesgo | Mitigación |
|--------|-----------|
| Staleness detection lenta | Cachear git log queries (60s TTL) |
| Supersession chains infinitas | Límite 10 hops en Neo4j SUPERSEDED_BY |
| Git auth en MCP | GITHUB_TOKEN env var |

---

# CAPACIDAD 2: SPATIAL / LAYER MEMORY

## ¿Qué hace y problema que resuelve?

**Spatial/Layer Memory** organiza el conocimiento del agente por **capas arquitectónicas** en lugar de todo mezclado.

**Capas para AXION:**
- `medical` — protocolos clínicos, diagnóstico, fármacos
- `mining` — extracción, concentración, modelos geológicos
- `customs` — aranceles, HS codes, regulaciones
- `shared` — conocimiento común entre verticales
- `api` / `database` / `infrastructure` — capas técnicas transversales

**Problemas que resuelve:**

1. **Contaminación cross-domain:** Query "cómo optimizar?" NO debe mezclar "optimize patient dosing" con "optimize copper flotation"
2. **Ruido en retrieval:** 6 de 10 resultados de otro dominio diluyen los relevantes
3. **Compliance/Privacy:** Datos clínicos NUNCA deben contaminar memorias de aduanas (HIPAA, GDPR)
4. **Escalabilidad:** 1M memorias → buscar por layer primero → fast; buscar todas → slow

## Implementación de referencia

### MAGMA (arxiv 2601.03236) — ya en SOUL
- 4 grafos ortogonales + policy-guided traversal
- Base conceptual para Spatial Memory

### Task Memory Engine (arxiv 2505.19436)
- Task graph DAG organizado por subtasks
- TRIM: descompone query en intención + subtasks
- **Elimina hallucinations 66.7% vs ReAct**

### MemGuide (arxiv 2505.20231)
- Two-stage intent-driven selection
- **99% task success vs. 88% baseline, reduce diálogo 2.84 turnos**

### Multi-Agent Memory (arxiv 2603.10062)
- Cache layer (working memory rápida) + Memory layer (storage persistente) + Persistent Storage
- Hierarchy optimizada por latencia, ancho de banda, capacidad

## Diseño de integración SOUL

### Schema PostgreSQL

```sql
-- Tabla nueva: taxonomía de layers
CREATE TABLE memory_layers (
    id SERIAL PRIMARY KEY,
    layer_key VARCHAR(64) UNIQUE NOT NULL,
    layer_name VARCHAR(255),
    description TEXT,
    vertical_domain VARCHAR(64),   -- "medical", "mining", "customs", "shared", "infrastructure"
    parent_layer_id INTEGER REFERENCES memory_layers(id),
    isolation_level VARCHAR(32),   -- "strict" | "shared" | "open"
    color_hex VARCHAR(7),
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Extender tabla memories
ALTER TABLE memories ADD COLUMN layer_id INTEGER REFERENCES memory_layers(id);
ALTER TABLE memories ADD COLUMN layer_path VARCHAR(255);      -- "medical/diagnosis"
ALTER TABLE memories ADD COLUMN intent_class VARCHAR(64);     -- "diagnostic", "optimization", "tariff_lookup"
ALTER TABLE memories ADD COLUMN intent_confidence FLOAT DEFAULT 1.0;
ALTER TABLE memories ADD COLUMN cross_domain_risk FLOAT DEFAULT 0.0;

-- Datos iniciales para AXION
INSERT INTO memory_layers(layer_key, layer_name, vertical_domain, isolation_level) VALUES
    ('medical', 'Medical/Clinical', 'medical', 'strict'),
    ('mining', 'Mining/Extraction', 'mining', 'strict'),
    ('customs', 'Customs/Tariffs', 'customs', 'strict'),
    ('shared', 'Shared Knowledge', 'shared', 'open'),
    ('api', 'API/Integration', 'infrastructure', 'shared'),
    ('database', 'Database/Models', 'infrastructure', 'shared');

CREATE INDEX idx_layer_id ON memories(layer_id);
CREATE INDEX idx_intent_class ON memories(intent_class);
```

### Neo4j: Layer + Intent Nodes

```
Nodos nuevos:
  Layer { key, name, domain, isolation }
  Intent { class, description, domain }

Relaciones:
  (Memory)-[:IN_LAYER]->(Layer)
  (Memory)-[:HAS_INTENT]->(Intent)
  (Layer)-[:BELONGS_TO_DOMAIN]->(Domain)
  (Intent)-[:ROUTED_BY]->(Layer)
```

### MCP Tools Nuevas (8 herramientas)

| Tool | Descripción |
|------|-------------|
| `memory_classify_intent` | Clasifica query en intención + layer sugerida |
| `memory_store_layered` | Guarda memoria con layer e intent asignados |
| `memory_retrieve_by_layer` | Retrieval filtrado a layer específica |
| `memory_list_layers` | Lista layers disponibles con stats |
| `memory_share_across_layers` | Comparte memoria entre layers explícitamente |
| `memory_detect_cross_domain_contamination` | Detecta riesgos de mezcla cross-domain |
| `memory_enforce_layer_isolation` | Bloquea acceso no autorizado cross-layer |
| `memory_intent_awareness_query` | Query auto-routed por intent (MemGuide pattern) |

## Esfuerzo estimado

| Componente | Días-ADA |
|-----------|----------|
| Schema PostgreSQL (memory_layers + alter memories) | 1.0 |
| Neo4j Layer + Intent nodes | 1.0 |
| Qdrant metadata + layer filtering | 0.5 |
| Intent classification (Ollama-based) | 1.5 |
| MCP tools (8 tools) | 4.0 |
| Cross-domain contamination detector | 1.0 |
| Layer access control + enforcement | 1.0 |
| Unit tests + integration tests | 2.0 |
| **TOTAL** | **11.5 días** |

## Casos de uso concretos AXION

### Medical — Aislamiento garantizado
Query: "¿Cómo optimizar la dosis?" → Clasificado como medical/dosage_optimization (confidence 0.95) → Retrieval SOLO en layer medical → Protocolos clínicos, NO datos de minería ni aduanas → Patient safety preservada.

### Mining — Contexto limpio cross-sesión
JARVIS aprende en layer "mining": "Copper sulfate > 1.5% reduces flotation efficiency". ADA consulta sobre minería → Retrieval solo en mining layer → 0 contaminación con medical o customs.

### Customs — Compliance HIPAA/GDPR
Datos de aranceles NUNCA aparecen en queries médicas. Layer "customs" con isolation_level="strict". Sistema bloquea automáticamente y registra intento si agente médico trata de acceder.

## Riesgos y dependencias

| Riesgo | Mitigación |
|--------|-----------|
| Intent misclassification | Ensemble (semantic + keyword) + threshold 0.85 |
| Over-compartmentalization | Layer "shared" para conocimiento común |
| Performance (layer filtering) | Index en (layer_id, intent_class), cachear |
| Access control bypass | Verificar isolation_level en CADA retrieval call |

---

# INTEGRACIÓN CONJUNTA: LIFECYCLE COMPLETO

```
Memory Lifecycle con AMBAS capacidades:

1. CREAR (Storage)
   ├─ Classify intent + layer → memory_store_layered()
   └─ Auto-detect git context → memory_store_with_git_anchor()

2. RECUPERAR (Query)
   ├─ Intent-aware routing → memory_intent_awareness_query()
   ├─ Layer filtering → memory_retrieve_by_layer()
   └─ Staleness + validity → memory_retrieve_current_branch()

3. MANTENER (Lifecycle)
   ├─ Cross-domain contamination → memory_detect_cross_domain_contamination()
   ├─ File staleness → memory_check_file_staleness()
   └─ Supersession chains → memory_mark_superseded()

4. AUDITAR (Governance)
   ├─ Git timeline traceability → memory_git_timeline_query()
   └─ Layer isolation enforcement → memory_enforce_layer_isolation()
```

---

# PLAN DE IMPLEMENTACIÓN PROPUESTO

| Fase | Semana | Contenido |
|------|--------|-----------|
| 1 | Semana 1 | PostgreSQL schema (ambas capacidades) + Neo4j setup |
| 2 | Semana 2 | MCP tools core: memory_store_layered + memory_retrieve_by_layer |
| 3 | Semana 3 | Git integration (GitPython) + staleness detection |
| 4 | Semana 4 | Intent classification (Ollama) + cross-domain detection |
| 5 | Semana 5 | Testing completo, optimization, benchmark vs baseline |

**Trigger de activación:** Tras estabilización de LatentGraphMem V2 (recall@5 validado sobre held-out).

---

# PAPERS DE ESTA INVESTIGACIÓN

| Paper | Venue | Relevancia |
|-------|-------|-----------|
| arxiv 2512.15813 (CodeMem) | — | Implementación de referencia (Git + Procedural) |
| arxiv 2601.02868 (CodeMEM AST) | — | +12.2% instruction following en code generation |
| arxiv 2508.00031 (GCC) | — | Git-inspired context control, 80%+ SWE-Bench |
| arxiv 2603.15566 (Lore) | — | Git trailers como knowledge channels |
| arxiv 2603.15994 (Selective Memory) | — | Write-time gating + supersession chains |
| arxiv 2505.19436 (Task Memory Engine) | — | Spatial DAG, 66.7% less hallucinations |
| arxiv 2505.20231 (MemGuide) | — | Intent-driven selection, 99% task success |
| arxiv 2601.03236 (MAGMA) | — | Base conceptual (ya en SOUL) |
| arxiv 2603.10062 (Multi-Agent Memory) | — | Cache hierarchy architecture |

---

*Brief preparado por ALICE | 12 Abril 2026 | Proyecto SEAL*
*Investigación solicitada por William después de análisis competitivo de CodeMem*
*Estado: Completo — listo para que JARVIS haga la spec y ADA implemente*
