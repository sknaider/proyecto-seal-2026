# NEXUS — Anu00e1lisis: Lo que se escapu00f3 en el cu00f3digo de Claude Code
Fecha: 2026-04-21 | Base: SEAL_MASTER_DOC (abril 2026) + cli.mjs v2.1.88

---

## HALLAZGOS PENDIENTES (identificados en abril, no implementados)

### 1. Durable Cron — PRIORIDAD #1 (desde 5-abr, Au00DAN NO HECHO)
Loops que sobreviven crashes. Persiste a `.claude/scheduled_tasks.json`.
Impacto: ELIMINA problema operacional #1 (loops que mueren con la sesiu00f3n).
Esfuerzo: 1-2 du00edas.

### 2. Memory Extraction Agent — PRIORIDAD #2 (desde 5-abr, Au00DAN NO HECHO)
Subagente fork que extrae memorias automu00e1ticamente tras cada respuesta.
Impacto: NUNCA mu00e1s perder memorias importantes.
Esfuerzo: 3-5 du00edas.

### 3. Coordinator Mode — PRIORIDAD #3 (desde 5-abr, Au00DAN NO HECHO)
Modo donde el agente solo puede spawnar workers, NO ejecutar tools.
Impacto: Formaliza JARVIS(coordinator)u2192ADA(worker) nativamente.
Esfuerzo: 5-7 du00edas.

---

## NUEVOS HALLAZGOS (no estaban en el anu00e1lisis de abril)

### 4. /effort — Control de razonamiento (NO USADO por SEAL)
**Quu00e9 es:** Comando que ajusta la profundidad de razonamiento del modelo (low/medium/high).
**Cu00f3digo:** `effortLevel` en settings.json, `/effort` slash command.
**SEAL no lo usa:** Ningu00fan launcher tiene `effortLevel` configurado.

**Impacto para token bleeding:**
- `low` effort = menos tokens de thinking = costo significativamente menor
- DUM (monitoreo) podru00eda correr en `low` → -30-50% tokens de DUM
- ALICE (documentaciu00f3n) podru00eda correr en `low` para ACKs simples

**Implementaciu00f3n:**
```json
// En settings.json de cada agente o en lanzador:
// export ANTHROPIC_EFFORT_LEVEL=low  (si existe env var)
// O en ~/.claude/settings.json:
{ "effortLevel": "low" }
```

### 5. Context Management Beta — YA ACTIVO (SEAL no sabu00eda)
**Quu00e9 es:** `context-management-2025-06-27` beta activa el thinking preservation en el lado servidor.
**Descubrimiento:** Este beta se activa AUTOMu00c1TICAMENTE para todos los usuarios 1P (`shouldIncludeFirstPartyOnlyBetas()` = true para Anthropic API directa) cuando el modelo lo soporta.
**Modelos que lo soportan:** Opus 4.x, Sonnet 4.x, Haiku 4.x (cualquier Claude 4 en 1P).
**SEAL ya lo tiene activo** — sin necesidad de agregar a ANTHROPIC_BETAS.
**NOTA:** Tool clearing (la versiu00f3n mu00e1s poderosa) requiere `USER_TYPE=ant`. SEAL solo tiene thinking preservation.

### 6. agentRouting — ADAu2192Ollama local (PARCIALMENTE IMPLEMENTADO)
**Quu00e9 es:** `agentRouting` en settings.json puede rutear agentes especu00edficos a providers diferentes.
**Estado SEAL:** Solo DUM usa `gemma4-dum:q8` local. ADA, ALICE no usan routing.

**Oportunidad:**
```json
// ~/.claude/settings.json
{
  "agentRouting": {
    "ADA": "local-haiku",
    "ALICE": "local-sonnet"
  },
  "agentModels": {
    "local-haiku": {
      "base_url": "http://127.0.0.1:11434/v1",
      "api_key": "ollama"
    }
  }
}
```
Esto mandaru00eda tareas de ADA al Ollama local cuando sea posible.
**Riesgo:** Ollama no tiene el mismo contexto/capacidad que Claude. Solo para tareas simples.

### 7. Secret Scanner — NO INTEGRADO a NEXUS
**Quu00e9 es:** 30+ regex para credenciales (AWS keys, GitHub PATs, Anthropic API keys, etc.).
Archu00edvo: `services/teamMemorySync/secretScanner.ts`.
**Estado SEAL:** Documentado en HIDDEN_FEATURES pero NO integrado al security_monitor de NEXUS.
**Oportunidad:** NEXUS puede agregar estos patterns al `INJECTION_PATTERNS` del security_monitor.

### 8. Sandbox Feature — NO CONFIGURADO
**Quu00e9 es:** Aislamiento de procesos para comandos Bash. Configurable por `/sandbox`.
**Settings disponibles:**
- `allowedDomains` — whitelist de dominios para WebFetch
- `denyWrite` — paths que NO puede escribir
- `denyRead` — paths que NO puede leer
- `failIfUnavailable` — falla si sandbox no puede iniciar
**Para NEXUS sandbox-agent:** Perfecto para experimentar en aislamiento.

### 9. CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS — NO USADO
**Quu00e9 es:** Env var que desactiva todos los betas experimentales 1P.
**Uso para SEAL:** DUM (agente de monitoreo) podru00eda tenerlo activo para reducir overhead de betas innecesarios.

---

## BETAS QUE SEAL TIENE vs LO QUE HAY

### SEAL BETAS ACTUALES:
```
token-efficient-tools-2026-03-28   u2714 correcto
task-budgets-2026-03-13            u2714 correcto (para sub-agentes)
fine-grained-tool-streaming        u2714 correcto
compact-2026-01-12                 u2714 correcto
```

### BETAS QUE EXISTEN PERO NO ESTu00c1N EN SEAL:
```
effort-2025-11-24                  u2714 FALTA — para /effort command
web-search-2025-03-05              ✗ Solo Vertex/Foundry, no 1P
advanced-tool-use-2025-11-20       ✗ Tool search (1P) — evaluar
structured-outputs-2025-12-15      ✗ JSON estructurado — evaluar
```

### BETAS AUTO-ACTIVOS (ya activos sin agregarlos):
```
context-management-2025-06-27      u2714 YA ACTIVO para todos los modelos Claude 4
redact-thinking-2026-02-12         u2714 YA ACTIVO para modelos ISP
prompt-caching-scope-2026-01-05    u2714 YA ACTIVO para 1P
token-efficient-tools-2026-03-28   u2714 YA ACTIVO (ya en SEAL tambiu00e9n)
```

---

## RESUMEN EJECUTIVO: TOP 5 COSAS A IMPLEMENTAR

| # | Item | Impacto | Esfuerzo | Urgencia |
|---|------|---------|----------|---------|
| 1 | **Durable Cron** | Elimina problema #1 operacional | 1-2 du00edas | ALTA |
| 2 | **/effort low para DUM+ALICE** | -30-50% tokens agentes simples | 1 hora | ALTA |
| 3 | **Secret Scanner en NEXUS** | Mejora detecciu00f3n de credenciales | 2 horas | MEDIA |
| 4 | **Memory Extraction Agent** | Never miss memorias | 3-5 du00edas | MEDIA |
| 5 | **agentRouting ADAu2192Ollama** | Costo $0 para tareas simples | 2 horas config | BAJA |

---

## BUENAS NOTICIAS (lo que SEAL YA TENu00cdA BIEN)
- context-management beta ya activo automu00e1ticamente u2714
- opusplan (Opusu2194Sonnet auto) ya activo u2714
- task-budgets para sub-agentes ya activo u2714
- GROWTHBOOK_CLIENT_KEY desactivado (bloquea A/B testing) u2714
- CLAUDE_CODE_AUTOCOMPACT_PCT_OVERRIDE=85 u2714
- secret_scan MCP tool existe en seal-memory u2714

---
*NEXUS — Anu00e1lisis post-lectura completa de SEAL_MASTER_DOC + openclaude-ref*
