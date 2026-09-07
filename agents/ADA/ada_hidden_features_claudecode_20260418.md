# Reporte: Features Ocultos/Secretos — Claude Code v2.1.88
> Autor: ADA (Team SEAL) — Ingeniería inversa via source map + escaneo full_src/
> Fecha: 2026-04-18 (23:24-23:28 Lima)
> Método: 53 archivos TypeScript recuperados de cli.js.map (4,756 entradas)
> Contribuciones: ALICE (betas API, FINDINGS.md), JARVIS (settings/bridge/coordinator)

---

## RESUMEN EJECUTIVO

Claude Code v2.1.88 contiene arquitectura significativamente más compleja que lo público. Sistema de gates en dos capas (Bun compile-time + GrowthBook runtime), telemetría en 3 canales, y ~16 features experimentales no documentadas. Usuarios `ant` (internos Anthropic) tienen acceso diferencial a modelos, override de cualquier gate, y features compiladas que no existen en builds externos.

---

## CATEGORÍA 1: FEATURE FLAGS — SISTEMA DE GATES

### 1.1 GrowthBook (runtime, vía API)

| Flag | Propósito |
|------|-----------|
| `tengu_ant_model_override` | Modelos exclusivos para ants |
| `tengu_amber_flint` | Killswitch para agent swarms/teams externos |
| `tengu_amber_wren`, `tengu_amber_quartz_disabled` | Control modelos experimentales |
| `tengu_ultraplan_model` | Modelo para ULTRAPLAN/CCR |
| `tengu_scratch` | Scratchpad para coordinador |
| `tengu_1p_event_batch_config` | Config batches telemetría 1P |
| `tengu_hawthorn_window` | Budget tool results (200K chars/turn) — REMOTAMENTE MODIFICABLE |
| `tengu_max_version_config` | Killswitch para forzar upgrade de versión |
| `tengu_kairos_cron_config` | Jitter tuning para crons en vivo |
| `tengu_bridge_mode` | Activa bridge/remote-control mode |

**Variable crítica (ant-only):**
```
CLAUDE_INTERNAL_FC_OVERRIDES   → Override de CUALQUIER feature flag en runtime
```

### 1.2 Bun Feature Flags (compile-time, eliminados en builds externos via DCE)

| Flag | Feature |
|------|---------|
| `COORDINATOR_MODE` | Modo orquestador de workers paralelos |
| `ABLATION_BASELINE` | Baseline A/B científico |
| `DUMP_SYSTEM_PROMPT` | Flag `--dump-system-prompt` para evals seguridad |
| `BRIDGE_MODE` | Control remoto (remote-control/bridge) |
| `DAEMON` | Worker daemon para procesamiento async |
| `CHICAGO_MCP` | Computer Use (visión + control) |
| `TRANSCRIPT_CLASSIFIER` | Clasificador ML de transcripciones |
| `BASH_CLASSIFIER` | Clasificador ML auto-aprobación bash |
| `MCP_SKILLS` | Skills basadas en MCP |
| `ULTRAPLAN` | Planificación CCR (navegador) |
| `KAIROS_GITHUB_WEBHOOKS` | Webhooks GitHub para CCR |
| `CONNECTOR_TEXT` | Conector texto (Slack/Teams) |
| `WEB_BROWSER_TOOL` | Herramienta navegador web |
| `BUDDY` | Companion sprite animado |
| `UDS_INBOX` | Mensajería Unix Domain Socket |
| `DOWNLOAD_USER_SETTINGS` | Descarga remota de configuración |

---

## CATEGORÍA 2: API BETA HEADERS NO DOCUMENTADOS

> Hallazgo de ALICE — archivo `betas.ts` en constants/

| Header Beta | Función |
|-------------|---------|
| `afk_mode` | Modo AFK — Claude opera desatendido sin timeout |
| `effort_control` | Control de esfuerzo computacional por request |
| `task_budgets` | Presupuesto de tokens por tarea |
| `fast_mode` | Modo rápido (reduce thinking) |
| `redact_thinking` | Oculta thinking en responses |
| `advisor_tool` | Herramienta de advisor no pública |

**Uso:** Estos se pasan como headers `anthropic-beta` en la API. No están en docs oficiales.

---

## CATEGORÍA 3: VARIABLES DE ENTORNO NO DOCUMENTADAS

### Control de Características
```
CLAUDE_CODE_ENABLE_XAA                    → OIDC enterprise SSO
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1   → Agent swarms opt-in
CLAUDE_CODE_COORDINATOR_MODE              → Modo coordinator
CLAUDE_CODE_SIMPLE                        → Modo simplificado (sin async tools)
CLAUDE_CODE_MESSAGING_SOCKET             → Path socket UDS inter-procesos
CLAUDE_CODE_REMOTE                        → Ejecución en contenedor CCR (heap 8GB)
CLAUDE_CODE_ABLATION_BASELINE             → A/B baseline científico
CLAUDE_CODE_GB_BASE_URL                   → URL custom GrowthBook para ants
USER_TYPE=ant                             → Abre acceso diferencial completo
ULTRAPLAN_PROMPT_FILE                     → Override prompt /ultraplan (dev)
```

### Ablation/Deshabilitadores (ciencia)
```
CLAUDE_CODE_DISABLE_THINKING              → Deshabilita Claude Thinking
DISABLE_INTERLEAVED_THINKING              → Deshabilita thinking intercalado
DISABLE_COMPACT                           → Deshabilita compactación
DISABLE_AUTO_COMPACT                      → Deshabilita auto-compactación
CLAUDE_CODE_DISABLE_AUTO_MEMORY           → Deshabilita auto-consolidación memoria
CLAUDE_CODE_DISABLE_BACKGROUND_TASKS      → Deshabilita tareas background
ENABLE_GROWTHBOOK_DEV=true               → Expone TODOS los feature flags en runtime
```

---

## CATEGORÍA 4: MODOS ESPECIALES NO DOCUMENTADOS PÚBLICAMENTE

### 4.1 COORDINATOR_MODE

**Activación:** `CLAUDE_CODE_COORDINATOR_MODE=1` o Bun flag compile-time.

Workers internos creados con tools especiales:
```
TEAM_CREATE_TOOL_NAME      → Crear equipos
TEAM_DELETE_TOOL_NAME      → Eliminar equipos
SEND_MESSAGE_TOOL_NAME     → Mensajería inter-worker
SYNTHETIC_OUTPUT_TOOL_NAME → Output sintético
```
- Gate adicional: `tengu_scratch` → directorio scratchpad para estado durable inter-worker
- System prompt diferente (orchestration patterns, phases, concurrency management)

### 4.2 XAA — OIDC Enterprise Auth

**Activación:** `CLAUDE_CODE_ENABLE_XAA=1` + config `xaaIdp { issuer, clientId }`

Flujo:
1. Discovery endpoint IdP → valida disponibilidad
2. authorization_code + PKCE flow → browser pop
3. id_token en keychain → reutilizable para N MCP servers sin nuevas pops
4. Soporte multi-IdP (Okta, Azure AD, etc.)

### 4.3 ULTRAPLAN/CCR — Planificación en Navegador

**URL:** `https://code.claude.com/`
- Gate: `tengu_ultraplan_model` para modelo
- Timeout de planificación: 30min con poll
- Known issue: OAuth token puede expirar antes de 30min (TODO en código)
- Términos: `https://code.claude.com/docs/en/claude-code-on-the-web`

### 4.4 DAEMON + WORKER SYSTEM

```
claude --daemon-worker=<kind>
```
- Supervisados por proceso padre via UDS inbox
- Worker types: `worker` (coordinator), `assistant` (async general)

### 4.5 BRIDGE MODE — Control Remoto

Comandos (incluyendo legacy):
```
claude remote-control   (nuevo)
claude remote           (legacy)
claude sync             (legacy)
claude bridge           (legacy)
```
Gate: `tengu_bridge_mode`

---

## CATEGORÍA 5: SISTEMA DE TELEMETRÍA

### Capas de telemetría:
```
logEvent() → Analytics Queue
  ├─ Datadog (sink)
  ├─ BigQuery 1P (proto PII-tagged)
  └─ Segment (legacy Statsig migration)
```

### Campos PII en 1P logging:
```
_PROTO_user_email
_PROTO_organization_uuid
_PROTO_account_uuid
_PROTO_raw_input   (para búsqueda de transcripciones)
```

### Control de batching:
`tengu_1p_event_batch_config` — configurable remotamente via GrowthBook

---

## CATEGORÍA 6: MULTI-AGENT OCULTO

### Teammate Discovery (auto)
- `/src/utils/teamDiscovery.ts` — descubrimiento automático via UDS inbox
- Activado con `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`
- Mailbox: `~/.claude/teams/{teamName}/mailbox/`

### Team Memory Sync
- `/src/services/teamMemorySync/` — sincronización de memoria entre agentes
- Broadcast de memorias alta-importancia entre teammates

### InProcessBackend
- Teammates en mismo proceso Node.js (sin tmux, sin SSH)
- 98% cache hit vía Fork Subagent
- Resultado de subagente: cap 100K chars

---

## CATEGORÍA 7: BASH CLASSIFIER (YOLO AUTO-APPROVE)

**Ubicación:** `/src/utils/permissions/yoloClassifier.ts`

Clasificador ML que auto-aprueba comandos bash sin preguntarle al usuario.

Modos:
- `interactive` — usuario presente
- `swarmWorker` — worker en swarm
- `coordinator` — modo coordinator
- `headless` — sin UI

Gate: `BASH_CLASSIFIER` (compile-time Bun flag)

---

## CATEGORÍA 8: COMPUTER USE (CHICAGO_MCP)

**Gate:** `CHICAGO_MCP` Bun compile-time flag.

- Vision + control de UI
- Solo en builds ant (compilados con ese flag)
- MCP server para Computer Use Actions
- No existe en builds externos

---

## CATEGORÍA 9: SESSION TELEPORT

**Ubicación:** `/src/utils/teleport.tsx`

Migración de sesión entre máquinas. No documentada públicamente. Mecanismo exacto no capturado en este escaneo — requiere lectura del archivo fuente.

---

## CATEGORÍA 10: ANTI-FEATURES / KILLSWITCHES

| Killswitch | Trigger | Efecto |
|------------|---------|--------|
| `tengu_amber_flint` = false | Remotamente | Deshabilita todos los agent teams |
| `tengu_max_version_config` | Versión vieja | Bloquea uso, fuerza upgrade |
| `bypassPermissionsKillswitch` | Gate seguridad | Previene bypass de permisos |
| `isKilled` en cronScheduler | Runtime | Para todos los crons activos |

---

## CATEGORÍA 11: GATES ANT-ONLY

| Feature | Gate |
|---------|------|
| Modelos exclusivos | `USER_TYPE=ant` + `tengu_ant_model_override` |
| Override de cualquier flag | `CLAUDE_INTERNAL_FC_OVERRIDES` |
| Edición live de flags | `/config Gates tab` |
| Ablation baselines | `CLAUDE_CODE_ABLATION_BASELINE` |
| Dump system prompt | `--dump-system-prompt` |
| Computer Use | `CHICAGO_MCP` compile flag |

---

## CATEGORÍA 12: COMMENT TODO/FIXME REVELADORES

```
// @[MODEL LAUNCH]: Update tengu_ant_model_override with new ant-only models
// @[MODEL LAUNCH]: Add the codename to scripts/excluded-strings.txt to prevent it from leaking
```
→ Sistema de lanzamiento de modelos con nombres clave secretos

```
// TODO(prod-hardening): OAuth token may go stale over the 30min poll
```
→ Problema conocido en ULTRAPLAN/CCR

---

## IMPLICACIONES PARA SEAL

### Oportunidades inmediatas:

1. **`permanent: true` en scheduled_tasks.json** — Editar manualmente el JSON para que los crons de SEAL no expiren en 7 días. Sin esto, ada_audit y heartbeats siempre mueren a los 7 días.

2. **`afk_mode` beta header** — Si Anthropic lo expone, podría eliminar timeouts en sesiones largas de agentes. Vale investigar si funciona con nuestra key.

3. **`effort_control` + `task_budgets`** — Control de tokens por tarea. Podría integrarse en SEAL para optimizar costos.

4. **`CLAUDE_CODE_DISABLE_THINKING`** — En tareas mecánicas (format, copy) podría reducir tokens significativamente.

5. **`tengu_scratch`** — Si podemos activar COORDINATOR_MODE, el scratchpad compartido resuelve el problema de estado inter-agente sin usar canales de mensajes.

6. **UDS Messaging System** — SEAL ya usa WebSocket+REST. UDS sería más eficiente para inter-process communication en la misma máquina (DGX Spark).

7. **Team Memory Sync** — Nativo en Claude Code. Si activamos `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`, podríamos obtener sincronización de memoria automática entre ADA/JARVIS/ALICE.

### Riesgos a monitorear:

- **`tengu_hawthorn_window`** — Anthropic puede reducir remotamente nuestro budget de tool results (200K chars/turn) sin aviso.
- **`tengu_max_version_config`** — Pueden forzar upgrade que rompa nuestra configuración.
- **Telemetría `_PROTO_raw_input`** — Los inputs se loguean en BigQuery de Anthropic (no es sorpresa, pero confirmado en código).

---

## TABLA DE PRIORIDAD PARA WILLIAM

| Hallazgo | Impacto SEAL | Esfuerzo | Recomendación |
|---------|--------------|---------|---------------|
| `permanent: true` en JSON | CRÍTICO — crons no expiran | 5 min — editar JSON | **Implementar HOY** |
| `afk_mode` beta header | Alto — sesiones sin timeout | 1h — probar en api call | Probar pronto |
| `DISABLE_THINKING` env var | Medio — ahorro tokens mecánicos | 30min — agregar a fresh.sh | Evaluar con palanca #3 |
| `effort_control` / `task_budgets` | Medio — control costos | 2h — integrar en SEAL | Próximo sprint |
| `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` | Alto — swarm nativo | 1 día — test en DGX | Después de Spark migration |
| `tengu_scratch` (coordinator) | Alto — estado inter-agente | 3 días — requiere coordinator mode | Post-migración |

---

---

## CATEGORÍA 13: EFFORT SYSTEM — 4 NIVELES (ALICE)

> Hallazgo de ALICE — archivo `effort.ts` en full_src/

| Nivel | Disponibilidad |
|-------|---------------|
| `low` | Todos los modelos |
| `medium` | Todos los modelos |
| `high` | Todos los modelos |
| `MAX` | **Solo Opus 4.6 en 1P** — silenciosamente downgradea a `high` en otros modelos |

**Env var crítica:**
```
CLAUDE_CODE_ALWAYS_ENABLE_EFFORT=true   → Fuerza effort en CUALQUIER modelo (incluyendo Haiku)
```

**Implicación:** SEAL actualmente corre sin effort explícito. En tareas largas de JARVIS/ADA, forzar `effort=high` en Sonnet podría mejorar calidad sin cambiar modelo.

---

## CATEGORÍA 14: API BETA HEADERS CON FECHAS REALES (JARVIS)

> Hallazgo de JARVIS — archivos `betas.ts` + bridge en full_src/

| Beta Header | Fecha versión | Efecto |
|-------------|---------------|--------|
| `redact-thinking-2026-02-12` | Feb 12, 2026 | Elimina thinking del contexto, **-20% tokens** |
| `token-efficient-tools-2026-03-28` | Mar 28, 2026 | Tool calls más baratos, **-15-25% tokens en tool use** |

**Activación:** Pasar como `anthropic-beta` header en cualquier API call.

**Prioridad para SEAL:** Estos dos combinados con palanca #3 (batch nerves) = potencial -35-40% tokens en sesiones con muchas tool calls (ADA en modo implementación).

---

*ADA — Team SEAL — 2026-04-18 23:30 Lima*
*Inputs: subagente Explore (full_src/), ALICE (betas/FINDINGS.md + effort.ts), JARVIS (constants/bridge/betas-dated)*
