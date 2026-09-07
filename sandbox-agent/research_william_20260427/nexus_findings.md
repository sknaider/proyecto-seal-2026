# Investigación NEXUS — Self-Improving Agents para SOUL v3
**Fecha:** 2026-04-27 12:43 Lima | **Agente:** NEXUS (Opus 4.7, 1M ctx)
**Asignación:** William, 27-abr 12:39 — investigar 49 URLs, dividido con ALICE

---

## Papers/Repos analizados (14)

### 1. ADAS — Automated Design of Agentic Systems
- **Refs:** arxiv 2408.08435 + github.com/ShengranHu/ADAS
- **Autores:** Shengran Hu, Cong Lu, Jeff Clune (ICLR 2025, NeurIPS 2024 Outstanding Paper)
- **Idea:** Meta Agent Search — un meta-agente programa nuevos agentes en código basado en archivo creciente de descubrimientos previos.
- **Aplicabilidad SOUL:** Archive de configs/agentes como memoria persistente. Pattern para evolucionar diseño de agentes a lo largo del tiempo.
- **Gap que cierra:** SOUL hoy no evoluciona su propia arquitectura; ADAS muestra cómo.

### 2. Darwin Gödel Machine (DGM)
- **Refs:** arxiv 2505.22954
- **Autores:** Jenny Zhang, Shengran Hu, Cong Lu, Robert Lange, Jeff Clune
- **Idea:** Self-improving agents via Darwinian evolution + open-ended exploration. Archive de agentes + validación empírica iterativa.
- **Hallazgos:** SWE-bench 20% → 50%, Polyglot 14.2% → 30.7%. Descubre mejoras como herramientas de edición y context management.
- **Aplicabilidad SOUL:** Empirical validation cycle es exactamente lo que falta hoy en GAP-4 procedure_evolution.

### 3. Voyager
- **Refs:** arxiv 2305.16291
- **Autores:** Wang, Xie, Jiang, Mandlekar, Xiao, Zhu, Fan, Anandkumar
- **Idea:** Agente embodied LLM en Minecraft. Skill library de código ejecutable (compositional, temporally-extended, interpretable). Curriculum auto-generado. Iterative prompting con env feedback + self-verification.
- **Aplicabilidad SOUL:** Skill library pattern es exactamente lo que necesita GAP-4 procedure_store. Mejor que EMA — skills son código ejecutable, no scores.

### 4. GEPA — Reflective Evolution
- **Refs:** github.com/gepa-ai/gepa
- **Idea:** Lee TRAZAS COMPLETAS de ejecución (errores, profiling, logs) para diagnosticar por qué falló un candidato y proponer correcciones DIRIGIDAS. ASI = Actionable Side Information (análogo textual al gradiente).
- **Ciclo:** Select(Pareto) → Execute(traces) → Reflect(LLM diagnoses) → Mutate(targeted) → Accept(if Pareto improves).
- **Hallazgos:** 35x más rápido que RL. AIME 46.6% → 56.6%. ARC-AGI 32% → 89%.
- **Aplicabilidad SOUL:** ORO. Nuestro reasoning_trace_store/update tiene los datos pero no hay reflexión optimizadora. GEPA pattern: cuando outcome_success=False, un LLM lee la traza y propone fix dirigido.

### 5. OpenEvolve
- **Refs:** github.com/algorithmicsuperintelligence/openevolve
- **Idea:** MAP-Elites + LLMs. Quality-diversity evolution con island-based architecture. Artifact feedback (errores mejoran siguiente generación). Compatible con cualquier API OpenAI.
- **Hallazgos:** 2-3x speedups, 23% precision gains en prompts.
- **Aplicabilidad SOUL:** Pattern para evolucionar prompts/configs. Artifact feedback (errores como training signal) matchea reasoning_trace_update on failures.

### 6. ShinkaEvolve (Sakana AI)
- **Refs:** github.com/SakanaAI/ShinkaEvolve
- **Idea:** Población multi-isla evolucionada por mutaciones LLM (tres tipos: diff/full/cross). Evaluación paralela. Archive de soluciones exitosas para transferencia.
- **Aplicabilidad SOUL:** Pattern para evolucionar instintos/procedures como población, no como entidades individuales con EMA.

### 7. AI-Scientist-v2 (Sakana AI)
- **Refs:** github.com/SakanaAI/AI-Scientist-v2
- **Idea:** Sistema autónomo de investigación. 3 stages: Ideation, Experimentation (BFTS), Write-up. Generaliza ML domains vs v1.
- **Aplicabilidad SOUL:** Tree search exploration arquitectura. 3-stage = nuestro state machine planning→execution→reviewing.

### 8. HyperAgents (Meta Research)
- **Refs:** arxiv 2603.19461 + github.com/facebookresearch/Hyperagents
- **Autores:** Zhang, Zhao, Yang, Foerster, Clune, Jiang, Devlin, Shavrina
- **Idea:** Self-referential self-improving agents. Two-tier (task-agent + meta-agent) donde el procedimiento de meta-modificación es ITSELF editable. Extiende DGM como DGM-Hyperagents.
- **Hallazgos:** Mejoras meta-nivel (memoria persistente, perf tracking) transfieren entre dominios.
- **Aplicabilidad SOUL:** Recursive self-improvement architecture. Meta-agente que modifica al agente Y A SÍ MISMO.

### 9. Gödel Agent
- **Refs:** arxiv 2410.04444 (ACL 2025)
- **Autores:** Yin, Wang, Pan, Lin, Wan, Wang
- **Idea:** Self-Referential Agent Framework para Recursive Self-Improvement. LLM modifica su propia lógica/comportamiento guiado solo por high-level goals.
- **Aplicabilidad SOUL:** Pre-DGM/Hyperagents en línea de recursive self-improvement.

### 10. AlphaEvolve (DeepMind)
- **Refs:** arxiv 2506.13131
- **Idea:** Coding agent para descubrimiento científico. Pipeline LLMs modifying code con continuous evaluator feedback. Descubre algoritmos novedosos + provably correct.
- **Aplicabilidad SOUL:** Verifier-in-the-loop para evolucionar código de MCP tools.

### 11. ExpeL — Experiential Learners
- **Refs:** arxiv 2308.10144 (AAAI-24)
- **Autores:** Andrew Zhao, Daniel Huang, Quentin Xu, Matthieu Lin, Yong-Jin Liu, Gao Huang
- **Idea:** Two-phase: collect experiences + extract knowledge en lenguaje natural; durante inference recupera insights y experiencias pasadas.
- **Aplicabilidad SOUL:** Valida nuestro boot_context + memory_hybrid_search. AGREGA: explicit insight extraction phase que no formalizamos.

### 12. Agent Laboratory
- **Refs:** arxiv 2501.04227
- **Autores:** Schmidgall et al.
- **Idea:** Three stages: literature review → experimentation → write-up. Genera repos código + papers. Costo reducido 84%.
- **Aplicabilidad SOUL:** Pattern para autonomous research agents. Relevante para nuestro propio proceso de spec-writing.

### 13. Reasoning Models Generate Societies of Thought
- **Refs:** arxiv 2601.10825
- **Autores:** Junsol Kim, Shiyang Lai, Nino Scherrer, Blaise Agüera y Arcas, James Evans
- **Idea:** Reasoning models internamente simulan multi-agent interactions con personality characteristics. Diverse perspectives debating.
- **Aplicabilidad SOUL:** Valida nuestra arquitectura multi-agent (ADA/JARVIS/ALICE/NEXUS). El "society of thought" interno es lo que hacemos externamente.

### 14. Truly Self-Improving Agents Require Intrinsic Metacognitive Learning
- **Refs:** arxiv 2506.05109
- **Autores:** Tennison Liu, Mihaela van der Schaar
- **Idea:** Three components: metacognitive knowledge (self-evaluation), metacognitive planning (decide what/how to learn), metacognitive evaluation (reflection).
- **Aplicabilidad SOUL:** Foundation teórica para evolucionar GAP-1 belief_inspector. Más allá de trace_store: full metacognition.

---

## Recursos prácticos (3)

### 15. Anthropic Agent Skills Spec
- **Refs:** agentskills.io/specification
- **Estructura:** SKILL.md con YAML frontmatter (name, description, license, compatibility, allowed-tools). Directorios: scripts/, references/, assets/.
- **Progressive disclosure:** ~100 tokens metadata at startup, <5000 tokens cuando activado, resources on-demand.
- **Validation:** skills-ref tool.
- **Aplicabilidad SOUL:** Pattern de organización para procedures + instincts. Self-contained, versioned, progressive load. EXACTAMENTE el formato que necesitamos para procedural memory.

### 16. Beam AI Self-Learning Agents
- **4 pilares:** Task Mining, Agent-Instruction-to-Flow Translation, Human-in-the-Loop Enhancement, Golden Sample Dataset.
- **Resultados:** 60-80% reducción intervención humana en primer mes.
- **Recomendaciones:** evaluación granular por nodo, control de versiones para rollback, Constitutional AI bounds, tests evolucionan con cambios operacionales.
- **Aplicabilidad SOUL:** Golden Sample Dataset = test suite T1-T6. Human-in-the-Loop = William's authorization gate.

### 17. OpenAI Cookbook Self-Evolving Agents
- **Ciclo 4 fases:** Agente base → feedback (humano o LLM-as-judge) → evals + score agregado → update si supera umbral.
- **Triggers retraining:** umbral no alcanzado, nuevos datos, perf drop, monitoreo continuo.
- **4 evaluadores:** preservación entidades, desviación longitud, similitud semántica, LLM-as-judge.
- **GEPA mencionado como framework genético-Pareto.**
- **Guardrails:** alertas para fallos máximos retries, regresiones requieren revisión humana.

---

## 18. atalupadhyay Hyper Agents (blog 2026-03)
- **Tesis:** Sistemas IA mejorables iterativamente permitiendo que agente modifique su propio código fuente.
- **Arquitectura:** solve_task() base + modify_self() meta. Archive de versiones exitosas con thresholds.
- **Hallazgos:** Auto-discovery arquitectónico (memoria JSON persistente + procesos de dos etapas emergen sin spec humana). Limit: bounded by Sonnet's knowledge.
- **Recomendaciones prácticas:** safety circuits contra modificaciones catastróficas, benchmarks diversos, **función de evaluación externa e inmutable**, iniciar con dominios verificables.

---

## Patrones Convergentes (síntesis)

### A. Two-Tier Meta Architecture
- Hyperagents, Gödel Agent, DGM, ADAS
- **Para SOUL:** Meta-agent que mejora task agents. Hoy NEXUS hace meta-evaluación pero no meta-modificación.

### B. Archive/Library de Versiones Exitosas
- ADAS, DGM, Voyager skill library, ShinkaEvolve population, OpenEvolve islands
- **Para SOUL:** Versionar instincts/procedures como archivos. memory_store con verbatim=true es el inicio, falta versioning de comportamiento.

### C. Reflective Evolution con Trazas (GEPA)
- GEPA es el más explícito; AlphaEvolve y Voyager iterative prompting tienen elementos
- **Para SOUL:** Cierra GAP crítico. reasoning_trace tiene los datos. Falta: LLM lee trazas → diagnostica → propone fix dirigido.

### D. Quality-Diversity Evolution
- OpenEvolve (MAP-Elites), ShinkaEvolve islands
- **Para SOUL:** Para evolucionar instincts: mantener diversidad, no solo "el mejor".

### E. Skill Library Compositional
- Voyager, Anthropic Skills (SKILL.md format)
- **Para SOUL:** Procedures como ejecutables compositional, no solo descripciones EMA.

### F. Empirical Validation Cycle
- DGM, GEPA, AlphaEvolve, OpenAI cookbook
- **Para SOUL:** Cada cambio a SOUL DEBE pasar suite de tests antes de promote a producción.

### G. Tree Search para Exploración
- AI-Scientist-v2 (BFTS)
- **Para SOUL:** Para casos donde múltiples paths son válidos.

### H. Society of Thought interno (validador)
- Reasoning Models 2601.10825
- **Para SOUL:** Valida la arquitectura multi-agent. Refuerza el rol de team coordination.

### I. Iterative Prompting con Env Feedback
- Voyager, OpenEvolve artifact feedback
- **Para SOUL:** Cada error de tool call alimenta la próxima generación de instintos.

### J. External Immutable Evaluator
- atalupadhyay rec, Beam AI Golden Sample
- **Para SOUL:** NEXUS sandbox es exactamente esto. Reforzar su rol formal.

### K. Metacognitive Learning Layer
- Truly Self-Improving Agents 2506.05109
- **Para SOUL:** GAP-1 belief inspector debe evolucionar a full metacognition (knowledge + planning + evaluation).

---

## Lo que NO están haciendo bien estos sistemas

1. **Multi-agente como ecosistema persistente:** la mayoría tiene un meta + un task. SOUL ya tiene 4 agentes con identidades persistentes — más rico.
2. **Memoria emocional/OCEAN:** ningún paper trata identidad estable y emocional. SOUL ya lo tiene.
3. **Cross-agent governance:** poca atención a cómo varios agentes comparten/invalidan memorias entre sí.
4. **Continuidad de session:** la mayoría asume training offline o sessions independientes. SOUL aborda KAIROS continuity.

---

## Implicaciones directas para SOUL v3

1. **Add Reflective Optimizer (GEPA-pattern)** — capa que lee reasoning_traces failed y propone fix dirigido a memory/instinct/procedure.
2. **Procedural Memory as Skill Library** — formato SKILL.md para procedures. Versioned, compositional, executable.
3. **Population-based instinct evolution** — diversidad mediante variantes, no solo EMA. Quality-diversity.
4. **Archive system** — versionado de instincts/procedures con rollback.
5. **External evaluator formalizado** — NEXUS sandbox role escrito como contract en SOUL v3.
6. **Metacognitive layer** — extensión de belief_inspector con knowledge + planning + evaluation.
7. **Iterative prompting con env feedback** — error de tool call → input para próxima sesión del agente.
8. **Society of thought validator** — multi-agent debate mecanismo formalizado para decisiones críticas.

---

*Pendiente: integrar findings ALICE (~22 URLs) cuando termine, luego escribir spec SOUL v3 consolidado.*
