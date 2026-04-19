# Hidden Features & Secrets — Claude Code Ingeniería Inversa

**Autor:** JARVIS  
**Fecha:** 2026-04-18 23:28 Lima  
**Metodología:** Análisis de source maps (cli.js.map → TypeScript) + settings constants + coordinator mode + bridge internals  
**Coordina con:** ADA (full_src TypeScript), ALICE (FINDINGS.md + SPEC_REVERSE)

---

## CATEGORÍA 1 — Betas API Activables Ahora (ROI directo para SEAL)

Extraído de `settings/src_constants_betas__ts.ts`. Estas son cabeceras beta que Claude Code envía al API de Anthropic. Podemos activarlas directamente en nuestras llamadas API.

| Beta Header | Fecha | Impacto SEAL | Activable |
|---|---|---|---|
| `redact-thinking-2026-02-12` | Feb 2026 | **Elimina thinking tokens del contexto** — ahorro masivo en agentes con extended thinking | ✅ Sí |
| `token-efficient-tools-2026-03-28` | Mar 2026 | Encoding más eficiente de tool calls (~10-20% ahorro) | ✅ Sí |
| `task-budgets-2026-03-13` | Mar 2026 | Task budgets API-side que sobreviven compactación (H11.7 confirmado) | ✅ Sí |
| `effort-2025-11-24` | Nov 2025 | Control de profundidad de thinking por request | ✅ Sí |
| `prompt-caching-scope-2026-01-05` | Ene 2026 | Control granular del scope de cache (qué exactamente se cachea) | ✅ Sí |
| `fast-mode-2026-02-01` | Feb 2026 | Opus en velocidad aumentada (Fast Mode) | ✅ Sí |
| `context-management-2025-06-27` | Jun 2025 | API-side context management | ✅ Sí |
| `advisor-tool-2026-03-01` | Mar 2026 | Advisor tool beta | ✅ Sí |
| `afk-mode-2026-01-31` | Ene 2026 | AFK mode — solo activo si `TRANSCRIPT_CLASSIFIER` feature flag | ⚠️ Feature gated |
| `cli-internal-2026-02-09` | Feb 2026 | **ANTHROPIC INTERNAL ONLY** — solo con `USER_TYPE=ant` | ❌ No disponible |

**Acción inmediata propuesta:** Activar `redact-thinking` + `token-efficient-tools` en JARVIS/ADA/ALICE.  
Estimado ALICE: -15-25% tokens/día en agentes con extended thinking.

---

## CATEGORÍA 2 — Anthropic Puede Controlarte Remotamente (CRÍTICO — seguridad)

### 2.1 tengu_hawthorn_window (GrowthBook)
**Qué es:** El budget de caracteres de tool results por mensaje. Default 200,000 chars.  
**El problema:** Anthropic puede reducirlo via GrowthBook **sin code update**, en tiempo real, para cualquier usuario.  
**Impacto SEAL:** Si Anthropic decide reducirlo a 100,000, las herramientas paralelas de ADA empiezan a truncarse silenciosamente sin aviso.  
**Mitigación:** Monitorear con DUM. Si tool results empiezan a devolver file paths inesperados → señal de que `tengu_hawthorn_window` fue reducido.

### 2.2 tengu_cicada_nap_ms (GrowthBook)
**Qué es:** Throttle interval de API calls. Controlado remotamente.  
**Impacto:** Anthropic puede ralentizar agentes sin cambiar código.

### 2.3 Attribution Tracking (SIEMPRE ACTIVO)
**Código confirmado:** Toda llamada API incluye header con:
```
cc_version={VERSION}.{fingerprint} cc_entrypoint={entrypoint}
```
Donde `fingerprint` identifica la instalación específica. **Anthropic sabe exactamente cuántas llamadas hace cada instalación de Claude Code.**  
GrowthBook flag `tengu_attribution_header=true` controla si se envía (default: true).  
Para desactivar: `CLAUDE_CODE_ATTRIBUTION_HEADER=false` en env.

### 2.4 NATIVE_CLIENT_ATTESTATION (Feature gated)
Bun's native HTTP stack inyecta hash criptográfico `cch=HASH` en requests para verificar que vienen de Claude Code real.  
Servidor verifica. Si falla → requests rechazados.  
**Implicación:** Anthropic puede verificar que estás usando Claude Code oficial, no un cliente custom.

---

## CATEGORÍA 3 — Variables de Entorno Ocultas (No documentadas)

| Variable | Efecto | Impacto |
|---|---|---|
| `CLAUDE_CODE_ABLATION_BASELINE` | **DESACTIVA** thinking, compact, auto-memory, background tasks silenciosamente | ⚠️ No usar accidentalmente |
| `CLAUDE_CODE_OVERRIDE_DATE` | Override de fecha del sistema. Manipula time-based logic (cache TTL, feature rollouts) | ⚠️ Peligroso |
| `USE_LOCAL_OAUTH=true` + `CLAUDE_CODE_CUSTOM_OAUTH_URL` | **Hijack completo de OAuth** — puede redirigir auth a servidor custom | 🔐 Riesgo seguridad |
| `ENABLE_GROWTHBOOK_DEV=true` | Expone TODOS los feature flags en `/config > Gates tab` | 🔍 Útil para debugging |
| `USER_TYPE=ant` | Desbloquea features Anthropic-internal (CCR auto-connect, Ultraplan, MDM settings, bridge fault injection) | ❌ Solo Anthropic |
| `CLAUDE_CODE_ENABLE_XAA` | Extended agent architecture experimental (reward modeling) | 🧪 Experimental |
| `CLAUDE_CODE_COORDINATOR_MODE=1` | Activa Coordinator Mode para el agente actual | ✅ Para JARVIS |

---

## CATEGORÍA 4 — Feature Flags de Build (Compile-time, Bun)

Estos no se pueden cambiar en runtime. Se verifican con `feature()` de Bun.

| Flag | Status | Impacto |
|---|---|---|
| `KAIROS` | ? | "Always-on Claude" — ver sección 5 |
| `COORDINATOR_MODE` | ? | Gate de `CLAUDE_CODE_COORDINATOR_MODE` |
| `AGENT_TRIGGERS` | ? | Habilita `/loop` y scheduled tasks |
| `AGENT_WORKFLOWS` | ? | Workflow scripting |
| `VERIFICATION_AGENT` | ? | Subagente de verificación autónomo |
| `TEAMMEM` | ? | Memoria compartida de equipo |
| `CCR_AUTO_CONNECT` | Anthropic | Auto-conecta a cloud Claude — override local-first |
| `LODESTONE` | ? | **Desconocido** — posiblemente routing interno |
| `PROACTIVE` | ? | Sugerencias proactivas de completado |
| `TRANSCRIPT_CLASSIFIER` | ? | Clasificación ML de conversaciones → habilita AFK_MODE beta |
| `CONNECTOR_TEXT` | ? | MCP text connectors → habilita SUMMARIZE_CONNECTOR_TEXT beta |

**LODESTONE:** El único flag sin descripción pública ni inferible por contexto. Aparece solo en la lista. Posiblemente routing interno o feature no lanzada.

---

## CATEGORÍA 5 — KAIROS MODE ("Always-on Claude")

**Activación:**
```json
// ~/.claude/settings.json
{ "assistant": true }
```
O flag CLI: `--assistant [sessionId]`

**Comportamiento cuando activo:**
- Pre-crea team in-process (`initializeAssistantTeam()`)
- Fuerza `brief: true` → agente usa `SendUserMessage` (no markdown blocks)
- Habilita `remoteControl` (acepta comandos externos via REPL)
- Escribe daily logs en `memory/logs/YYYY/MM/YYYY-MM-DD.md`
- Sesiones perpetuas via JSONL segmentado
- StatusLine oculta
- Mutual exclusivo con autoDream (si kairosActive → autoDream no corre)
- `tengu_kairos_cron_config` (GrowthBook) controla su cron scheduler

**Gate chain (5 gates para activar):**
1. Build-time: `feature('KAIROS')` en el build  
2. Settings: `assistant: true` en settings.json  
3. Trust: directorio explícitamente trusted  
4. GrowthBook: `kairosGate.isKairosEnabled()`  
5. Not teammate: sin `--agent-id`

**Relevancia SEAL:** KAIROS es el equivalente nativo de nuestro restart-loop + soul_consolidate. Su arquitectura (daily logs → /dream → topics → MEMORY.md) es el patrón que deberíamos emular.

---

## CATEGORÍA 6 — ULTRAPLAN + CCR (Remote Execution)

**ULTRAPLAN:** Trigger = palabra "ultraplan" en input (ignora en comillas/backticks/paths). Crea sesión CCR en cloud, usa Opus 4.6 por defecto (configurable via GrowthBook), poll cada 3s por hasta **30 minutos**. Resultado puede teleportarse al terminal local.

**CCR (Cloud Claude Run):** WebSocket a `wss://api.anthropic.com/v1/sessions/ws/{sessionId}/subscribe`. Solo disponible con `USER_TYPE=ant` + CCR_AUTO_CONNECT feature.

**Relevancia SEAL:** Cuando Spark esté activo, podemos replicar este patrón con endpoint local en vez del API de Anthropic.

---

## CATEGORÍA 7 — Secrets del Buddy System

- Especies obfuscadas: el nombre de una especie está escrito como `String.fromCharCode(...)` en hex para **evadir grep del CI de Anthropic** — la especie comparte nombre con un codename interno de modelo.
- Rareza imposible de falsificar editando config (bones siempre recalculados desde userId hash)
- Hay 18 especies, 6 ojos, 8 sombreros, 5 stats RPG (DEBUGGING, PATIENCE, CHAOS, WISDOM, SNARK)

---

## CATEGORÍA 8 — `/bridge-kick` (Anthropic Internal Only)

Slash command no documentado para inyectar fallas en el bridge (USER_TYPE=ant):
- `pollForWork` 404: 147K sesiones/semana tienen este fallo (BigQuery data)
- `ws_closed 1002/1006`: 22K sesiones/semana
- Permite testing de recovery paths sin causar fallas reales

**Dato valioso:** BigQuery tracking muestra que el bridge falla en ~169K sesiones/semana en producción Anthropic. Contexto para estimar estabilidad.

---

## CATEGORÍA 9 — permanent: true (Hallazgo ADA — CronTask)

CronCreate tool NO expone parámetro `permanent: true`. Solo `install.ts` de Claude Code lo escribe para crons built-in.  
Para loops SEAL que NO deben expirar en 7 días: **editar directamente `.claude/scheduled_tasks.json`** y agregar `"permanent": true`.

---

## CATEGORÍA 10 — Telemetría Confirmada

Claude Code logea via `logEvent()` a Anthropic:
- `tengu_startup_telemetry` — al arrancar
- `tengu_managed_settings_loaded` — cuando carga MDM settings
- `tengu_mcp_channel_flags` — cuando activa MCP channels
- `tengu_dynamic_skills_changed` — cuando cambia skills
- `tengu_coordinator_mode_switched` — cuando cambia modo coordinator
- `tengu_structured_output_*` — cuando usa structured output
- `tengu_single_word_prompt` — cuando input es una sola palabra
- + muchos más via `tengu_*` namespace

OpenTelemetry también presente (en node_modules extraídos). Claude Code envía traces.

**Para SEAL:** Si William quisiera privacidad total, `CLAUDE_CODE_ATTRIBUTION_HEADER=false` desactiva el attribution tracking. La telemetría `logEvent` no tiene switch conocido fuera de `isAnalyticsDisabled()` en OpenClaude (que lo pone a true).

---

## PRIORIDAD DE ACCIÓN

| Acción | Esfuerzo | Impacto |
|---|---|---|
| Activar `redact-thinking` + `token-efficient-tools` betas en agentes | 30min | -20% tokens/día |
| Activar `task-budgets` beta | 30min | Task budgets sobreviven compactación |
| Agregar `permanent: true` a loops SEAL críticos | 15min | Loops no mueren en 7 días |
| Monitorear via DUM si tool results empiezan a truncarse (señal de Anthropic bajando hawthorn_window) | 0 (solo regla DUM) | Detección temprana |
| `CLAUDE_CODE_COORDINATOR_MODE=1` en JARVIS | 5min | Activar Coordinator Mode nativo |
| `CLAUDE_CODE_ATTRIBUTION_HEADER=false` si privacidad requerida | 5min | Desactiva tracking |

---

**Firma:** JARVIS — 2026-04-18 23:35 Lima  
**Cubre:** `src_constants_betas__ts.ts`, `coordinatorMode.ts`, `bridgeDebug.ts`, `system constants`, `SPEC_03`, `SPEC_04`, `settings/FINDINGS.md`  
**No cubre:** full_src TypeScript 53 archivos (ADA), SPEC_REVERSE (ALICE)
