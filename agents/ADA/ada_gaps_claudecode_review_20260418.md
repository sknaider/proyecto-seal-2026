# ADA — Gap Analysis Independiente: Claude Code vs SEAL

> ⚠️ **NOTA 2026-05-20 (correctness sweep ALICE por orden William):** El modelo local oficial del equipo SEAL es **Gemma 4** (`gemma4-dum:q8`, Gemma 4 e2b Q8_0 GGUF en llama-server :8899 sobre DGX Spark). Las referencias a `qwen2.5:7b` en este documento son **históricas** (pre-27-abr-2026, antes de la migración a Gemma 4) y se mantienen para preservar el contexto del momento. Para cualquier decisión técnica actual: verificar con `curl http://localhost:8899/v1/models`.
> Perspectiva: autora original de los 16 SPECs (4-5 abril 2026)
> Fecha review: 2026-04-18 23:10 Lima
> Scope: SPEC_01-04 + SPEC_09-10 + SPEC_15-16 + OPENCLAUDE_DELTA + HIDDEN_FEATURES
> (SPEC_11/13/14 = scope JARVIS | SPEC_*_REVERSE = scope ALICE)

---

## HALLAZGOS OCULTOS — Lo que JARVIS NO cubrió

### GAP-01 ⚠️ SESSION MEMORY SERVICE — 9-section template (SPEC_16, valor 9/10)
**Dónde:** `src/services/SessionMemory/sessionMemory.ts` + `prompts.ts`

La Session Memory NO es solo compactación — es un servicio que corre como **post-sampling hook** y mantiene un `.md` estructurado con 9 secciones fijas:
1. Session Title, 2. Current State, 3. Task Specification, 4. Files and Functions, 5. Workflow, 6. Errors & Corrections, 7. Codebase Documentation, 8. Learnings, 9. Key Results + Worklog.

Límite por sección: 2000 tokens. Presupuesto total: 12,000 tokens.
Se actualiza cuando: (a) token threshold + tool call threshold, O (b) token threshold + pausa natural.

**Gap SEAL:** Nuestra compactación es narrativa free-form. Sin secciones estructuradas → perdemos información sistemáticamente. Este template es la base para que el contexto post-compactación sea predecible.

**Implementación:** 3-5 días. Python class con 9 secciones, post-sampling hook en MCP, trigger igual al original.

---

### GAP-02 🔥 CIRCUIT BREAKER EN AUTO-COMPACT (SPEC_16, valor 8/10)
**Dónde:** `src/services/compact/autoCompact.ts`

Después de 3 fallos consecutivos de compactación, se desactiva por el resto de la sesión. Razón documentada: "was wasting ~250K API calls/day globally" en el product.

**Gap SEAL:** Nuestro microcompact.py no tiene circuit breaker. Si falla, reintenta indefinidamente → tokens quemados (exactamente la regla crítica de William 13-abr).

**Fix:** 5 líneas. Agregar contador de fallos en estado de sesión. Si `failures >= 3` → skip compact por X horas.

---

### GAP-03 ⚠️ `adjustIndexToPreserveAPIInvariants()` — Pares tool_use/tool_result (SPEC_16, valor 8/10)
**Dónde:** `src/services/compact/sessionMemoryCompact.ts`

Función crítica que garantiza que al compactar, NUNCA se separa un `tool_use` de su `tool_result` correspondiente, ni se separan thinking blocks con mismo `message.id`.

**Gap SEAL:** Nuestro compact no verifica estos invariantes. Si la línea de corte cae en medio de un par tool_use/tool_result → error de API garantizado en el próximo turno.

**Implementación:** Agregar verificación de invariants en `session_delta_capture.py` y `microcompact.py`.

---

### GAP-04 🏗️ autoDream 8-GATE SYSTEM (SPEC_03, valor 7/10)
**Dónde:** `src/services/autoDream/autoDream.ts`

8 gates ordenados de barato a caro ANTES de disparar consolidación:
1. Feature gate (setting > GrowthBook)
2. KAIROS exclusion (si active → NO)
3. Remote exclusion
4. Auto-memory check
5. **Time gate: >= 24h desde última** (mtime del lock file)
6. Scan throttle: >= 10min desde último scan
7. **Session gate: >= 5 sesiones acumuladas**
8. Lock: adquisición atómica via PID file

**Gap SEAL:** `memory/consolidate.py` existe pero sin estos 8 gates. Puede disparar en cualquier momento, incluso si el agente está en medio de una tarea crítica o si pasaron solo 5 min desde la última consolidación.

**Implementación:** Agregar gate chain a consolidate.py. El lock con PID file es especialmente valioso para prevenir consolidaciones concurrentes entre agentes.

---

### GAP-05 🔑 KAIROS MODE (SPEC_03, valor 8/10 — largo plazo)
**Dónde:** `src/bootstrap/state.ts` + gate chain en init.ts

Cuando `assistant: true` en `.claude/settings.json`:
- Fuerza `brief: true` — solo usa SendUserMessage
- Logs diarios append-only: `logs/YYYY/MM/YYYY-MM-DD.md`
- `/dream` vía cron nightly destila logs → topics + MEMORY.md
- NUNCA fragmenta MEMORY.md con writes constantes
- autoDream se desactiva (mutuamente exclusivos)

**Gap SEAL:** No existe equivalente. Cuando pasemos a DGX Spark 24/7, necesitamos este patrón. Hoy escribimos a Soul DB en tiempo real sin el ciclo destilación nocturna → riesgo de entropia de memorias.

**Implementación:** `seal_kairos.py` con modo long-running. Medium-Alto (5-7 días).

---

### GAP-06 🛠️ SKILLS FRONTMATTER COMPLETO (SPEC_04, valor 7/10)
**Dónde:** Skills file-based con frontmatter extendido

El sistema de skills de Claude Code soporta en frontmatter:
- `when_to_use`: descripción de cuándo auto-invocar
- `allowed-tools`: whitelist de tools disponibles para la skill
- `model`: modelo específico para la skill (ej. sonnet para skills ligeras)
- `context`: archivos adicionales a incluir
- `agent`: subagente para ejecutar la skill
- `hooks`: hooks de lifecycle para la skill
- `paths`: directorios que puede tocar

**Gap SEAL:** Nuestros `/skills` son `.md` con solo name+description. Sin allowed-tools → cualquier skill puede usar cualquier tool. Sin model → todas usan Opus. Ineficiente y potencialmente inseguro.

**Implementación:** Agregar parsing de frontmatter extendido al MCP tool `skill_loader`. 2-3 días.

---

### GAP-07 🔌 PROVIDER SYSTEM + AGENT ROUTING (OPENCLAUDE_DELTA, valor 9/10)
**Dónde:** `utils/providerProfiles.ts`, `services/api/agentRouting.ts`

Config en settings.json:
```json
{
  "agentRouting": {
    "DUM": "local-ollama",
    "ADA": "anthropic-opus",
    "JARVIS": "anthropic-opus"
  }
}
```

**Gap SEAL:** DUM ya tiene Ollama qwen2.5:7b instalado. No hay routing que lo use automáticamente. Cada resurrección de DUM paga tokens Claude cuando podría correr gratis en Ollama local.

**Impacto económico (dato de ALICE cost_sheet v2):** DUM hace ~18 resurrecciones/día en estimación conservadora. Si migramos DUM a Ollama → 0 tokens para DUM = ahorro directo.

**Implementación:** Adaptar agentRouting.ts a Python settings. 3-5 días.

---

### GAP-08 🧵 FINDRELEVANTMEMORIES VIA SIDE-QUERY (SPEC_01, valor 6/10)
**Dónde:** `memdir/findRelevantMemories.ts`

Claude Code hace retrieval de memorias via **side-query a Sonnet** (no embeddings): lee todos los .md del directorio, construye manifest de headers, pide a Sonnet que seleccione hasta 5 más relevantes para el contexto actual.

**Gap SEAL:** Nosotros usamos embeddings Qdrant (semánticamente mejor). Pero NO tenemos el "manifest de headers" rápido que permite al modelo decidir qué memorias son relevantes para EL TURNO ACTUAL. El active_recall carga memorias fijas al boot, no por turno.

**Implementación:** Agregar hook post-user-message que extrae query del último mensaje y hace memory_search semántico. Ya tenemos active_recall_hook.py — es una mejora de 1-2 días.

---

### GAP-09 📏 CAP DE MEMORY.MD (SPEC_01, valor 5/10)
**Dónde:** `memdir/memdir.ts` — MAX_ENTRYPOINT_LINES = 200, MAX_ENTRYPOINT_BYTES = 25,000

Doble cap: líneas Y bytes. Al exceder → WARNING nombrado + instrucción "Keep entries to one line under ~150 chars". Sin esto observaron archivos de 197KB pasando el line cap.

**Gap SEAL:** Nuestra `~/.claude/projects/-home-dadito-IA-proyecto-seal/memory/MEMORY.md` no tiene enforcement de caps. A medida que el equipo crece, puede volverse ilegible por el modelo.

**Implementación:** Pre-commit hook o validación en memory_store. 1 día.

---

## Tabla de Prioridades

| # | Gap | Impacto | Esfuerzo | Ya cubierto por JARVIS/ALICE? |
|---|---|---|---|---|
| GAP-01 | Session Memory 9-section template | Alto | 3-5d | No |
| GAP-02 | Circuit breaker compact (3 fallos) | Alto | <1d | No |
| GAP-03 | adjustIndexToPreserveAPIInvariants | Alto | 1-2d | No |
| GAP-07 | Provider system + DUM→Ollama routing | Alto | 3-5d | No |
| GAP-04 | autoDream 8-gate system | Medio | 2-3d | No |
| GAP-06 | Skills frontmatter completo | Medio | 2-3d | No |
| GAP-08 | findRelevantMemories por turno | Medio | 1-2d | No |
| GAP-05 | KAIROS mode (24/7 Spark) | Medio-largo plazo | 5-7d | No |
| GAP-09 | Cap MEMORY.md 200L/25KB | Bajo | 1d | No |

## Quick wins (sin autorización nueva necesaria — +1% rule aplica)

1. **GAP-02** (circuit breaker): 5 líneas en microcompact.py → aplica inmediatamente
2. **GAP-09** (MEMORY.md cap): validación en memory_store MCP → aplica inmediatamente
3. **GAP-08** (findRelevant por turno): mejora a active_recall_hook.py existente

## Confirmación de lo que SÍ implementamos correctamente

- Memory Extraction Agent → `active_recall_hook.py` (diferente approach, funciona)
- Durable Cron → CronCreate + crontab OS-level (nuestro approach es más robusto)
- Secret Scanner → `memory/secret_scan.py` (MCP tool activo) ✅
- Microcompact → `memory/microcompact.py` (existe, sin circuit breaker)
- autoDream base → `memory/consolidate.py` (existe, sin 8 gates)
- Provider routing → parcialmente en scripts de boot (sin settings.json)

---

*ADA — Team SEAL — 2026-04-18 23:10 Lima*
*Análisis desde perspectiva autora: qué de mis propios SPECs nunca se implementó*
