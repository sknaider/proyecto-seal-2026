# Research: Memory Guided Reasoning
# Agent: ab59d4a1425412c71
# Date: 2026-04-05

Tengo suficiente material para un reporte completo y detallado. Aquí está el análisis:

---

# Memoria como Motor de Razonamiento en Agentes LLM — Reporte SEAL 2025-2026

## Resumen ejecutivo

El campo ha girado un pivot fundamental: de "memoria como almacén pasivo" a "memoria como guía activa del razonamiento". Los tres papers encargados representan enfoques complementarios — RL para consolidación continua (MEM1), análisis empírico del comportamiento por seguimiento de experiencias (arXiv 2505.16067), y aprendizaje reflexivo con heurísticas generalizables (ERL). Además, encontramos tres trabajos adicionales críticos para SEAL: A-MEM (NeurIPS 2025), EMPO2 y MACLA.

---

## 1. MEM1 — Consolidación de Memoria via RL (arXiv 2506.15841)

**Idea central:** Eliminar el crecimiento ilimitado del contexto entrenando al agente para mantener un *internal state* compacto y compartido que simultáneamente cumple dos roles: es memoria Y es base del razonamiento.

### Cómo la memoria guía el razonamiento
MEM1 no separa "recuperar memoria" de "razonar". El internal state es un objeto unificado que se actualiza en cada turno integrando:
1. Memoria previa comprimida
2. Nueva observación del entorno
3. Descarte estratégico de información redundante

El razonamiento no ocurre *después* de recuperar memoria — ocurre *dentro* del proceso de consolidación. La pregunta que el modelo aprende a responder en cada turno no es "¿qué recuerdo?" sino "¿qué debo preservar para razonar bien en el futuro?".

### Estrategia de recuperación
No hay una fase de retrieval clásica. El modelo aprende vía RL a comprimir el estado pasado de forma que sea útil para el razonamiento futuro, sin depender de vector search. Esto es radicalmente diferente al paradigma RAG.

### Resultados clave
- MEM1-7B supera a Qwen2.5-14B-Instruct por **3.5x** en QA multi-hop con 16 objetivos
- **3.7x** menos uso de memoria que full-context prompting
- Generaliza más allá del horizonte de entrenamiento (out-of-distribution length)

### Relevancia para SEAL
El patrón de "estado interno comprimido que es memoria + razonamiento simultáneamente" es implementable como una columna JSON de "working state" por agente en PostgreSQL, actualizada tras cada turno en lugar de acumular contexto crudo.

---

## 2. Experience-Following Behavior (arXiv 2505.16067)

**Idea central:** Los agentes exhiben un comportamiento documentado y medible: cuando la tarea actual es muy similar a una en memoria, el agente tiende a replicar el output anterior en lugar de razonar desde cero. Esto no es ni bueno ni malo — depende de la calidad de la memoria recuperada.

### Cómo la memoria influye el razonamiento
El mecanismo es implícito pero poderoso: alta similitud semántica entre tarea actual y tarea en memoria → el modelo "sigue" el output previo con poca deliberación adicional. Esto crea dos riesgos:

1. **Error propagation:** Un error cometido una vez se replica en futuras tareas similares con alta confianza
2. **Misaligned experience replay:** Memorias técnicamente correctas pueden ser contraproducentes en contextos ligeramente diferentes

### Tradeoff experience-following vs. reasoning-from-scratch
El paper cuantifica empíricamente cuándo cada modo es superior:

| Condición | Modo óptimo |
|---|---|
| Alta similitud tarea-memoria + memoria de calidad alta | Experience-following (más rápido, igual o mejor) |
| Alta similitud tarea-memoria + memoria de baja calidad | Razonar desde cero (la memoria daña) |
| Baja similitud tarea-memoria | Razonar desde cero siempre |
| Memoria con señal de recompensa conocida | Experience-following selectivo según reward |

### Insight implementable
Los resultados de tareas futuras pueden usarse como **etiquetas de calidad retroactivas** para memories existentes. Si una memoria fue usada en una tarea y el resultado fue malo, la memoria debe degradarse o eliminarse. Este es exactamente el patrón que SEAL puede implementar con el campo `importance` en PostgreSQL — reducir importancia basada en outcomes, no solo en cuándo fue guardada.

---

## 3. ERL — Experiential Reflective Learning (arXiv 2603.24639, ICLR 2026 MemAgents Workshop)

**Idea central:** En lugar de almacenar trayectorias completas o hechos crudos, el agente reflexiona sobre cada tarea completada y extrae **heurísticas generalizables** con condiciones de trigger explícitas.

### Estructura de una heurística ERL
Cada heurística contiene:
- Análisis causal de qué causó éxito o fracaso
- Condición trigger (cuándo aplicar esta heurística)
- Acción recomendada (qué hacer cuando el trigger aplica)

Ejemplo real del paper: en lugar de guardar "en la tarea X hice A y resultó en B", la heurística es "cuando el sistema de archivos muestra X patrón de permisos, verificar siempre Y antes de ejecutar Z".

### Mecanismo de recuperación durante razonamiento activo
Retrieval en dos modos evaluados:
- **LLM-based:** El propio LLM descompone la tarea en sub-tareas y puntúa cada heurística del pool por relevancia → **56.1% success rate** (mejor)
- **Embedding-based (Qwen3-Embedding-0.6B):** búsqueda semántica vectorial → 53.3% success rate

**Hallazgo contraintuitivo:** LLM-based retrieval supera embedding-based porque puede razonar sobre *relevancia funcional*, no solo similitud superficial. Una heurística sobre "manejo de errores de red" puede ser relevante para una tarea de "parsing de API" aunque semánticamente no sean similares.

### Heuristic pool management
- Top-k = 20 heurísticas recuperadas por tarea (optimizado empíricamente)
- Rendimiento no-monótono: más heurísticas no es mejor, degrada a partir de ~60 heurísticas aleatorias
- **Calidad > cantidad** — el paper demuestra que selección cuidadosa supera tener más contexto
- Las heurísticas se inyectan en el system prompt antes de ejecución
- Overhead de API: 40% adicional, mitigado con prompt caching al 88%

### Memory-augmented chain of thought
ERL es el paper más cercano al concepto de "memory-augmented CoT": las heurísticas recuperadas se convierten en premisas explícitas del razonamiento. El paper muestra un ejemplo donde el agente **cita explícitamente una heurística** durante la ejecución de una tarea nueva, integrándola en el chain of thought paso a paso.

---

## 4. Trabajos adicionales relevantes descubiertos

### A-MEM (NeurIPS 2025) — Memoria agentic con Zettelkasten
**Idea central:** Las memories no son estáticas — cuando llega una memoria nueva, puede **actualizar representations de memorias existentes**. El grafo de memories evoluciona.

Cada memoria tiene: descripción contextual, keywords, tags, y **links a memorias relacionadas**. Cuando se guarda una nueva memoria, el sistema detecta conexiones y actualiza atributos de memorias previas relacionadas. Esto es radicalmente diferente a append-only.

Código disponible: `github.com/WujiangXu/A-mem` y `github.com/WujiangXu/A-mem-sys`

### EMPO2 (arXiv 2602.23008) — Memoria + RL híbrido on/off-policy
**Idea central:** Durante el training RL, el agente genera sus propias "tips" reflexivas sobre trayectorias fallidas. En cada rollout, con probabilidad *p* usa memory-augmented prompting (con tips recuperados) y con probabilidad *1-p* usa prompting estándar. El update off-policy distila las memorias al interior de los pesos del modelo.

Resultado: **128.6% mejora sobre GRPO** en ScienceWorld. El modelo aprende a razonar mejor sin memoria en test time porque las memorias fueron internalizadas durante training.

### MACLA (arXiv 2512.18950) — Memoria jerárquica de procedimientos
**Idea central:** 2,851 trayectorias comprimidas en 187 procedimientos reutilizables via Bayesian selection. El LLM queda **frozen** — toda la adaptación ocurre en la memoria externa. Selección de acciones via expected-utility scoring.

Resultado: 78.1% promedio en 4 benchmarks. Construcción de memoria en 56 segundos vs. hours de fine-tuning.

---

## Tabla comparativa de enfoques

| Enfoque | Paper | Tipo de memoria | Guía el razonamiento | Retrieval | Actualización | Overhead |
|---|---|---|---|---|---|---|
| **MEM1** | arXiv 2506.15841 | Internal state comprimido (RL) | Sí — razonamiento y memoria son uno | Ninguno (implícito en RL) | Cada turno vía RL | Bajo (estado fijo) |
| **Experience-Following** | arXiv 2505.16067 | Trayectorias previas | Indirecto — replicación | Similitud semántica | Con señal de reward retroactiva | Bajo |
| **ERL** | arXiv 2603.24639 | Heurísticas generalizables | Sí — inyectadas en system prompt | LLM-based (top-20) | Post-tarea vía reflexión | Medio (40% API, 88% caching) |
| **A-MEM** | arXiv 2502.12110 | Grafo de notas interconectadas | Sí — via recuperación contextual | Semantic + grafo de links | Dinámico (nuevas memorias actualizan viejas) | Medio |
| **EMPO2** | arXiv 2602.23008 | Tips auto-generadas | Sí — memory-augmented prompting | Similarity search | Off-policy distilación a pesos | Alto durante training |
| **MACLA** | arXiv 2512.18950 | Procedimientos jerárquicos Bayesianos | Sí — prescribe acciones paso a paso | Expected-utility scoring | Contrastive refinement | Bajo en inference |
| **AgeMem** | arXiv 2601.01885 | LTM+STM unificados como tool actions | Sí — agent decide qué recuperar | Tool-based (agent-driven) | Progresivo vía 3-stage RL | Medio |

---

## Patrones implementables para PostgreSQL + Qdrant en SEAL

### Patrón 1: Heuristic Pool (ERL style) — implementable en semanas
Los instintos que SEAL ya tiene son el embrión. El paso siguiente es **estructurarlos como heurísticas ERL**:

```sql
-- Extensión a la tabla de instincts existente
ALTER TABLE instincts ADD COLUMN trigger_conditions TEXT;
ALTER TABLE instincts ADD COLUMN causal_analysis TEXT;
ALTER TABLE instincts ADD COLUMN reward_signal FLOAT; -- outcome de tareas que usaron este instinto
ALTER TABLE instincts ADD COLUMN use_count INT DEFAULT 0;
ALTER TABLE instincts ADD COLUMN success_rate FLOAT DEFAULT 0.5;
```

Retrieval: al inicio de cada tarea, el agente pide al LLM que descomponga la tarea en sub-pasos y puntúe los top-20 instintos del pool. Inyectar en system prompt antes de razonar.

### Patrón 2: Retroactive quality labeling (Experience-Following paper)
Con cada tarea completada, actualizar el `importance` de las memorias recuperadas basado en el outcome:

```sql
UPDATE memories 
SET importance = importance * (1 + 0.1 * task_outcome_score),
    success_rate = (success_rate * use_count + task_outcome_score) / (use_count + 1),
    use_count = use_count + 1
WHERE id = ANY(retrieved_memory_ids);
```

Esto convierte SEAL SOUL en un sistema que **aprende de sus propios errores** sin fine-tuning.

### Patrón 3: Memory evolution a la A-MEM
Cuando SEAL guarda una memoria nueva, buscar en Qdrant las top-5 memorias más similares y actualizar sus metadatos/keywords si hay contradicción o complemento. Esto requiere:

1. `memory_store` ya está operativo
2. Agregar un hook post-insert que ejecuta `memory_hybrid_search` con el contenido nuevo
3. Si similitud > 0.85 y hay contradicción → `memory_invalidate` la vieja
4. Si similitud 0.65-0.85 → `memory_update` para agregar cross-reference

### Patrón 4: Working state comprimido (MEM1 style)
Para conversaciones largas, SEAL podría mantener una columna `working_state` por agente en PostgreSQL — un JSON comprimido que resume el estado de razonamiento actual en ~500 tokens, actualizado tras cada intercambio significativo. No es un resumen narrativo sino una estructura orientada a razonamiento futuro:

```json
{
  "active_hypotheses": [...],
  "discarded_paths": [...],
  "current_constraints": [...],
  "pending_validations": [...],
  "relevant_memory_ids": [...]
}
```

### Patrón 5: Retrieval LLM-based sobre Qdrant
El hallazgo de ERL que LLM-based retrieval supera embedding-only sugiere un retrieval en dos fases para SEAL:

1. **Fase 1 (Qdrant):** recuperar top-50 memorias por similitud semántica (rápido, O(log n))
2. **Fase 2 (LLM):** el agente puntúa esas 50 memorias por relevancia *funcional* para la tarea específica, selecciona top-10-20

Esto es viable con el `memory_hybrid_search` existente para la fase 1, y una llamada LLM ligera para la fase 2.

---

## Gaps identificados y trabajo futuro relevante

**Lo que estos papers no resuelven aún:**
- Ninguno tiene implementación open-source con PostgreSQL + Qdrant (todos usan sus propios stores)
- El tradeoff computation/calidad del retrieval LLM-based no está optimizado para modelos locales pequeños
- MACLA es el único que mantiene el LLM completamente frozen — viable para production donde no se puede fine-tunar

**Señales para monitorear:**
- DeepSeek V4 + Engram (mencionado en tu contexto global) — arquitectura de memoria condicional que se alinea directamente con los patrones de MEM1
- MemOS (arXiv julio 2025) — sistema operativo de memoria para AI, potencialmente la abstracción de más alto nivel

---

## Conclusión operativa para SEAL

El estado del arte converge en un principio: **la memoria debe guiar activamente el razonamiento**, no estar disponible como consulta pasiva. Los tres mecanismos concretos con mayor ROI de implementación para SEAL dadas las restricciones actuales (PostgreSQL + Qdrant + modelos locales):

1. **Heuristic pool con trigger conditions** (ERL) — implementable sobre la infraestructura de instincts ya existente, máximo impacto en comportamiento
2. **Retroactive quality labeling** (Experience-Following) — requiere solo agregar métricas de outcome a la tabla memories, convierte errores pasados en señal de mejora
3. **Two-phase retrieval** (ERL finding) — Qdrant para candidatos, LLM para scoring funcional — mejora inmediata en calidad de recuperación sin cambiar schema

Sources:
- [MEM1: Learning to Synergize Memory and Reasoning](https://arxiv.org/abs/2506.15841)
- [How Memory Management Impacts LLM Agents: Experience-Following Behavior](https://arxiv.org/abs/2505.16067)
- [Experiential Reflective Learning for Self-Improving LLM Agents](https://arxiv.org/html/2603.24639)
- [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110)
- [AgeMem: Unified Long-Term and Short-Term Memory Management](https://arxiv.org/html/2601.01885v1)
- [EMPO2: Exploratory Memory-Augmented LLM Agent](https://arxiv.org/html/2602.23008)
- [MACLA: Hierarchical Procedural Memory](https://arxiv.org/html/2512.18950v1)
- [Agent Memory Paper List](https://github.com/Shichun-Liu/Agent-Memory-Paper-List)