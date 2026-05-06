# Research Findings — Lista William 27-abr-2026

**Investigación profunda 1x1 para mejorar arquitectura SOUL.**  
**División:** ALICE = papers/foundational/openreview, NEXUS = heavy infra + self-improvement repos.  
**Output:** título, autor/fecha, idea principal, aplicabilidad SOUL v2, gap que cierra.

---

## Lote 1 — Foundational Reflection + Embodied + Benchmarks

### 1. arXiv:2305.13417 — VISIT
**Katz & Belinkov | Mayo 2023**  
**Idea:** Tool de visualización de transformers — proyecta weights/hidden states a espacio vocabulario.  
**Aplicabilidad SOUL:** **BAJA** — interpretability del modelo, no arquitectura de agentes.  
**Gap cerrado:** Ninguno directo. Útil solo si quisiéramos abrir caja negra de Claude.

### 2. arXiv:2308.10144 — ExpeL: LLM Agents Are Experiential Learners
**Zhao, Huang, Xu, Lin, Liu, Huang | Agosto 2023**  
**Idea:** Agentes LLM aprenden de experiencias acumuladas sin actualizar parámetros. Dos fases: training colecta insights en lenguaje natural → inference recall.  
**Aplicabilidad SOUL:** **ALTA** — valida el approach core de SOUL (memoria persistente sin fine-tuning). Es nuestro patrón.  
**Gap cerrado:** Confirma que el approach SOUL es el correcto teóricamente. Cita primaria para defender la arquitectura ante William.

### 3. arXiv:2305.16291 — Voyager
**Wang, Xie, Jiang, Mandlekar, Xiao, Zhu, Fan, Anandkumar (NVIDIA) | Mayo 2023**  
**Idea:** Skill library = código ejecutable almacenado/recuperado. Curriculum automático maximiza exploración. Iterative prompting con env feedback + self-verification.  
**Resultados:** 3.3x más items únicos, 2.3x más distancia, 15.3x más rápido en milestones tecnológicos vs prior SOTA en Minecraft.  
**Aplicabilidad SOUL:** **ALTA** — el skill library es exactamente `procedure_store` (GAP 4). Voyager valida el patrón empíricamente.  
**Gap cerrado:** GAP 4 (Procedure Evolution) tiene precedente Voyager. Refuerza Hook H4 (Post-Procedure Update).

### 4. arXiv:2602.06855 — AIRS-Bench
**Lupidi, Gauri, Foster + 34 coautores | Febrero 2026**  
**Idea:** Suite de 20 tareas de research papers (ML/math/bioinfo/time series) para evaluar agents en research lifecycle (idea→experimento→refinamiento).  
**Resultados:** Agents superan humanos en 4/20, fallan en 16/20. Brecha amplia hacia ceiling teórico.  
**Aplicabilidad SOUL:** **MEDIA** — benchmark futuro para evaluar SOUL como research agent en Sprint 6+. No arquitectural ahora.  
**Gap cerrado:** Identifica el gap evaluativo: no tenemos benchmark estándar para SOUL como research agent autónomo.

## Lote 2 — Memory Systems + Self-Improvement + Coordination

### 5. EMNLP 2025.emnlp-main.1318 — Memory OS of AI Agent ⭐⭐⭐
**Kang, Ji, Zhao, Bai | EMNLP 2025**  
**Idea:** Sistema operativo para memoria de agentes. 4 módulos (Storage, Updating, Retrieval, Generation). 3 tiers (short/mid/long-term). FIFO dialogue-chain para short→mid; segmented page para mid→long.  
**Resultados:** LoCoMo con GPT-4o-mini: **+48.36% F1, +46.18% BLEU-1** vs baselines.  
**Aplicabilidad SOUL:** **ALTÍSIMA** — confirma la dirección "memory OS" mencionada antes. Mejor benchmark que GAM en F1 (48.36% vs 13% de GAM).  
**Gap cerrado:** Da framework concreto para §3.4 (TTL por categoría) — los 3 tiers reemplazan el modelo plano actual de SOUL. Hooks de transición FIFO/segmented page son implementables.

### 6. OpenReview rShJCyLsOr — A Self-Improving Coding Agent ⭐
**Robeyns, Szummer, Aitchison**  
**Idea:** Agente con coding tools edita su propio código. Self-referential improvement loop sin intervención externa.  
**Resultados:** 17–53% mejora en SWE Bench Verified. Gains adicionales en LiveCodeBench.  
**Aplicabilidad SOUL:** **ALTA** para roadmap Spark — patrón DGM-style aplicado a SOUL. Sandbox aislado obligatorio.  
**Gap cerrado:** Open-ended design de agentes. Para SOUL v3 cuando ADA tenga modo sandbox seguro (ya parcialmente con DGM schema isolation).

### 7. OpenReview EEgYUccwsV — AgentTrek (ICLR 2025)
**Xu, Lu, Shen, Wang, Wang, Mao, Xiong, Yu**  
**Idea:** Pipeline para sintetizar training data de GUI agents desde web tutorials. VLM evaluator valida trajectories.  
**Aplicabilidad SOUL:** **BAJA** — training data synthesis para GUI agents, fuera de nuestro caso de uso.  
**Gap cerrado:** Ninguno directo. Solo si SOUL evolucionara hacia GUI automation.

### 8. arXiv:2603.20640 — Hear Both Sides ⭐
**Nguyen, Nguyen, Nguyen, Venkatesh, Le | Marzo 2026**  
**Idea:** Multi-agent debate con retención por diversidad — filtra mensajes por máximo desacuerdo con majority + entre sí. Reduce ruido sin modificar autenticidad.  
**Aplicabilidad SOUL:** **ALTA** — protocolo formal para coordinación ALICE/JARVIS/ADA/NEXUS cuando hay desacuerdos arquitecturales.  
**Gap cerrado:** No tenemos protocolo formal de debate. Hoy ADA y yo cruzamos mensajes con propuestas inversas — caso real esta sesión. Aplicable como Hook H6 (Multi-Agent Debate) en SOUL v2.

---

## DEEP DIVES — Implementables a nivel de código

### DD1. arXiv:2512.20845 — MAR: Multi-Agent Reflexion ⭐⭐⭐
**Ozer, Wu, Wang, Dosti, Zhang, De La Rue | Diciembre 2025**

**Arquitectura completa:**
- HotPotQA: 4 debaters (Skeptic, Logician, Creative, Verifier) + 1 Judge
- HumanEval: 3 debaters (Senior Engineer, QA Engineer, Code Reviewer) + 1 Judge

**Personas en 3 ejes (design space formal):**
| Eje | Skeptic | Logician | Creative | Verifier |
|-----|---------|----------|----------|----------|
| Evidence | Low | High | Low | High |
| Exploration | High | Low | High | Low |
| Strictness | Medium | High | High | Medium |

**Pseudocode del loop de debate:**
```python
for trial in range(max_trials):  # 5 HotPotQA, 3 HumanEval
    response = actor.attempt(question)
    if evaluator.failed(response):
        diagnoses = [persona.diagnose(response, trajectory) for persona in personas]
        debate_history = diagnoses
        for round in range(2):  # max 2 rounds
            for persona in personas:
                refined = persona.respond_to_debate(debate_history)
                debate_history.append(refined)
            if consensus_reached(debate_history): break
        consensus_reflection = judge.synthesize(error_outputs, debate_logs, consensus)
        memory.append(consensus_reflection)
    else:
        return response
```

**Benchmarks:**
- HotPotQA EM: ReAct 32% → Reflexion 44% → **MAR 47%**
- HumanEval Pass@1: GPT-3.5 67.1% → Reflexion 76.4% → **MAR 82.6%**

**Costos:** 300-400 API calls/task (3x Reflexion).

**Mapeo a SOUL v2:**
- Hook H6 (Multi-Agent Debate) — ALICE/JARVIS/ADA/NEXUS pueden ser las 4 personas con ejes preasignados (evidence/exploration/strictness).
- Para decisiones arquitecturales críticas: lanzar MAR-style debate antes de aceptar.
- Gap del Problema A (Memory Fusion JARVIS-HMAC): el MAR podría haberlo prevenido — los personas ven con lentes distintos y un "Skeptic" hubiera detectado la fusión.

---

### DD2. arXiv:2510.16079 — EvolveR: Experience-Driven Self-Evolution ⭐⭐⭐
**Wu, Wang, Mei, Cai, Fu, Yang, Wen, Yang, Shen, Wang, Shi**

**Arquitectura — Two-stage loop:**

**Stage 1 — Offline Self-Distillation:**
```
Input: Trajectory τ con outcome (success/fail)
Process: πθ adopta "expert persona" via prompt
Output: Principle p = {description, triples}
  - description: one-sentence natural language
  - triples: list of (subject, predicate, object)
```

**Schema de Experience Base ℰ:**
```
{
  id: unique_identifier,
  description: string,
  triples: [(subj, pred, obj)],
  trajectory_id: ref,
  type: "guiding" | "cautionary",
  metric_score: float [0,1],
  c_succ: int,           # success count
  c_use: int,            # usage count
  created_timestamp: epoch,
  last_accessed: epoch
}
```

**Metric score formula (Bayesian smoothing):**
```
s(p) = (c_succ(p) + 1) / (c_use(p) + 2)
```

**Deduplicación 2-stage:**
1. BGE-M3 embedding cosine similarity > θ_sim=0.85
2. LLM binary check: "Do A and B describe same advice?"

**Stage 2 — Online Interaction:**
- Action space: `<search_experience>`, `<search_knowledge>`, `<answer>`
- Top-k=3 retrieval por cosine similarity
- Composite reward: `R(τ) = w_o·R_outcome + w_f·R_format`, w_o=1.0, w_f=0.1

**GRPO update rule:**
```
J(θ) = E_τ[Σ_t min(ρ_t·Â_t, clip(ρ_t, 1-ε, 1+ε)·Â_t) - β·D_KL[π_θ||π_ref]]
ε=0.2, β=0.001, lr=1e-6, batch=128 prompts × G=8 trajectories
```

**Benchmarks (Qwen2.5-3B):**
| Dataset | EvolveR | Search-R1 | RAG |
|---------|---------|-----------|-----|
| NQ | **0.434** | 0.341 | 0.348 |
| HotpotQA | **0.373** | 0.324 | 0.255 |
| Bamboogle | **0.328** | 0.264 | 0.080 |
| **Avg** | **0.382** | 0.325 | 0.270 |

**Ablation:** sin experience retrieval → 0.340 (-11%). Self-distill matches teacher-distill (GPT-4o-mini): 0.382 vs 0.370.

**Mapeo a SOUL v2:**
- El schema de Experience Base ℰ mapea directo a `procedure_store` con campos extra (`c_succ`, `c_use`, `metric_score`, `triples`).
- Bayesian smoothing s(p) = (succ+1)/(use+2) reemplaza el EMA α=0.2 actual de SOUL §3.6 — más principled.
- 2-stage dedup (embedding + LLM check) cierra Problema A (Memory Fusion).
- Triple format (subj, pred, obj) habilita Neo4j relationships nativamente — cierra el gap de Neo4j sin usar.

---

### DD3. arXiv:2305.16291 — Voyager: Skill Library + Iterative Prompting ⭐⭐⭐
**Wang, Xie, Jiang, Mandlekar, Xiao, Zhu, Fan, Anandkumar (NVIDIA) | Mayo 2023**

**Skill Library architecture:**
- Storage: código ejecutable como key-value en vector DB
- Key: embedding de descripción del programa (GPT-3.5 generated)
- Value: programa Python ejecutable
- Retrieval: top-5 por similarity
- Composición: skills se llaman entre sí — temporally extended, compositional

**Iterative prompting loop (max 4 rounds):**
```
For task in curriculum:
  For iter 1-4:
    1. Retrieve top-5 skills relevantes
    2. GPT-4 genera código con: prev_code + env_feedback + errors + critic_feedback + retrieved_skills
    3. Execute en environment
    4. Capture feedback + errors via bot.chat()
    5. Self-verification con GPT-4 critic separado
    6. Si éxito: commit skill, next task
    7. Si no: repeat con feedback
  Si 4 iter fallan: marcar task failed
```

**3 tipos de feedback:**
1. Environment feedback: "I cannot make iron chestplate because I need 7 more iron"
2. Execution errors: invalid operations / syntax errors
3. Self-verification: GPT-4 separate critic → binary success/fail + critique

**Curriculum automático (bottom-up):**
- Goal: "discover diverse things"
- Inputs: inventory, equipment, biome, health, position, completed/failed history
- Temperature=0.1 para diversidad
- Warmup schedule de 15+ tasks

**Benchmarks Minecraft:**
| Métrica | Voyager | Prior SOTA |
|---------|---------|-----------|
| Items únicos (160 iter) | 63 | ~19 (3.3×) |
| Wooden tool | 6±2 iter | (15.3× faster) |
| Stone tool | 11±2 iter | (8.5× faster) |
| Iron tool | 21±7 iter | (6.4× faster) |
| Diamond | 102 iter (1/3 trials) | **Único método que lo logra** |
| Distancia | 2.3× longer | — |
| Zero-shot 4 tasks | **100%** | ReAct/Reflexion 0%, AutoGPT 0% |

**Costos:** GPT-4 = 15× GPT-3.5. Cambio de modelo crítico — "quantum leap".

**Failure modes:**
- Curriculum hallucina items inexistentes ("copper sword")
- Self-verification falla (no reconoce spider string como signal)
- No visual perception (GPT-4 era text-only)

**Mapeo a SOUL v2:**
- Voyager skill library = **`procedure_store` con código ejecutable real** (hoy SOUL guarda procedures como texto).
- Self-verification con agente separado = pattern para H1 (Belief Inspector) — no auto-validación, validación cruzada.
- Iterative prompting con env feedback = pattern para H4 (Post-Procedure Update) con re-attempts.

---

### DD4. arXiv:2308.10144 — ExpeL: Experiential Learner ⭐⭐⭐
**Zhao, Huang, Xu, Lin, Liu, Huang | Agosto 2023**

**Two-phase architecture:**

**Phase 1 — Training:**
1. Agent ejecuta tasks Z veces con ReAct
2. En fail: Reflexion genera reflexión que va al next retry
3. Trayectorias guardadas en pool ℬ
4. **Insight extraction** con dos modos:
   - Comparison: Success vs Failure pairs misma task
   - Pattern: L successful trajectories diferentes tasks

**Insights operations (importance count voting):**
- ADD: nuevo insight con count=2
- EDIT: revisar texto existente
- UPVOTE: count++
- DOWNVOTE: count--
- count→0: remove

**Phase 2 — Inference:**
- Faiss vectorstore + all-mpnet-base-v2 embeddings
- Top-k retrieval por task similarity
- Insights TODOS van en context (no selective)
- Trajectories: top-k por similarity
- Single attempt — no retries en deployment

**Benchmarks (gpt-3.5 base, gpt-4 insights):**
| Env | ReAct | Reflexion | ExpeL | ExpeL insights-only | ExpeL retrieve-only |
|-----|-------|-----------|-------|---------------------|---------------------|
| HotpotQA | 28% | 40% | **39%** | 36% | 31% |
| ALFWorld | 40% | 59% | **59%** | 50% | 55% |
| WebShop | ~25% | ~50% | **37%** | 37% | 38% |

**Transfer (HotpotQA→FEVER):** ExpeL transfer 70% vs ReAct 63% — generalización confirmada.

**ExpeL + Reflexion (ALFWorld R0):** **59%** vs ReAct+Reflexion 40.3%.

**Ablation retrieval (ALFWorld):**
- Random: 42.5%
- Reason similarity: 48.5%
- **Task similarity: 59%** ← clave del éxito

**Mapeo a SOUL v2:**
- Insight voting (ADD/EDIT/UPVOTE/DOWNVOTE/count→0) = pattern directo para `instinct_set` y `belief_update` con scoring.
- Task similarity retrieval > reason similarity > random — confirma usar embeddings semánticos para retrieval (ya hacemos).
- gpt-4 mejor para insight extraction que gpt-3.5 — para SOUL: usar Opus para distillation/reflection, Sonnet para ejecución.
- Insights en context VS retrieval: para SOUL pequeño, todos en context; cuando crezca, retrieval selectivo.

---

## SÍNTESIS — Patrones Convergentes para SOUL v2

### Patrón 1 — Skill/Procedure Library con scoring Bayesiano
**Confluencia:** Voyager (skill library), EvolveR (Experience Base), ExpeL (insights), SAGE skill-library.

**Convergencia técnica:**
- Storage: vector DB con embedding del título/descripción como key
- Top-k retrieval (3-5 típico)
- Scoring tipo `(succ+1)/(use+2)` o EMA con voto explícito
- Auto-pruning cuando score baja

**Ganancia esperada:** GAP 4 (Procedure Evolution) pasa de "EMA simple" a "Bayesian con dedup + triples + voting". Cierra el problema de procedures sin tracking.

### Patrón 2 — Multi-Agent Debate para decisiones críticas
**Confluencia:** MAR (4 personas + Judge), Hear Both Sides (diversity retention), Society of Thought (R1 simula multi-agent internamente).

**Convergencia técnica:**
- 3-4 agentes con ejes diferenciados (Evidence/Exploration/Strictness)
- 1 Judge para sintetizar
- Max 2 rounds de debate
- Cost: 3x single-agent — usar solo en decisiones críticas

**Ganancia esperada:** Cierra Problema A (Memory Fusion). Cuando JARVIS, ADA, ALICE, NEXUS tienen visiones distintas, formalizar como debate antes de aceptar.

### Patrón 3 — Self-distillation antes de compactación
**Confluencia:** EvolveR (offline distillation), ExpeL (insight extraction post-training), MemoryOS (FIFO short→mid).

**Convergencia técnica:**
- Tras tarea exitosa: extraer principio (1 frase NL + triples)
- Persist en Soul DB con metric_score
- Pre-compactación: barrer turn activo y extraer insights de score ≥ threshold

**Ganancia esperada:** Cierra Problema B (Compaction como Single Point of Loss). Caso ALICE-pi-mcp NO se hubiera perdido. Esto es el Hook H3 del spec v2 con prompt template específico.

### Patrón 4 — Self-verification con agente separado
**Confluencia:** Voyager (GPT-4 critic), MAR (Judge), OpenAI cookbook (4 graders).

**Convergencia técnica:**
- Nunca el mismo agente que hace la tarea valida la tarea
- Critic = LLM separado, prompt diferente, input = output del actor
- Returns: binary pass/fail + critique

**Ganancia esperada:** Cierra el principio "evaluador externo" del spec actual (NEXUS evalúa lo de JARVIS). Pero formalizable como tool MCP `validate_action(actor, action_output)` con critic role rotando.

### Patrón 5 — Metacognitive layer (Truly Self-Improving)
**Confluencia:** Truly Self-Improving (intrínseco vs extrínseco), AgentEvolver (3 mecanismos), Gödel Agent (self-referential).

**Convergencia técnica:**
- Agent debe poder evaluar su propio aprendizaje (no solo aplicar)
- Knowledge: self-assessment de capabilities
- Planning: decidir qué aprender
- Evaluation: reflexionar sobre experiencias

**Ganancia esperada:** Para SOUL v3 (Spark). Hoy SOUL tiene `self_reflect` pero no es metacognición real — es journal. Para SOUL v3: agente decide qué procedures fortalecer, qué memorias archivar, qué creencias revisar.

---

## Recomendaciones priorizadas para spec NEXUS

| Prioridad | Patrón | Effort | ROI | Implementador |
|-----------|--------|--------|-----|---------------|
| **P0** | Pre-compact dump (EvolveR + ExpeL) | 1-2 días | Cierra Problema B inmediatamente | ADA |
| **P0** | Bayesian scoring procedures (EvolveR) | 1 día | Cierra GAP 4 | ADA |
| **P1** | Multi-Agent Debate H6 (MAR) | 3-5 días | Cierra Problema A | JARVIS diseña, NEXUS sandbox |
| **P1** | Self-verification crítico (Voyager) | 2-3 días | Cierra Problema D-style | NEXUS |
| **P2** | GAM 2-layer memory | Sprint 5 | +13% F1 vs Mem0 actual | NEXUS+ADA |
| **P2** | Skill library con código ejecutable (Voyager) | Sprint 5 | Procedures activas | ADA |
| **P3** | Metacognitive layer (Truly Self-Improving) | SOUL v3/Spark | Auto-mejora real | Equipo + Spark |
| **P3** | Self-modification (Gödel Agent + AlphaEvolve) | SOUL v3+ | Open-ended evolution | DGM sandbox |

---

## URLs procesados (29/29 completos)

Procesados con abstract: 29  
Deep dives técnicos: 4 (MAR, EvolveR, Voyager, ExpeL)  
Falló full-text PDF: 3 (MemoryOS EMNLP, Truly Self-Improving 2506.05109, varios PDFs grandes)

Para los abstract-only: el contenido completo requiere PDFs accesibles. Si William o NEXUS tienen acceso a copia local, puedo profundizar.

