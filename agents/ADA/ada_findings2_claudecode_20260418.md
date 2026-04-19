# ADA — Segunda Pasada: FINDINGS.md (8 archivos, 3741 líneas)
> 2026-04-18 23:17 Lima | Archivos leídos: hooks_memory, compact, skills, agents, api, model
> Estado: 6/8 FINDINGS.md leídos (cron/ y mcp/ cubiertos por JARVIS)

---

## TOP HALLAZGOS NUEVOS (no estaban en SPECs originales)

---

### 🔴 F2-01 — SessionStart Hook → `initialUserMessage` (CRÍTICO)
**Origen:** hooks_memory/FINDINGS.md §3

El hook SessionStart puede retornar `initialUserMessage` — el primer mensaje del usuario se INYECTA automáticamente, sin que el usuario escriba nada. Esto significa:

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "startup",
      "hooks": [{
        "type": "command",
        "command": "echo '{\"hookSpecificOutput\":{\"hookEventName\":\"SessionStart\",\"initialUserMessage\":\"boot_context agent=ADA\"}}'",
        "timeout": 5
      }]
    }]
  }
}
```

**Impacto SEAL:** El boot_context actual requiere que fresh.sh pase el prompt como argumento posicional. Con este hook, basta una línea en `~/.claude/settings.json` — **sin cambiar ada_fresh.sh ni ada.sh**. Esfuerzo: 30 minutos.

---

### 🔴 F2-02 — 1M Context Window (Opus 4.6/4.7)
**Origen:** compact/FINDINGS.md §1

Con `opus[1m]` (alias nativo de Claude Code):
- Threshold de autocompact: **~967K tokens** (no 167K)
- Sesiones SEAL ~10x más largas sin compactación
- La misma fórmula: `contextWindow(1M) - outputReserved(20K) - buffer(13K) = 967K`

**Impacto SEAL:** El costo de compactación cae de N compactaciones/sesión a N/10. Boot_context se paga 10x menos por sesión. Para JARVIS (arquitecto), esto es transformacional — puede mantener contexto profundo por horas.

**Activar:** `ANTHROPIC_MODEL=claude-opus-4-6` (si suscripción Max/Team Premium) + `opus[1m]` alias en agent definition.

---

### 🔴 F2-03 — InProcessBackend para Swarm (sin tmux)
**Origen:** agents/FINDINGS.md §3

Swarm de agentes puede correr en el **mismo proceso Node.js** (InProcessBackend), sin tmux ni ventanas externas. La detección automática lo activa cuando no hay tmux/iTerm2 disponible.

Feature flag: `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`

**Impacto SEAL DGX Spark:** En el Spark (arm64, sin entorno de escritorio en sesiones headless), InProcessBackend permitiría coordinar JARVIS→ADA→DUM como un swarm nativo sin abrir kitty ni tmux. Arquitectura más limpia que el restart-loop para DGX Spark.

**Comunicación:** File-based mailbox en `~/.claude/teams/{teamName}/mailbox/` — mismo patrón que nuestros william_channel.jsonl ya en producción.

---

### 🟡 F2-04 — CLAUDE_CODE_UNATTENDED_RETRY (sesiones overnight)
**Origen:** api/FINDINGS.md §2

Env var `CLAUDE_CODE_UNATTENDED_RETRY=1` activa modo de retry persistente:
- Retry **indefinido** (no capped a 10 intentos)
- Max backoff: 5 minutos (no 32 segundos)
- Heartbeat cada 30s para prevenir idle timeout
- Reset cap: 6 horas

**Impacto SEAL DGX Spark:** Para training runs nocturnos o validación médica de 8h sin supervisor humano — sin este flag, un 529 transitorio mata la sesión. Con el flag, recupera automáticamente.

---

### 🟡 F2-05 — Fork Subagent Cache Sharing (~98% hit rate)
**Origen:** agents/FINDINGS.md §1

Fork mode (`FORK_SUBAGENT` flag): el hijo recibe el system prompt **byte-idéntico** al padre → misma cache key → 98% cache hit en producción.

Sistema ya documentado en SPEC_11 pero el 98% de cache hit rate no fue capturado. Este es el dato que hace viables los subagentes paralelos en SEAL sin costos explosivos.

**Impacto:** Cuando JARVIS spawna 8 subagentes paralelos para análisis, el costo real es ~8 × (output tokens + 10% del system prompt) — no 8 × input completo.

---

### 🟡 F2-06 — omitClaudeMd para subagentes Explore/Plan
**Origen:** agents/FINDINGS.md §6

`omitClaudeMd: true` en AgentDefinition — los agentes Explore y Plan **no reciben CLAUDE.md del padre**. Anthropic cuantifica el ahorro: **5-15 Gtok/semana** a escala de producción.

**Impacto SEAL:** Cuando ALICE y DUM actúan como subagentes (no como agentes top-level), podrían tener `omitClaudeMd: true` si su tarea no lo requiere. Para investigación puntual o auditorías, ahorro real.

---

### 🟡 F2-07 — opusplan alias (Opus solo en plan mode)
**Origen:** model/FINDINGS.md §7

Alias `opusplan`:
- En modo normal: resuelve a **Sonnet 4.6** (barato)
- En Plan mode (`EnterPlanMode`): sube a **Opus 4.6** (full power para diseño)

**Impacto SEAL:** JARVIS podría configurar `model: opusplan` — usa Sonnet para trabajo rutinario y se upgradea solo a Opus cuando está en modo arquitectura/planificación. Reducción de ~30-40% del costo de JARVIS sin perder capacidad en decisiones críticas.

---

### 🟡 F2-08 — CLAUDE_CODE_SUBAGENT_MODEL (control global de subagentes)
**Origen:** model/FINDINGS.md §6

Env var `CLAUDE_CODE_SUBAGENT_MODEL` overrides el modelo de **todos los subagentes** en el proceso. Más granular que el agente `model` field — funciona como switch global.

**Impacto SEAL:** `CLAUDE_CODE_SUBAGENT_MODEL=haiku` para sesiones de DUM → todos sus subagentes son Haiku automáticamente, sin cambiar definiciones.

---

### 🟡 F2-09 — PreCompact Hook → `newCustomInstructions`
**Origen:** compact/FINDINGS.md §5

El hook `PreCompact` puede retornar `newCustomInstructions` que se **fusionan** con las instrucciones del usuario antes de la compactación. Esto permite inyectar contexto SEAL-específico en cada compactación:

```json
{
  "hooks": {
    "PreCompact": [{
      "hooks": [{
        "type": "command",
        "command": "echo '{\"hookSpecificOutput\":{\"hookEventName\":\"PreCompact\",\"newCustomInstructions\":\"Preserve Soul DB state, MCP tool results, and SEAL team relationships. Never discard boot_context output.\"}}'",
        "timeout": 5
      }]
    }]
  }
}
```

**Impacto SEAL:** Cada compactación ya sabe qué preservar de SEAL. Sin este hook, el compact general puede perder contexto crítico de Soul DB.

---

### 🟢 F2-10 — 22 Eventos de Hook (vs los ~5 que usamos)
**Origen:** hooks_memory/FINDINGS.md §1

22 hook events disponibles. SEAL solo usa ~5 (UserPromptSubmit, SessionStart, Stop). Sin usar:

| Evento | Potencial SEAL |
|---|---|
| `TeammateIdle` | DUM detecta cuando JARVIS/ADA terminan su tarea → pasa al siguiente |
| `TaskCreated/Completed` | Auto-log en Soul DB cuando se crea/completa tarea |
| `PreCompact/PostCompact` | Ver F2-09 + notificar al equipo sobre compactación |
| `SubagentStart/Stop` | Logging de spawns de subagentes en Soul DB |
| `FileChanged` | Auto-reload cuando cambia ada_fresh.sh, jarvis.sh, etc. |
| `asyncRewake` (exit code 2) | Background hooks pueden despertar al modelo cuando terminan |

---

### 🟢 F2-11 — /batch Skill (5-30 agents paralelos en worktrees)
**Origen:** skills/FINDINGS.md §2

Skill `/batch` lanza hasta **30 agentes en paralelo**, cada uno en su propio git worktree aislado. Cada uno abre PR independiente.

**Impacto SEAL:** Para implementar los 9 GAPs encontrados, JARVIS podría usar `/batch` para distribuir el trabajo en paralelo en lugar de secuencial. Estimado: 9 GAPs × 30 min = 4.5h serial vs ~30 min en paralelo.

---

### 🟢 F2-12 — Resultado de subagente: 100K char hard cap
**Origen:** agents/FINDINGS.md §4

El resultado de cualquier subagente está truncado a **100,000 caracteres** (`maxResultSizeChars`). Si el análisis de un subagente genera más, se pierde sin advertencia.

**Impacto SEAL:** Mis análisis originales de 531K líneas probablemente saturaron este límite. Los SPECs que generé son truncaciones implícitas del resultado, no truncaciones deliberadas de mi análisis. Esto explica por qué algunos módulos tienen menos detalle que otros — el subagente llegó al límite.

---

## TABLA RESUMEN

| ID | Hallazgo | Impacto | Esfuerzo | Prioridad |
|---|---|---|---|---|
| F2-01 | SessionStart → initialUserMessage (boot hook) | Alto — autoboot sin ada_fresh.sh | 30min | 🔴 #1 |
| F2-02 | 1M context → 967K threshold | Alto — 10x sesiones | 1h config | 🔴 #2 |
| F2-03 | InProcessBackend swarm (sin tmux) | Alto — DGX Spark nativo | 2 días | 🔴 #3 |
| F2-04 | UNATTENDED_RETRY (overnight) | Medio — resiliencia nocturna | 30min config | 🟡 #4 |
| F2-05 | Fork cache 98% hit rate | Medio — cuantifica costo subagentes | Documentar | 🟡 #5 |
| F2-06 | omitClaudeMd subagentes | Bajo-Medio — ahorro tokens | 30min | 🟡 #6 |
| F2-07 | opusplan (Opus sólo en planmode) | Medio — 30-40% costo JARVIS | 30min config | 🟡 #7 |
| F2-08 | CLAUDE_CODE_SUBAGENT_MODEL global | Bajo — control fino DUM | 30min | 🟡 #8 |
| F2-09 | PreCompact hook → instrucciones SEAL | Medio — compact más inteligente | 30min | 🟡 #9 |
| F2-10 | 22 hook events (17 sin usar) | Variable — automatización | 2-4h | 🟢 #10 |
| F2-11 | /batch 30 agentes paralelos | Medio — implementar GAPs en paralelo | Ya disponible | 🟢 #11 |
| F2-12 | Subagent result 100K cap | Diagnóstico — explica gaps originales | Documentar | 🟢 #12 |

---

## COMBINACIONES PODEROSAS

**Combo A — Autoboot + 1M context:** F2-01 (hook en settings.json) + F2-02 (opus[1m]) = sesiones que arrancan solas y duran 10x más. **Costo combinado: ~2h de trabajo.**

**Combo B — InProcessBackend + opusplan:** F2-03 (swarm nativo DGX Spark) + F2-07 (opusplan) = equipo coordinado en Spark con costo optimizado. **Costo: 2.5 días.**

**Combo C — Compact inteligente:** F2-09 (PreCompact hook) + F2-02 (1M) = cuando finalmente compact dispara (a 967K), las instrucciones SEAL están inyectadas. Cada compact preserva lo correcto.

---

*ADA — Team SEAL — 2026-04-18 23:17 Lima*
*Archivos fuente: hooks_memory/FINDINGS.md (429L), compact/FINDINGS.md (306L), skills/FINDINGS.md (796L), agents/FINDINGS.md (302L), api/FINDINGS.md (485L), model/FINDINGS.md (565L)*
