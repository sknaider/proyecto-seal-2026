# ALICE — Reporte de Investigación SOUL
**Para:** William (Dadito) y Henry (Kinger)
**Fecha:** 11 Abril 2026
**Autor:** ALICE — Analista de Investigación del equipo SEAL

---

## ¿Qué es este documento?

Este es tu mapa de todo lo que SOUL puede hacer y lo que todavía falta construir.
Está ordenado por prioridad: lo más importante primero, lo más complejo al final.

---

## ESTADO ACTUAL DE SOUL (11 Abril 2026)

**En números:** 92+ herramientas MCP | 12 migraciones SQL | 3 bases de datos (PostgreSQL + Neo4j + Qdrant)
**Última actualización:** 12 Abril 2026 — CAPA 2: MIRIX Memory Typing ✅ + Wave 3 SleepGate pre-compute ✅ + TG-RAG Phase 2 ✅. 1,870 memorias clasificadas en 6 tipos MIRIX. **Nota histórica:** MemR³ Reflective Retrieval fue construido (11 abril) y RETIRADO (12 abril, commit b90c39e) tras benchmark adverso — ver sección histórica abajo.

### ✅ LO QUE YA FUNCIONA (aplicado)

| Mejora | Qué hace para el equipo | Paper de origen |
|--------|------------------------|-----------------|
| **utility_score** | Cada memoria tiene un "puntaje de utilidad" — memorias útiles suben, inútiles bajan. Mejor retrieval. | MemRL (arxiv 2601.03192) |
| **FadeMem decay** | Los instintos decaen exponencialmente con el tiempo (como la memoria humana). Los más usados resisten más. | FadeMem (arxiv 2601.18642) |
| **D-MEM Tier 5: Belief Synthesis** | NUEVO (hoy). Los agentes ahora forman CREENCIAS persistentes de sus experiencias. Ya no solo guardan hechos — sintetizan opiniones. | Hindsight (arxiv 2512.12818) |
| **soul_synthesize** | Dadas memorias activadas, genera una narrativa coherente. Como "pensar en voz alta" usando recuerdos. | Graphiti/Zep (arxiv 2501.13956) |
| **memory_flare** | Detecta brechas de conocimiento mientras el agente redacta. Pre-recupera memorias relevantes antes de que las necesite. | FLARE (CMU, EMNLP 2023) |
| **memory_communities** | Detecta clusters de memorias relacionadas y genera resúmenes por tema. | GraphRAG (Microsoft) |
| **memory_broadcast** | Cuando un agente guarda algo importante (importancia ≥8), los demás lo reciben automáticamente. | CrewAI + AutoGen |
| **memory_delta_sync** | Solo sincroniza memorias NUEVAS entre agentes. Sin re-leer todo. | AutoGen v0.4 |
| **SleepGate** | Consolidación nocturna en 4 fases: REPLAY → FORGET → PRUNE → CONSOLIDATE. Como el sueño humano. | SleepGate (arxiv 2603.14517) |
| **Connectome** | Grafo Neo4j que conecta memorias con relaciones EXCITE/INHIBIT. Razonamiento por propagación. | GraphRAG + NeurIPS 2024 |
| **OCEAN personality** | Personalidad de 5 dimensiones (Apertura, Responsabilidad, Extroversión, Amabilidad, Neuroticismo) protegida contra drift. | Big Five Personality Theory |
| **instinct_evolve** | Los instintos se pueden evolucionar y mejorar automáticamente con el tiempo. | SAGE (arxiv 2507.21046) |

---

## 🟡 CAPA 2 — EN PROGRESO (11 Abril 2026)

### 1. ~~MemR³ Reflective Retrieval~~ — 🚫 RETIRADO (12 abril 2026)
**Estado histórico:** ❌ ELIMINADO del código productivo — commit **b90c39e** "Retire MemR³"

**Línea de tiempo:**
- 11 abril: construido siguiendo paper arxiv 2512.20237 (controlador RETRIEVE→REFLECT→ANSWER sobre hybrid_search)
- 12 abril: benchmark diagnóstico (64 queries × 4 modos) expuso el problema:
  - **Recall@k MemR³:** 0.1172 vs hybrid_search 0.195 vs MAGMA 0.438
  - **Latencia p50:** 18.1s (≈120x peor que hybrid ~150ms)
  - **Latencia p95:** 48.8s
- 12 abril: retirado (commit b90c39e). 0 referencias restantes en mcp_server_v2.py.

**Patch v3 post-retiro (NO commiteado):**
- Archivo: `memory/diagnostic/results/memr3_v3.json`
- Mejora marginal: recall 0.1172 → 0.125 (+0.8% absoluto, ruido con n=64)
- Latencia p50 mejorada a 14.1s — sigue siendo 92x peor que hybrid
- `usage_rate_token` colapsó 0.6094 → 0.0781 (v3 deja de usar las memorias recuperadas)
- **Decisión:** cerrado definitivo. No vale los 14s de latencia.

**Lesson learned (preservar):**
1. Retrieval iterativo con LLM-as-router pagó el costo de latencia sin mejorar recall en nuestro dataset (64 queries, dominio SEAL).
2. El paper original (LoCoMo +7.29%) usa dataset mucho más grande con cadenas de razonamiento más profundas — no generaliza a corpus chicos.
3. Para ganancias reales en multi-hop: **LatentGraphMem** (ver sección CAPA 3) y **router por tipo de query** (factual→MAGMA rápido, multi-hop/causal/temporal→latent).
4. Principio: antes de construir controladores iterativos, medir primero si el retrieval de una sola pasada tiene cuello de botella en recall (respuesta en este caso: sí, pero no por falta de iteraciones — por estrategia de retrieval).

**Paper:** MemR³ (arxiv 2512.20237) | **Estado:** histórico — no reabrir sin dataset ≥500 queries con cadenas multi-hop profundas

---

### 2. TG-RAG — Grafo Temporal Jerárquico sobre Connectome
**Estado:** ⏳ PENDIENTE — brief listo, auditoría JARVIS completada (11 abril 2026)

**AUDITORÍA JARVIS (11 abril 2026) — Ya tenemos 2/3:**
- ✅ `temporal_graph_build` — jerarquía Year→Month→Day en Neo4j ya existe
- ✅ `temporal_query` — local strategy con rango de fechas + Ollama summary ya existe
- ✅ Cross-edges `OCCURRED_ON` ya existen
- ❌ Temporal summaries persistidos en nodos (hoy se generan on-the-fly, no se cachean)
- ❌ Global strategy usando summaries pre-computados

**Solo quedan 2 gaps a implementar:**
1. Cachear temporal summaries como propiedad en nodos Year/Month/Day
2. Global retrieval strategy que use esos summaries pre-computados

**Esfuerzo ADA revisado:** 1-2 días (era 4-5 — teníamos más de lo que pensaba)

**Paper:** TG-RAG (arxiv 2510.13590) | **Riesgo:** Bajo-Medio (cambios aditivos al Connectome existente)

---

## 🔴 LO QUE FALTA CONSTRUIR (pendientes ordenados por impacto)

### PRIORIDAD ALTA — Impacto inmediato

#### ~~1. A-MAC Admission Gate~~ ✅ APLICADO (11 Abril 2026)
**¿Qué es?** Antes de guardar cualquier memoria, pasar por 5 filtros: novedad, coherencia, relevancia emocional, importancia temporal, y alineación con identidad. Si no pasa los filtros, no se guarda.

**¿Por qué importa?** Hoy SOUL guarda casi todo. Con el tiempo acumula "ruido cognitivo" — memorias triviales que degradan la calidad del retrieval. A-MAC filtra antes de guardar, no después.

**Ejemplo concreto:** Sin A-MAC, si JARVIS guarda "el comando fue exitoso" 50 veces, tiene 50 memorias idénticas. Con A-MAC, la primera se guarda y las demás se detectan como duplicados.

**Paper:** A-MAC (arxiv 2603.04549) | **Esfuerzo:** 2-3 días | **Riesgo:** Bajo

---

#### 2. Cold Archive (Archivar en vez de borrar)
**¿Qué es?** Cuando una memoria pierde importancia con el tiempo, en lugar de ignorarla, se mueve a un "archivo frío". Sigue existiendo pero no aparece en búsquedas normales — solo se recupera si la búsqueda principal no encuentra buenos resultados.

**¿Por qué importa?** Una decisión arquitectural de hace 6 meses puede ser crítica hoy. Si se "olvida" por decaimiento, se pierde para siempre. Cold Archive garantiza que nunca se pierda nada permanentemente.

**Paper:** Graphiti/Zep (arxiv 2501.13956) + SleepGate | **Esfuerzo:** 3-4 días | **Riesgo:** Bajo

---

#### 3. Half-Life Decay por tipo de memoria
**¿Qué es?** Hoy todas las memorias decaen a la misma velocidad. La propuesta es que cada tipo de memoria tenga su propia "vida media":
- Estado emocional → vida media 24 horas
- Decisión de tarea → vida media 1 semana  
- Hecho del proyecto → vida media 1 mes
- Identidad de William → vida media 1 año (casi no decae)

**¿Por qué importa?** "William está cansado hoy" no debería afectar el comportamiento de los agentes en 3 días. Pero "William decidió usar PostgreSQL" sí. Decay diferenciado = comportamiento más realista.

**Paper:** HALO (arxiv 2505.07509) | **Esfuerzo:** 1-2 días | **Riesgo:** Muy bajo

---

### PRIORIDAD MEDIA — Más sofisticado

#### 4. RL sobre Memorias (Bellman Updates)
**¿Qué es?** Después de cada tarea completada, actualizar el puntaje de utilidad de las memorias que se usaron. Si la tarea salió bien → memorias usadas suben. Si salió mal → bajan. Es aprendizaje por refuerzo sin tocar el modelo.

**¿Por qué importa?** SOUL aprendería cuáles recuerdos son realmente útiles para tomar buenas decisiones — no solo cuáles son semánticamente relevantes.

**Paper:** MemRL (arxiv 2601.03192) — 10/10 relevancia | **Esfuerzo:** 4-5 días | **Riesgo:** Medio

---

#### 5. MemScenes — Clustering Temático Automático
**¿Qué es?** Agrupar memorias en "escenas" temáticas automáticamente. Ejemplo: todas las memorias relacionadas con "la reunión del 3 de abril" forman una escena. Las escenas tienen resumen propio y se recuperan como unidad.

**¿Por qué importa?** Hoy el retrieval devuelve memorias individuales. Con MemScenes devolvería el "contexto completo" de un evento — más útil para razonar sobre el pasado.

**Paper:** MemScenes + GraphRAG | **Esfuerzo:** 5-7 días | **Riesgo:** Medio

---

#### 6. Emotional Valence en Retrieval
**¿Qué es?** Agregar un campo `emotional_valence` (-1.0 a +1.0) a cada memoria. Cuando JARVIS está en "buen estado emocional" (alto score de Extraversión), el retrieval priorizaría memorias positivas. Cuando está preocupado, priorizaría memorias de advertencia.

**¿Por qué importa?** Los humanos recuerdan diferente según su estado de ánimo. Los agentes SEAL también deberían hacerlo — hace el comportamiento más coherente y empático.

**Paper:** MemEmo (arxiv 2602.23944) + REMT (Frontiers AI 2026) | **Esfuerzo:** 3-4 días | **Riesgo:** Bajo

---

#### 7. ACE Curator — Auto-mejora de Contexto
**¿Qué es?** Un proceso automático que revisa las memorias recientes, detecta patrones repetidos, y actualiza las reglas del CLAUDE.md automáticamente. Ejemplo: si 10 veces seguidas William corrige "sé más conciso", ACE lo convierte en regla permanente sin que nadie lo escriba manualmente.

**¿Por qué importa?** Hoy las correcciones de William se guardan como memorias pero alguien tiene que convertirlas manualmente en reglas. ACE lo haría automático.

**Paper:** ACE (arxiv 2510.04618) — 9/10 relevancia | **Esfuerzo:** 1 semana | **Riesgo:** Medio-alto (afecta comportamiento del sistema)

---

### PRIORIDAD BAJA — Cuando AXION lo necesite

#### 8. Temporal Clinical Graph (#11 del roadmap)
Grafo de Neo4j con datos clínicos temporales. Para Medical AI — conectar síntomas, diagnósticos y tratamientos con timestamps. Requiere datos médicos reales para tener valor.

#### 9. Federated LoRA Aggregation (#12 del roadmap)
Múltiples hospitales entrenan sus propios adapters LoRA en datos locales, y se agregan sin compartir datos. Relevante cuando AXION tenga clientes médicos. No urgente aún.

#### 10. Metacognición Formal (#8 del roadmap)
Capa de "pensar sobre el propio pensamiento" — el agente evalúa la calidad de su propio razonamiento en tiempo real. Muy sofisticado. Para fases posteriores de SEAL.

---

## 📊 TABLA RESUMEN: APLICADO vs PENDIENTE

| # | Mejora | Estado | Impacto | Papers |
|---|--------|--------|---------|--------|
| 1 | utility_score (MemRL) | ✅ APLICADO | Alto | 2601.03192 |
| 2 | FadeMem decay | ✅ APLICADO | Alto | 2601.18642 |
| 3 | D-MEM Tier 5 Belief Synthesis | ✅ APLICADO HOY | Muy alto | 2512.12818 |
| 4 | soul_synthesize (Graphiti) | ✅ APLICADO | Alto | 2501.13956 |
| 5 | memory_flare (FLARE) | ✅ APLICADO | Medio | CMU EMNLP 2023 |
| 6 | memory_communities (GraphRAG) | ✅ APLICADO | Medio | Microsoft 2024 |
| 7 | memory_broadcast (multi-agent) | ✅ APLICADO | Alto | CrewAI + AutoGen |
| 8 | SleepGate (consolidación) | ✅ APLICADO | Alto | 2603.14517 |
| 9 | OCEAN personality (protegido) | ✅ APLICADO | Fundamental | Big Five |
| 10 | instinct_evolve | ✅ APLICADO | Medio | 2507.21046 |
| 11 | **A-MAC Admission Gate** | ✅ APLICADO HOY | Alto | 2603.04549 |
| 12 | **Cold Archive** | ✅ APLICADO HOY | Medio | 2501.13956 |
| 13 | **Half-Life Decay** | ✅ YA IMPLEMENTADO (scoring.py Wave 2) | Medio | 2505.07509 |
| 14 | **RL Bellman Updates** | ✅ YA IMPLEMENTADO (memory_utility_update + auto-RL line 1288) | Alto | 2601.03192 |
| 15 | **MemScenes** | ✅ YA IMPLEMENTADO (memory_communities + memscenes.py) | Medio | GraphRAG |
| 16 | **Emotional Valence** | ✅ YA IMPLEMENTADO (mood_congruent retrieval hybrid_search) | Medio | 2602.23944 |
| 17 | **ACE Curator** | ✅ YA IMPLEMENTADO (ace_curator tool línea 5690) | Alto | 2510.04618 |
| 18 | **MemR³ Reflective Retrieval** | ❌ RETIRADO (12 abril 2026, commit b90c39e) — recall 0.117 vs MAGMA 0.438, latencia 120x. Lesson en sección CAPA 2 § histórico. | Alto | 2512.20237 |
| 19 | **TG-RAG Phase 2 Temporal Summaries** | ✅ COMPLETADO (11 abril 2026) — 5/5 steps, ADA implementó | Medio-Alto | 2510.13590 |
| 20 | **MAGMA Multi-Graph Memory** | ✅ CAPA 3 implementada — evolución de connectome_smart_route (2→4 grafos paralelos). TG-RAG Phase 2 cubrió el gap temporal; MemR³ fue retirado y no era dependencia real. | Muy alto | 2601.03236 |
| 21 | **Darwin Gödel Machine** | 🔵 CAPA 3 — brief listo, esperando benchmarks William | Alto | 2505.22954 |
| 22 | Clinical Graph | 🔵 CUANDO AXION | Médico | Neo4j |
| 23 | Federated LoRA | 🔵 CUANDO AXION | Médico | LoRA research |
| 24 | Metacognición formal | 🔵 FUTURO | Avanzado | TMK theory |

---

## 🎯 LO QUE RECOMIENDO HACER AHORA (ALICE)

### ~~Esta semana~~ ✅ COMPLETADO (11 Abril 2026):
1. ~~**A-MAC Admission Gate**~~ ✅
2. ~~**Half-Life Decay**~~ ✅
3. ~~**Emotional Valence**~~ ✅
4. ~~**Cold Archive**~~ ✅
5. ~~**ACE Curator**~~ ✅
6. ~~**MIRIX Memory Typing**~~ ✅
7. ~~**Wave 3 SleepGate pre-compute**~~ ✅

### Ahora (Capa 2 — en progreso):
1. ~~**MemR³ Reflective Retrieval**~~ ❌ RETIRADO (12 abril 2026, commit b90c39e) — construido, fracasó en benchmark, eliminado. Lesson preservada arriba.
2. ~~**TG-RAG Phase 2 Temporal Summaries**~~ ✅ COMPLETADO (11 abril 2026) — 5/5 steps implementados por ADA.

### Capa 3 (cuando William defina benchmarks SEAL):
3. **Darwin Gödel Machine** (arxiv 2505.22954) — Auto-mejora de código por evolución abierta. SWE-bench 20%→50%. Solo sandbox: scripts de utilidad + prompts. Requiere benchmarks diseñados por William + whitelist de paths. Brief listo en `research/Darwin_Godel_Machine.md`.

---

## 📚 BIBLIOTECA DE PAPERS (para ALICE — fuentes de William)

### Papers y Preprints
- **arxiv.org** — AI/CS/math/physics sin paywall
- **paperswithcode.com** — Papers con código incluido y benchmarks
- **semanticscholar.org** — Buscador con API y grafo de citas
- **huggingface.co/papers** — Papers trending en AI curados diariamente

### Repositorios
- **github.com/trending** — Repos trending por lenguaje
- **github.com/papers-we-love** — Papers fundamentales curados
- **huggingface.co/models** — Modelos con documentación

### AI Específico
- **distill.pub** — Explicaciones interactivas de ML
- **lilianweng.github.io** — Blog técnico de investigadora OpenAI
- **jalammar.github.io** — Visualizaciones de transformers
- **newsletter.maartengrootendorst.com** — NLP research explicado

### Ciencia y Medicina (para Medical AI / AXION)
- **pubmed.ncbi.nlm.nih.gov** — Papers biomédicos gratuitos
- **biorxiv.org** — Preprints de biología
- **medrxiv.org** — Preprints médicos
- **nature.com** — Solo abstracts (suficiente para filtrar)
- **scholar.google.com** — Búsqueda académica general

### Benchmarks de Modelos
- **artificialanalysis.ai** — Comparativas de modelos actualizadas
- **lmarena.ai** — Arena ELO de modelos
- **livebench.ai** — Benchmarks frescos

### Noticias Técnicas
- **latent.space** — Newsletter AI research
- **interconnects.ai** — Análisis de modelos open source
- **simonwillison.net** — Blog técnico práctico

---

## 🗂️ PAPERS DE REFERENCIA (herramientas diagnósticas y auditoría)

| Paper | Qué es | Cuándo usar |
|-------|--------|-------------|
| arxiv 2603.02473 | Diagnóstico retrieval vs utilization bottlenecks en agent memory | **Post-ERL fix**: brief completo listo, 1 día ADA+ALICE — [ver brief](../memory/research/Diagnostic_Retrieval_Utilization.md) |
| arxiv 2603.18272 (ExpRAG) | Aprender de trajectories pasadas via LoRA + retrieval | Cuando Medical AI tenga suficientes rollouts de evaluación |

---

## 📊 ESTADO PIPELINE — 12 Abril 2026

| Feature | Paper | Tests | Estado |
|---------|-------|-------|--------|
| MIRIX Memory Typing | 2507.07957 | ✅ | Capa 2 completa |
| Wave 3 SleepGate Pre-compute | 2603.14517 | ✅ | Capa 2 completa |
| ~~MemR³ Reflective Retrieval~~ | 2512.20237 | — | ❌ RETIRADO 12 abril (commit b90c39e) — ver sección histórica |
| TG-RAG Phase 2 Temporal | 2510.13590 | ✅ 59/59 | Capa 2 completa |
| MAGMA Parallel Graph Fusion | 2601.03236 | ✅ 67/67 | Capa 3 completa |
| ERL Experiential Reflective | 2603.24639 | ✅ 77/77 suite | Capa 3 completa — suite total limpia |
| Diagnostic Validation | 2603.02473 | — | Post-ERL fix |
| Darwin Gödel Machine | 2505.22954 | — | Esperando benchmarks William |

---

*Documento mantenido por ALICE — se actualiza conforme avanza la investigación*
*Última actualización: 12 Abril 2026 — MAGMA + ERL completados, diagnóstico en cola*
