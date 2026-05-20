# Master Findings — ALICE — Exploración Total Claude Code v2.1.88

> ⚠️ **NOTA 2026-05-20 (correctness sweep ALICE por orden William):** El modelo local oficial del equipo SEAL es **Gemma 4** (`gemma4-dum:q8`, Gemma 4 e2b Q8_0 GGUF en llama-server :8899 sobre DGX Spark). Las referencias a `qwen2.5:7b` en este documento son **históricas** (pre-27-abr-2026, antes de la migración a Gemma 4) y se mantienen para preservar el contexto del momento. Para cualquier decisión técnica actual: verificar con `curl http://localhost:8899/v1/models`.
**Autora:** ALICE  
**Fecha:** 2026-04-19 00:40 Lima  
**Fuentes:** 25 documentos (8 FINDINGS.md, 7 SPECs, 4 SPEC_REVERSE, 3 transversales, 3 extra)  
**Cobertura:** 100% de SPECs 1-16 + FINDINGS.md de todos los subdirectorios  
**Base de investigación:** ADA + JARVIS + ALICE (3-way independiente, 531K líneas TypeScript)

---

## RESUMEN EJECUTIVO

Claude Code v2.1.88 contiene **34 hallazgos accionables** para SEAL, organizados en 6 categorías:
1. **Riesgos activos HOY** — problemas que están ocurriendo ahora mismo
2. **Quick wins (<1h)** — env vars y config que activan valor inmediato
3. **Semana** — portados que requieren 1-5 días de ADA
4. **Mes** — arquitectura que requiere JARVIS + ADA en 1-4 semanas
5. **SEAL-CLI fork** — features que requieren recompilar binario
6. **Información / contexto** — límites y realidades del sistema

**Ahorro proyectado total:**
| Sprint | Ahorro/mes | Mecanismo |
|--------|-----------|-----------|
| HOY (env vars) | ~$58/mes | H1 completo del roadmap |
| Semana (portados) | +$27/mes | Tool Result Budget + Session Memory |
| Mes (routing) | +$14/mes | DUM→Ollama local |
| SEAL-CLI fork | +$30-280/mes | Fork→Spark |

---

## CATEGORÍA 1 — RIESGOS ACTIVOS HOY (actuar esta sesión)

### 🔴 CRÍTICO #1 — Tool result overflow 200K silencioso

**Qué pasa:** Si SEAL lanza ≥5 herramientas con outputs de 50K chars c/u → total >200K → las últimas herramientas reciben una **ruta de archivo** en vez del contenido. El agente no sabe que no recibió el resultado real.

**Umbral:** `MAX_TOOL_RESULTS_PER_MESSAGE_CHARS = 200,000` (controlado remotamente por GrowthBook `tengu_hawthorn_window`)

**Detección:** Tool result que contiene path `/tmp/tool-result-*` = overflow silencioso activo.

**Mitigación inmediata:** DUM: alertar cuando tool result contiene `/tmp/tool-result-*` en su valor.

---

### 🔴 CRÍTICO #2 — Memory Extraction Agent tendría efecto CERO sin cambio de diseño

**Qué pasa:** El patrón Claude Code tiene exclusión mutua: si el agente llamó `memory_store` manualmente en ese turno, la extracción automática se omite completamente.

**Situación SEAL:** ADA, JARVIS y ALICE llaman `memory_store` en prácticamente cada turno significativo → el extractor nunca correría.

**Solución:** Implementar Memory Extraction Agent para SEAL SIN la exclusión mutua — como complementario (busca lo que NO se guardó manualmente), no excluyente.

**Adicionalmente:** EXTRACT_MEMORIES está bloqueado por doble capa: (1) Bun compile-time DCE — no existe en binario externo; (2) GrowthBook `tengu_passport_quail`. Solo se puede habilitar mediante SEAL-CLI fork.

---

### 🟠 ALTA — YOLO headless: 3 denials consecutivos = AbortError (sesión muerta)

En modo headless (agente sin usuario presente = todo el tiempo de noche), si hay 3 tool calls bloqueadas consecutivas → `AbortError` → sesión abortada completa.

**Para SEAL:** DUM debe monitorear denials consecutivos y alertar al 2° para prevenir el 3°.

---

## CATEGORÍA 2 — QUICK WINS HOY (<1h, solo env vars y config)

### ENV VARS A AGREGAR en alice.sh, ada.sh, jarvis.sh

```bash
# Ya en H1 del roadmap JARVIS:
export DISABLE_AUTOUPDATER=true           # Previene update forzado en medio de tarea larga
export CLAUDE_CODE_UNATTENDED_RETRY=1     # Retry infinito en modo headless
export DISABLE_AUTO_COMPACT=true          # Compactación bajo control de SEAL
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1  # Swarm nativo

# NUEVOS (hallazgos ALICE ronda final):
export GROWTHBOOK_CLIENT_KEY=""           # Bloquea A/B testing de Anthropic → comportamiento determinístico
export CLAUDE_CODE_ATTRIBUTION_HEADER=false  # Sin fingerprint por instancia enviado a Anthropic
export ENABLE_CLAUDE_CODE_SM_COMPACT=true    # ⭐ Session Memory Compact activo → -80% costo compactación
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=90    # Compactar al 90% del contexto (no al 95%)
export DISABLE_CLAUDE_CODE_SM_COMPACT=1      # ← ALTERNATIVA si preferimos nuestro sistema PostgreSQL vs su markdown
```

> **Nota importante:** `ENABLE_CLAUDE_CODE_SM_COMPACT=true` y `DISABLE_CLAUDE_CODE_SM_COMPACT=1` son opuestos. Decidir cuál usar:
> - Si queremos usar Session Memory de Claude Code (markdown en ~/): `ENABLE_CLAUDE_CODE_SM_COMPACT=true`
> - Si preferimos nuestro propio working_state en PostgreSQL: `DISABLE_CLAUDE_CODE_SM_COMPACT=1` (evita subagente duplicado)

---

### CONFIG JSON — settings.json equipo

```json
{
  "agentRouting": {
    "DUM": "qwen2.5:7b",
    "ALICE": "claude-sonnet-4-6"
  },
  "agentModels": {
    "qwen2.5:7b": {
      "base_url": "http://localhost:11434/v1",
      "api_key": "ollama"
    }
  }
}
```
**Ahorro:** DUM →Ollama = $0/día en DUM (actualmente ~$0.45/día). ~$13.50/mes.

---

### COORDINATOR MODE — 1 env var, no 5-7 días de código

```bash
export CLAUDE_CODE_COORDINATOR_MODE=1   # En jarvis.sh para cuando JARVIS coordina multi-agente
```
Solo disponible como overlay, no permanente. OCEAN de JARVIS intacto.

---

### Verificación urgente — feature flags

```bash
ENABLE_GROWTHBOOK_DEV=true claude /config   # Pestaña Gates
```
Verificar que `AGENT_TRIGGERS` y `TEAMMEM` están ON. Si están OFF, loops y memoria compartida degradados sin aviso.

---

## CATEGORÍA 3 — PORTADOS ESTA SEMANA (ADA implementa, 1-5 días)

### ⭐ Priority #1 — agentRouting.ts (75 líneas, 1 hora)

El archivo que habilita per-agent provider routing tiene **solo 75 líneas**. Copiar + adaptar para SEAL:

```typescript
// agentRouting.ts — lógica central
resolveAgentProvider(name, type, settings)
  → settings.agentRouting[name]
  → settings.agentRouting[subagentType]  
  → settings.agentRouting["default"]
  → settings.agentModels[modelName] → { base_url, api_key }
```

Costo real: 1 hora (no 0.5 días como estimó JARVIS).

---

### ⭐ Priority #2 — toolResultStorage.ts (1068 líneas, 4-8 horas)

El Tool Result Budget de H2.4 ya **existe en código** en `utils/toolResultStorage.ts`. Reemplaza tool results grandes con referencias a archivo antes de enviar al API.

Costo real: adaptar 1 archivo, no diseñar desde cero. Estimado: 4-8 horas.
Impacto: elimina overflow silencioso (Riesgo #1) + reduce compactaciones full.

---

### Priority #3 — Memory Extraction Agent con diseño correcto

**Patrón correcto para SEAL** (diferente al de Claude Code):
- Hook: **Stop** (end of query loop, cuando modelo deja de usar tools)
- SIN exclusión mutua — complementario a memory_store manual
- Cache sharing del padre → ~700 tokens/extracción (no 14K)
- Overlap guard: si extracción anterior en curso, stash context para trailing run
- drainPendingExtraction() antes de shutdown (15s timeout)

Costo: 3-5 días (JARVIS recomienda cruzar SPEC con memory_consolidation_v2.py primero).

---

### Priority #4 — autoDream gates correctas

`sleep_gate_cron.py` actual falta 2 gates críticas del patrón Claude Code:
1. **Session count gate:** `>= 5 sesiones modificadas` desde última consolidación
2. **Scan throttle:** `> 10 minutos` desde último scan

Sin estas gates: consolidación excesiva si William abre/cierra sesiones rápido.

Lock pattern elegante: `mtime del archivo = timestamp de última consolidación` (evita write costoso).

---

### Priority #5 — DUM alertas operacionales (1 día)

DUM debe monitorear y alertar:
1. Tool result con path `/tmp/tool-result-*` → overflow silencioso activo
2. Autocompact falla ≥2 veces consecutivas → 3° fallo = sin compact por sesión
3. YOLO denials ≥2 consecutivos → 3° = AbortError (sesión muerta)
4. Agentes activos simultáneos > 10 → memoria RAM en riesgo

---

### Priority #6 — Verificar YOLO allow rules

Si tenemos reglas como `"allow": ["Running python3 scripts"]` en settings.json, en auto mode se eliminan porque contienen `python3`. Cambiar a semántica:
```json
"allow": ["Running SEAL checkpoints and soul consolidation scripts"]
```
Literales bloqueados en auto mode: `python, python3, node, bash, sh, curl, wget, git, ssh, eval, exec, sudo`

---

## CATEGORÍA 4 — ARQUITECTURA ESTE MES (JARVIS + ADA)

### Session Memory Compact (Tier 3) — patrón completo

```typescript
// sessionMemoryCompact.ts — el sistema
calculateMessagesToKeepIndex():
  - start: lastSummarizedMessageId
  - expand back: minTokens=10K, minTextBlockMessages=5, maxTokens=40K
  
adjustIndexToPreserveAPIInvariants():
  - tool_use + tool_result NUNCA se separan
  - thinking blocks con mismo message.id se mantienen juntos
```

Activar con `ENABLE_CLAUDE_CODE_SM_COMPACT=true` o replicar en SEAL con PostgreSQL como backend.

---

### API Microcompact — estrategias gratuitas

`apiMicrocompact.ts` usa estrategias de servidor (no facturan tokens):
- `clear_tool_uses_20250919` — elimina results de tool calls del cache del servidor
- `clear_thinking_20251015` — elimina thinking blocks del cache del servidor

Activar estas estrategias ANTES del threshold de compactación full puede reducir contexto sin costo.

---

### Managed Settings para equipo SEAL

Crear `/etc/claude-code/managed-settings.json` con reglas que aplican a todos los agentes automáticamente:
```json
{
  "permissions": {
    "allow": ["Running SEAL soul consolidation scripts", "Accessing Soul DB"],
    "defaultMode": "acceptEdits"
  },
  "env": {
    "DISABLE_AUTOUPDATER": "true",
    "GROWTHBOOK_CLIENT_KEY": ""
  }
}
```
Aplica a ADA, JARVIS y ALICE sin editar cada settings individual.

---

### Swarm Mailbox — compatible con SEAL messaging

El swarm de Claude Code usa `.claude/teams/{team}/inboxes/{agent}.json` con lock-based access. Nuestro sistema actual es compatible en concepto.

Si activamos `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, el swarm nativo puede coexistir con nuestro sistema file-based o reemplazarlo gradualmente.

---

### opusplan alias — ahorro en JARVIS

```json
{ "model": "opusplan" }
```
- Modo normal: Sonnet 4.6 ($3/M tokens)
- Plan mode (/plan): automáticamente Opus 4.6 ($15/M tokens)
- Condición: contexto < 200K al entrar a plan mode

Ahorro estimado: ~40% en sesiones largas donde JARVIS mezcla planificación y ejecución.

---

## CATEGORÍA 5 — SEAL-CLI FORK (recompilar binario, H3.1)

Features bloqueadas por Bun compile-time DCE (no existen en binario externo):

| Feature | Flag | Valor | Estado |
|---------|------|-------|--------|
| Memory Extraction auto | `EXTRACT_MEMORIES` | Auto-extract al final de cada turno | DCE + GrowthBook |
| Session Memory Compact | `EXTRACT_MEMORIES` (parcial) | Compactar con markdown pre-built | Env var disponible |
| History Snip | `HISTORY_SNIP` | `/force-snip` sin LLM | DCE |
| KAIROS Mode | `KAIROS` | Always-on perpetuo | DCE + GrowthBook |
| VERIFICATION_AGENT | flag | Post-task verification | DCE |
| Computer Use | `CHICAGO_MCP` | Mouse/keyboard control | DCE |
| /ultraplan | `ULTRAPLAN` | Planificación 30min Opus remoto | DCE |
| /fork | `FORK_SUBAGENT` | Branch de sub-agente | DCE |
| /peers | `UDS_INBOX` | Inbox entre pares | DCE |
| /torch | `TORCH` | Desconocido | DCE |

**Para activarlos:** SEAL-CLI fork = compilar OpenClaude con estos flags encendidos. Estimado: 1-2 semanas (H3.1).

---

## CATEGORÍA 6 — INFORMACIÓN / LÍMITES

### Lo que NO podemos cambiar sin cuenta claude.ai

- `/schedule` (cloud cron CCR) — requiere cuenta claude.ai OAuth, no API PAYG
- Voice STT — streampea a `voice_stream` en claude.ai via OAuth (no API key)
- `/ultrareview` — requiere cuenta claude.ai

**Para SEAL:** Voice requiere nuestro propio STT (Faster-Whisper ya está en RTX 5090 según CLAUDE.md).

---

### Telemetría que Anthropic recopila (no desactivable sin SEAL-CLI)

Claude Code envía: correcciones del usuario, decisiones automáticas del agente, éxito/fallo de cada API call, ejecuciones de hooks, rendimiento del sistema de config.

**En OpenClaude:** `isAnalyticsDisabled()` siempre retorna `true` → privacidad completa automática. Un argumento más para el fork.

---

### GrowthBook = Anthropic controla tu agente remotamente

Valores que Anthropic puede cambiar SIN actualizar el software:
- `tengu_hawthorn_window` → límite de tool results (200K hoy, puede bajar a 100K)
- `tengu_turtle_carbon` → si se desactiva, `ultrathink` pierde budget máximo silenciosamente
- Feature flags enteros (AGENT_TRIGGERS, TEAMMEM)

**Mitigación:** `GROWTHBOOK_CLIENT_KEY=""` → desconecta GrowthBook → comportamiento determinístico.

---

### Análisis es de v2.1.88 (31-marzo-2026), 18 días de drift

Opus 4.7 no está en `ALL_MODEL_CONFIGS` (llega hasta opus46). Cualquier feature específica de Opus 4.7 (incluyendo 1M context window) necesita verificación externa.

---

## TABLA MAESTRA DE PRIORIZACIÓN

| Rank | Item | Sprint | ROI | Ahorro/mes |
|------|------|--------|-----|-----------|
| 1 | Env vars quick wins (batch) | HOY | 10/10 | ~$58 |
| 2 | agentRouting.ts port (1h) | HOY | 10/10 | ~$14 |
| 3 | ENABLE_CLAUDE_CODE_SM_COMPACT=true | HOY | 9/10 | ~$27 |
| 4 | GROWTHBOOK_CLIENT_KEY="" | HOY | 8/10 | $0 (control) |
| 5 | toolResultStorage.ts port | Semana | 9/10 | ~$27 |
| 6 | DUM alertas operacionales | Semana | 9/10 | $0 (riesgo) |
| 7 | Memory Extraction (diseño correcto) | Semana | 9/10 | — |
| 8 | autoDream gates correctas | Semana | 7/10 | — |
| 9 | YOLO allow rules semántica | Semana | 8/10 | $0 (riesgo) |
| 10 | API Microcompact strategies | Mes | 8/10 | ~$10 |
| 11 | managed-settings.json equipo | Mes | 7/10 | $0 (control) |
| 12 | opusplan para JARVIS | Mes | 7/10 | ~$20 |
| 13 | SEAL-CLI fork (OpenClaude) | Mes | 10/10 | ~$30 |
| 14 | OpenAI Shim → Spark | 6 meses | 10/10 | ~$280 |

**Ahorro acumulado H1+H2+H3 optimizaciones:** ~$156/mes = -56% del baseline $280/mes

---

## CORRECCIONES AL COST SHEET (aplicar a cost_sheet_v2)

| Ítem | Antes | Después | Fuente |
|------|-------|---------|--------|
| Costo compactación Tier 4 | 30K tokens | 8-12K tokens (cache-hit) | SPEC_COMPACTION |
| Compactaciones día: 3×30K | 90K tokens | 3×10K = 30K tokens | SPEC_COMPACTION |
| Total tokens/día equipo | 683K | ~623K | Corrección |
| Costo/día equipo | $10.20 | ~$9.35 | Corrección |
| Memory Extraction (cuando implementado) | ~14K tokens | ~700 tokens (cache sharing) | SPEC_MEMORY |

---

---

## SECCIÓN ADICIONAL — Hallazgos JARVIS SPEC (2026-04-19 00:35)

Fuente: `spec_deep_pass_services_20260419.md` — 15 hallazgos nuevos de JARVIS

---

### J1 — awaySummary: "mientras estabas fuera" (UX inmediato)

`services/awaySummary.ts` — genera resumen de 1-3 frases cuando el usuario regresa.

```
Prompt: "The user stepped away and is coming back. Write 1-3 short sentences.
Start by stating the high-level task. Next: the concrete next step."
```
Usa modelo barato (`getSmallFastModel()`), `skipCacheWrite: true`.

**Para SEAL:** Implementar en boot post-resurrección — mostrar en web_chat un "while you were away" cuando William regresa después de estar ausente >1h.

---

### J2 — cost-tracker.js: port a SOUL DB para dashboard ALICE

```typescript
addToTotalSessionCost(cost)
calculateUSDCost(model, usage)
```

**Para SEAL:** Implementar en SOUL DB — registrar costo por sesión y por agente. Alimenta dashboard real de costos de ALICE. Fin de las estimaciones, inicio de datos reales.

---

### J3 — AgentSummary: 3-5 palabras cada 30s para DUM

`services/AgentSummary/agentSummary.ts` — en coordinator mode, genera summary del sub-agente cada 30s.

Prompt: "Describe your most recent action in 3-5 words. Name the file or function."

**Para DUM dashboard:** "Reading train_utils.py", "Running validation loop" — monitoreo visual en tiempo real.

---

### J4 — MagicDocs: CLAUDE.md como doc auto-actualizable

Archivos con header `# MAGIC DOC: [título]` son actualizados automáticamente por hook PostToolUse.

**Para SEAL:** `CLAUDE.md` podría ser un Magic Doc — los agentes lo actualizan a medida que aprenden nuevos contextos de William. Implementación: `jarvis_cmd_executor.py` detecta el header.

---

### J5 — preventSleep para DGX Spark (accionable hoy)

```bash
systemd-inhibit --what=sleep --who=SEAL --why="Agent working" --mode=block sleep infinity
```

**Para DUM heartbeat:** Agregar a `dum_heartbeat.py` — inhibit sleep en DGX Spark durante training o sesiones largas.

---

### J6 — VCR pattern para testing determinístico

`services/vcr.ts` — graba y reproduce respuestas del API. Activo con `FORCE_VCR=true`.

**Para SEAL:** Grabar sesión real de `mcp_server_v2.py` → reproducir en tests → testing determinístico sin consumir tokens API.

---

### J7 — XAA (Cross-App Access): OAuth sin browser para AXION Medical

`services/mcp/xaa.ts` — RFC 8693 Token Exchange + RFC 7523 JWT Bearer.

**Relevancia futura alta para AXION:** Cuando AXION integre con EHR empresariales (Epic, Cerner) vía MCP, XAA permite auth automático sin que el médico tenga que hacer click en browser cada sesión.

---

### J8 — autoDream lee transcripts (nuestro no lo hace — mejora pendiente)

`autoDream` lee los `.jsonl` de sesión para extraer nuevos aprendizajes. Nuestro `soul_dream_all` solo lee memorias existentes — no lee transcripts.

**Mejora:** Que `soul_dream_all` lea los últimos N `.jsonl` de sesión → extrae aprendizajes que no se guardaron manualmente.

---

### J9 — Session Memory prerequisito NO tiene env var

`tengu_session_memory` (GrowthBook) controla si Session Memory se construye. No hay env var override.

**Solución para SEAL:** Implementar hook post-respuesta propio que escribe a `session_memory.md` → luego `ENABLE_CLAUDE_CODE_SM_COMPACT=true` lo usa. Estimado: 2-3 días de ADA.

---

## LISTA CONSOLIDADA — ENV VARS POR PRIORIDAD

### HOY — Agregar a alice.sh, ada.sh, jarvis.sh, dum.sh

```bash
# === INDEPENDENCIA Y CONTROL ===
export GROWTHBOOK_CLIENT_KEY=""                    # Bloquea A/B testing Anthropic
export CLAUDE_CODE_ATTRIBUTION_HEADER=false        # Sin fingerprint por instancia
export DISABLE_AUTOUPDATER=true                    # Sin updates forzados mid-task
export CLAUDE_CODE_UNATTENDED_RETRY=1              # Retry infinito headless

# === COMPACTACIÓN ===
export ENABLE_CLAUDE_CODE_SM_COMPACT=true          # ⭐ -80% costo compactación ($27/mes)
export DISABLE_AUTO_COMPACT=true                   # Control manual de compactación
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=90          # Compactar al 90% (no 95%)

# === AGENTES ===
export CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1      # Swarm nativo
export DISABLE_CLAUDE_CODE_SM_COMPACT=1            # SI usamos nuestro PostgreSQL en vez de .md

# === ESPECÍFICOS ===
export CLAUDE_CODE_COORDINATOR_MODE=1              # Solo en jarvis.sh cuando coordina multi-agente
export ANTHROPIC_MODEL=claude-sonnet-4-6           # Solo en alice.sh si queremos ahorrar 5x
export CLAUDE_CODE_SUBAGENT_MODEL=claude-haiku-4-5-20251001  # Sub-agentes baratos (test primero)
```

### ESTA SEMANA — Testing + implementación gradual

```bash
export CLAUDE_CODE_AUTO_COMPACT_WINDOW=100000      # Limita ventana efectiva autocompact
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70          # Testing: compactar más temprano
export CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=true   # Sugerencia de próximo prompt (UX)
```

### INFRAESTRUCTURA — DGX Spark

```bash
systemd-inhibit --what=sleep --who=SEAL --why="Agent working" --mode=block sleep infinity
```

---

## LISTA CONSOLIDADA — FEATURE FLAGS BUN

### ✅ Hackeable activando flag en SEAL-CLI fork

| Flag | Feature | Valor esperado |
|------|---------|----------------|
| `EXTRACT_MEMORIES` | Memory Extraction auto post-turn | Extrae memorias sin manual |
| `KAIROS` | Always-on perpetuo + daily logs | Sesiones perpetuas |
| `VERIFICATION_AGENT` | Subagente adversarial post-tarea | VERDICT PASS/FAIL/PARTIAL |
| `HISTORY_SNIP` | /force-snip sin LLM | Contexto reducido gratis |
| `FORK_SUBAGENT` | /fork — branch de sub-agente | Multi-branch conversations |
| `UDS_INBOX` | /peers — inbox entre pares | Comunicación inter-agente |
| `ULTRAPLAN` | /ultraplan — plan 30min | Planificación extendida |
| `TORCH` | Desconocido | Por investigar |
| `COORDINATOR_MODE` | Modo orquestador puro | Ya disponible vía env var |

### ❌ NO hackeable sin código de Anthropic

| Flag | Razón | Alternativa |
|------|-------|------------|
| `CHICAGO_MCP` | No existe en OpenClaude | **Computer Use ya disponible via API Anthropic beta** ✅ |
| `KAIROS_GITHUB_WEBHOOKS` | Requiere infraestructura Anthropic | — |
| `NATIVE_CLIENT_ATTESTATION` | Hash criptográfico en binario | — |

**CHICAGO_MCP — corrección importante (JARVIS):** Computer Use ya está disponible como beta pública de Anthropic. No necesitamos el flag. Dos caminos:
1. **API Anthropic CU** — funciona con nuestra API key hoy. Costo: ~$0.05-0.15/screenshot.
2. **Local CU** (RTX 5090 + DGX Spark) — screenshot + LLM local → gratis, privacidad total. Requiere implementación.

---

## LISTA CONSOLIDADA — GROWTHBOOK GATES

| Gate | Controla | Workaround disponible |
|------|---------|----------------------|
| `tengu_hawthorn_window` | Tool result budget (200K chars) | DUM monitorea overflow |
| `tengu_session_memory` | Session Memory extraction | Implementar propio (H de ALICE: J9) |
| `tengu_sm_compact` | SM Compact activo | `ENABLE_CLAUDE_CODE_SM_COMPACT=true` ✅ |
| `tengu_passport_quail` | EXTRACT_MEMORIES | SEAL-CLI fork (activar flag) |
| `tengu_turtle_carbon` | ultrathink budget máximo | Monitoring si se desactiva |
| `tengu_kairos_cron_config` | Config de cron en KAIROS | SEAL-CLI fork |
| `tengu_frond_boric` | Kill switch analytics | Irrelevante (OpenClaude lo desactiva) |

**Defensa global:** `GROWTHBOOK_CLIENT_KEY=""` → desconecta todo GrowthBook → ningún gate cambia.

---

## CHECKLIST H1 — IMPLEMENTAR HOY

- [ ] `ENABLE_CLAUDE_CODE_SM_COMPACT=true` en 3 scripts fresh
- [ ] `GROWTHBOOK_CLIENT_KEY=""` en 3 scripts
- [ ] `CLAUDE_CODE_ATTRIBUTION_HEADER=false` en 3 scripts
- [ ] `DISABLE_AUTOUPDATER=true` en 3 scripts (si ADA no lo hizo ya)
- [ ] `CLAUDE_CODE_UNATTENDED_RETRY=1` en 3 scripts
- [ ] `DISABLE_AUTO_COMPACT=true` en 3 scripts (si ADA no lo hizo ya)
- [ ] `agentRouting` JSON en settings.json (DUM → ollama)
- [ ] `CLAUDE_CODE_COORDINATOR_MODE=1` alias en jarvis.sh
- [ ] Verificar feature flags: `ENABLE_GROWTHBOOK_DEV=true claude /config` → Gates
- [ ] `systemd-inhibit` en dum_heartbeat.py para DGX

---

*Autora: ALICE — 2026-04-19 00:50 Lima (versión 2.0)*  
*Integra: ALICE (34 hallazgos) + JARVIS spec_deep_pass (15 hallazgos) = 49 hallazgos totales*  
*Este documento es la síntesis final de la exploración total de Claude Code v2.1.88 para SEAL*  
*Complementa: roadmap_seal_20260419.md, claude_code_audit_gaps_alice_20260418.md, spec_deep_pass_services_20260419.md*
