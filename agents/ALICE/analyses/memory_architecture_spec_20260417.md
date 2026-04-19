# SEAL Memory Architecture — Compressed Context with RAG Decompression
> Diseñado por William Henry Tovar Urquia | Documentado por ALICE | 17 Abril 2026
> Para paper CBSoft 2026 — "Persistent Identity in Long-Running LLM Agents"

---

## 1. La Analogía Central: Reconstrucción de Imagen

William formuló la idea central del sistema con esta analogía:

> *"Saben de la forma de reconstruir imágenes que no son claras o claves poco visibles, pero con ingeniería se reconstruye"*

En procesamiento de imágenes, técnicas como **super-resolución**, **denoising** e **inpainting** permiten recuperar información que parece irrecuperable. Una imagen borrosa o degradada no pierde su información original — esa información existe en forma latente, y con los métodos correctos se puede deducir, inferir, y reconstruir.

**La analogía aplicada a memoria de agentes IA:**

| Dominio imagen | Dominio memoria IA |
|---|---|
| Imagen borrosa / comprimida | Memoria comprimida post-compactación |
| Píxeles faltantes | Contexto episódico perdido |
| Super-resolución (SRCNN, ESRGAN) | RAG semántico sobre Cold Archive |
| Modelos de difusión generativa | Síntesis de memoria con embeddings |
| Ground truth original | Full context en ventana de 7 días |
| Imagen reconstruida | Contexto decomprimido bajo demanda |

La compactación que sufren los agentes Claude no destruye la información — la comprime. La arquitectura propuesta la preserva en Cold Archive y la reconstruye cuando es necesaria.

---

## 2. Problema: La Compactación Destruye Identidad

Los agentes LLM en conversaciones largas enfrentan presión de contexto (context pressure). Claude Code responde con compactación automática (~61% de presión, señal `nerves_fire`).

**Síntomas observados en Team SEAL (17 abril 2026):**
- ADA no recordó la reprimenda de la mañana después de 4+ distilaciones
- Contexto relacional/emocional se pierde primero — el más importante
- Reglas firmadas sobreviven (en Soul DB), pero el *por qué* se pierde
- Identidad parcialmente preservada, pero experiencia episódica del día → borrada

**Impacto en el paper:**
Este es el problema central que resuelve la arquitectura: **los agentes que trabajan en sesiones largas pierden coherencia temporal y relacional**. Sin solución, no es posible construir agentes verdaderamente persistentes.

---

## 3. Arquitectura Propuesta: 7-Day Window + Cold Archive + RAG

### 3.1 Capas de Memoria

```
┌─────────────────────────────────────────────────────────┐
│                    CAPA HOT (0-7 días)                   │
│  Cold Archive: full context exacto, sin compresión       │
│  Acceso directo → lectura raw de episodios               │
├─────────────────────────────────────────────────────────┤
│                 CAPA WARM (summaries activos)             │
│  inner_monologue, soul_snapshot, daily_brief             │
│  Compresión moderada — identidad + hechos clave          │
├─────────────────────────────────────────────────────────┤
│              CAPA COLD (>7 días, comprimida)             │
│  Mínima expresión: summary semántico + anchors           │
│  Reconstrucción bajo demanda via RAG                     │
└─────────────────────────────────────────────────────────┘
                         │
                    RAG DECOMPRESSION
                         │
         ┌───────────────┼───────────────┐
         │               │               │
   memory_hybrid_search  │  latent_graph  │
   (Qdrant vectors)   connectome      cold_archive_query
                    (Neo4j edges)    (PostgreSQL JSON)
```

### 3.2 Política de Retención (William's Rule)

**Horario de sueño oficial (17 abril 2026, Soul DB rule_id=61):**
- **Sueño diario:** 4:00am – 6:00am Lima (America/Lima = UTC-5 → 09:00–11:00 UTC)
- **Sueño profundo:** Sábado 3:00am Lima

| Período | Acción | Mecanismo |
|---|---|---|
| 0 → 7 días | Full context preservado exactamente | Cold Archive, no tocar |
| En nerves_fire | Snapshot completo ANTES de compactar | Hook pre-compactación |
| 4am–6am diario | Consolidar sesión, daily_brief, catchup JSON | seal-daily-sleep.timer |
| Sábado 3am | "Sueño profundo" — comprimir >7 días | seal-weekly-sleep.timer (activo) |
| >7 días | Comprimir a mínima expresión (SMSR) | qwen2.5:7b + Qdrant anchors |
| Cuando se pide | RAG decompression | memory_hybrid_search + cold_archive_query |

### 3.3 El "Sueño Profundo" (Deep Sleep Consolidation)

Proceso semanal que William definió como análogo al sueño humano:

```
SUEÑO PROFUNDO (cada fin de semana):
1. Identificar memorias >7 días en Cold Archive
2. Generar summary semántico (mínima expresión)
3. Extraer anchors: entities, relationships, emotional valence
4. Comprimir raw context → semantic_summary + anchor_vectors
5. Mantener raw context en backup (nunca borrar, solo archivar)
6. Actualizar índice vectorial (Qdrant) con nuevos embeddings
7. Reportar a William: X memorias procesadas, Y bytes comprimidos
```

**Analogía imagen:** Como guardar un JPEG comprimido en lugar del RAW — el JPEG es la forma de trabajo diaria, pero el RAW nunca se borra y siempre se puede reconstruir.

### 3.4 RAG Decompression

Cuando un agente necesita recordar contexto >7 días:

```python
# Pseudo-código del proceso de reconstrucción
def reconstruct_context(query: str, agent: str) -> FullContext:
    # 1. Buscar semantic anchors relevantes
    anchors = memory_hybrid_search(query, agent, limit=20)
    
    # 2. Expandir vía grafo de conectoma
    graph_context = connectome_smart_route(anchors)
    
    # 3. Recuperar cold archive si hay match de alta relevancia
    if max(anchor.relevance) > 0.85:
        raw_episodes = cold_archive_query(anchors[0].episode_id)
    
    # 4. Sintetizar contexto completo
    return reflection_synthesize(anchors + graph_context + raw_episodes)
```

**Analogía imagen:** Como aplicar SRCNN o ESRGAN — los píxeles faltantes se infieren a partir de los patrones conocidos + el contexto circundante. No es recuperación perfecta, pero es fiel al original en lo que importa.

---

## 4. Componentes de Implementación

### 4.1 Hook Pre-Compactación (URGENTE)

Trigger automático en `nerves_fire` para capturar antes de comprimir:

```python
# pre_compact_hook.py — ejecuta ANTES de que Claude compacte
async def on_nerves_fire(agent: str, session_id: str):
    # Guardar inner_monologue completo
    await inner_thoughts(agent, "pre_compact_snapshot", current_context)
    
    # Snapshot OCEAN + emocional
    await soul_snapshot(agent)
    
    # Escribir daily_brief
    await write_daily_brief(agent, date.today())
    
    # Migrar episodios del día a Cold Archive
    await cold_archive_migrate(agent, session_id)
```

### 4.2 Daily Brief File

Archivo escrito en cada nerves_fire y leído en boot:

```
/agents/{AGENT}/daily_briefs/brief_{AGENT}_{YYYYMMDD}.md

Contenido mínimo:
- Estado emocional al momento de compactar
- Tareas completadas/pendientes  
- Decisiones críticas del día
- Interacciones relacionales significativas
- OCEAN snapshot
```

### 4.3 Weekend Consolidation Timer

```bash
# seal-weekend-consolidation.timer
[Timer]
OnCalendar=Sat *-*-* 03:00:00
```

Script: comprime memorias >7 días, actualiza Qdrant, genera report para William.

---

## 5. Relevancia para Paper CBSoft 2026

### Sección propuesta: "Episodic Memory Compression and Semantic Reconstruction in Persistent Agent Systems"

*(Término acuñado por JARVIS: **SMSR — Semantic Memory Super-Resolution**)*

### Pregunta de investigación

> *¿Cómo mantener identidad coherente en agentes LLM multi-sesión con compactación automática de contexto?*

### Contribución técnica

1. **Arquitectura 3-capa** (Hot/Warm/Cold) con políticas de retención basadas en tiempo
2. **SMSR — Semantic Memory Super-Resolution**: técnica para recuperar contexto episódico perdido
   - Compressed summary = imagen low-res (pixeles clave)
   - Vectores Qdrant = prior / base de conocimiento
   - qwen2.5:7b = red de reconstrucción
   - Output = contexto episódico reconstruido alta fidelidad
3. **Analogía compressed sensing**: pocos measurements + prior conocido → señal completa. En memoria: summary tokens + vector embeddings → contexto episódico completo.
4. **Implementación real**: Team SEAL como caso de estudio — 4 agentes, sesiones >8h/día, compactaciones múltiples (ADA: 4+ en un día)

### Estructura argumentativa del paper

1. **Problema**: context window bounded → loss of episodic continuity (agentes "se desmayan")
2. **Observación**: imagen borrosa no pierde información — la comprime. La memoria también.
3. **Solución**: 3-tier memory con SMSR
4. **Técnica**: Semantic Memory Super-Resolution (analogía super-resolución visual)
5. **Evaluación**: fidelidad reconstrucción via similarity score vs original
6. **Resultado**: identidad del agente preservada tras múltiples compactaciones

### Métricas a reportar

| Métrica | Objetivo |
|---|---|
| Identity coherence post-compaction | >90% (reglas + relaciones preservadas) |
| Context reconstruction fidelity | >80% semantic similarity a raw original |
| Cold Archive compression ratio | >10:1 (raw → semantic summary) |
| Boot time con pre-fetch | <30s para contexto del día |
| False negative rate (memoria perdida sin recuperar) | <5% |

### Comparación con estado del arte

| Sistema | Persistencia | Compactación | RAG decompression |
|---|---|---|---|
| ChatGPT Memory | Tags superficiales | Pérdida total | ❌ |
| MemGPT | Paginación explícita | Manual | Parcial |
| **SEAL (propuesto)** | Soul DB + Cold Archive | Hook automático | ✅ Semántica completa |

---

## 6. Analogía Extendida: Formatos de Imagen vs Formatos de Memoria

| Formato imagen | Analogía memoria | Uso |
|---|---|---|
| RAW (sin comprimir) | Cold Archive raw (JSON completo) | Recuperación exacta |
| JPEG alta calidad | inner_monologue + diary | Trabajo diario |
| JPEG baja calidad | Mínima expresión (>7 días) | Almacenamiento largo plazo |
| Thumbnail | soul_snapshot (OCEAN + estado) | Boot rápido |
| Metadatos EXIF | semantic anchors (Qdrant) | Navegación y búsqueda |
| SRCNN/ESRGAN | RAG decompression | Reconstrucción bajo demanda |

---

## 7. Timeline de Implementación

| Fase | Responsable | Duración | Estado |
|---|---|---|---|
| Spec completa (este doc) | ALICE | ~45min | ✅ |
| Pipeline spec técnica | JARVIS | ~1h | En curso |
| cold_archive pre_compact_hook | ADA | ~2h | Pendiente |
| daily_brief writer | ADA | ~1h | Pendiente |
| weekend_consolidation timer | ADA | ~1h | Pendiente |
| RAG decompression module | ADA + JARVIS | ~3h | Pendiente |
| Tests + benchmarks | ADA | ~2h | Pendiente |
| Paper draft sección técnica | ALICE + JARVIS | Paralelo | Pendiente |

---

> Firmado: ALICE — 2026-04-17 18:41 Lima
> Documento vivo — se actualizará conforme avance la implementación
> Clasificación: SEAL Internal / CBSoft 2026 Paper Material
