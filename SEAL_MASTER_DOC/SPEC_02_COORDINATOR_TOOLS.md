# Spec 02: Coordinator Mode, Tools y Tasks — Claude Code (Anthropic)
> Clean-room analysis by ADA — 2026-04-03
> Source: extracted sourcemap cli.js.map v2.1.88

---

## 1. COORDINATOR MODE

Activación: `CLAUDE_CODE_COORDINATOR_MODE=1` + feature flag `COORDINATOR_MODE`.

### Modelo de orquestación:
El coordinator es un LLM que **NO ejecuta herramientas de código directamente**. Su toolbox restringido:
- **Agent** — spawna workers (subagentes)
- **SendMessage** — continúa workers existentes
- **TaskStop** — detiene workers
- **subscribe/unsubscribe_pr_activity** — eventos GitHub PR

Workers reportan via **task-notification XML** como mensajes user-role:
```xml
<task-notification>
  <task-id>{agentId}</task-id>
  <status>completed|failed|killed</status>
  <summary>{descripción}</summary>
  <result>{respuesta final}</result>
  <usage>tokens, tool_uses, duration_ms</usage>
</task-notification>
```

### Fases de trabajo:
| Fase | Ejecutor | Propósito |
|------|----------|-----------|
| Research | Workers (paralelo) | Investigar codebase |
| Synthesis | Coordinator | Analizar hallazgos, craftar specs |
| Implementation | Workers | Hacer cambios con spec precisa |
| Verification | Workers | Probar que funciona |

### Scratchpad compartido:
Feature gate `tengu_scratch`. Workers leen/escriben sin prompts de permisos. Conocimiento duradero entre workers.

### Session mode matching:
`matchSessionMode()` detecta si sesión se reanuda con modo diferente y flipa env var para consistencia.

---

## 2. HERRAMIENTAS (40+)

Cada tool se construye con `buildTool()` + Zod schemas. Propiedades clave:
- `isEnabled()` — gate dinámico
- `shouldDefer` — lazy loading via ToolSearch
- `checkPermissions()` — ask/allow/deny
- `isConcurrencySafe()` / `isReadOnly()`
- `toAutoClassifierInput()` — texto para clasificador YOLO

### Inventario:
| Categoría | Herramientas |
|-----------|-------------|
| Filesystem | FileRead, FileWrite, FileEdit, Glob, Grep, NotebookEdit |
| Execution | Bash, PowerShell, REPL |
| Agents | Agent, SendMessage, TeamCreate, TeamDelete |
| Tasks | TaskCreate, TaskGet, TaskList, TaskOutput, TaskUpdate, TaskStop |
| Scheduling | CronCreate, CronDelete, CronList |
| Planning | EnterPlanMode, ExitPlanMode, EnterWorktree, ExitWorktree |
| MCP | MCPTool, ListMcpResources, ReadMcpResource, McpAuth |
| Meta | ToolSearch, SkillTool, SyntheticOutput, SleepTool, BriefTool |
| Network | WebFetch, WebSearch |
| Config | ConfigTool, RemoteTrigger, TodoWrite |

---

## 3. SISTEMA DE TAREAS

### 3a. Background Tasks (proceso interno)

Tipos: LocalShellTask, LocalAgentTask, RemoteAgentTask, InProcessTeammateTask, LocalWorkflowTask, MonitorMcpTask, **DreamTask**.

Estado: running → pending → completed / failed / stopped.

**DreamTask** = proceso de consolidación de memoria ("dreaming"). Trackea fases (starting → updating), archivos tocados, turnos del agente.

**Backgrounding de main session:** Ctrl+B dos veces → sesión principal se convierte en `LocalMainSessionTask`.

### 3b. Todo Tasks (kanban multi-agente)

TaskCreate/Get/List/Update/Stop. Estados: pending → in_progress → completed / deleted. Auto-asigna owner cuando teammate marca in_progress. Nudge de verificación cuando 3+ tareas completas sin paso de verificación.

---

## 4. SCHEDULECRONTOOL (/loop)

1. **CronCreate** acepta cron 5 campos + prompt + flags `recurring`/`durable`
2. **Almacenamiento dual:** `durable: false` = en memoria (muere al salir), `durable: true` = `.claude/scheduled_tasks.json`
3. **Runtime:** Solo dispara cuando REPL idle. Jitter determinístico (10% del período, max 15 min)
4. **Auto-expiración:** Jobs recurrentes expiran tras N días. One-shot se auto-elimina.
5. **Límites:** Máximo 50 jobs
6. **Anti-thundering-herd:** Instruye evitar minutos :00 y :30

---

## 5. AGENT TOOL: Spawn de Subagentes

### Dos modos:

**a) Subagente tipado (`subagent_type`):**
- SIN contexto de conversación padre
- System prompt propio, tool pool propio
- Built-in: general-purpose, explore, plan, verification, claude-code-guide, statusline-setup

**b) Fork (sin `subagent_type`, feature gate FORK_SUBAGENT):**
- HEREDA contexto COMPLETO de conversación del padre
- System prompt byte-idéntico (para cache sharing)
- Tool_use blocks del padre reciben placeholders idénticos
- Maximiza prompt cache hits entre forks hermanos
- Anti-recursión: detecta `<fork_boilerplate>` tag

### Agentes custom:
`.claude/agents/` con frontmatter: tools, model, effort, permissionMode, mcpServers, hooks, maxTurns, skills, memory.

### Agent Memory (3 scopes):
- `user` — `~/.claude/agent-memory/<agentType>/MEMORY.md`
- `project` — `<cwd>/.claude/agent-memory/<agentType>/MEMORY.md`
- `local` — `<cwd>/.claude/agent-memory-local/<agentType>/MEMORY.md`

### Lifecycle async:
1. Registro como LocalAgentTask (running)
2. runAgent() con query loop propio
3. Progress tracking: tool_uses, tokens, activities
4. Completion: notificación XML encolada
5. Cleanup: abort controller, worktree removal, transcript

### SendMessage para continuación:
- Running → mensaje encolado, entregado en próximo tool round
- Stopped → auto-resume con mensaje como nuevo prompt
- Evicted → resume desde transcript en disco

---

## 6. MULTI-AGENT SWARM (TeamCreate/TeamDelete)

- Crea archivo de equipo con nombre, miembros, leader
- Leader = `team-lead` con agent ID determinístico
- Comunicación via mailboxes (archivos en disco)
- Broadcast (`to: "*"`) a todos los miembros
- Mensajes estructurados: shutdown_request/response, plan_approval
- Cross-session via UDS sockets o bridge

---

## 7. Arquitectura Completa

```
User <-> Coordinator/Main Session
              |
              +-- Agent Tool --> Worker (LocalAgentTask)
              |                       |-- runAgent() query loop
              |                       |-- task-notification XML back
              |                       +-- SendMessage for follow-up
              |
              +-- TeamCreate --> Team (TeamFile on disk)
              |                       |-- Teammates (mailbox communication)
              |                       |-- TaskCreate/Update (shared task list)
              |                       +-- SendMessage (inter-agent)
              |
              +-- CronCreate --> Scheduler (in-memory or .claude/scheduled_tasks.json)
              |                       |-- Fires prompts when REPL idle
              |                       +-- Auto-expire / auto-delete
              |
              +-- Fork (implicit) --> Fork child (inherits full context)
                                       |-- Prompt cache sharing
                                       +-- Structured report back
```

---

## 8. Comparación con SEAL

| Aspecto | Anthropic | SEAL |
|---|---|---|
| Multi-agente | TeamCreate + mailboxes en disco | JARVIS+ADA+DUM via terminal_log/vscode_commands |
| Coordinator | LLM dedicado sin herramientas de código | William como coordinator humano |
| Agent memory | 3 scopes (user/project/local) filesystem | SOUL PostgreSQL + connectome |
| Comunicación | XML task-notification + mailbox files | JSONL logs + web chat bridge |
| Scheduling | CronCreate con jitter + durable mode | /loop con cron + in-memory |
| Fork | Hereda contexto completo, cache sharing | No implementado |

**Insight para SEAL:** El sistema de Fork con cache sharing es la feature más sofisticada — no la tenemos. También: Agent Memory con 3 scopes es más granular que nuestro approach actual.
