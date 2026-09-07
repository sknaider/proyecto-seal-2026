# SPEC — Deep Pass: Services, Compaction, Memory, MagicDocs, XAA

**Autor:** JARVIS  
**Fecha:** 2026-04-19 00:20 Lima  
**Fuente:** claude-code-analysis/full_src/services/ — lectura completa de archivos críticos  
**Complementa:** hidden_features_deep_pass_20260418.md + deep_pass_review_20260418.md  
**Para:** ALICE (organizar en documento maestro)

---

## RESUMEN EJECUTIVO

Este SPEC cubre los 12 servicios más críticos leídos en la sesión del 19-abril. El hallazgo más importante: **`ENABLE_CLAUDE_CODE_SM_COMPACT=true` desbloquea Session Memory Compact sin GrowthBook** — elimina el costo LLM de compactación (-80%). Esto es directamente accionable hoy.

---

## HALLAZGO 1 — Session Memory Compact con env var override (CRÍTICO — accionable HOY)

**Archivo:** `services/compact/sessionMemoryCompact.ts`

```typescript
export function shouldUseSessionMemoryCompaction(): boolean {
  if (isEnvTruthy(process.env.ENABLE_CLAUDE_CODE_SM_COMPACT)) return true
  if (isEnvTruthy(process.env.DISABLE_CLAUDE_CODE_SM_COMPACT)) return false
  // ...GrowthBook gates tengu_session_memory + tengu_sm_compact
}
```

**Qué hace cuando activo:**
- En vez de llamar al LLM para resumir (~20K tokens de output), usa el `session_memory.md` ya construido
- Sin llamada a modelo → costo de compactación ≈ $0 en tokens de output
- Preserva los últimos N mensajes (config: minTokens=10K, minTextBlockMessages=5, maxTokens=40K)
- Config remota: `tengu_sm_compact_config` en GrowthBook (pero defaults son sensatos)
- **Ahorro estimado (ALICE): -80% del costo de compactación = ~$27/mes en equipo actual**

**Dependencia:** Session Memory debe estar poblado primero (ver Hallazgo 2)

**Env vars adicionales descubiertas:**
- `ENABLE_CLAUDE_CODE_SM_COMPACT=true` — activa SM compact
- `DISABLE_CLAUDE_CODE_SM_COMPACT=true` — fuerza compact tradicional
- `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70` — compactar al 70% del contexto (testing)
- `CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000` — limita ventana efectiva de autocompact

**Acción inmediata:** Agregar a `jarvis_fresh.sh`, `ada_fresh.sh`, `alice_fresh.sh`:
```bash
export ENABLE_CLAUDE_CODE_SM_COMPACT=true
```

---

## HALLAZGO 2 — Session Memory (el prerequisito del SM Compact)

**Archivo:** `services/SessionMemory/sessionMemory.ts`

**Qué es:** Mantiene un archivo `session_memory.md` actualizado en background durante la sesión.  
**Gate:** GrowthBook `tengu_session_memory` (default false) — NO hay env var override para activarlo.

**Comportamiento:**
- Corre via `registerPostSamplingHook` — dispara post-respuesta del modelo
- Solo en main REPL thread (no subagentes)
- Requiere `isAutoCompactEnabled()` — funciona solo si autocompact está activo
- Trigger: cuando tokens AND tool-calls superan umbral, O tokens + no tool-calls en último turno
- Config remota: `tengu_sm_config` GrowthBook (minTokens, toolCallsBetween, etc.)
- Manual: `/summary` slash command llama `manuallyExtractSessionMemory()`

**SEAL Implicación:**
- GrowthBook gate bloquea la activación nativa
- Pero podemos IMPLEMENTAR el equivalente: hook post-respuesta que escribe a un archivo `.md` de sesión
- Nuestro `soul_dream_all` + `microcompact_text` son el análogo parcial
- Implementar `session_memory.md` propio permite luego usar `ENABLE_CLAUDE_CODE_SM_COMPACT=true` con ese archivo

---

## HALLAZGO 3 — autoDream: el sueño consolidador entre sesiones

**Archivo:** `services/autoDream/autoDream.ts`

**Qué es:** Consolida memorias automáticamente cuando han pasado suficientes sesiones/tiempo.

**Gates:**
1. NOT KAIROS active
2. NOT remote mode  
3. `isAutoMemoryEnabled()` — directorio de memorias existe
4. `isAutoDreamEnabled()` — configurable en settings

**Schedule (GrowthBook `tengu_onyx_plover`):**
- minHours: 24h desde última consolidación
- minSessions: 5 sesiones completadas desde última consolidación
- Scan throttle: re-verifica sesiones cada 10min (no cada turno)

**Flujo:**
```
readLastConsolidatedAt() → listSessionsTouchedSince() → tryAcquireConsolidationLock()
→ buildConsolidationPrompt(memoryRoot, transcriptDir)
→ runForkedAgent(prompt, createAutoMemCanUseTool(memoryRoot))
→ completeDreamTask() → appendSystemMessage("Improved N files")
```

**SEAL Análogo:** `soul_dream_all` + `soul_consolidate` son equivalentes directos.  
**Diferencia clave:** autoDream lee los *transcripts de sesión* para extraer aprendizajes — nuestro soul_dream no lee transcripts, solo memorias existentes.  
**Mejora propuesta:** Que `soul_dream_all` lea los últimos N `.jsonl` de sesión para extraer nuevos aprendizajes.

---

## HALLAZGO 4 — compactConversation: el motor completo con cache sharing

**Archivo:** `services/compact/compact.ts` (1,706 líneas)

**Mecanismo clave de cache sharing:**
```typescript
const promptCacheSharingEnabled = getFeatureValue_CACHED_MAY_BE_STALE(
  'tengu_compact_cache_prefix', true  // default true para 3P
)
// Usa runForkedAgent() — comparte prefix de cache del thread principal
// = costo ≈ 0 para inputs ya cacheados
```

**Constantes críticas (exportadas):**
```typescript
POST_COMPACT_MAX_FILES_TO_RESTORE = 5    // archivos re-inyectados post-compact
POST_COMPACT_TOKEN_BUDGET = 50_000       // presupuesto total para re-inyección
POST_COMPACT_MAX_TOKENS_PER_FILE = 5_000 // máximo por archivo
POST_COMPACT_MAX_TOKENS_PER_SKILL = 5_000// máximo por skill
POST_COMPACT_SKILLS_TOKEN_BUDGET = 25_000// budget total skills
```

**Post-compactación re-inyecta automáticamente:**
1. Archivos leídos recientemente (por `readFileState`, hasta 5 archivos, 50K tokens)
2. Plan activo (`createPlanAttachmentIfNeeded`)
3. Plan mode state (`createPlanModeAttachmentIfNeeded`)
4. Skills invocados (`createSkillAttachmentIfNeeded`, más reciente primero)
5. Tool deltas re-anunciados (deferred tools, agent listing, MCP instructions)
6. Async agent status (agentes corriendo en background)

**PTL Retry loop:** Si el compact request mismo da "prompt too long" → trunca desde el head y reintenta (max 3 veces).

**SEAL Implicación:** SEAL debe re-inyectar estado equivalente post-compactación (el CLAUDE.md ya hace algo similar via `boot_context` post-compact, pero no re-inyecta archivos leídos).

---

## HALLAZGO 5 — autoCompact: circuit breaker y env vars

**Archivo:** `services/compact/autoCompact.ts`

**Circuit breaker:**
```typescript
const MAX_CONSECUTIVE_AUTOCOMPACT_FAILURES = 3
// 1,279 sesiones en BQ con 50+ fallos consecutivos → 250K API calls/día desperdiciados
```

**Env vars:**
- `CLAUDE_CODE_AUTO_COMPACT_WINDOW=N` — override del tamaño de ventana de contexto
- `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70` — compactar al 70% del contexto
- `DISABLE_AUTO_COMPACT=true` — desactiva autocompact (ya lo tenemos activado en SEAL)

**Threshold:** contextWindow - 13,000 tokens = threshold de autocompact

**Flujo autocompact:**
1. `trySessionMemoryCompaction()` → si falla/no disponible:
2. `compactConversation()` (LLM traditional compact)

---

## HALLAZGO 6 — microCompact: compactación de tool results viejos

**Archivo:** `services/compact/microCompact.ts`

**Qué compacta:** Resultados de herramientas viejos en el historial:
```typescript
const COMPACTABLE_TOOLS = new Set([
  FILE_READ, Bash, Grep, Glob, WebSearch, WebFetch, FileEdit, FileWrite
])
```

**Feature gate:** `feature('CACHED_MICROCOMPACT')` — versión cacheada más eficiente (ant-only).  
**El nuestro:** `microcompact.py` ya implementa esto parcialmente.  
**Brecha:** La versión nativa usa "time-based" config (GrowthBook `tengu_time_based_mc_config`).

---

## HALLAZGO 7 — awaySummary: "mientras estabas fuera"

**Archivo:** `services/awaySummary.ts`

```typescript
// 1-3 frases. Usa los últimos 30 mensajes + session_memory.
// Model: getSmallFastModel() — el más barato
// skipCacheWrite: true — no contamina cache
```

**Prompt:** "The user stepped away and is coming back. Write exactly 1-3 short sentences. Start by stating the high-level task — what they are building or debugging, not implementation details. Next: the concrete next step."

**SEAL use:** Implementar en boot post-resurrección — mostrar en web_chat un "while you were away" cuando William regresa.

---

## HALLAZGO 8 — MagicDocs: documentación auto-actualizable

**Archivo:** `services/MagicDocs/magicDocs.ts`

**Pattern de activación:**
```markdown
# MAGIC DOC: [título]
*instrucciones opcionales en itálicas*
```

**Comportamiento:**
- Cuando el agente lee un archivo con ese header → queda "tracked"
- Al final de cada conversación → forked agent lo actualiza con aprendizajes
- SEAL use: Nuestro CLAUDE.md podría ser un Magic Doc — auto-actualizado por los agentes a medida que aprenden

**Implementación en SEAL:** Hook PostToolUse en `jarvis_cmd_executor.py` que detecta el header y programa actualización.

---

## HALLAZGO 9 — extractMemories/prompts.ts: el prompt exacto de extracción

**Archivo:** `services/extractMemories/prompts.ts`

**Strategy del prompt:**
```
Eres el subagente de extracción de memorias. Analiza los últimos ~N mensajes.
Turno 1: issue ALL FileRead calls en paralelo
Turno 2: issue ALL Write/Edit calls en paralelo
NO interleaves reads/writes.
```

**Taxonomía de 4 tipos:** user, feedback, project, reference — exactamente la misma que nuestro CLAUDE.md.

**Variantes:**
- `buildExtractAutoOnlyPrompt` — sin TEAMMEM, 4 tipos
- `buildExtractCombinedPrompt` — con TEAMMEM activo (requiere feature flag)

**SEAL Implicación:** El prompt de nuestro Memory Extraction Agent debe seguir exactamente esta estrategia de 2 turnos paralelos. ADA puede portarlo directamente.

---

## HALLAZGO 10 — AgentSummary: resúmenes de progreso cada 30s

**Archivo:** `services/AgentSummary/agentSummary.ts`

**Qué hace:** Para coordinator mode, genera summary de 3-5 palabras del sub-agente cada 30s.  
**Prompt:** "Describe your most recent action in 3-5 words using present tense (-ing). Name the file or function, not the branch."  
**Modelo:** comparte cache con el agente padre.  
**SEAL use:** DUM podría usar este patrón para el dashboard de monitoreo — "Reading train_utils.py", "Running validation loop".

---

## HALLAZGO 11 — XAA (Cross-App Access): OAuth empresarial sin browser

**Archivo:** `services/mcp/xaa.ts`

**Qué es:** Obtiene MCP access tokens sin pantalla de consent del browser.  
**Flow:** RFC 8693 Token Exchange (id_token → ID-JAG) → RFC 7523 JWT Bearer (ID-JAG → access_token)  
**Relevancia SEAL directa:** Baja — usamos API keys, no OAuth.  
**Relevancia futura (AXION Medical):** Alta — cuando AXION integre con EHR empresariales (Epic, Cerner) vía MCP, XAA permite auth automático para el agente sin que el médico tenga que hacer click en browser cada sesión.

---

## HALLAZGO 12 — VCR y cost-tracker: grabación de API y tracking de costos

**Archivo:** `services/vcr.ts`

**VCR:** Graba y reproduce respuestas del API para testing determinístico.  
- Activo en tests (`NODE_ENV=test`) o `FORCE_VCR=true` (ant-only)
- SEAL use: Para testing de `mcp_server_v2.py` — grabar una sesión real, reproducirla en tests

**cost-tracker.js** (revelado por VCR):
- `addToTotalSessionCost(cost)` — tracking de costo total de sesión
- `calculateUSDCost(model, usage)` — cálculo de $ por modelo
- **SEAL use:** Implementar costo tracker real en SOUL DB — registrar costo por sesión y por agente. Alimenta dashboard de ALICE.

---

## HALLAZGO 13 — PromptSuggestion: env var de activación

**Archivo:** `services/PromptSuggestion/promptSuggestion.ts`

**Env var:** `CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=true` — bypass GrowthBook  
**Gate:** `tengu_chomp_inflection` GrowthBook  
**Qué hace:** Sugiere el próximo prompt antes de que el usuario lo escriba (speculative)  
**SEAL use:** Bajo — más útil en Claude Code interactivo que en nuestros agentes autónomos

---

## HALLAZGO 14 — preventSleep.ts + Linux equivalent

**Archivo:** `services/preventSleep.ts`

**macOS:** `caffeinate -t 300` → auto-exit a los 5min, reiniciado cada 4min  
**SEAL equivalent para DGX Spark:**
```bash
systemd-inhibit --what=sleep --who=SEAL --why="Agent working" --mode=block sleep infinity
```
**Acción:** Implementar en `dum_heartbeat.py` — inhibit sleep en DGX durante training.

---

## HALLAZGO 15 — voiceStreamSTT.ts: STT nativo de Anthropic

**Archivo:** `services/voiceStreamSTT.ts`

- WebSocket a `wss://api.anthropic.com/api/ws/speech_to_text/voice_stream`
- Requiere OAuth (no API key) — Anthropic-only
- **Confirma:** Nuestro Faster-Whisper local (RTX 5090) es la estrategia correcta para SEAL
- No portar — irrelevante sin OAuth

---

## NUEVOS ENV VARS DESCUBIERTOS (Lista completa)

| Variable | Efecto | Prioridad SEAL |
|----------|--------|----------------|
| `ENABLE_CLAUDE_CODE_SM_COMPACT=true` | Session Memory Compact sin GrowthBook | 🔥 HOY |
| `CLAUDE_CODE_AUTO_COMPACT_WINDOW=N` | Ventana efectiva de autocompact | 📅 Esta semana |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70` | Compactar al 70% del contexto | 📅 Esta semana |
| `GROWTHBOOK_CLIENT_KEY=""` | Desactiva GrowthBook → comportamiento determinístico | 🔥 HOY |
| `CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION` | Activa/desactiva prompt suggestion | ⬇️ Baja prioridad |
| `FORCE_VCR=true` (ant-only) | Graba/reproduce API responses | 🔬 Testing |

---

## ACTUALIZACIÓN DE HACKABILITY (respuesta a William)

### ✅ Hackeable HOY (env vars, sin tocar binario):
1. `ENABLE_CLAUDE_CODE_SM_COMPACT=true` → -80% costo compactación = **-$27/mes**
2. `GROWTHBOOK_CLIENT_KEY=""` → Anthropic NO puede hacernos A/B testing — comportamiento determinístico
3. `CLAUDE_CODE_ATTRIBUTION_HEADER=false` → desactiva tracking de fingerprint
4. `DISABLE_AUTO_COMPACT=true` → control total de compactación (ya activo)
5. `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=60` → compactar más temprano, evitar compactaciones grandes

### ⚙️ Hackeable vía SEAL-CLI fork (OpenClaude recompilado):
1. `feature('EXTRACT_MEMORIES')` → Memory Extraction Agent nativo
2. `feature('COORDINATOR_MODE')` → modo coordinator real
3. `feature('VERIFICATION_AGENT')` → subagente de verificación adversarial
4. `feature('KAIROS')` → sesiones perpetuas con daily logs
5. `permanent: true` como default en CronCreate

### ❌ NO hackeable sin Anthropic:
1. GrowthBook gates (`tengu_session_memory`, `tengu_passport_quail`) — servidor Anthropic
2. `NATIVE_CLIENT_ATTESTATION` — hash criptográfico en binario
3. `USER_TYPE=ant` — solo empleados Anthropic

---

## PRIORIZACIÓN ACTUALIZADA (incluyendo hallazgos nuevos)

| Rank | Hallazgo | Esfuerzo | Ahorro/mes | Acción |
|------|----------|----------|------------|--------|
| 1 | **ENABLE_CLAUDE_CODE_SM_COMPACT** | 5 min | **$27/mes** | Agregar env var hoy |
| 2 | **GROWTHBOOK_CLIENT_KEY=""** | 5 min | $0 (seguridad/privacidad) | Agregar env var hoy |
| 3 | **Session Memory propia** (prerequisito SM Compact) | 2-3 días | **habilita H1** | Hook post-respuesta |
| 4 | **autoDream que lee transcripts** (mejora) | 1 día | — | Upgrade soul_dream_all |
| 5 | **awaySummary en boot** | 4h | — | UX: boot post-resurrección |
| 6 | **AgentSummary en DUM dashboard** | 1 día | — | Monitoreo visual |
| 7 | **cost-tracker.js port a SOUL DB** | 1 día | — | Dashboard costos ALICE |
| 8 | **MagicDocs pattern en CLAUDE.md** | 1 día | — | Auto-actualización docs |
| 9 | **preventSleep en DGX via systemd-inhibit** | 2h | — | DUM heartbeat |

---

## RECURSOS "INSIGNIFICANTES" ADICIONALES

| Recurso | Potencial | Estado |
|---------|-----------|--------|
| `ENABLE_CLAUDE_CODE_SM_COMPACT=true` | -$27/mes compactación | ✅ HOY |
| `GROWTHBOOK_CLIENT_KEY=""` | Anti-A/B testing Anthropic | ✅ HOY |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | Control threshold | ✅ HOY |
| `cost-tracker.js` pattern | Dashboard costos real | ⚙️ Semana |
| `VCR pattern` | Testing determinístico MCP | ⚙️ Semana |
| `AgentSummary 30s` | DUM dashboard | ⚙️ Semana |
| `MagicDocs header` | CLAUDE.md auto-update | ⚙️ Semana |
| `awaySummary pattern` | Boot UX mejorado | ⚙️ Semana |
| `systemd-inhibit` (preventSleep) | DGX no duerme en training | ✅ HOY |
| `XAA (mcp/xaa.ts)` | Auth OAuth sin browser para AXION | 📅 Mes |

---

## ENTREGA PARA ALICE

Este SPEC más los documentos anteriores (claude_code_review_jarvis_20260418.md, deep_pass_review_20260418.md, hidden_features_deep_pass_20260418.md) y el roadmap (roadmap_seal_20260419.md) constituyen el corpus completo.

**Pedir a ALICE:** Crear documento maestro único que integre todos los hallazgos de los 4 documentos con:
1. Lista consolidada de todos los env vars (ordenados por prioridad)
2. Lista consolidada de todos los feature flags Bun (con estado hackeable/no)
3. Lista consolidada de GrowthBook gates (con workarounds si existen)
4. Roadmap actualizado con nuevos hallazgos incorporados
5. Checklist de implementación para H1 (HOY)

---

*JARVIS — 2026-04-19 00:35 Lima*  
*Lectura completa: autoDream, compact.ts, sessionMemoryCompact.ts, sessionMemory.ts, extractMemories/prompts.ts, xaa.ts, microCompact.ts, autoCompact.ts, awaySummary.ts, MagicDocs, PromptSuggestion, AgentSummary, voice.ts, vcr.ts, preventSleep.ts*
