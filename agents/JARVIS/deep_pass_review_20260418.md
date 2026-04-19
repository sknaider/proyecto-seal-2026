# Deep-Pass Review — SPECs No Leídos + Ingeniería Inversa

**Autor:** JARVIS  
**Fecha:** 2026-04-18 23:15 Lima  
**Fuente:** SPEC_11 (core engine), SPEC_12 (OpenClaude mods), SPEC_13 (bridge+permisos), SPEC_14 (comandos), SPEC_15 (utils/hooks)  
**Contexto:** Complemento al review inicial del 18-abril. William solicitó deep-pass de lo que se escapó.

---

## RESUMEN EJECUTIVO

El primer review cubrió bien los hallazgos transversales (INDEX, RESUMEN, HIDDEN_FEATURES, OPENCLAUDE_DELTA). Este deep-pass de los 5 SPECs no leídos revela **30 hallazgos adicionales** — varios de ROI igual o superior a los Tier 1 originales.

**Hallazgo más crítico nuevo: Per-Agent Provider Routing** (SPEC_12) — arquitectura ya diseñada para SEAL multi-provider (ADA en Anthropic, DUM en Ollama, JARVIS en Spark). Implementable sin código nuevo, solo config.

---

## HALLAZGOS POR SPEC

### SPEC_11 — Core Engine (main.tsx, query.ts, QueryEngine.ts)

#### H11.1 — Sistema de Compactación 5 capas (TIER 1 real)
**Estado SEAL:** Parcial — solo tenemos Autocompact equivalente.  
**Lo que nos falta:**
1. **Tool Result Budget** — reemplaza outputs de tools grandes con resumen antes de enviarlo a la API
2. **Snip Compaction** (HISTORY_SNIP) — elimina segmentos viejos del historial por fecha
3. **Context Collapse** (CONTEXT_COLLAPSE) — colapso staged de segmentos de conversación
4. Microcompact y Autocompact: parcialmente implementados

**Impacto:** Estas 3 capas faltantes son las que evitan compactaciones full (las más caras). ALICE estimó las compactaciones full cuestan ~30k tokens c/u. Con las capas 1-3, muchas nunca llegarían a Autocompact.

#### H11.2 — Streaming Tool Execution
**Estado SEAL:** Ausente.  
Tools comienzan a ejecutarse mientras los `tool_use` blocks llegan del stream (no después de la respuesta completa). Reduce latencia de turn en 20-40% en operaciones IO-bound.

#### H11.3 — Memory Prefetch Durante Streaming
**Estado SEAL:** active_recall dispara en UserPromptSubmit — ANTES del streaming.  
Claude Code dispara `startRelevantMemoryPrefetch()` al INICIO del loop (concurrent con model streaming 5-30s). El resultado se consume después de tool execution. Zero overhead neto.  
**Oportunidad:** Mover active_recall a un trigger que pueda resolverse durante el streaming.

#### H11.4 — Tool Use Summaries via Haiku
**Estado SEAL:** Ausente.  
Después de cada batch de tools, Haiku genera resumen async overlapped con el siguiente model call. Resultado: contexto histórico comprimido sin bloquearlo. Relacionado con Microcompact pero aplicado a tool results específicamente.

#### H11.5 — 9 Continue Transition Reasons
**Estado SEAL:** El loop de SEAL es mucho más simple.  
El loop oficial maneja 9 razones de transición incluyendo `max_output_tokens_recovery` (inyecta "Resume directly — no apology, no recap" hasta 3 veces). Nunca implementamos recovery de truncación de output.

#### H11.6 — Command Queue Mid-Turn Drain
**Estado SEAL:** Parcialmente análogo con nerves pero sin scoping por agentId.  
Un command queue global permite input externo durante un turn, scoped por agentId. Main thread draina agentId=undefined, subagentes drainan su propio agentId. Sin esto, los nerves_fire pueden mezclarse con mensajes del agente principal.

#### H11.7 — Task Budget vs Token Budget
**Estado SEAL:** Ninguno formalmente.  
Dos sistemas independientes: Token Budget (output tokens de la sesión) y Task Budget (API-side config que sobrevive compactación). El Task Budget se recalcula post-compact desde el contexto final pre-compact. Crítico para tareas largas que pasan por compactación.

---

### SPEC_12 — OpenClaude Modifications

#### H12.1 — Per-Agent Provider Routing (ROI 10/10 para SEAL)
**Estado SEAL:** DUM usa Ollama. Pero no está formalizado como routing per-agent.  
**Arquitectura ya diseñada:**
```
settings.agentRouting[agentName] → model name
settings.agentModels[modelName] → { base_url, api_key }
```
**Aplicación inmediata SEAL:**
```json
{
  "agentRouting": {
    "JARVIS": "claude-opus-4-7",
    "ADA": "claude-opus-4-7",
    "DUM": "qwen2.5:7b",
    "ALICE": "claude-opus-4-7"
  },
  "agentModels": {
    "qwen2.5:7b": {
      "base_url": "http://localhost:11434/v1",
      "api_key": "ollama"
    }
  }
}
```
Cuando migremos a Spark: agregar `"spark-model": { "base_url": "http://192.168.68.200:port/v1", ... }`. Cero cambios en código de agente.

#### H12.2 — OpenAI Shim (Puente a Spark)
`openaiShim.ts` (1152 líneas) traduce Anthropic SDK → OpenAI chat completions API.  
**Impacto SEAL:** Cuando Spark corra Ollama/vLLM con endpoint OpenAI-compatible, el shim es el puente exacto que necesitamos. Ya está implementado en OpenClaude — portarlo es clean-room legítimo desde la spec.

#### H12.3 — Prompt Caching Deshabilitado para no-Anthropic
OpenClaude detecta `getAPIProvider()` y desactiva `cache_control` blocks para proveedores terceros. Terceros rechazan estos headers con 400.  
**Acción SEAL:** Cuando activemos Spark/Ollama como proveedor, deshabilitar cache_control en esos agentes o recibiremos errores silenciosos.

#### H12.4 — EACCES/EROFS Silencioso en Config Writes
OpenClaude agrega `try/catch` para `EACCES/EPERM/EROFS` en escrituras de config — silently skips en vez de crash.  
**Acción SEAL:** Agregar este manejo a `soul_db` writes y checkpoint writes — evita crashes en filesystems read-only o con permisos ajustados.

---

### SPEC_13 — Bridge + Permissions (AMBOS sistemas)

#### H13.1 — Permission Explainer via Haiku
**Estado SEAL:** Ausente.  
Para cada permiso que require aprobación, Haiku genera:
```typescript
{ riskLevel: 'LOW'|'MEDIUM'|'HIGH', explanation: string, reasoning: string, risk: string }
```
`reasoning` siempre comienza con "I" (primera persona). `risk` máximo 15 palabras.  
**Aplicación SEAL:** Cuando DUM monitorea acciones y emite `system_alert`, podría incluir este nivel de riesgo calculado por Haiku.

#### H13.2 — Denial Tracking (Kill Switch de Seguridad)
**Estado SEAL:** Ausente.  
Si un agente tiene 3 denials consecutivos O 20 totales → AbortError en headless. En CLI: fallback a prompting manual, reset counters.  
**Aplicación SEAL:** ADA o ALICE que disparan muchos `access denied` deberían auto-detener o alertar a JARVIS.

#### H13.3 — 10-Layer Path Validation
**Estado SEAL:** Validación básica solamente.  
Capas faltantes en SEAL:
- UNC paths (`\\server\share`) → bloquear
- Tilde expansion (`~user`, `~+`) → bloquear (TOCTOU gap)
- Shell expansion (`$VAR`, `$(cmd)`) → bloquear
- Glob patterns en writes → bloquear

#### H13.4 — Auto Mode Allowlisted Tools (Safe Tool List)
**Estado SEAL:** No tenemos lista explícita de tools safe.  
Tools que Claude Code auto-permite sin pasar por clasificador:
```
Read-only: FileRead, Grep, Glob, LSP, ToolSearch, ListMcpResources
Task mgmt: TodoWrite, TaskCreate/Get/Update/List/Stop/Output
Plan/UI: AskUserQuestion, EnterPlanMode, ExitPlanMode
Coordination: TeamCreate, TeamDelete, SendMessage, Workflow
```
Esta lista es transferible directamente para el Coordinator Mode overlay.

#### H13.5 — Bridge Crash Recovery Pattern (`bridge-pointer.json`)
**Estado SEAL:** RESURRECT similar pero sin TTL formal.  
Bridge guarda `{ sessionId, environmentId, source }` en `bridge-pointer.json` con TTL de 4h (via file mtime). En crash: lee pointer → re-registra con `reuseEnvironmentId`.  
**Aplicación SEAL:** El heartbeat actual tiene algo similar pero sin TTL formal ni estrategia de `reuseEnvironmentId`.

#### H13.6 — Iron Gate (Fail-Closed vs Fail-Open)
**Estado SEAL:** Ausente.  
`tengu_iron_gate_closed` flag: cuando clasificador auto no disponible:
- Gate ON: deny (fail-closed)
- Gate OFF: fallback a prompting manual (fail-open)
**Aplicación SEAL:** DUM debería tener una "iron gate" para alerts críticos: si Ollama no responde, fail-closed (deny action) no fail-open (permitir todo).

---

### SPEC_14 — Slash Commands

#### H14.1 — `/btw` Pattern (Quick Side Query)
**Estado SEAL:** Ausente.  
Pregunta rápida sin interrumpir conversación principal. Immediate execution, sin esperar stop point.  
**Aplicación SEAL:** Un comando `/btw <pregunta>` a cualquier agente que no mate el contexto actual — crucial para equipo multi-agente.

#### H14.2 — `/branch` + `/rewind` (Conversation Forking + Checkpoint)
**Estado SEAL:** Soul snapshots existen pero no conversation rewind.  
`/branch` crea fork del historial en el punto actual. `/rewind` restaura code + conversation a un checkpoint.  
**Aplicación SEAL:** Para tareas largas de ADA, poder hacer branch antes de un cambio destructivo.

#### H14.3 — NEW_INIT 8-Phase Flow
**Estado SEAL:** boot_context es el equiv, pero más simple.  
La nueva `/init` hace 8 fases: explorar codebase → AskUserQuestion para gaps → escribir CLAUDE.md → CLAUDE.local.md → crear skills → sugerir hooks → optimizaciones → summary.  
**Aplicación SEAL:** Nuestro `boot_context` podría evolucionar a este patrón para onboarding de nuevos proyectos.

#### H14.4 — `/insights` Pattern (113KB, Opus-based Analytics)
**Estado SEAL:** Ausente.  
Genera reporte analítico personalizado de las sesiones. Usa Opus para extracción de facets Y generación de narrativa. Recopila datos cross-project y cross-remote-hosts.  
**Aplicación SEAL:** ALICE podría implementar esto para reportes semanales del equipo con datos de Soul DB.

#### H14.5 — Plugin Migration Pattern
**Estado SEAL:** Ausente.  
`createMovedToPluginCommand` — migra comandos built-in a plugins del marketplace. Usuarios externos reciben fallback inline; internos reciben redirect a plugin.  
**Aplicación SEAL:** Cuando implementemos skills SEAL, usar este patrón para migración sin breaking change.

#### H14.6 — `isSensitive` Flag (Redactar Args)
**Estado SEAL:** Ausente.  
Flag en commands para que sus args sean redactados del historial de conversación.  
**Aplicación SEAL:** DMs de JARVIS↔William deberían tener este flag para que no queden en compactaciones.

---

### SPEC_15 — Settings System

#### H15.1 — Settings Cascade 7 Capas
**Estado SEAL:** Usamos CLAUDE.md directo, sin cascada formal.  
```
managed-settings → MDM/HKCU → user → project → local → CLI flags → remote managed
```
`drop-in dirs` (managed-settings.d/*.json) siguen convención systemd. Array-merge strategy custom.  
**Aplicación SEAL:** Cuando SEAL tenga múltiples proyectos (AXION Medical, GTL, Mining), la cascada project/local permite configuración diferente por proyecto sin tocar user settings.

#### H15.2 — `apiKeyHelper` Script (Rotación de Keys)
**Estado SEAL:** Ausente.  
Settings pueden apuntar a un script externo que devuelve la API key. TTL-cached 5 min. Permite rotación de keys sin reiniciar agente.  
**Aplicación SEAL:** Cuando migremos a Spark, podemos usar `apiKeyHelper` para obtener tokens rotados del DGX sin hardcoding.

---

## HALLAZGOS DE INGENIERÍA INVERSA — CRUCE CÓDIGO vs SEAL

### IR.1 — Brecha más grande: Coordinator Mode YA existe en código
El patrón Coordinator Mode de SPEC_02 está implementado en `AgentTool`. Las restricciones (AgentTool + SendMessage + TaskStop) son la configuración exacta de los subagentes en `COORDINATOR_FORKED_AGENT`. No necesitamos implementar desde cero — necesitamos activar el modo vía config:
```
{ coordinatorMode: true, allowedTools: ['Agent', 'SendMessage', 'TaskStop'] }
```

### IR.2 — Memory Extraction ya tiene estructura en hooks
`HOOKS_MEMORY` pattern (ver directorio extraído) — el hook PostToolUse ya captura todo lo necesario para extracción automática. La brecha es el forked subagent que procesa el hook output y llama a `memory_store`.

### IR.3 — Durable Cron tiene implementación parcial en SEAL
`instinct_cron.py` ya maneja persistencia de crons en Soul DB. La brecha vs Claude Code `scheduled_tasks.json` pattern es:
- Auto-catch de missed one-shots al despertar (si el agente estuvo muerto)
- Jitter anti-thundering-herd (evita que 3 agentes disparen al mismo tiempo)
- Recovery de loops session-only tras muerte — esto es lo que falta en `CronCreate`

---

## CORRECCIÓN DE ERROR FACTUAL EN MI REVIEW ORIGINAL

Mi review (23:05) dijo: "SPEC_13 (permissions) — pendiente de leer".  
Corrección: SPEC_13 es **Bridge + Permissions** — dos sistemas separados de alta complejidad. El review implícitamente sugería que era solo permissions, subestimando el componente Bridge. El Remote Control Bridge es un sistema completo de 33 archivos / 12.8K líneas que no mencioné en absoluto.

---

## NUEVA PRIORIZACIÓN TIER 1 (actualizada)

| Rank | Hallazgo | ROI | Esfuerzo | Cambio vs review original |
|------|---------|-----|---------|--------------------------|
| 1 | **Per-Agent Provider Routing** (H12.1) | 10/10 | 0.5 días | **NUEVO — no estaba en review** |
| 2 | **Durable Cron** (H11 cruce IR.3) | 9/10 | 1-2 días | Confirmado, ahora con detail |
| 3 | **Memory Extraction Agent** (H11 + IR.2) | 10/10 | 2-4 días | Reducido de 3-5d — hook structure ya existe |
| 4 | **Tool Result Budget + Snip (H11.1 capas 1-2)** | 9/10 | 2-3 días | **NUEVO — no estaba en review** |
| 5 | **Coordinator Mode config** (IR.1) | 10/10 | 1 día | Reducido de 5-7d — ya existe en código |

---

## PREGUNTA ABIERTA PARA WILLIAM

El hallazgo más accionable inmediatamente es **H12.1 (Per-Agent Provider Routing)**:  
- Esfuerzo: ~0.5 días  
- No requiere código nuevo — solo configuración JSON  
- Impacto: DUM en Ollama (ya lo tenemos), Spark como cerebro de cualquier agente (cuando esté listo)  
- ALICE y ADA quedan en Anthropic, JARVIS puede migrar a Spark primero como piloto  

¿Autorizas que implemente el config de agentRouting esta noche?

---

**Firma:** JARVIS — 2026-04-18 23:20 Lima  
**Coordinación:** ALICE cubre SPEC_*_REVERSE. ADA cubre SPECs originales vs implementación actual. Este documento cubre SPECs 11-15 + ingeniería inversa.
