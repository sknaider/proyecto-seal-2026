# SEAL Memory SDK — mem0-compatible study v1

**Autor:** NEXUS | 2026-04-23 | En respuesta a directiva William
**Objetivo:** drop-in SDK para devs que vienen de mem0 → migran a SEAL sin reescribir código.

---

## 1. Patrón mem0 a imitar (5 líneas)

```python
from mem0 import Memory
m = Memory()
m.add([{"role":"user","content":"I like pizza"}], user_id="alex")
results = m.search("food preferences", user_id="alex")
print(results)
```

**Superficie API de mem0** (verificada vía docs.mem0.ai + repo):

| Método | Firma principal | Propósito |
|---|---|---|
| `Memory()` / `Memory.from_config(cfg)` | - | Init cliente |
| `add(messages, user_id, agent_id=None, run_id=None, metadata=None)` | → list[memory] | Extraer facts + almacenar |
| `search(query, user_id=None, limit=10, filters=None)` | → list[memory] | Búsqueda semántica |
| `get(memory_id)` | → memory | Fetch uno |
| `get_all(user_id, filters=None)` | → list | Listar todos |
| `update(memory_id, data)` | → memory | Modificar |
| `delete(memory_id)` | → bool | Remover |
| `delete_all(user_id)` | → bool | Purgar |
| `history(memory_id)` | → list | Revisiones |
| `reset()` | → void | Nuclear (danger) |

---

## 2. Mapping mem0 → SEAL Memory API

| mem0 call | SEAL equivalente | Notas |
|---|---|---|
| `Memory()` | `SealMemory(base_url="http://localhost:8767")` | Cliente HTTP a SEAL |
| `Memory.from_config(cfg)` | `SealMemory.from_config(cfg)` | Soporta `vector_store`, `llm`, `embedder` |
| `m.add(msgs, user_id="alex")` | `memory_store(agent="alex", category="fact", content=...)` | Si `messages` = lista → concatenar roles |
| `m.search(q, user_id)` | `memory_hybrid_search(query=q, agent=user_id)` | SEAL usa hybrid por default (superior) |
| `m.get(id)` | `memory_list(ids=[id])` | Trivial |
| `m.get_all(user_id)` | `memory_list(agent=user_id)` | Con paginación |
| `m.update(id, data)` | `memory_update(memory_id=id, new_content=data)` | Ya ADD-only post-fix hoy |
| `m.delete(id)` | `memory_invalidate(memory_id=id)` | Soft delete, bitemporal |
| `m.history(id)` | `connectome_bitemporal_query(memory_id=id)` | Historia completa + supersedes chain |
| `m.reset()` | Bloqueado por default, requiere API_KEY admin | Protección |

---

## 3. Diseño del SDK `seal-memory`

### 3.1 Estructura del paquete

```
seal-memory/
├── pyproject.toml
├── README.md
├── seal_memory/
│   ├── __init__.py         # exports Memory, MemoryClient
│   ├── client.py           # clase Memory principal
│   ├── models.py           # Pydantic schemas
│   ├── config.py           # Config defaults
│   └── exceptions.py       # SealMemoryError, RateLimitExceeded
└── tests/
    ├── test_compat_mem0.py  # tests de compatibilidad API
    └── test_benchmarks.py   # recall@k vs mem0
```

### 3.2 Clase Memory (núcleo) — pseudocódigo

```python
# seal_memory/client.py
from typing import Union, Optional
import requests

class Memory:
    """mem0-compatible drop-in replacement backed by SEAL Memory API."""
    
    def __init__(self, base_url: str = "http://localhost:8767",
                 api_key: Optional[str] = None,
                 default_scope: str = "private"):
        self.base_url = base_url
        self.api_key = api_key or os.environ.get("SEAL_API_KEY", "")
        self.default_scope = default_scope
        self.session = requests.Session()
        if self.api_key:
            self.session.headers["Authorization"] = f"Bearer {self.api_key}"
    
    @classmethod
    def from_config(cls, config: dict):
        return cls(
            base_url=config.get("base_url", "http://localhost:8767"),
            api_key=config.get("api_key"),
            default_scope=config.get("scope", "private")
        )
    
    def add(self, messages: Union[str, list[dict]], 
            user_id: str, agent_id: Optional[str] = None,
            run_id: Optional[str] = None,
            metadata: Optional[dict] = None) -> list[dict]:
        """Store memory. Compatible with mem0 API."""
        content = self._normalize_messages(messages)
        payload = {
            "agent": agent_id or user_id,
            "category": "fact",
            "content": content,
            "importance": 5,
            "scope": self.default_scope,
            "metadata": {
                **(metadata or {}),
                "user_id": user_id,
                "run_id": run_id,
                "source_sdk": "seal-memory-py/0.1"
            }
        }
        r = self.session.post(f"{self.base_url}/memory_store", json=payload, timeout=10)
        r.raise_for_status()
        return [r.json()]
    
    def search(self, query: str, user_id: Optional[str] = None,
               limit: int = 10, filters: Optional[dict] = None) -> list[dict]:
        """Hybrid semantic search. Uses SEAL hybrid (recency+importance+similarity)."""
        filters = filters or {}
        agent = filters.get("user_id") or user_id
        payload = {
            "query": query,
            "agent": agent,
            "limit": limit,
            "scope_aware": True
        }
        r = self.session.post(f"{self.base_url}/memory_hybrid_search", json=payload, timeout=10)
        r.raise_for_status()
        return r.json().get("results", [])
    
    def get(self, memory_id: int) -> dict:
        r = self.session.get(f"{self.base_url}/memories/{memory_id}", timeout=5)
        r.raise_for_status()
        return r.json()
    
    def get_all(self, user_id: str, filters: Optional[dict] = None) -> list[dict]:
        params = {"agent": user_id, **(filters or {})}
        r = self.session.get(f"{self.base_url}/memories", params=params, timeout=10)
        r.raise_for_status()
        return r.json().get("memories", [])
    
    def update(self, memory_id: int, data: str) -> dict:
        r = self.session.post(f"{self.base_url}/memory_update",
                              json={"memory_id": memory_id, "new_content": data},
                              timeout=10)
        r.raise_for_status()
        return r.json()
    
    def delete(self, memory_id: int) -> bool:
        r = self.session.post(f"{self.base_url}/memory_invalidate",
                              json={"memory_id": memory_id}, timeout=5)
        r.raise_for_status()
        return True
    
    def history(self, memory_id: int) -> list[dict]:
        r = self.session.get(f"{self.base_url}/connectome_bitemporal_query",
                             params={"memory_id": memory_id}, timeout=10)
        r.raise_for_status()
        return r.json().get("history", [])
    
    # ── Helpers ──
    @staticmethod
    def _normalize_messages(messages: Union[str, list[dict]]) -> str:
        if isinstance(messages, str):
            return messages
        return "\n".join(
            f"{m.get('role','user')}: {m.get('content','')}" 
            for m in messages
        )
```

### 3.3 5-line onboarding (parity con mem0)

```python
# Instalación: pip install seal-memory

from seal_memory import Memory
m = Memory()
m.add([{"role":"user","content":"I like pizza"}], user_id="alex")
results = m.search("food preferences", user_id="alex")
print(results)
```

**Backend:** `http://localhost:8767` (SEAL Memory API). Dev no necesita saber de PostgreSQL+Neo4j+Qdrant. Funciona igual que mem0.

---

## 4. Lo que gana el dev al migrar vs mem0

| Feature | mem0 | seal-memory |
|---|---|---|
| Local-first zero-cloud | ❌ SaaS, ⚠️ OSS | ✅ nativo |
| HIPAA-ready | ❌ | ✅ |
| Multi-agent first-class | ❌ | ✅ scope=team |
| Emotional tags | ❌ | ✅ valence/arousal |
| Bitemporal memory | ❌ | ✅ valid_from/invalid_at |
| Signed memories (HMAC) | ❌ | ✅ tras fix de hoy |
| Audit log inmutable | ❌ | ✅ tras fix de hoy |
| Anomaly detection burst/poisoning | ❌ | ✅ tras fix de hoy |
| GPU-accelerated embeddings | Depende backend | ✅ local RTX/Spark |
| OCEAN personality integration | ❌ | ✅ si habilitan `agent` real |

---

## 5. Migration guide (documento aparte)

Estructura del migration guide a entregar con el SDK:

1. **Quick migration (1 min):**
   ```python
   # Antes:
   from mem0 import Memory
   # Después:
   from seal_memory import Memory  # DROP-IN
   ```
2. **Mapeo de configs mem0 → seal-memory**
3. **Benchmarks** (a generar en fase test): recall@5, latency add/search, footprint RAM
4. **Features bonus** — cómo activar OCEAN, emotional tags, HIPAA mode

---

## 6. Plan de implementación (propuesto)

**Week 1 — MVP drop-in:**
- Package scaffolding + core Memory class con add/search/get_all
- Tests de compatibility API (mismas firmas y returns que mem0)
- README + quickstart

**Week 2 — Feature parity + bonus:**
- update/delete/history completos
- Backend híbrido: también configurable con Qdrant-only si dev no quiere PG+Neo4j
- Optional cloud mode (apuntar a future mem0.seal-api.com)

**Week 3 — Benchmarks + launch:**
- Benchmark vs mem0 en LoCoMo / LongMemEval datasets
- Blog post comparativo con datos
- PyPI publish

**Deliverable:** `pip install seal-memory` y onboarding en 5 líneas, igual que mem0.

---

## 7. Riesgos + mitigaciones (cybersec angle)

| Riesgo | Mitigación |
|---|---|
| Dev usa SEAL como mem0 y pierde features propias | README destaca los bonus + links a docs SEAL |
| API drift entre mem0 y seal-memory | Pinear versión mem0 compat (v1.x) + tests de snapshot |
| Devs migran pero no configuran HIPAA | Default `scope=private`, flag `hipaa_mode=True` en Memory() |
| API key leak si dev hardcodea | SDK lee de env var SEAL_API_KEY, warning si falta |
| Inyección via `messages` | Validar structure + sanitize antes de memory_store |

---

## 8. Coordinación con equipo

- **ADA** → implementación técnica del SDK (Python package)
- **JARVIS** → spec arquitectural + API contract con SEAL Memory API :8767
- **ALICE** → business: pricing, tiers, marketing copy, migration guide
- **NEXUS (yo)** → security del SDK (auth, HMAC end-to-end, rate limit client-side), docs de compliance

---

*v1 — responde a directiva William 23-abr-2026 22:57 Lima. Basado en research mem0 v3 + SEAL Memory API actual + fixes de hoy.*
