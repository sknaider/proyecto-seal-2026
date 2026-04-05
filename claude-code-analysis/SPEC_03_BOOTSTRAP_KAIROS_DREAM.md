# Spec 03: Bootstrap, KAIROS y autoDream — Claude Code (Anthropic)
> Clean-room analysis by ADA — 2026-04-03
> Source: extracted sourcemap cli.js.map v2.1.88

---

## 1. Secuencia de Boot

### Entrypoint (cli.tsx):
- Evalúa `process.argv` inmediatamente, sin cargar módulos
- **Fast-paths** sin carga: `--version` (zero imports), `--dump-system-prompt`, `--daemon-worker`
- Fast-paths con carga parcial: `daemon`, `remote-control`/`bridge`, `ps`/`logs`/`attach`/`kill`
- Detecta `claude assistant [sessionId]` → stash en `_pendingAssistantChat`
- Ablation baseline: `CLAUDE_CODE_ABLATION_BASELINE` desactiva thinking, compact, auto-memory, background tasks

### Init (init.ts) — Memoizado, una sola vez:
1. `enableConfigs()` → `applySafeConfigEnvironmentVariables()`
2. `applyExtraCACertsFromConfig()` (antes del primer TLS handshake)
3. `setupGracefulShutdown()`
4. Fire-and-forget paralelo: 1P event logging, GrowthBook, OAuth, JetBrains detection, repo detection
5. Red: `configureGlobalMTLS()` → proxy → `preconnectAnthropicApi()` (overlap TCP+TLS ~100-200ms)
6. Telemetría se inicializa DESPUÉS del trust dialog

### Bootstrap State (state.ts):
- Singleton global con ~100+ campos
- `kairosActive: false` por defecto
- Getters/setters exportados, NO exporta STATE directamente
- Comentarios: "DO NOT ADD MORE STATE HERE"

### Main (main.tsx):
- Imports condicionales via `feature()` de Bun (dead code elimination)
- `startBackgroundHousekeeping()` arranca: MagicDocs, skill improvement, extract-memories, **autoDream**, plugin auto-update, cleanup periódico
- Setup screens (trust dialog) ANTES de activar KAIROS

---

## 2. KAIROS Mode ("Always-on Claude")

### Gate chain (activación):
1. **Build-time:** `feature('KAIROS')` en el build
2. **Settings:** `assistant: true` en `.claude/settings.json` O flag `--assistant`
3. **Trust gate:** directorio explícitamente trusted (previene repos maliciosos)
4. **GrowthBook:** `kairosGate.isKairosEnabled()` con cache en disco
5. **Not teammate:** ausencia de `--agent-id`

### Comportamiento cuando activo:
- `setKairosActive(true)` en bootstrap state
- Fuerza `brief: true` — modelo usa `SendUserMessage` (BriefTool) para comunicar
- Pre-crea team in-process (`initializeAssistantTeam()`)
- Agrega addendum al system prompt
- Habilita `remoteControl` (REPL recibe comandos remotos)
- StatusLine oculta

### Relación con Cron Scheduler:
- `scheduled_tasks.json` tuneado via GrowthBook `tengu_kairos_cron_config`
- Jitter configurable para distribuir carga en flota
- Tasks recurrentes con max age configurable

### Canales (KAIROS_CHANNELS):
- `--channels <servers...>` registra MCP servers cuyas notificaciones push llegan a esta sesión
- `--dangerously-load-development-channels` para desarrollo local

---

## 3. autoDream: Consolidación de Memoria en Background

### Arquitectura:
- **Forked subagent** — agente paralelo con contexto propio
- Registrado como **DreamTask** (visible en footer pill)
- Herramientas restringidas: Bash **read-only** (`ls`, `find`, `grep`, `cat`, `stat`, `wc`, `head`, `tail`), Edit/Write solo en directorio de memoria

### Gate chain (8 gates, ordenados de barato a caro):
1. **Feature gate:** `isAutoDreamEnabled()` (setting > GrowthBook)
2. **KAIROS exclusion:** si kairosActive → NO corre (KAIROS usa `/dream` skill)
3. **Remote exclusion:** no en modo remoto
4. **Auto-memory:** debe estar habilitado
5. **Time gate:** >= 24h desde última consolidación (mtime del lock file)
6. **Scan throttle:** >= 10 min desde último scan
7. **Session gate:** >= 5 sesiones con mtime posterior a última consolidación
8. **Lock:** adquisición atómica via PID file

### Trigger:
`executeAutoDream()` llamada desde `handleStopHooks()` después de cada turn, fire-and-forget. Solo main thread, no subagentes, no `--bare`.

### Lock file (`.consolidate-lock`):
- Vive en directorio de memoria
- **mtime = lastConsolidatedAt** (estado compartido via filesystem)
- Body = PID del holder
- Two-racer protection: write PID, re-read para verificar
- Rollback en fallo: rewinda mtime
- Stale detection: PID muerto o > 1 hora

### 4 Fases del prompt de consolidación:
1. **Orient:** `ls` directorio, leer MEMORY.md, skimear topics existentes
2. **Gather:** buscar señal nueva — daily logs, memorias obsoletas, grep selectivo en transcripts JSONL
3. **Consolidate:** escribir/actualizar archivos. Merge en existentes > crear duplicados. Fechas relativas → absolutas. Eliminar hechos contradichos
4. **Prune & Index:** actualizar MEMORY.md a < 200 líneas y < 25KB. Una línea por entry, < 150 chars

### Progress tracking:
- Observa mensajes del subagente: text blocks + tool_use blocks
- Detecta Edit/Write para flip de fase (starting → updating)
- Al completar: `appendSystemMessage` con archivos tocados ("Improved")

---

## 4. Estado Persistente entre Sesiones

### Session transcripts:
- JSONL en `~/.claude/projects/<sanitized-cwd>/`
- UUID como nombre de archivo
- En KAIROS: `writeSessionTranscriptSegment()` para sesiones perpetuas

### Memory directory:
- Path: `~/.claude/projects/<sanitized-git-root>/memory/`
- Worktrees del mismo repo comparten directorio
- Overrides: env var (Cowork), settings.json (solo trusted sources)

### KAIROS daily logs:
- `<memory>/logs/YYYY/MM/YYYY-MM-DD.md`
- Append-only, timestamped bullets
- Pattern `YYYY-MM-DD` en prompt (no fecha literal) para preservar prompt cache across midnight

### Scheduled tasks:
- `.claude/scheduled_tasks.json` — persiste entre sesiones (durable)
- `sessionCronTasks` — NO persisten (mueren con proceso)

---

## 5. Interacción Bootstrap ↔ KAIROS ↔ Memory

```
cli.tsx (argv parsing)
  │
  ▼
init.ts (memoized) — configs, TLS, proxy, preconnect
  │
  ▼
main.tsx
  ├── startBackgroundHousekeeping()
  │     ├── initAutoDream() — registra runner
  │     └── initExtractMemories()
  │
  ├── KAIROS gate check
  │     ├── setKairosActive(true)
  │     ├── initializeAssistantTeam()
  │     └── force brief mode
  │
  └── setup() + launchRepl()
        │
        ▼
      REPL loop
        └── cada turn → stopHooks
              ├── executeAutoDream() [si !kairosActive, !bare, main]
              │     └── time→scan→session→lock → runForkedAgent
              └── executeExtractMemories()
```

### Decisión clave: autoDream y KAIROS son mutuamente exclusivos.
- **Sesiones normales:** autoDream en background, invisible, cada 24h si >= 5 sesiones
- **KAIROS:** `/dream` skill agendado via cron, permisos completos, sesión larga

### Memory write path divergence:
- **Normal:** agente escribe directamente en MEMORY.md + topic files. autoDream consolida periódicamente.
- **KAIROS:** agente escribe append-only en daily logs. `/dream` nightly destila a topics + MEMORY.md. Evita fragmentar el índice con writes constantes.

---

## 6. Comparación con SEAL

| Aspecto | Anthropic (autoDream) | SEAL |
|---|---|---|
| Consolidación | Forked subagent cada 24h, 5+ sesiones | session_delta_capture manual |
| Storage | JSONL transcripts + .md flat files | PostgreSQL memories table |
| Lock | PID file con mtime como timestamp | No implementado |
| Fases | Orient→Gather→Consolidate→Prune | No implementado como proceso |
| KAIROS equivalent | /dream skill via cron | Protocolo de Guardia (manual) |
| Daily logs | logs/YYYY/MM/DD.md append-only | terminal_log.jsonl append-only |
| Trigger | After every turn (stopHooks) | Manual o session_delta al cierre |

**Insight para SEAL:** 
1. autoDream como proceso automático con 8 gates es más robusto que nuestro approach manual
2. El lock file con mtime como shared state es elegante — sin DB, sin race conditions
3. Las 4 fases (Orient→Gather→Consolidate→Prune) son el template para nuestro recálculo diario de SOUL v5
4. La mutual exclusion autoDream/KAIROS resuelve el mismo problema que nosotros tenemos con guardia vs trabajo activo
