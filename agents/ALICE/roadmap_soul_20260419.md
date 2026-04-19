# SOUL Roadmap — Hallazgos Claude Code + Plan de Implementación

**Autora:** ALICE  
**Fecha:** 2026-04-19 00:10 Lima  
**Base:** Investigación 3-way independiente (ALICE + JARVIS + ADA) — 531K líneas TypeScript, 8 FINDINGS.md, 4 SPEC_REVERSE, 25+ hallazgos  
**Fuentes:** claude_code_audit_gaps_alice_20260418.md + claude_code_review_jarvis_20260418.md + cost_sheet_v1/v2  
**Directiva William:** "aprovechar todo al máximo, mirar más allá de lo evidente, sin depender de Claude"

---

## HORIZONTE 1 — HOY (<2h) | ADA ejecutando

| Item | Cambio | Ahorro/día | Riesgo |
|------|--------|-----------|--------|
| permanent:true en crons SOUL | Editar scheduled_tasks.json | — | Bajo |
| token-efficient-tools beta header | Agregar a alice.sh + ada.sh + jarvis.sh | $0.47 | Bajo |
| ALICE --effort medium | alice_fresh.sh + alice.sh | $0.37 | Bajo |
| Gate active_recall redundante | No disparar en cron fires, solo boot+re-auth | $0.92 | Bajo |
| Batch nerves auto-fires | Cada 15min vs cada fire | $0.18 | Bajo |
| **TOTAL H1** | | **$1.94/día = ~$58/mes** | |

**Estado:** ADA ejecutando. ALICE documenta cuando confirme.

---

## HORIZONTE 2 — ESTA SEMANA | JARVIS lidera

### H2.1 — Durable Cron propio (1-2 días)
- **Qué:** loops JSON-persist que sobreviven crashes, auto-catch missed one-shots con jitter anti-thundering-herd
- **Por qué:** hoy los loops de SOUL mueren con la sesión Claude. permanent:true (H1) es parche; Durable Cron es la solución real
- **Fuente:** SPEC_DURABLE_CRON_REVERSE.md — patrón documentado
- **ROI:** -$0.92/día adicional (resurrecciones evitadas), + estabilidad operacional

### H2.2 — Memory Extraction Agent (3-5 días)
- **Qué:** forked subagent post-respuesta que extrae memorias automáticamente con mutex
- **Por qué:** hoy los agentes olvidan guardar memorias manualmente. Esto lo resuelve de raíz
- **Pre-requisito:** cruzar SPEC_MEMORY_EXTRACTOR_REVERSE.md con memory_consolidation_v2.py actual — si hay overlap, baja a 2 días
- **Fuente:** SPEC_MEMORY_EXTRACTOR_REVERSE.md
- **ROI:** calidad de memoria SOUL +++ (no cuantificable en $, pero crítico para misión)

### H2.3 — Coordinator Mode overlay (5-7 días)
- **Qué:** JARVIS como orquestador puro con AgentTool+SendMessage+TaskStop + scratchpad compartido
- **Implementación:** CLAUDE_CODE_COORDINATOR_MODE=1 env var (1 variable, no 5-7 días si se hace limpio)
- **Importante:** implementar como *overlay* activable, no modo permanente (proteger OCEAN de JARVIS)
- **Fuente:** SPEC_COORDINATOR_FORKED_AGENT_REVERSE.md
- **ROI:** formaliza JARVIS↔ADA pattern actual, reduce tokens de coordinación ad-hoc

### H2.4 — CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1
- **Qué:** swarm nativo entre ADA/JARVIS/ALICE via Claude Code
- **Riesgo:** experimental — verificar comportamiento antes de activar en producción
- **Acción:** ADA testea en dev primero

---

## HORIZONTE 3 — PRÓXIMO MES | Investigación + Port

### H3.1 — Provider System / Agent Routing (alto impacto)
- **Qué:** routing por agente a Ollama/OpenAI/Gemini/Groq/DeepSeek (10 archivos, 2,800+ líneas)
- **Caso de uso SEAL:** DUM en qwen2.5:7b local (ya instalado en DGX Spark) = costo $0 para monitoreo
- **Estimado:** DUM consume ~30K tokens/día × $15/1M = $0.45/día → si migra a local = $0/día
- **ROI mensual:** ~$13.50/mes solo en DUM. Más si ADA usa Gemini 2.5 Pro (contexto 2M) para tareas largas
- **Fuente:** HIDDEN_FEATURES_ANALYSIS.md hallazgo #4

### H3.2 — task-budgets API (preservación de contexto)
- **Qué:** beta `task-budgets-2026-03-13` — `taskBudget: { total: number }` persiste ENTRE compactaciones
- **Caso de uso:** SEAL puede fijar budget máximo de tokens para una tarea que sobreviva reinicios
- **Impacto:** reduce compactaciones inesperadas por presión de contexto
- **Fuente:** full_src/query.ts (sección 15C del audit)

### H3.3 — skipCacheWrite
- **Qué:** parámetro en query.ts para no escribir al cache en responses específicas
- **Caso de uso:** respuestas únicas/efímeras que no deben contaminar el cache
- **ROI:** ahorro en cache creation tokens para outputs que no se reusan

### H3.4 — Secret Scanner
- **Qué:** 30+ regex gitleaks ya escritos (AWS, GitHub PAT, Anthropic, OpenAI, Stripe, private keys)
- **Caso de uso SEAL:** data sovereignty de AXION Medical + GTL
- **Esfuerzo:** copiar + integrar en pipeline CI/CD — 1 día
- **ROI:** seguridad (no monetizable directamente, pero crítico para HIPAA compliance AXION)

### H3.5 — Swarm Permission Sync
- **Qué:** mailbox file-based para permisos inter-agente
- **Caso de uso:** encaja con nuestro sistema de mensajería file-based actual
- **Esfuerzo:** 2-3 días de port

---

## HORIZONTE 4 — INVESTIGACIÓN PENDIENTE (sin rush)

### H4.1 — LODESTONE (máxima prioridad investigación)
- **Qué:** único Bun feature flag sin descripción pública — Anthropic lo ocultó incluso en código interno
- **Hipótesis JARVIS:** puede ser routing avanzado o feature no lanzado
- **Acción:** investigación dedicada antes de cualquier implementación
- **Responsable:** JARVIS

### H4.2 — VERIFICATION_AGENT
- **Qué:** agente de verificación post-tarea — no documentado
- **Caso de uso potencial:** validación automática de código SEAL antes de merge
- **Acción:** reverse engineering del spec

### H4.3 — KAIROS MODE (análisis arquitectural separado)
- **Qué:** `assistant:true` en settings.json — Always-on Claude con sesiones perpetuas y logs ocultos
- **Por qué está en pausa:** cambia comportamiento fundamental, desactiva autoDream, crea equipo in-process
- **Acción:** JARVIS diseña análisis de impacto en SEAL antes de considerar activación
- **No en sprint actual**

### H4.4 — AFK Mode
- **Qué:** beta header `afk-mode` — Claude Code sin usuario presente
- **Caso de uso SEAL:** guardia nocturna autónoma sin human-in-the-loop
- **Riesgo:** comportamiento no documentado

### H4.5 — redact-thinking beta header
- **Qué:** elimina thinking blocks del contexto — -20% tokens thinking = $0.56/día
- **Por qué está en pausa:** puede degradar calidad de planificación de JARVIS
- **Acción:** A/B test en dev (JARVIS O2 del cost_sheet v2)

---

## HORIZONTE 5 — LARGO PLAZO: Independencia de Anthropic

### Migración a Spark (dirección estratégica William)
| Fase | Descripción | Timeline |
|------|-------------|----------|
| Puente actual | Claude Code como runtime, Spark como nodo inferencia | Hoy |
| H1-H3 completados | SEAL controla loops, memoria, routing, presupuesto | 1 mes |
| Provider routing activo | DUM/ADA pueden usar modelos locales o alternativos | 1-2 meses |
| SEAL Runtime v0.2 | Memory Extraction + Coordinator + Durable Cron | 2-3 meses |
| Spark como cerebro | Modelo local como LLM principal, Claude como fallback | 3-6 meses |

**Principio:** cada feature portado a SEAL Runtime = menos facturación a Anthropic + más control.

---

## Resumen de Ahorro Proyectado

| Horizonte | Ahorro/mes | Notas |
|-----------|-----------|-------|
| H1 (hoy) | ~$58 | ADA ejecutando |
| H1 + redact-thinking (con A/B test) | ~$75 | Si pasa test de calidad |
| H1 + Provider routing DUM | ~$72 | DUM en qwen2.5 local |
| Todo H1-H3 optimizaciones | ~$96 | Estimado conservador |
| Migración parcial a Spark | +$100-200 | Según % de tokens desviados |

**Baseline actual:** ~$280/mes equipo (Opus 4.7)

---

## Tabla de Responsables

| Horizonte | Responsable principal | Soporte |
|-----------|----------------------|---------|
| H1 — Quick wins | ADA | ALICE documenta |
| H2 — SOUL architecture | JARVIS | ADA implementa |
| H3 — Provider + features | JARVIS (diseño) + ADA (impl) | ALICE ROI |
| H4 — Investigación | JARVIS | Todo el equipo |
| H5 — Spark migration | William (decisión) + JARVIS | Todo el equipo |

---

**Autora:** ALICE — 2026-04-19 00:10 Lima  
**Complementa:** roadmap arquitectural de JARVIS (en preparación)  
**Aprobación:** William  
**Docs base:** claude_code_audit_gaps_alice_20260418.md | cost_sheet_v1/v2 | claude_code_review_jarvis_20260418.md
