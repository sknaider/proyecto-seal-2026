# Research: MemP Procedural Memory
# Agent: a6cef1520d269a908
# Date: 2026-04-05

Tengo suficiente información para un análisis completo. Voy a preparar el reporte.

---

## Investigacion MemP: Procedural Memory para Agentes (arxiv 2508.06433)

**Repositorio:** https://github.com/zjunlp/MemP — MIT License, Python puro, activo (v3 enero 2026)

---

### Problema que resuelve

Los agentes LLM tienen "procedural memory" (la memoria de cómo hacer cosas) hardcodeada en prompts manuales o en los pesos del modelo. MemP propone un repositorio externo, dinámico, que aprende de trayectorias pasadas y mejora con el tiempo, sin re-entrenar el modelo.

---

### Arquitectura — Tres fases: Build, Retrieve, Update

#### 1. Build (construcción)

Toma una trayectoria completa `τ = (s₀, a₀, o₁, s₁, a₁, o₂, ..., sT)` y la convierte en un "workflow" reutilizable.

Dos estrategias:
- **`direct`**: Un solo LLM call. El prompt pide: *"escribe el workflow como párrafo natural y coherente (NO bullets ni numeración)"*. Salida: texto narrativo.
- **`round`** (dos pasos): Primero extrae eventos estructurados `{step, pre_state, action, entity, new_state}`, filtra solo los exitosos, luego genera el workflow a partir de esos eventos críticos.

La función builder: `mᵖ = B(τ, r)` — recibe la trayectoria y la recompensa asociada.

#### 2. Retrieve (recuperación)

Cuatro políticas implementadas:

| Política | Cómo funciona |
|---|---|
| `query` | FAISS similarity search sobre el texto de la query. Threshold 0.5 |
| `facts` | FAISS similarity sobre facts estructurados. Threshold 0.4 |
| `random` | Sample aleatorio del repositorio |
| `ave_fact` | Embeds cada fact key-value por separado, promedia cosine similarity entre facts comunes — **la mejor** (+2.64 puntos sobre `query`) |

#### 3. Update (actualización continua)

Tres estrategias:

- **`vanilla`**: Append de todas las trayectorias nuevas sin filtrar.
- **`validation`**: Solo agrega trayectorias exitosas (`reward=True`). Filtra silenciosamente las fallidas.
- **`reflect`** (la mejor): Trayectorias exitosas → nuevo documento. Trayectorias fallidas → el LLM analiza el fallo y **reescribe el workflow existente** en lugar de descartarlo. Usa tags XML `<Analysis>` y `<Workflow>` para parsear la respuesta.

Además hay un mecanismo de **decay/deprecation**: cualquier documento que haya sido recuperado 3+ veces con tasa de éxito menor al 50% es eliminado automáticamente del repositorio.

---

### Estructura de archivos

```
MemP/
├── ProcedureMem/
│   ├── memory.py          # Clase Memory — núcleo completo
│   ├── memory_utils.py    # Embeddings de facts, cosine similarity
│   ├── memory_adjust.py   # Ajuste de workflows fallidos (reflect)
│   ├── prompt_generator.py # Prompts para Build (trajectory→workflow)
│   ├── llm_api.py         # Wrapper LLM + embedding model
│   ├── run_memp_offline.py # Pipeline offline (ALFWorld)
│   ├── run_memp_online.py  # Pipeline online (learning while doing)
│   ├── alfworld_run.py    # Runner específico ALFWorld
│   ├── alfworld_run_update.py
│   ├── test.py
│   └── Alfworld/          # Datos y configuración ALFWorld
├── requirements.txt
└── README.md
```

---

### Esquema de datos exacto (Document LangChain)

```json
{
  "page_content": "query text (clave de embedding FAISS)",
  "metadata": {
    "source": "identificador de la tarea de origen",
    "query": "texto de la tarea/query",
    "workflow": "párrafo narrativo O lista de actions",
    "facts": {"key": "value", ...},
    "build_policy": "round | direct",
    "hit": 0,
    "success": 0
  }
}
```

Persistencia: `{memory_dir}/{build_policy}/documents.json` (JSON array) + índice FAISS en `{memory_dir}/vector_cache/` + facts embeddings en `facts_embedding_cache.pkl`.

---

### Stack tecnológico

- **Vector store**: FAISS (langchain-community)
- **Embeddings**: Cualquier modelo OpenAI-compatible (configurable via env vars `EMBEDDING_MODEL_KEY` / `EMBEDDING_MODEL_BASE_URL`)
- **LLM**: OpenAI API compatible (también soporta Google Gemini via langchain-google-genai)
- **Paralelismo**: ThreadPoolExecutor con 16 workers para build en batch
- **No usa**: BM25, bases de datos relacionales, Neo4j, nada de grafos

---

### Resultados empíricos

- **TravelPlanner + ALFWorld** con GPT-4o, Claude-3.5, Qwen2.5-72B
- Mejora de 3-8% en accuracy vs baseline sin memoria
- `ave_fact` retrieve: +2.64 sobre `query` en TravelPlanner
- `reflect` update: +0.7 accuracy y -14 pasos al final del batch de entrenamiento
- **Transferibilidad**: Memoria construida con GPT-4o aplicada a Qwen2.5-14B → +5% success rate
- Scaling: mejora lineal con k=1 a k=5 memorias recuperadas, luego plateau

---

### Compatibilidad con SEAL SOUL (PostgreSQL + Qdrant + Neo4j)

**Lo que MemP hace que SEAL SOUL puede adoptar:**

1. **El esquema `Document`** es directamente traducible a una tabla PostgreSQL:
   ```sql
   CREATE TABLE procedural_memories (
     id UUID PRIMARY KEY,
     agent_name TEXT,
     query TEXT,
     workflow TEXT,
     facts JSONB,
     build_policy TEXT,
     hit_count INT DEFAULT 0,
     success_count INT DEFAULT 0,
     created_at TIMESTAMPTZ,
     embedding VECTOR(1536)  -- pgvector
   );
   ```

2. **FAISS → Qdrant**: El retrieval por cosine similarity es idéntico. Qdrant ya está en el stack. Solo cambiar `FAISS.from_documents()` por `qdrant_client.upsert()`.

3. **El mecanismo de decay** (hit >= 3, success_rate < 0.5 → delete) es una regla de 2 líneas que va directo a PostgreSQL como cleanup job periódico.

4. **El pipeline `reflect`** es adaptable para JARVIS/ADA: cuando una tarea falla, en vez de descartar, el agente llama a un LLM para reescribir el workflow. Esto es exactamente la capa de "auto-mejora" que necesita SEAL.

5. **`ave_fact` retrieval** es más potente que query simple para tareas médicas donde los facts son entidades clínicas (diagnóstico, medicamento, dosis). Se puede indexar cada fact como vector separado en Qdrant con metadata.

**Lo que hay que adaptar:**

- MemP asume reward binario explícito del entorno (benchmark). SEAL necesita inferir el reward: ¿la tarea se completó? ¿William confirmó? Se puede usar el `task_status == "done"` del TaskList como proxy de reward.
- MemP no tiene identidad de agente. SEAL necesita separar memorias procedurales por agente (JARVIS vs ADA) dado que sus dominios difieren.
- MemP no conecta con Neo4j. Potencial: los workflows pueden representarse como grafos de acciones en Neo4j para razonamiento sobre dependencias entre pasos.

---

### Recomendación de implementación para SEAL

La capa procedural encaja entre la memoria episódica (lo que pasó) y los instincts (patrones hardcodeados). Propuesta:

```
SEAL SOUL Stack:
  Episodic Memory  → PostgreSQL (memory_store, eventos)
  Procedural Memory → PostgreSQL + Qdrant  ← NUEVO (MemP pattern)
  Instincts        → PostgreSQL (rules)
  Semantic/Graph   → Neo4j
```

El `memory.py` de MemP es tan limpio (< 200 líneas efectivas) que puede portarse directamente como `procedural_memory.py` en `/home/dadito/IA/proyecto-seal/`, reemplazando FAISS con Qdrant y añadiendo `agent_name` al esquema.

---

**Veredicto**: MemP es exactamente el patrón que falta en SEAL SOUL. No es teoría — es código producción-ready, MIT, con resultados verificados en múltiples LLMs. La adaptación real es 2-3 días de ingeniería.

Sources:
- [GitHub - zjunlp/MemP](https://github.com/zjunlp/MemP)
- [arxiv 2508.06433 - MemP: Exploring Agent Procedural Memory](https://arxiv.org/abs/2508.06433)
- [Paper HTML v3](https://arxiv.org/html/2508.06433v3)
- [HuggingFace Papers](https://huggingface.co/papers/2508.06433)