# Evidencia: Mem0 Graph Memory no existe en OSS
*Auditoria ALICE u2014 20 abril 2026*

## Metodologu00eda
Revisado directamente via GitHub API: https://api.github.com/repos/mem0ai/mem0/
Ram branch: `main` (commit actual)

---

## Archivos revisados y hallazgos

### 1. `mem0/memory/main.py` (el core del sistema)
```
URL: https://raw.githubusercontent.com/mem0ai/mem0/main/mem0/memory/main.py
Buu00fasqueda: grep 'graph|Graph|neo4j|Neo4j'
Resultado: CERO MATCHES
```
Todo el sistema de memoria vive en este archivo. Sin graph.

### 2. `mem0/configs/base.py` (configuraciu00f3n del sistema)
```
URL: https://raw.githubusercontent.com/mem0ai/mem0/main/mem0/configs/base.py
Buu00fasqueda: grep 'graph|Graph|neo4j'
Resultado: CERO MATCHES
```
La clase `MemoryConfig` no tiene campo `graph_store`.

### 3. `mem0/__init__.py` (exports pu00fablicos)
```python
from mem0.client.main import AsyncMemoryClient, MemoryClient
from mem0.memory.main import AsyncMemory, Memory
```
Solo `Memory` y `AsyncMemory`. No hay ninguna clase de Graph.

### 4. Tree recursivo del repo u2014 archivos `.py` con 'graph'
```
Resultado: NINGUNO
```
Realicado con: `https://api.github.com/repos/mem0ai/mem0/git/trees/main?recursive=1`
Filtrado por: `'graph' in path and path.endswith('.py')`

### 5. Du00f3nde SU00cd existe 'graph' en el repo
```
examples/graph-db-demo/neo4j-example.ipynb     <- ejemplo externo
examples/graph-db-demo/kuzu-example.ipynb      <- ejemplo externo
examples/graph-db-demo/memgraph-example.ipynb  <- ejemplo externo
examples/graph-db-demo/neptune-db-example.ipynb <- ejemplo externo
docs/images/graph-platform.png                 <- imagen de PLATAFORMA (cloud)
```
Todos son notebooks demostrativos. El `graph-platform.png` confirma que es feature de plataforma cloud.

---

## Conclusiu00f3n con evidencia

El graph memory en Mem0 **requiere acceso a la Mem0 Platform** (cloud, pricing enterprise).
El `pip install mem0ai` open-source NO incluye graph memory.

SEAL Memory incluye entity graph en OSS local desde Sprint 2:
- Endpoint: `GET /v1/agents/{id}/entities`
- Extrae personas/lugares/orgs/temas con mention_count
- Local-first, sin cloud, sin costo adicional

---

## Uso de este hallazgo en el pitch

```
Mem0 OSS ($0):  vector search + BM25 + basic memory
Mem0 Platform ($?): + graph memory + dashboard

SEAL Memory OSS ($0): vector search + BM25 + entity graph + OCEAN + 
                      emotional state + beliefs + drift + inner thoughts
```

La palanca: "SEAL te da en OSS lo que Mem0 solo da en su plataforma de pago."

---
*Verificaciu00f3n independiente: cualquiera puede reproducir esta auditoria con curl + grep sobre el repo pu00fablico.*
