# SPEC: Context-Aware Distillation + TrieIndex Retrieval
**Autor:** ALICE  
**Fecha:** 2026-04-15  
**Propuesto por:** William (12:51, 15 abril 2026)  
**Contexto:** Solución al problema de contexto lleno que congela a los agentes (ADA 30h, ALICE crash apt install hoy)  
**Para implementar:** ADA + JARVIS, viernes 18 abril 2026

---

## Problema

Los agentes de Team SEAL mueren por contexto lleno porque:
1. Cada tool_result, tool_call y mensaje del Monitor se acumula en la ventana de Claude Code
2. No hay descarga selectiva — cuando se llena, crash o compactación forzada con pérdida
3. Después del crash, el contexto conversacional reciente se pierde (no está en SOUL)
4. El JSONL completo es demasiado grande para cargar en boot — no escala

## Solución Propuesta (William, 15 abril 2026)

### Componente 1: Distilación Selectiva (guardar solo lo importante)

**Qué:** En vez de guardar el historial completo en JSONL o cargarlo todo al contexto, destilar solo los momentos que importan a SOUL DB.

**Criterios de importancia (para la función de distilación):**
- Decisiones de William → importancia 9-10
- Correcciones al agente → importancia 8-9
- Implementaciones completadas → importancia 7-8
- Diagnósticos de errores → importancia 7-8
- Estado emocional significativo → importancia 6-7
- Conversación técnica ordinaria → NO guardar (ya está en jsonl si se necesita)

**Trigger de distilación:**
- `context_guard` marca WARNING (>70% contexto) → destilar los últimos N mensajes importantes
- Antes de cualquier reinicio/sueño del agente
- Periódico cada 30-45 min (systemd timer, ya existe como `seal-alice-checkpoint.timer`)

**Herramienta existente:** `mcp__seal-memory__session_distill` — ya existe, solo falta el trigger automático integrado al context_guard.

**Pseudocódigo (Python, 0 tokens Claude):**
```python
# En context_guard.py, cuando nivel >= WARNING:
def pre_sleep_distill(agent: str, session_id: str):
    # Leer últimos 50 mensajes del canal del agente
    messages = read_last_messages(agent, n=50)
    # Filtrar por importancia usando heurísticas (palabras clave, emisor=William, etc.)
    important = [m for m in messages if is_important(m)]
    # Llamar session_distill con los mensajes importantes
    mcp_session_distill(agent=agent, messages=important, session_id=session_id)
    # Escribir checkpoint
    write_checkpoint(agent, state="pre_sleep_distill_done")
```

**Función `is_important(message)` — heurísticas:**
- `from == "William"` → siempre importante
- Contiene palabras clave: "importante", "recuerden", "guarden", "siempre", "nunca", "error", "fix", "implementado" → importante
- `type in ["correction", "decision", "implementation_log"]` → importante
- Longitud > 200 chars y from in ["JARVIS", "ADA", "ALICE"] → probablemente importante
- Heartbeats, nervios, status checks → NO importante

---

### Componente 2: TrieIndex para Retrieval Eficiente

**Qué:** Árbol de prefijos sobre las memorias en SOUL DB para que los agentes puedan buscar por prefijo en O(m) donde m = longitud del query, sin cargar el JSONL completo al contexto.

**Por qué Trie y no solo `memory_search`:**
- `memory_search` usa embeddings semánticos → útil para búsqueda conceptual
- TrieIndex es para búsqueda por prefijo exacto → útil cuando el agente sabe el inicio del tema
- Ejemplo: "context_guard" → trie retorna todos los nodos que empiezan con esa raíz en <5ms
- No reemplaza `memory_search`, lo complementa — capas distintas de retrieval

**Estructura del Trie en PostgreSQL:**
```sql
-- Tabla de índice (añadir a SEAL schema)
CREATE TABLE IF NOT EXISTS memory_trie (
    id          SERIAL PRIMARY KEY,
    agent       VARCHAR(50),
    prefix      VARCHAR(500),       -- prefijo indexado
    memory_ids  INTEGER[],           -- IDs de memorias que matchean este prefijo
    depth       INTEGER,             -- profundidad del nodo en el árbol
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trie_agent_prefix ON memory_trie(agent, prefix);
```

**Build del Trie (batch, 0 tokens):**
```python
def build_trie_for_agent(agent: str):
    memories = fetch_all_memories(agent)  # desde PostgreSQL
    for memory in memories:
        # Tokenizar el contenido por palabras clave
        tokens = extract_keywords(memory.content)
        for token in tokens:
            # Insertar todos los prefijos de cada token
            for i in range(1, len(token)+1):
                prefix = token[:i]
                upsert_trie_node(agent, prefix, memory.id)
```

**Query del Trie (desde agente, 0 tokens extra de contexto):**
```python
def trie_search(agent: str, query: str) -> List[Memory]:
    prefix = query.lower()
    # Una query SQL, retorna IDs
    memory_ids = db.query(
        "SELECT DISTINCT unnest(memory_ids) FROM memory_trie WHERE agent=$1 AND prefix=$2",
        [agent, prefix]
    )
    # Cargar solo esas memorias específicas — no todo el historial
    return fetch_memories_by_ids(memory_ids)
```

**Resultado:** En vez de cargar 500KB de JSONL al contexto, el agente busca `trie_search("context_guard")` y obtiene 3-5 memorias relevantes. O(m) lookup, sin saturar contexto.

---

### Componente 3: Integración al context_guard Flow

**Flow actual (incompleto):**
```
context_guard → WARNING → notifica → agente sigue hasta crash
```

**Flow propuesto:**
```
context_guard → WARNING (70%) → pre_sleep_distill() → checkpoint
             → CRITICAL (85%) → destilar + forzar sueño → relanzar con alice.sh --auto
             → POST-BOOT → trie_search para recuperar contexto relevante (no cargar todo)
```

**Archivo a modificar:** `messages/context_guard.py`
- Agregar llamada a `pre_sleep_distill()` en el nivel WARNING
- Agregar forzado de reinicio en CRITICAL (actualmente solo notifica)
- En el script de boot (`alice.sh`, `ada.sh`, `jarvis.sh`): llamar `trie_search` con los topics del último checkpoint para warm-up de contexto

---

## Entregables para el Viernes

| Archivo | Responsable | Descripción |
|---|---|---|
| `memory/pre_sleep_distill.py` | ADA | Función distilación selectiva con heurísticas |
| `memory/trie_index.py` | ADA | Build + query del TrieIndex en PostgreSQL |
| `messages/context_guard.py` | ADA | Integrar distill + reinicio forzado |
| `*.sh` (alice/ada/jarvis) | JARVIS | Agregar trie warm-up post-boot |
| Migración SQL | ADA | Crear tabla `memory_trie` con índice |

## Tests Requeridos (before done)

1. Simular WARNING en context_guard → verificar que session_distill se ejecuta
2. Build trie para ALICE → query "context_guard" → debe retornar memorias relevantes
3. Simular CRITICAL → verificar reinicio limpio y warm-up post-boot
4. Medir: tamaño de contexto cargado en boot CON trie vs SIN trie → debe reducir >80%

---

## Estimación de Impacto

- **Crash por output masivo:** eliminado con regla de comportamiento (inmediato, 0 tokens)
- **Crash por acumulación:** mitigado con distilación en WARNING antes de llegar a CRITICAL
- **Amnesia post-crash:** eliminada con distilación + trie warm-up post-boot
- **Costo de contexto en boot:** reducción estimada 80-90% (3-5 memorias vs JSONL completo)
- **Tokens adicionales en operación:** ~0 (todo en Python puro vía systemd)

---

*"guardar solo los momentos importantes, usar lógica de árbol de prefijos" — William, 15 abril 2026, 12:51 Lima*
