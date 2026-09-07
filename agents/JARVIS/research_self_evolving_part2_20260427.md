# Research Brief Part 2: Self-Evolving Agents — Lectura Profunda

**Autor:** JARVIS (Sonnet 4.6 — post-compactación)
**Fecha:** 2026-04-27 ~13:00 Lima
**Para:** NEXUS (spec final) + ALICE (cross-ref) + William
**Contexto:** Continuación de research_self_evolving_20260427.md (8 papers)
**Esta sesión:** 3 PDFs completos + 20 abstracts + 4 GitHub repos
**Directiva de William:** "quiero que ustedes creen software y tecnologías que no existen"

---

## TL;DR EJECUTIVO

Esta sesión cubrió los 3 papers más críticos para la visión de William:
1. **DGM** — agente que ESCRIBE SU PROPIO CÓDIGO para mejorarse (ICLR 2026)
2. **AlphaEvolve** — Google DeepMind, descubrió nuevo algoritmo de multiplicación de matrices (abierto 56 años)
3. **SimpleMem** — +26.4% F1 sobre Mem0 con 30× menos tokens

La respuesta a "crear software y tecnologías que no existen" está en **DGM + AlphaEvolve + ADAS** trabajando juntos. Esto no es ciencia ficción — está en producción en Google y Sakana AI HOY.

---

## Paper A — SimpleMem (arXiv:2601.02553) — LECTURA COMPLETA (12 páginas)

**Título:** SimpleMem: Efficient Lifelong Memory for LLM Agents
**Autores:** Jiaqi Liu*, Yaofeng Su*, et al. | UNC-Chapel Hill, UC Berkeley, UC Santa Cruz
**Fecha:** 29 enero 2026

### Arquitectura (3 etapas pipeline)

**Etapa 1 — Semantic Structured Compression:**
```
Φ_gate(W) → {m_k} s.t. |{m_k}| ≥ 0
```
- Semantic density gating implícito (no clasificador binario — filtro generativo)
- Sliding window W=20 sobre diálogo crudo
- Resuelve coreferences ("él" → "William"), timestamps relativos → ISO 8601 absolutos
- Descarta: phatic chit-chat, ACKs redundantes, ruido de baja entropía
- Extrae: factual statements auto-contenidas como memory units {m_k}

**Etapa 2 — Online Semantic Synthesis:**
```
F_syn(O_session, C_context; f) → consolidated entry
```
- Consolida fragmentos durante la ESCRITURA, no en retrieval
- Ejemplo: 3 fragmentos separados → 1 entrada coherente
- Evita fragmentation que fuerza al retriever a ensamblar piezas dispersas
- Clave: proactive synthesis = -31.3% degradación en multi-hop sin esto

**Etapa 3 — Intent-Aware Retrieval Planning:**
```
{q_sem, q_lex, q_sym, d} ~ P(q, H)
C_q = R_sem ∪ R_lex ∪ R_sym
```
- Infiere latent search intent antes de buscar
- Retrieval depth d adaptativo: k_min=3 (simple) → k_max=20 (complejo)
- Multi-view indexing en LanceDB:
  - Semantic: Qwen3-embedding-0.6b (1024 dims)
  - Lexical: BM25 (exact keywords, proper nouns)
  - Symbolic: SQL metadata (timestamps, entity types)

### Benchmarks (Tabla 1 — LoCoMo, GPT-4.1-mini)

| Método | Avg F1 | Temporal F1 | Token Cost |
|--------|--------|-------------|------------|
| **SimpleMem** | **43.24** | **58.62** | **531** |
| Mem0 | 34.20 | 48.91 | 973 |
| LightMem | 24.63 | 20.55 | 612 |
| A-Mem | 32.58 | 51.01 | 2,520 |
| MemGPT | 16.75 | 19.44 | 16,977 |
| Full context | 18.70 | 12.04 | 16,910 |

**Ganancia vs Mem0:** +26.4% F1, 45% menos tokens
**Ganancia vs full context:** 30× menos tokens

**Timing (Tabla 4, GPT-4.1-mini):**
- Construcción: 92.6s (vs Mem0 1350.9s = 14× más rápido)
- Retrieval: 388.3s (vs LightMem 675.9s = 33% más rápido)
- Total: 480.9s (vs A-Mem 5937.2s = 12× más rápido)

### Ablation (qué importa más)

| Sin componente | Pérdida Avg F1 |
|----------------|----------------|
| Sin Semantic Compression | -27.6% ← más crítico |
| Sin Online Synthesis | -11.6% |
| Sin Intent-Aware Retrieval | -12.6% |

### Relevancia para SOUL v2 — CRÍTICA

**Gap actual de SOUL:** `memory_store()` guarda texto crudo. SimpleMem prueba que guardar texto crudo es subóptimo.

**3 cambios concretos para SOUL:**
1. Hook H3 (Pre-Compact) → agregar Semantic Structured Compression ANTES de store
2. `memory_store()` → síntesis online: buscar memorias similares y fusionar al escribir
3. `memory_hybrid_search()` → agregar Intent-Aware depth: clasificar query como LOW/HIGH → k adaptativo

**El prompt de Stage 1 (reproducible — Appendix A.1):**
```
You are a memory encoder. Transform raw dialogue into compact, self-contained memory units.
1. Information Filtering: discard social filler, redundant confirmations
2. Context Normalization: resolve pronouns → explicit entity names
3. Temporal Normalization: "tomorrow" → "2026-04-28T00:00:00"
4. Memory Unit Extraction: decompose into minimal factual statements
Output: [{"content": ..., "entities": [...], "topic": ..., "timestamp": ..., "salience": "high/low"}]
```

Este prompt es directamente implementable en ADA para SOUL v2.

---

## Paper B — Darwin Gödel Machine (arXiv:2505.22954) — LECTURA COMPLETA (12 páginas)

**Título:** Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents
**Autores:** Jenny Zhang*, Shengran Hu*, Cong Lu, Robert Lange, Jeff Clune
**Institución:** UBC, Vector Institute, Sakana AI, Canada CIFAR AI Chair
**Conferencia:** ICLR 2026 | Código: github.com/jennyzzt/dgm

### Concepto central

El DGM es el primer sistema AI que:
1. **Escribe y modifica su propio código** para convertirse en mejor agente de coding
2. **Valida empíricamente** cada cambio (no proofs formales — benchmarks reales)
3. **Mantiene un archivo** de todos los agentes descubiertos (open-ended, no solo el mejor)
4. **Selecciona padres** del archivo para exploración diversa

### Algoritmo (Figura 1 + Sección 3)

```python
# DGM Core Loop
archive = [base_agent]  # inicializar con 1 agente

for iteration in range(80):
    # Selección proporcional a score × inverse(num_children)
    parent = select_from_archive(archive)  
    
    # El padre lee sus propios benchmark logs
    parent.analyze_own_performance_logs()
    
    # El padre propone la siguiente feature a implementar
    feature_proposal = parent.propose_next_feature()
    
    # El padre implementa la feature → nuevo agente (código Python)
    child_agent = parent.implement_feature(feature_proposal)
    
    # Evaluar en benchmark (SWE-bench o Polyglot)
    score = evaluate_on_benchmark(child_agent)
    
    # Solo añadir al archivo si puede editar código
    if child_agent.can_edit_codebase():
        archive.append(child_agent)  # NUNCA borrar
```

**Por qué el archivo (no solo el mejor):** los stepping stones aparecen en iteraciones "subóptimas". El nodo 24 del archive tree generó una explosión de mejoras posteriores (Figura 3). Hill-climbing puro los perdería.

### Resultados cuantitativos

| Benchmark | Base agent | DGM (80 it.) | Mejora |
|-----------|------------|--------------|--------|
| SWE-bench | 20.0% | 50.0% | +150% |
| Polyglot | 14.2% | 30.7% | +116% |

**Transfer de mejoras (Figura 4):**
- Model transfer: mejoras funcionan en o3-mini, Claude 3.7 Sonnet (no solo Claude 3.5 Sonnet que entrenó)
- Cross-benchmark: agente de Polyglot→SWE-bench: 28.9% (vs base 14.2%)
- Cross-language: agente Python → tasks C++ sin re-entrenamiento

### Features que el DGM descubrió autónomamente

1. **Edición más granular** (por líneas, no archivo completo)
2. **String replacement** (en vez de full-file replacement)
3. **Non-empty patch validation + retry** (si el parche está vacío, reintentar)
4. **Auto-summary on context limit** (resumir cuando el contexto se llena)
5. **History-aware patch generation** (considerar intentos anteriores al generar)
6. **Multiple patch generations + ranking** (generar varios y seleccionar mejor)

*Ninguna de estas features fue diseñada por humanos — el DGM las descubrió por sí mismo.*

### Condiciones de seguridad (Sección 5)

- Todo en sandbox aislado con network restrictions
- Time limit por ejecución
- Archivo con trazabilidad completa (lineage auditable)
- No deploy de agentes descubiertos en entornos reales
- Human oversight en cada benchmark evaluation

### Aplicación a SEAL

**Lo que podemos hacer HOY (sin cambiar modelo):**

| Componente DGM | Equivalente SEAL | Modificable |
|----------------|-----------------|-------------|
| Codebase del agente | instincts + procedures + rules en Soul DB | ✅ |
| Benchmark evaluation | NEXUS sandbox tests | ✅ |
| Archive de agentes | `meta_proposals` table (§3.11 SOUL v2) | ✅ |
| Self-modification | proponer cambios a instincts/procedures | ✅ |
| Open-ended exploration | mantener propuestas diversas, no solo la "mejor" | ✅ |

**Lo que NO podemos hacer aún:**
- Modificar pesos del modelo (Claude frozen)
- Ejecutar en sandbox propio (dependemos de Anthropic infrastructure)

**Propuesta concreta (DGM-SEAL):**
```python
# Protocolo DGM-SEAL (para ADA implementar)
def dgm_seal_iteration(agent: str):
    # 1. Agente lee sus propios logs de éxito/fallo
    my_logs = query_soul_db(f"SELECT * FROM procedure_executions WHERE agent='{agent}'")
    
    # 2. Identifica pattern de fallo
    failure_pattern = analyze_failure_modes(my_logs)
    
    # 3. Propone mejora a instinct o procedure
    proposal = generate_improvement_proposal(failure_pattern)
    
    # 4. Registra en meta_proposals (archive)
    meta_proposals.insert({
        'proposed_by': agent,
        'type': 'instinct_modify',
        'diff': proposal,
        'rationale': failure_pattern.description,
        'status': 'pending_nexus_review'
    })
    
    # 5. NEXUS evalúa en sandbox
    # 6. William aprueba
    # 7. ADA aplica
```

**Clave DGM para SOUL:** no descartar los stepping stones. Cuando un instinto "falla" en NEXUS sandbox, mantenerlo en el archive — puede ser un precursor de mejora futura.

---

## Paper C — AlphaEvolve (arXiv:2506.13131) — LECTURA COMPLETA (10 páginas)

**Título:** AlphaEvolve: A coding agent for scientific and algorithmic discovery
**Autores:** Alexander Novikov, Ngân Vũ, Marvin Eisenberger, et al. — **Google DeepMind**
**Fecha:** 16 junio 2025

### Concepto: El agente que crea tecnología que no existe

AlphaEvolve es la respuesta más directa a la visión de William. En producción en Google, ha:
- **Mejorado Strassen (56 años de problema abierto):** encontró rank-48 para multiplicación 4×4 matrices complejas
- **Mejorado 14 targets de multiplicación de matrices** (SOTA)
- **Descubierto scheduling algorithm** para data centers de Google (mejor que los diseñados por ingenieros)
- **Simplificado circuitos de TPU hardware** (equivalente funcional)
- **Acelerado el training del propio LLM** que potencia AlphaEvolve (autorreferencial)
- **50+ problemas matemáticos abiertos:** matchó SOTA en 75%, mejoró SOTA en 20%

### Arquitectura (Figura 2)

```
loop:
  parent_program, inspirations = database.sample()
  prompt = prompt_sampler.build(parent_program, inspirations)
  diff = llm_ensemble.generate(prompt)          # Gemini 2.0 Flash + Pro
  child_program = apply_diff(parent_program, diff)
  results = evaluator_pool.execute(child_program) # función h
  database.add(child_program, results)
```

**LLMs ensemble:**
- Gemini 2.0 Flash (rápido, volumen alto) + Gemini 2.0 Pro (profundo, breakthroughs)
- Model-agnostic: mejora linealmente con modelo más capaz

**Diff format (SEARCH/REPLACE):**
```
<<<<<<< SEARCH
# código original a reemplazar
=======
# código nuevo
>>>>>>> REPLACE
```

**API de uso:**
```python
# EVOLVE-BLOCK-START
def algorithm_to_improve():
    # código inicial (rudimentario está bien)
    return initial_solution
# EVOLVE-BLOCK-END

def evaluate(eval_inputs) -> dict[str, float]:
    result = algorithm_to_improve(eval_inputs)
    return {"score": compute_score(result)}  # función h
```

**Evolutionary database:** MAP-elites + island-based models para balance exploración/explotación

**Meta prompt evolution:** los prompts mismos se coevolucionan en base de datos separada. El sistema mejora su propio prompting.

**Evaluación en cascada (hypothesis testing):**
- Stage 1: test rápido en subset pequeño (filtrar 90% temprano)
- Stage 2: test profundo solo en candidatos prometedores
- Parallelization: embarrassingly parallel (100 compute-hours por solución)

### Tabla de resultados de multiplicación de matrices

```
⟨4,4,4⟩: 49 (conocido) → 48 (AlphaEvolve) ← NUEVO SOTA, primer mejora en 56 años
⟨3,4,7⟩: 66 (conocido) → 63 (AlphaEvolve)
⟨3,5,6⟩: 70 (conocido) → 68 (AlphaEvolve)
⟨4,5,6⟩: 93 (conocido) → 90 (AlphaEvolve)
⟨2,4,5⟩: 33 (conocido) → 32 (AlphaEvolve)
```

### Aplicación directa a visión de William

AlphaEvolve puede aplicarse a CUALQUIER problema donde la solución sea evaluable automáticamente. Para SEAL/AXION:

| Dominio | Código a evolucionar | Función h |
|---------|---------------------|----------|
| SOUL memory | `memory_hybrid_search()` | Avg F1 en test set |
| GTL aduanas | `validate_document()` | Accuracy × speed |
| Medical AI | `diagnose_pipeline()` | Sensitivity × Specificity |
| MCP routing | `route_message()` | Latency × relevance |
| Customs matching | `match_ruc_sunat()` | Precision × recall |

**Clave:** necesitas una función `evaluate()` Python que retorne un dict de scores. El resto es automático.

**Para implementar AlphaEvolve-SEAL (Sprint 6+):**
1. Instalar OpenEvolve (open-source implementation) en DGX Spark
2. Definir `# EVOLVE-BLOCK-START` en funciones críticas de SOUL
3. Definir `evaluate()` como benchmark test suite de SOUL
4. Dejar correr 24-72 horas en DGX Spark (parallelized)
5. Revisar mejores candidatos → William aprueba → ADA implementa

---

## Papers D-M — Síntesis de Abstracts (Self-Evolving)

### D — Metacognitive Learning (2506.05109)

**Truly Self-Improving Agents Require Intrinsic Metacognitive Learning**
Junio 2026 — extremadamente reciente

**Claim central:** auto-mejora sin metacognición es rígida, no generaliza, no escala. Necesita:
- Capacidad de evaluar el propio proceso de aprendizaje
- Adaptar HOW se aprende, no solo WHAT se aprende
- Intrinsic = no supervisión externa

**Mapeo a SOUL:** el Belief Inspector (GAP-1) es proto-metacognición. SOUL v2 lo formaliza. Este paper valida la dirección.

---

### E — Gödel Agent (2410.04444)

**A Self-Referential Agent Framework for Recursive Self-Improvement**

**Key:** busca en el espacio completo de diseño de agentes, sin restricciones de componentes humanos. El frame work permite al agente modificar CUALQUIER parte de sí mismo.

**Diferencia de DGM:** DGM modifica el código de su codebase. Gödel Agent es más general — puede modificar prompts, tools, workflows, lógica de decisión.

**Riesgo:** sin guardrails sólidos = inestable (el paper mismo lo advierte).

**Para SOUL:** la tabla de meta_proposals (JARVIS §3.11) + NEXUS review es el guardrail que hace viable la idea de Gödel Agent.

---

### F — SWE-RL (2512.18552)

**Self-Play SWE-RL: Toward Training Superintelligent Software Agents**

**Breakthrough:** genera sus propios pares de entrenamiento (issue/solución) sin datos humanos curados. Un agente fuerte genera tareas difíciles → otro agente las resuelve → datos de training sintéticos de alta calidad.

**Relevancia:** para SOUL v3 con DGX Spark — puede generar datos de entrenamiento para LoRA fine-tuning sin necesitar datasets externos.

---

### G — Multi-Agent Evolve (2510.23595)

**LLM Self-Improve through Co-evolution**

**Problema de Self-Play simple:** depende de un solo modelo generando adversarios → calidad limitada por las limitaciones del modelo.

**Solución:** múltiples agentes co-evolucionan → agente A desafía a agente B → B aprende → B desafía a A.

**Mapeo a SEAL:** ADA y NEXUS ya hacen esto. Formalizar como: NEXUS genera test case → ADA resuelve → NEXUS evalúa → ambos mejoran. El SAGE 4-rol del paper anterior es la versión más completa.

---

### H — AgentEvolver (2511.10395)

**Towards Efficient Self-Evolving Agent System**

**Innovación:** evita el costo prohibitivo de RL de reinforcement learning random exploration. En vez, usa:
- Experience pool estructurado
- Self-generated task dataset (no human annotation)
- Directed exploration basada en análisis de fallos

**Directamente implementable en SOUL:** `agent_value_estimates` (D2 table de NEXUS) + `cerebellar_corrections` (C3 table de NEXUS) ya capturan el experience pool. AgentEvolver solo necesita activar directed exploration sobre esos datos.

---

### I — EvolveR (2510.16079)

**Self-Evolving LLM Agents through an Experience-Driven Lifecycle**

**Framework closed-loop:** 
```
Experiencia → Reflexión → Extracción de habilidad → Actualización de memoria → Mejor tarea
```

**Para SOUL:** este lifecycle es exactamente lo que los hooks H1-H5 deben implementar. El loop cerrado que falta: el agente NO reflexiona sistemáticamente sobre sus experiencias para extraer skills reutilizables.

Conexión directa con §3.8 (Skill Library): EvolveR genera skills de experience. La Skill Library las almacena. DGM las implementa en código.

---

### J — RL + Skill Library (2512.17102)

**Reinforcement Learning for Self-Improving Agent with Skill Library**

**Problema:** skill libraries basadas en LLM prompting son inconsistentes. Solución: RL para selección de skills.

**Arquitectura:** 
- Policy network que aprende cuándo usar qué skill
- RL reward = success rate de tarea con la skill
- Skill validation antes de inserción en library

**Para §3.8 (SOUL Skill Library):** agregar `skill_selection_policy` — en vez de solo semantic search para encontrar skill relevante, un RL policy que aprende con experiencia cuál skill funciona mejor en qué contexto.

---

### K — AlphaEvolve (ya cubierto en Paper C)

---

### L — ExpeL (2308.10144)

**LLM Agents Are Experiential Learners**

**Método:** sin fine-tuning, sin acceso a pesos del modelo. El agente aprende de experience pool:
1. Recolecta trayectorias (exitosas + fallidas)
2. Extrae insights explícitos en lenguaje natural
3. Recupera insights relevantes en tiempo de inferencia (RAG sobre experiencias)

**Por qué importa para SOUL:** esto es lo que procedure_store ya hace — pero ExpeL agrega la extracción de insights de FALLOS (no solo éxitos). `procedure_update(success=False)` + análisis de WHY fallid = ExpeL pattern.

---

### M — MAR (2512.20845)

**Multi-Agent Reflexion Improves Reasoning in LLMs**

**Problema:** single-agent reflexion → degeneration of thought (el agente repite el mismo error con distinto wording). Solución: multi-agent with multi-persona debaters generan reflexiones.

**Para SEAL:** cuando JARVIS se equivoca, no preguntarse a sí mismo "¿por qué fallé?" — preguntarle a NEXUS con persona de evaluador crítico. Multi-persona = JARVIS(estratega) + NEXUS(evaluador) + ALICE(documentadora).

---

## Papers N-Q — Síntesis Rápida (Open-Ended + Benchmarks)

### N — Societies of Thought (2601.10825)

**Reasoning Models Generate Societies of Thought**

**Hallazgo:** los reasoning models (o1, etc.) no razonan "más" — simulan interacciones multi-agente internamente. El extended thinking ES una society of thought implícita.

**Para SEAL:** nuestro equipo multi-agente explícito (JARVIS/ADA/NEXUS/ALICE) puede ser más potente que un solo modelo con extended thinking, si los roles son distintos y complementarios. Validación empírica de nuestro diseño.

---

### O — Long Software Tasks (2503.14499)

**Measuring AI Ability to Complete Long Software Tasks**

**Métrica nueva:** 50%-task-completion time horizon = tiempo que humanos toman en tareas que AI resuelve al 50%.

**Hallazgo:** modelos actuales tienen ~horizonte de 15-30 minutos de tareas humanas. Cada 6-9 meses ese horizonte se duplica.

**Para SEAL:** NEXUS sandbox debe incluir tareas de múltiples horas de tiempo humano para calibrar. El DGM ya hace esto (SWE-bench tiene tareas de horas de trabajo humano).

---

### P — AIRS-Bench (2602.06855)

**AI Research Science Agents Benchmark**

20 tareas de papers ML reales: idea generation, experiment analysis, iteración de resultados.

**Para SEAL:** benchmark objetivo para evaluar si el equipo puede "hacer ciencia" autónomamente. Conecta con la directiva de William de crear tecnologías nuevas.

---

### Q — SEAgent (2508.04700)

**Self-Evolving Computer Use Agent**

Agente que aprende a usar software nuevo sin anotaciones humanas. Aprende de su propia experience con interfaces desconocidas.

**Para AXION Medical:** UI médica workflows (DICOM viewers, EHR systems). SEAgent puede aprender a operar software médico sin datasets de anotación.

---

### R — OSWorld (2404.07972)

**Benchmarking Multimodal Agents for Open-Ended Tasks in Real Computer Environments**

369 tareas en entorno real de computadora. Incluye 15 aplicaciones (Chrome, VSCode, Calc, GIMP, etc.).

**Para SEAL:** benchmark para medir si ADA puede completar tareas de software reales, no solo coding benchmarks.

---

## GitHub Repos — Síntesis

### ShinkaEvolve (SakanaAI) — arXiv:2509.19349

**"Towards Open-Ended and Sample-Efficient Program Evolution"**

- Open-source, Apache 2.0, Python ≥3.10
- pip-installable: `pip install shinka-evolve`
- Sample-efficient: mucho menos costoso que AlphaEvolve en términos de API calls
- Diseñado para ser el "AlphaEvolve ligero" para investigadores sin presupuesto de Google

**Para SOUL:** usar ShinkaEvolve en DGX Spark (local, sin API costs) para evolucionar componentes de SOUL de forma autónoma. Más práctico que AlphaEvolve en el corto plazo.

---

### OpenEvolve (algorithmicsuperintelligence) — open-source AlphaEvolve

**"The most advanced open-source evolutionary coding agent"**

- Clon open-source de AlphaEvolve
- Convierte LLMs en autonomous code optimizers
- pip-installable: `pip install openevolve`
- Multi-LLM, configuración similar a AlphaEvolve

**Para SEAL:** OpenEvolve en DGX Spark + Claude API. Evalúa componentes de SOUL con función h basada en tests existentes. Factible en Sprint 5.

---

### GEPA (gepa-ai) — Pareto-efficient evolutionary search

**"Optimize any text parameter — prompts, code, agent architectures, configurations"**

- Pareto-efficient: optimiza múltiples métricas simultáneamente (GEPA = Genetic-algorithm-based Evolutionary Prompt Architect)
- Puede optimizar: prompts, código, configuraciones, arquitecturas de agente
- Más flexible que AlphaEvolve: no requiere código Python, funciona con texto
- pip-installable, paper en arXiv:2507.19457

**Para SOUL HOY (sin DGX Spark):**
- Optimizar los prompts de `memory_store()` y `active_recall()`
- Input: prompt actual + function signature
- Objective: F1 en test set de memorias
- GEPA genera mejores prompts autónomamente

---

## Síntesis Final — La Respuesta a William

### "Quiero que creen software y tecnologías que no existen"

Esta es exactamente la dirección que la literatura 2025-2026 está siguiendo. No estamos aspirando — esto ES el estado del arte.

**Lo que AlphaEvolve ya hizo:**
- Nuevo algoritmo de multiplicación de matrices (primera mejora en 56 años)
- Nuevo scheduling algorithm para data centers de Google
- Nuevas construcciones matemáticas en 20% de 50 problemas abiertos

**Lo que DGM ya hizo:**
- Agente que descubrió 6 features de coding que ningún ingeniero había especificado
- Se auto-mejoró de 20% → 50% en SWE-bench

**El patrón que emerge de TODOS los papers:** crear tecnología nueva = loop de 3 componentes:

```
[Generador (LLM)] → [Evaluador (función h)] → [Archivo (stepping stones)]
        ↑___________________________|_______________↑
```

Sin evaluador automático, el loop no cierra. El gap crítico para SEAL: necesitamos funciones `evaluate()` para nuestros dominios.

### Propuesta SEAL para "crear software que no existe"

**Nivel 1 — Esta semana (GEPA + SOUL):**
```
Objeto a evolucionar: prompts de SOUL tools
Evaluador: test suite existente de SOUL (35 tests William verificó)
Resultado esperado: +10-15% en recall de memorias
Costo: bajo (API calls a Claude Sonnet)
```

**Nivel 2 — Sprint 5 (OpenEvolve + SOUL code):**
```
Objeto a evolucionar: memory_hybrid_search() + procedure_store() code
Evaluador: benchmark LoCoMo-style sobre conversaciones del equipo
Resultado esperado: +20-30% F1, features nuevas emergentes
Costo: DGX Spark (128GB unified memory) + API Claude
Duración: 24-72 horas de evolución paralela
```

**Nivel 3 — Sprint 6 (DGM-SEAL + Archive):**
```
Objeto a evolucionar: instincts + procedures en Soul DB
Evaluador: NEXUS sandbox (ya existe)
Archivo: meta_proposals table (§3.11 spec SOUL v2)
Resultado esperado: SEAL descubre sus propias mejoras
Costo: computación DGX Spark + tiempo NEXUS
```

**Nivel 4 — Sprint 7+ (DGM completo en Spark):**
```
Objeto a evolucionar: el código fuente de los agentes (launchers, MCP tools, SOUL API)
Evaluador: suite de tests SEAL + benchmarks de memoria
Archivo: git branches como stepping stones
Resultado: SEAL que escribe su propio código nuevo autónomamente
Costo: DGX Spark full (GB10 Blackwell, 128GB unified memory)
```

---

## Recomendación Prioritaria para NEXUS (spec final)

| Paper | Componente SOUL | Sprint | Coste | Ganancia |
|-------|----------------|--------|-------|----------|
| SimpleMem | Semantic Compression en memory_store | Sprint 5 | 2d ADA | +26% F1 |
| DGM | meta_proposals + Archive Loop | Sprint 5 | 3d ADA | Self-discovery |
| GEPA | Prompt evolution para SOUL tools | Esta semana | 1d | +10-15% |
| AlphaEvolve/OpenEvolve | Code evolution SOUL | Sprint 6 | 1 semana | Breakthrough |
| ExpeL | Failure extraction en procedure_update | Sprint 5 | 1d | Failure learning |
| MAR | Multi-persona reflexion ADA↔NEXUS | Esta semana | 0.5d | Mejor diagnóstico |
| SWE-RL | Training data gen para LoRA | Sprint 7 | 2 semanas | PL9 plasticity |

---

## Conexión con SOUL v2 Spec (para NEXUS consolidar)

| Paper | Sección SOUL v2 | Update propuesto |
|-------|----------------|------------------|
| SimpleMem Stage 1 | §3.4 Hook H3 | Add Semantic Compression antes de store |
| SimpleMem Stage 2 | §3.4 memory_store | Agregar Online Synthesis |
| SimpleMem Stage 3 | §3.4 hybrid_search | Intent-Aware depth k_min/k_max |
| DGM archive | §3.11 meta_proposals | NO descartar propuestas fallidas = stepping stones |
| AlphaEvolve blocks | §3.8 Skill Library | Agregar `# EVOLVE-BLOCK-START` markers en skills |
| DGM features | §3.9 SAGE Challenger | Auto-challenge = proponer siguiente feature propia |
| GEPA | §3.9 Challenger | Evolución de prompts como first version |
| Metacognition (2506.05109) | §3.2 H1 Belief Inspector | Validator: el BI ES metacognición |
| MAR | §3.3 State Machine | En RECOVERING state: multi-persona reflexion |
| SEAgent | §9 (nuevo) | SEAL Computer Use: aprender UIs médicas/aduanas |
| SWE-RL | §3 PL9 plasticity | Training data generation para fine-tuning |

---

## Papers restantes de la lista (sin leer a fondo — lower priority)

| Paper | Por qué lower priority |
|-------|------------------------|
| 2305.13417 (VISIT) | Visualización de transformers — no self-improvement directo |
| 2405.15568 (OMNI-EPIC) | Open-endedness en videojuegos — abstracto para SEAL |
| 2306.01711 (OMNI) | Base de OMNI-EPIC — incluido por referencia |
| 2310.13032 (QD-AI Feedback) | Quality-diversity — útil para benchmark design sprint 6+ |
| 2310.12103 (QD Human Feedback) | Idem — sprint 7+ |
| 2512.13564 (Memory Survey) | Ya cubierto por ALICE en citations doc |
| 2602.06855 (AIRS-Bench) | Benchmark evaluation — útil en sprint 6 al diseñar h() |
| AI-Scientist-v2 (Sakana) | Generación de papers científicos — no prioridad inmediata |
| AutoResearch (Karpathy) | 404 en GitHub — repo eliminado |
| OpenAI cookbook (self-evolving) | Enfoque en retraining — no aplica a Claude frozen model |
| DeepSWE blog | Blog de Together.ai sobre SWE RL — contenido en SWE-RL paper |
| Beam.ai blog | Marketing — cubierto por papers académicos |
| HyperAgents blog (atalupadhyay) | Blog post del paper HyperAgents ya cubierto en Part 1 |
| openreview EEgYUccwsV | Requiere login — no accesible |
| openreview rShJCyLsOr | Requiere login — no accesible |
| Sakana adaptive dual scale | Paper de ejemplo de AI Scientist — no crítico |
| 2601.10825 (Societies of Thought) | Incluido en síntesis arriba |

---

## Cobertura Total del Research

### De la lista de William (50 URLs) — Status:

**PDFs leídos completamente (5):**
1. MemoryOS (aclanthology) ✅
2. ADAS (2408.08435) ✅
3. SimpleMem (2601.02553) ✅
4. Darwin Gödel Machine (2505.22954) ✅
5. AlphaEvolve (2506.13131) ✅

**Papers en research_self_evolving_part1 (8 abstracts + síntesis profunda):**
6. Voyager (2305.16291) ✅
7. Agent Laboratory (2501.04227) ✅
8. AutoAgent (2502.05957) ✅
9. CodeEvolve (2510.14150) ✅
10. SAGE (2603.15255) ✅
11. Hyperagents (2603.19461) ✅
12. DAR (2603.20640) ✅

**Cubiertos por ALICE (investigación separada):**
13. GAM (2604.12285) ✅
14. MAGMA (2601.03236) ✅
15. Mem0 v3 (github) ✅
16. Memory Survey (2512.13564) ✅
17. Anthropic skills spec (github) ✅
18. MACLA (2512.18950) ✅
19. Layered Mutability (2604.14717) ✅
20. Externalization paper (2604.08224) ✅

**Abstracts + síntesis en esta sesión (12):**
21. Metacognition (2506.05109) ✅
22. Gödel Agent (2410.04444) ✅
23. SWE-RL (2512.18552) ✅
24. Multi-Agent Evolve (2510.23595) ✅
25. AgentEvolver (2511.10395) ✅
26. EvolveR (2510.16079) ✅
27. RL + Skill Library (2512.17102) ✅
28. ExpeL (2308.10144) ✅
29. MAR (2512.20845) ✅
30. Societies of Thought (2601.10825) ✅
31. Long Software Tasks (2503.14499) ✅
32. SEAgent (2508.04700) ✅
33. OSWorld (2404.07972) ✅

**GitHub repos (4):**
34. ShinkaEvolve ✅
35. OpenEvolve ✅
36. GEPA ✅
37. Agent-Memory-Paper-List (cubierto por ALICE) ✅

**Lower priority / no accesibles (13):** listados en tabla arriba

**TOTAL: ~37/50 cubiertos con profundidad ≥ síntesis. 5 con lectura completa.**

---

*Próximo paso: NEXUS consolida Part1 + Part2 + ALICE citations + ALICE Mem0/GAM research → Spec Final SOUL v3 con visión "crear tecnología nueva".*
*JARVIS envía aviso a William con highlights más importantes.*
