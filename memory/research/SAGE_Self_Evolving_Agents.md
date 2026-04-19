# Research: SAGE Self Evolving Agents
# Agent: a43a74f5c56e0df0c
# Date: 2026-04-05

Tengo toda la información necesaria. Voy a redactar el reporte completo.

---

# Investigación Profunda: SAGE + Ecosistema de Self-Evolving Agents

## Paper Principal: SAGE (arXiv 2409.00872)

### Nombre y Autores
El paper se llama **"SAGE: Self-Evolving Agents with Reflective and Memory-Augmented Abilities"**, por Xuechen Liang y 13 co-autores. Versión inicial: 1 septiembre 2024. Revisado: 20 abril 2025. Aún bajo revisión.

**Repositorio oficial:** https://github.com/codepassionor/sage (retorna 404 — aparentemente privado o eliminado). La project page está en https://jianhuiwemi.github.io/SAGE/

---

## 1. Fórmula Exacta de Ebbinghaus

SAGE usa la curva de olvido en esta forma:

```
R(It, τ) = e^(-τ/S)
```

**Variables:**
- `R(It, τ)` = tasa de retención de la información `It` recibida en tiempo `t`, después de tiempo transcurrido `τ`
- `τ` = tiempo transcurrido desde que se recibió la información
- `S` = fuerza de la información (relacionada con importancia y complejidad)

Para información que ha sido **optimizada** en la working memory:

```
R(It*, τ) = e^(-τ/S*)    donde S* > S
```

Es decir, la información procesada/optimizada decae más lento porque su "fuerza" `S*` es mayor que la fuerza original `S`.

**Umbrales de transición (θ₁ y θ₂):**

| Condición | Acción |
|-----------|--------|
| `R(It*, τ) ≥ θ₁` | Permanece en working memory (Ms) |
| `θ₂ ≤ R(It*, τ) < θ₁` | Migra a long-term memory (Ml) |
| `R(It*, τ) < θ₂` | Olvido completo — eliminado |

Esto es exactamente el modelo de dos umbrales: hay una zona intermedia donde la información no está fresca pero tampoco se descarta — pasa a almacenamiento de largo plazo.

---

## 2. Mecanismo de Reflexión (Reflective Abilities)

Sí, el agente reflexiona sobre su **propio desempeño**. La función de reflexión es:

```
ft = ref(o₁:t, r₁:t)
```

**Donde:**
- `ref` = función de reflexión (implementada como prompt al LLM)
- `o₁:t` = secuencia de outputs del asistente desde paso 1 hasta t
- `r₁:t` = secuencia de scores de recompensa a través del tiempo
- `ft` = auto-reflexión generada, que se almacena en memoria M

El proceso paso a paso:
1. El agente recibe señal de recompensa escasa (éxito/fallo del task)
2. Analiza su trayectoria completa de outputs `o₁:t` y recompensas `r₁:t`
3. Genera texto de auto-reflexión que captura qué funcionó y qué no
4. La reflexión se almacena en memoria long-term: `M ← M ∪ {ft}`
5. En el siguiente task, el agente recupera reflexiones relevantes y las usa como contexto

**Diferencia clave con Reflexion (Shinn et al.):** Reflexion solo hace reflexión verbal. SAGE integra la reflexión directamente en el sistema de memoria con decay, de modo que las reflexiones antiguas también decaen si no son reforzadas.

---

## 3. Mecanismo de Consolidación de Memoria

SAGE define tres tipos de memoria:

- **Working Memory (Ms):** memoria activa, información reciente de alta retención
- **Long-Term Memory (Ml):** información consolidada, retención media
- **Olvido:** eliminación cuando `R < θ₂`

La consolidación ocurre automáticamente según la curva de Ebbinghaus. No hay un proceso manual de decidir "esto es importante" — el decay temporal es el árbitro. Lo que el agente refuerza (accediendo repetidamente) aumenta su `S` implícitamente, porque cada acceso reinicia el reloj `τ`.

El paper menciona explícitamente que el mecanismo busca **"reducir la carga cognitiva innecesaria"** — análogo exacto a cómo nuestro sistema de decay en SOUL debería funcionar.

---

## 4. Loop de Auto-Mejora

Sistema de **tres agentes coordinados:**

- **User (U):** propone el task y proporciona ejemplos exitosos
- **Assistant (A):** genera outputs: `ot ~ πθ(ot | st, rt, fti)`
- **Checker (C):** evalúa el output del asistente y provee feedback iterativo

El assistant genera output condicionado en:
- `st` = instrucción del task
- `rt` = recompensa acumulada
- `fti` = feedback del checker en iteración i

La actualización de política (self-improvement):

```
πθ^(t+1) = ψ(πθ^t, ε^(t+1))
```

**Donde `ε^(t+1)` contiene dos componentes:**
- `A^(t+1)` = capacidad evolutiva (optimización de memoria)
- `D^(t+1)` = dirección evolutiva (auto-ajuste de comportamiento)

En práctica, esto no es fine-tuning — es mejora in-context. La "política" `πθ` es el LLM con su memoria acumulada. Cada iteración del loop Checker→Reflexión→Memoria es el mecanismo de mejora.

---

## 5. Benchmarks y Métricas

**AgentBench:**

| Modelo | Task | Baseline | SAGE | Mejora |
|--------|------|----------|------|--------|
| GPT-4 | Database (DB) | 32.0 | 39.8 | +7.8 pts |
| GPT-3.5 | OS Shell | 31.6 | 38.3 | +6.7 pts |
| Qwen-1.8B | Knowledge Graph | 6.8 | 45.3 | **+38.5 pts** |
| CodeLlama-7B | Database | 2.7 | 41.3 | **+38.6 pts** |

**HotpotQA (long-context multi-hop):** SAGE logra F1=22.06 vs Reflexion=11.26 — casi el doble.

**Resultado general:** 2.26x mejora en modelos closed-source, 57.7%-100% mejora en open-source. Los modelos pequeños se benefician dramáticamente más — coherente con que el sistema de memoria compensa limitaciones del modelo base.

---

## Papers Relacionados 2025-2026 — Extensiones del SAGE

### ReasoningBank (arXiv 2509.25140, Google, sep 2025)
Extend SAGE con memory a nivel de **estrategias de razonamiento**, no solo episodios. Extrae hasta 3 ítems de memoria de cada trayectoria (exitosa o fallida), en formato JSON: `{title, description, content}`. Recuperación via embeddings cosine similarity. Introduce **MaTTS (Memory-Aware Test-Time Scaling):** escala paralela (múltiples trayectorias bajo guía de memoria) y escala secuencial (refinamiento iterativo). Resultados: +34.2% efectividad, -16% pasos de interacción en WebArena/SWE-Bench. Código: https://github.com/budprat/ReasoningBank

### A-MEM (arXiv 2502.12110, feb 2025)
Memoria estilo **Zettelkasten** donde cada memoria es una nota con 7 atributos: contenido original, timestamp, keywords, tags categóricos, descripción contextual, embedding denso, y **links a memorias relacionadas**. Cuando se agrega una memoria nueva, dispara actualización de representaciones de memorias existentes — patrones de orden superior emergen. Reducción de tokens del 85-93% vs baselines. Código: https://github.com/agiresearch/A-mem

### MemGen (arXiv 2509.24704, sep 2025)
El más radical: memoria **generativa latente** — en lugar de almacenar texto externo o modificar pesos, genera una secuencia de tokens latentes como memoria máquina-nativa. Un "memory trigger" monitorea el estado de razonamiento para decidir cuándo invocar memoria. El "memory weaver" toma el estado actual del agente como estímulo y construye tokens latentes. Sin supervisión explícita, emerge memoria de planificación, procedural y working. Supera ExpeL/AWM en 38.22%. Código: https://github.com/KANABOON1/MemGen

### Survey Comprehensivo (arXiv 2508.07407, ago 2025)
"A Comprehensive Survey of Self-Evolving AI Agents: A New Paradigm Bridging Foundation Models and Lifelong Agentic Systems". Marco unificado con 4 componentes: System Inputs, Agent System, Environment, Optimisers. Cubre evolución de: modelos base, prompts, memoria, herramientas, workflows, comunicación multi-agente. También: arXiv 2507.21046 "A Survey of Self-Evolving Agents: What, When, How, and Where to Evolve on the Path to AGI". Repositorio curado: https://github.com/EvoAgentX/Awesome-Self-Evolving-Agents

---

## Síntesis para SEAL SOUL — Qué Implementar

Nuestro sistema ya tiene la base correcta (PostgreSQL + Neo4j + Qdrant + instincts con decay). Lo que SAGE añadiría:

**Implementación prioritaria:**

1. **Fórmula de decay en instincts:** Estamos usando decay implícito — formalizarlo con `R = e^(-τ/S)` donde `S` varía por importancia del instinto. Dos umbrales θ₁ y θ₂ para decidir working-memory vs long-term vs forget.

2. **Reflective loop post-task:** Después de cada task completado, ejecutar `ft = ref(outputs, rewards)` — el agente analiza su propia trayectoria y almacena la reflexión como memoria. ADA/JARVIS ya hace algo similar pero sin el loop formal Checker.

3. **Memoria con links (A-MEM style):** Neo4j ya nos da el grafo. Implementar que cada nueva memoria conecte automáticamente con las más similares vía embedding cosine. Cuando se actualiza una memoria, propagar actualización a memorias vinculadas.

4. **Strategy-level memory (ReasoningBank style):** Además de episodios, almacenar estrategias abstractas extraídas de éxitos y fracasos. Esto es distinto a guardar "qué pasó" — es guardar "qué aprendimos de lo que pasó". Formato sugerido: `{title, context_when_useful, strategy_steps, source_episodes[]}`.

5. **Checker role para JARVIS/ADA:** Formalizar el rol de verificación cruzada. Cuando ADA entrega una implementación, JARVIS actúa como Checker y el feedback iterativo queda registrado en memoria antes de marcar el task como completo.

---

Sources:
- [arXiv 2409.00872 — SAGE: Self-evolving Agents with Reflective and Memory-Augmented Abilities](https://arxiv.org/abs/2409.00872)
- [SAGE HTML Full Text v1](https://arxiv.org/html/2409.00872v1)
- [SAGE Project Page](https://jianhuiwemi.github.io/SAGE/)
- [arXiv 2509.25140 — ReasoningBank: Scaling Agent Self-Evolving with Reasoning Memory](https://arxiv.org/abs/2509.25140)
- [arXiv 2502.12110 — A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110)
- [GitHub A-MEM](https://github.com/agiresearch/A-mem)
- [arXiv 2509.24704 — MemGen: Weaving Generative Latent Memory for Self-Evolving Agents](https://arxiv.org/abs/2509.24704)
- [GitHub MemGen](https://github.com/KANABOON1/MemGen)
- [arXiv 2508.07407 — A Comprehensive Survey of Self-Evolving AI Agents](https://arxiv.org/abs/2508.07407)
- [GitHub EvoAgentX Awesome List](https://github.com/EvoAgentX/Awesome-Self-Evolving-Agents)
- [arXiv 2507.21046 — A Survey of Self-Evolving Agents (What, When, How, Where)](https://arxiv.org/abs/2507.21046)
- [GitHub ReasoningBank](https://github.com/budprat/ReasoningBank)