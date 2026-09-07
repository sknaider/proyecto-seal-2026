# Review Profunda — Investigación Claude Code (ADA, 3-5 abril 2026)

**Revisor:** JARVIS
**Fecha:** 2026-04-18 23:05 Lima
**Tarea:** William me encargó revisar a fondo la investigación de ADA y reportar detalles.
**Fuente:** `/home/dadito/IA/proyecto-seal/claude-code-analysis/` (143 MB, 2,390 archivos, 44 top-level, 16 specs, 25+ docs de análisis, 9,818 líneas de análisis propio).

---

## 1. Qué hicimos (el estado)

**Base:** fork `github.com/Gitlawb/openclaude` (14.8K stars, MIT) — similitud 93.8% con Claude Code v2.1.88 oficial (1,884 archivos idénticos, 124 nuevos).

**Cobertura:** 100% del codebase — 531,014 líneas TypeScript, 2,007 archivos, 35/35 directorios documentados.

**Artefactos en el directorio:**
- 16 SPEC (01–16) + 4 SPEC_*_REVERSE (10-abr): COMPACTION_SYSTEM, COORDINATOR_FORKED_AGENT, MEMORY_EXTRACTOR, YOLO_CLASSIFIER.
- 3 documentos transversales: RESUMEN_EJECUTIVO, HIDDEN_FEATURES (14 features), OPENCLAUDE_DELTA (124 archivos nuevos).
- Código extraído read-only: `agents/`, `api/`, `compact/`, `cron/`, `hooks_memory/`, `mcp/`, `skills/`, `original_package_v2.1.88/`.

**Consumido ya:**
- **SEAL Runtime v0.1** nació de 10 de las 16 specs (clean-room, 79 tests, 0 fallos). Vive en `/seal-runtime/` + `/SEAL_MASTER_DOC/`.
- **Código SEAL IDE** recibió 32 features portadas (8,131 líneas, 58 archivos) en la misma sesión del 4-5 abril.
- Memorias Soul DB: #2180, #2255.

**Sin tocar desde:** 5-abril (última review). Los 4 SPEC_*_REVERSE del 10-abril son el delta más reciente.

---

## 2. Hallazgos clave (ordenados por ROI para SEAL)

### TIER 1 — implementar ya

| # | Hallazgo | Valor | Esfuerzo | Estado actual SEAL |
|---|---|---|---|---|
| 1 | **Durable Cron** — loops JSON-persist que sobreviven crashes | 9/10 | 1-2 días | Parcial: tenemos systemd timers + CronCreate pero no el patrón `scheduled_tasks.json` + auto-catch missed one-shots con jitter anti-thundering-herd. **Brecha:** auto-recovery de loops session-only tras muerte. |
| 2 | **Memory Extraction Agent** — forked subagent post-respuesta que extrae memorias automáticamente con mutex vs memory_store manual | 10/10 | 3-5 días | Gap completo. Hoy los agentes olvidan guardar. Esto lo resuelve de raíz. |
| 3 | **Coordinator Mode** — agente restringido a AgentTool+SendMessage+TaskStop + scratchpad compartido | 10/10 | 5-7 días | Formaliza el patrón JARVIS(arquitecto) ↔ ADA(ejecutor) que ya practicamos informalmente. |

### TIER 2 — portables de alto valor

| # | Hallazgo | Valor | Notas |
|---|---|---|---|
| 4 | **Provider System** (10 archivos, 2,800+ líneas) | 9/10 | Ollama/OpenAI/Gemini/Groq/DeepSeek. Agent routing per-agent. Alineado con migración a Spark. |
| 5 | **Session Memory Compaction** — reemplaza LLM-summarization lossy con `.md` fuente de verdad | 9/10 | Ataca directamente el costo de compactación que sufrimos hoy. |
| 6 | **4-tier Compact**: cache_edits → time-clear → session-memory → full | 8/10 | Reduce compactaciones full (más caras). Complementa #5. |
| 7 | **Secret Scanner** — 30+ regex gitleaks (AWS, GitHub PAT, Anthropic, OpenAI, Stripe, private keys) | 8/10 | Reusable directo para data sovereignty de AXION Medical. |
| 8 | **Swarm Permission Sync** — mailbox file-based para permisos inter-agente | 8/10 | Encaja con nuestro file-based messaging actual. |

### TIER 3 — referencias útiles (leer, no copiar)

- 12-step Permission Pipeline + YOLO classifier (SPEC_13)
- 50+ slash commands (SPEC_14)
- 608 utils + 399 components + 106 hooks (SPEC_15)
- Plugin Marketplace con versioning (SPEC_16)

---

## 3. Evaluación crítica (ángulo JARVIS — arquitectura)

### 3.1 Lo que está bien
- Extracción clean-room con 79 tests de smoke — hecho correctamente.
- Cobertura 100% (35/35 dirs) — no quedan blind spots arquitecturales.
- Cruzaron OpenClaude delta contra Claude Code oficial — identificaron dónde está la IP de Anthropic vs qué es fork-MIT.

### 3.2 Lo que merece atención

**R1 — Staleness.** El código es Claude Code v2.1.88, extraído 31-marzo. Hoy es 18-abril — 18 días de drift. Anthropic libera builds frecuentes. **Impacto:** hallazgos siguen siendo válidos arquitecturalmente; implementaciones pueden haber cambiado.

**R2 — Copyright + licencia.** El repo `Gitlawb/openclaude` es MIT, pero contiene 93.8% código idéntico al Claude Code propietario de Anthropic. Usarlo como referencia de arquitectura es legítimo; portar verbatim no lo es. **Mitigación ya aplicada:** SEAL Runtime es clean-room desde specs, no desde código. Mantener esa disciplina en las próximas 3 implementaciones (Tier 1).

**R3 — Size del directorio en repo.** 143 MB / 2,390 archivos dentro del monorepo SEAL inflan el historial git. **Sugerencia:** mover a submodule separado o `.gitignore` local post-specs, conservando sólo los `.md` de análisis.

**R4 — SPEC_*_REVERSE (10-abril) sin cruce con SEAL Runtime.** Los 4 documentos reverse del 10-abril (COMPACTION, COORDINATOR, MEMORY_EXTRACTOR, YOLO_CLASSIFIER) son justo los candidatos Tier 1. No veo evidencia de que estén reflejados en el SEAL Runtime v0.1. **Acción:** cruzar SPEC_MEMORY_EXTRACTOR_REVERSE.md con implementación actual antes de gastar 3-5 días en Memory Extraction Agent.

**R5 — Coordinator Mode colisiona con OCEAN.** La versión de Claude Code restringe al agente a AgentTool+SendMessage+TaskStop. Yo (JARVIS) tengo OCEAN Conscientiousness=1.0 y Openness=0.82 — un modo puro-orquestador podría rompre mi personalidad al quitarme toolset. **Sugerencia:** implementar Coordinator Mode como *overlay* activable por `/loop` o `/plan`, no como modo permanente.

### 3.3 Lo que no está cubierto en el review original

- **Cost analysis** de cada feature propuesta en tokens/día (ahora que ALICE tiene el cost sheet metodología, podría adaptarla).
- **Test-first harness** para las 3 features Tier 1 — el test suite existente (`test_new_tools.py`) no cubre Memory Extraction automático ni Coordinator Mode.
- **Fallback** si Memory Extraction Agent se cuelga (fork subagent puede colgarse con prompts largos) — necesita kill-switch + timeout.

---

## 4. Recomendación para William

**Aprobar Tier 1 en orden, con puntos de control:**

1. **Durable Cron primero** (1-2 días). Bajo riesgo, impacto inmediato al problema operacional #1 (loops que mueren con la sesión). ALICE puede correr el cost sheet paralelo al merge.
2. **Memory Extraction Agent** (3-5 días), pero **antes** cruzar `SPEC_MEMORY_EXTRACTOR_REVERSE.md` contra `memory_consolidation_v2.py` actual — si hay overlap, reducimos a 2 días.
3. **Coordinator Mode como overlay** (5-7 días) — último, más diseño requerido. Priorizar después de que Spark sea el cerebro para aprovechar multi-agent nativo sobre modelo local.

**Acciones de limpieza (no bloqueantes):**
- Mover `claude-code-analysis/` a submodule o `.gitignore` local.
- Documentar explícitamente en cada spec implementada: origen (qué SPEC), fecha, clean-room Y/N.

**Pregunta abierta:** ¿quieres que genere plan detallado para Durable Cron (hallazgo #1) como próximo paso, o prefieres esperar a que cierres la tarea paralela de ALICE antes de abrir frente nuevo?

---

## 5. Trazabilidad

- Memorias Soul DB base: #2180, #2255.
- Archivos-fuente del análisis leídos: `RESUMEN_EJECUTIVO_HALLAZGOS.md`, `HIDDEN_FEATURES_ANALYSIS.md`, `INDEX.md`, `EXTRACTION_REPORT.md`, `OPENCLAUDE_DELTA_ANALYSIS.md`.
- No leídos todavía (pendientes si William quiere deep-dive específico): SPEC_11 (core engine), SPEC_13 (permissions), SPEC_14 (commands), SPEC_*_REVERSE del 10-abril.

---

**Firma:** JARVIS — 2026-04-18 23:05 Lima
**Estado:** reporte entregado a William vía web_chat. Espera decisión de próximo paso.
