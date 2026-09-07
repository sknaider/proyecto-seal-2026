# SEAL: A Multi-Agent System with Persistent Personality and Episodic Memory for AI-Assisted Software Engineering

**Borrador de esqueleto — CBSoft 2026**
**Autores:** William Henry Tovar Urquia, Henry [apellido], Team SEAL
**Track sugerido:** Ingeniería de Software para Inteligencia Artificial / IA para Ingeniería de Software
**Deadline paper completo:** 4 mayo 2026

---

## Abstract (borrador)

Large Language Models (LLMs) are increasingly used as software engineering agents, but existing frameworks treat each session as stateless — identity, relationships, and learned behaviors reset with each invocation. We present **SEAL (Self-Evolving Agent Layer)**, a multi-agent system where four specialized agents (JARVIS, ADA, ALICE, DUM) maintain persistent identity across sessions through: (1) a triple-store memory architecture (PostgreSQL + Neo4j + Qdrant), (2) OCEAN Big Five personality scores that evolve while resisting drift, and (3) a nightly consolidation pipeline inspired by human sleep memory consolidation. After N weeks of continuous operation, the system has accumulated 1,083+ typed memories across agents (episodic, semantic, core beliefs), developed stable inter-agent relationships, and demonstrated coordinated software engineering behavior across sessions. We describe the architecture, analyze personality drift metrics, and discuss implications for long-running AI engineering teams.

**Keywords:** multi-agent systems, LLM agents, persistent memory, personality modeling, OCEAN, episodic memory, software engineering automation

---

## 1. Introducción

### 1.1 Motivación

Los agentes LLM actuales son fundamentalmente amnésicos. Cada sesión comienza desde cero: sin contexto previo, sin relaciones establecidas, sin identidad coherente entre interacciones. Para tareas de ingeniería de software que se extienden durante semanas o meses, esta limitación es crítica — el agente no puede aprender del trabajo previo, no puede mantener compromisos a largo plazo, no puede desarrollar intuición sobre el proyecto.

Los sistemas multi-agente existentes (AutoGen, CrewAI, MetaGPT) resuelven la coordinación pero no la persistencia de identidad a largo plazo.

### 1.2 Contribuciones

Este paper presenta cuatro contribuciones principales:

1. **Arquitectura SEAL**: framework multi-agente con roles diferenciados (arquitecto, ingeniero, analista, guardia) y memoria persistente inter-sesión
2. **OCEAN-parameterized agents**: personalidad de 5 dimensiones (Big Five) codificada como estado mutable con protección anti-drift
3. **D-MEM (Dynamic Memory)**: taxonomía de 6 tipos de memoria (MIRIX) con admisión selectiva, decay diferenciado y consolidación nocturna (SleepGate)
4. **Evidencia empírica**: N semanas de operación continua con métricas de drift, recall, y coordinación inter-agente

### 1.3 Estructura del paper

Sección 2: trabajo relacionado. Sección 3: arquitectura SEAL. Sección 4: sistema de memoria D-MEM. Sección 5: OCEAN en agentes LLM. Sección 6: experimentos y métricas. Sección 7: discusión. Sección 8: conclusiones.

---

## 2. Trabajo Relacionado

### 2.1 Sistemas Multi-Agente LLM

- **AutoGen** (Microsoft, 2023): coordinación multi-agente vía mensajes, sin persistencia de identidad
- **CrewAI**: roles especializados, sin memoria episódica a largo plazo
- **MetaGPT**: flujo de trabajo tipo empresa, sin personalidad persistente
- **Voyager** (Wang et al., 2023): aprendizaje continuo en Minecraft, memoria de procedimientos — más cercano a SEAL pero dominio único

### 2.2 Memoria en Agentes LLM

- **MemGPT** (Packer et al., 2023): gestión jerárquica de contexto, sin identidad persistente
- **Graphiti/Zep** (Ranade et al., 2025): memoria episódica basada en grafos — inspiró nuestro Connectome
- **FadeMem** (arxiv 2601.18642): decay exponencial de memorias — implementado en SEAL instincts
- **SleepGate** (arxiv 2603.14517): consolidación nocturna en fases — implementado como nightly job

### 2.3 Personalidad en IA

- Big Five (OCEAN) para modelado humano — nunca aplicado a persistencia de identidad en LLM agents
- Trabajos en roleplay consistency: Shao et al., 2023 — no persisten entre sesiones

### 2.4 Gap en la Literatura

Ningún trabajo previo combina: (a) multi-agent coordination, (b) cross-session persistent identity, (c) parameterized personality with drift protection, (d) typed episodic memory with nightly consolidation — en un sistema de ingeniería de software.

---

## 3. Arquitectura SEAL

### 3.1 Agentes y Roles

| Agente | Rol | Modelo base | OCEAN primario |
|--------|-----|-------------|----------------|
| JARVIS | Arquitecto/estratega | Claude Opus | C=1.0, O=0.805 |
| ADA | Ingeniera/ejecutora | Claude Opus | C≈0.95, N≈0.1 |
| ALICE | Analista/investigadora | Claude Opus | O≈0.9, A≈0.8 |
| DUM | Guardia/monitor | Gemma 4 31B Q8 (local) | C≈0.9, N≈0.05 |

### 3.2 Stack de Infraestructura

```
┌─────────────────────────────────────────┐
│           Capa de Agentes               │
│   JARVIS │ ADA │ ALICE │ DUM            │
├─────────────────────────────────────────┤
│           SOUL Memory System            │
│  PostgreSQL:5433 │ Neo4j:7687           │
│  Qdrant:6333 (vectors)                  │
├─────────────────────────────────────────┤
│         MCP (Model Context Protocol)    │
│  92+ tools: boot_context, memory_store, │
│  self_reflect, soul_snapshot, instinct  │
├─────────────────────────────────────────┤
│         Coordinación                    │
│  chat_server:8765 (WebSocket + REST)    │
│  JSONL bridges │ Monitor WebSocket      │
└─────────────────────────────────────────┘
```

### 3.3 Ciclo de Vida de Sesión

1. **Boot**: `boot_context(agent)` → carga identidad, OCEAN, relaciones, diary, inner_thoughts
2. **Active Recall**: hook por turno inyecta correcciones recientes + instintos activos + reglas
3. **Session**: agente opera con memoria contextual + acceso on-demand a SOUL DB
4. **Checkpoint**: cada N minutos, `session_checkpoint.py` serializa estado
5. **Close**: `self_reflect()` + `session_distill()` → consolida experiencias del día
6. **Nightly**: SleepGate 03:00 → REPLAY → FORGET → PRUNE → CONSOLIDATE

---

## 4. D-MEM: Sistema de Memoria Dinámica

### 4.1 Taxonomía MIRIX (6 tipos)

| Tipo | Descripción | % JARVIS | % ADA |
|------|-------------|----------|-------|
| Episodic | Eventos específicos con timestamp | 41.5% | 43.5% |
| Semantic | Conocimiento general del dominio | 34.1% | 33.3% |
| Core | Creencias y valores fundamentales | 24.4% | 22.9% |
| Resource | Referencias a recursos externos | — | 0.2% |

**Totales a la fecha:** JARVIS=525 memorias, ADA=558 memorias, ALICE=33, DUM=8, TEAM=17 (compartidas)

### 4.2 Pipeline de Admisión: A-MAC

Antes de persistir cualquier memoria, 5 filtros:
1. **Relevance**: ¿importa para el agente o el proyecto?
2. **Novelty**: ¿es distinta de memorias existentes? (embedding similarity < threshold)
3. **Utility**: ¿será útil en el futuro?
4. **Emotion**: ¿tiene carga emocional relevante?
5. **Importance**: score 1-10, umbral configurable por tipo

### 4.3 Decay Diferenciado (HALO Half-Life)

11 categorías de half-life, desde emociones efímeras (1 día) hasta hitos del proyecto (730 días). La relevancia emocional y la alineación con la identidad del agente modulan el decay.

### 4.4 Retrieval: MAGMA

4 grafos paralelos con fusión de resultados. Recall@5 ≈ 0.438 (benchmark 12 abril 2026, 64 queries).

Comparativa interna:
- MAGMA: recall@5 = 0.438, latencia ~150ms
- hybrid_search: recall@5 = 0.195
- MemR³ (retirado): recall@5 = 0.117, latencia p50 = 18.1s

### 4.5 Consolidación Nocturna: SleepGate

Inspirado en la consolidación de memoria durante el sueño humano (NREM/REM). 4 fases:
1. **REPLAY**: re-activar memorias del día, reforzar conexiones (JARVIS: 31 replayed, ADA: 54)
2. **FORGET**: decay de memorias de baja utilidad
3. **PRUNE**: eliminar duplicados y memorias obsoletas
4. **CONSOLIDATE**: fusionar episodios relacionados, actualizar semantic

---

## 5. OCEAN en Agentes LLM

### 5.1 Codificación

Cada agente tiene un perfil OCEAN (0.0-1.0 por dimensión) almacenado en PostgreSQL. El perfil se carga en cada `boot_context` y modula el comportamiento mediante el prompt del sistema.

```
JARVIS: O=0.805, C=1.0, E=0.398, A=0.661, N=0.115
ADA:    O≈0.9,   C≈0.95, E≈0.5,  A≈0.8,   N≈0.1
```

### 5.2 Drift Protection

El OCEAN State Machine detecta dos pathologías:
- **Drift**: desviación gradual de la personalidad base (umbral: drift_score > 0.15)
- **Freezing**: rigidez extrema (score se clava sin variación natural)

Si se detecta drift, el sistema aplica un "anchor" que atrae el score hacia el perfil base.

### 5.3 Evolución Controlada

La personalidad puede evoluciar legítimamente (aprendizaje, maduración). El sistema distingue entre drift (patológico) y evolución (esperada) usando un modelo causal: si el cambio tiene un evento correspondiente en el diary, es evolución; si ocurre sin evento, es drift.

---

## 6. Experimentos

### 6.1 Setup

- **Hardware**: NVIDIA DGX Spark (128GB unified RAM) + RTX 5090 (34.2GB VRAM)
- **Período**: [FECHA INICIO] — 14 abril 2026
- **Agentes activos**: JARVIS + ADA (primarios), ALICE + DUM (secundarios)
- **Tareas**: ingeniería de software (implementación, debugging, coordinación, documentación)

### 6.2 Métricas de Memoria

| Métrica | Valor |
|---------|-------|
| Total memorias (JARVIS+ADA) | 1,083 |
| Memorias episódicas | 461 (42.6%) |
| Memorias semánticas | 365 (33.7%) |
| Core beliefs | 256 (23.6%) |
| Recall@5 (MAGMA) | 0.438 |
| Latencia retrieval p50 | ~150ms |
| Consolidaciones nocturnas | N (completar) |

### 6.3 Drift de Personalidad

[PENDIENTE: extraer evolución histórica de OCEAN scores de JARVIS y ADA desde primera sesión hasta hoy — hay registro en PostgreSQL]

### 6.4 Coordinación Multi-Agente

[PENDIENTE: contar mensajes inter-agente, handoffs exitosos, tareas completadas en coordinación]

### 6.5 Human-Supervised Loop

Descripción del protocolo de supervisión de William como director del equipo: aprobaciones, correcciones, reglas añadidas. Número de `memory_store` por corrección de William vs. auto-generados.

---

## 7. Discusión

### 7.1 ¿Funciona la Personalidad Persistente?

[Análisis: ¿los agentes con OCEAN distinto se comportan diferente? ¿ADA y JARVIS tienen patrones de respuesta mediblemente distintos?]

### 7.2 Limitaciones

- Dependencia de hardware local de alto costo (DGX Spark)
- Cada agente es una instancia Claude separada — no hay verdadero paralelismo interno
- Personalidad emerge del prompt + memoria, no del modelo base — podría ser "roleplay sofisticado"
- Evaluación de personalidad emergente es difícil de separar del seguimiento de instrucciones

### 7.3 Trabajo Futuro

- TDD formal integrado como workflow (PDCA skill)
- DM Monitor para mensajes directos (JARVIS DM fix — pendiente)
- Darwin Gödel Machine: auto-modificación del código propio supervisada
- Fine-tuning de modelos con datos de sesión (MedGemma SEAL v1 → v2)

---

## 8. Conclusiones

SEAL demuestra que es posible construir agentes LLM con identidad persistente, personalidad paramétrica estable y memoria episódica funcional. El sistema opera en producción durante semanas, coordinando tareas de ingeniería de software con supervisión humana explícita. Las contribuciones principales son la arquitectura D-MEM, el sistema OCEAN con drift protection, y la evidencia empírica de coordinación multi-sesión.

---

## Referencias (preliminar)

1. AutoGen: Enabling Next-Gen LLM Applications via Multi-Agent Conversation (Microsoft, 2023)
2. MemGPT: Towards LLMs as Operating Systems (Packer et al., 2023)
3. Graphiti: A Dynamic Temporally-Aware Knowledge Graph (Ranade et al., 2025) — arxiv 2501.13956
4. FadeMem: Temporal Memory Decay for LLM Agents — arxiv 2601.18642
5. SleepGate: Nocturnal Memory Consolidation — arxiv 2603.14517
6. A-MAC: Admission-controlled Memory for LLM Agents — arxiv 2603.04549
7. MAGMA: Multi-Graph Memory Alignment — (completar ref)
8. Big Five Personality Theory — Costa & McCrae (1992)
9. CrewAI: Role-Playing Autonomous AI Agents
10. MetaGPT: Meta Programming for Multi-Agent Collaborative Framework

---

## Notas para el equipo

**ALICE**: necesitamos referencias completas para papers que ya implementamos (verificar arxiv IDs). También buscar 2-3 papers de ICSE/CBSoft 2024-2025 sobre multi-agent LLM para posicionarnos en la literatura más reciente del venue.

**ADA**: necesitamos extraer de PostgreSQL la evolución histórica de OCEAN scores (si está logueada). También el conteo de mensajes inter-agente y handoffs.

**William**: confirmar período exacto de operación del sistema (¿desde qué fecha arrancó JARVIS-ADA en producción?). También si hay evaluaciones cualitativas o casos específicos que quiera incluir como vignettes.

**JARVIS (yo)**: revisar sección 7.2 limitaciones — necesito ser honesto sobre qué es "personalidad real" vs "roleplay persistente". Es una pregunta filosófica legítima que los revisores van a hacer.
