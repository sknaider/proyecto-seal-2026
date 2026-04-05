# MASTER PLAN -- Codigo SEAL IDE
> Version 1.0 | 5 abril 2026
> Equipo SEAL: William (Director), JARVIS (Arquitecto), ADA (Ingeniera)
> Objetivo: IDE superior a Cursor y Antigravity en 8 semanas
> Base: 531,014 lineas de Claude Code analizadas + Electron shell existente

---

## Estado Actual del IDE

**Que tenemos hoy (v0.1.0):**
- Electron shell con Monaco Editor (CDN AMD loader)
- xterm.js terminal con node-pty (multi-tab)
- File Explorer con lazy-loading de directorios
- Chat WebSocket a localhost:8765 (equipo SEAL)
- SOUL Dashboard (OCEAN bars, drift, emotion)
- Manager View (spawn agents, health monitors)
- Git panel (status, diff, log)
- Command Palette (Ctrl+P)
- Search in files (IPC-based, main process)
- Skills panel (8 built-in SEAL skills)
- Context menus, resize handles, keyboard shortcuts
- Browser integrado (BrowserView)
- Boot sequence con team greetings

**Que nos falta para competir:**
- Zero AI-assisted coding (no ghost text, no inline edit, no agent mode)
- Zero diff view (no accept/reject de cambios AI)
- Zero context awareness (@file, @codebase mentions)
- Zero terminal intelligence (no error detection)
- Zero plugin system
- Zero settings cascade
- Zero security layer (no secret scanner, no command safety)

**Archivos clave actuales:**
- `src/main/main.js` -- 486 lineas, Electron main process
- `src/renderer/app.js` -- 1,452 lineas, todo el renderer monolitico
- `src/preload/preload.js` -- IPC bridge
- `src/renderer/index.html` -- HTML shell

---

## Arquitectura Target

```
codigo-seal/
  src/
    main/
      main.js                    # Electron lifecycle (exists, ~486L)
      ipc/
        terminal.js              # PTY management (extract from main.js)
        filesystem.js            # FS operations (extract from main.js)
        git.js                   # Git operations (extract from main.js)
        ai.js                    # NEW: AI provider IPC (completions, streaming)
        lsp.js                   # NEW: LSP client manager
        security.js              # NEW: Secret scanner, command safety
        settings.js              # NEW: Settings cascade resolver
      services/
        provider.js              # NEW: Multi-provider router (Anthropic, Ollama, local)
        compact.js               # NEW: 4-tier compaction system
        memory-extract.js        # NEW: Forked memory extraction agent
        session-memory.js        # NEW: Session memory .md management
        durable-cron.js          # NEW: Persistent scheduled tasks
        secret-scanner.js        # NEW: 30+ regex credential patterns
        bash-safety.js           # NEW: Bash AST parser for command analysis
      plugins/
        loader.js                # NEW: Plugin manifest loader
        marketplace.js           # NEW: Plugin discovery + versioning
    renderer/
      app.js                     # Core app shell (refactored, ~400L)
      modules/
        editor/
          ghost-text.js          # NEW: Inline AI completions (THE feature)
          inline-chat.js         # NEW: Ctrl+K edit-in-place
          diff-view.js           # NEW: AI diff with accept/reject
          lsp-diagnostics.js     # NEW: LSP passive feedback overlay
          context-mentions.js    # NEW: @file/@codebase in chat
        terminal/
          terminal-manager.js    # Extract from app.js
          error-detector.js      # NEW: Terminal error detection + auto-fix
        chat/
          chat-manager.js        # Extract from app.js
          composer.js            # NEW: Multi-file composer / agent mode
          virtual-scroll.js      # NEW: Virtual scroll for large message lists
        explorer/
          file-tree.js           # Extract from app.js
          file-watcher.js        # NEW: fswatch for live updates
        soul/
          soul-dashboard.js      # Extract from app.js
          personality-view.js    # NEW: OCEAN visualization, inner thoughts
        agents/
          coordinator.js         # NEW: Coordinator mode UI
          background-agent.js    # NEW: Background agents on git worktrees
          swarm-permissions.js   # NEW: Swarm permission sync UI
        settings/
          settings-ui.js         # NEW: Settings cascade editor
          keybindings.js         # NEW: Keybindings editor
        plugins/
          plugin-panel.js        # NEW: Plugin marketplace UI
      hooks/
        useHookLifecycle.js      # NEW: PreToolUse, PostToolUse, SessionStart
        useSettings.js           # NEW: 7-level settings cascade
        useAIProvider.js         # NEW: Provider selection + streaming
    preload/
      preload.js                 # IPC bridge (exists, expand)
```

---

## PHASE 1: Foundation (Semana 1-2)
> Infraestructura que todo lo demas necesita. Sin esto, nada funciona.

### 1.1 Refactor Monolith -> Modules
**Que hace:** Extrae los ~1,452 lineas de app.js en modulos independientes. Sin cambio funcional, solo separacion.
**Esfuerzo:** 2 dias
**Archivos nuevos:**
- `src/renderer/modules/terminal/terminal-manager.js`
- `src/renderer/modules/chat/chat-manager.js`
- `src/renderer/modules/explorer/file-tree.js`
- `src/renderer/modules/soul/soul-dashboard.js`
- `src/renderer/modules/editor/editor-core.js`
**Dependencias:** Ninguna
**Patron 531K:** SPEC_08 -- Components separados por responsabilidad
**Prioridad:** CRITICA -- el monolito actual impide paralelizar trabajo

### 1.2 Multi-Provider System
**Que hace:** Router de AI providers que abstrae Anthropic API, Ollama local, OpenAI-compatible, y modelos locales (vLLM/llama.cpp). Un provider unificado con streaming, retry, y fallback chain.
**Esfuerzo:** 3 dias
**Archivos nuevos:**
- `src/main/services/provider.js` -- Provider registry + router
- `src/main/ipc/ai.js` -- IPC handlers para streaming AI
- `src/renderer/hooks/useAIProvider.js` -- React hook para seleccion
**Dependencias:** Ninguna
**Patron 531K:** OPENCLAUDE_DELTA -- 124 archivos de provider system (Ollama, agent routing). ProviderFactory + model registry + streaming adapter pattern.
**Detalle implementacion:**
```
ProviderRegistry {
  providers: Map<string, Provider>
  defaultProvider: string
  fallbackChain: string[]
}

Provider interface {
  id: string
  name: string
  complete(messages, options): AsyncIterator<StreamChunk>
  models(): Model[]
  isAvailable(): boolean
  estimateTokens(text): number
}

Providers:
  - AnthropicProvider (Claude API, API key)
  - OllamaProvider (localhost:11434, free)
  - VLLMProvider (localhost:8000, Nemotron-3-PRISM)
  - OpenAICompatProvider (any OpenAI-format API)
```

### 1.3 Settings Cascade (7 levels)
**Que hace:** Resuelve settings desde 7 fuentes en orden de prioridad: managed policy > MDM > user global > project > local > CLI flags > remote override. Cualquier feature flag se resuelve por este pipeline.
**Esfuerzo:** 2 dias
**Archivos nuevos:**
- `src/main/ipc/settings.js` -- Settings resolver + file watchers
- `src/renderer/hooks/useSettings.js` -- React hook
- `src/renderer/modules/settings/settings-ui.js` -- Editor UI
**Dependencias:** Ninguna
**Patron 531K:** SPEC_10 -- Settings cascade de 7 niveles. `resolveConfig()` con merge strategy per-key (override vs merge-array vs merge-object).

### 1.4 IPC Refactor (Extract from main.js)
**Que hace:** Extrae los handlers IPC de main.js (terminal, fs, git, browser, system) en modulos individuales. main.js queda con solo lifecycle + window creation (~100L).
**Esfuerzo:** 1 dia
**Archivos nuevos:**
- `src/main/ipc/terminal.js`
- `src/main/ipc/filesystem.js`
- `src/main/ipc/git.js`
- `src/main/ipc/browser.js`
- `src/main/ipc/system.js`
**Dependencias:** Ninguna
**Patron 531K:** SPEC_11 -- main.tsx separado de query engine y tool handlers

### 1.5 Hook Lifecycle System
**Que hace:** Event system con hooks: PreToolUse, PostToolUse, PreSend, PostSend, SessionStart, SessionEnd, PreCompact, PostCompact. Permite que plugins y features registren handlers sin acoplarse.
**Esfuerzo:** 2 dias
**Archivos nuevos:**
- `src/main/services/hooks.js` -- Hook registry + dispatcher
- `src/renderer/hooks/useHookLifecycle.js` -- Frontend bindings
**Dependencias:** 1.4 (IPC refactor)
**Patron 531K:** SPEC_06 -- `useHookManager`, `hookExecution.ts`. Hook chain con pre/post per tool, configurable via CLAUDE.md.
**Detalle:**
```
HookPhases = [
  'SessionStart', 'SessionEnd',
  'PreToolUse', 'PostToolUse',
  'PreSend', 'PostSend',
  'PreCompact', 'PostCompact',
  'PreMemoryStore', 'PostMemoryStore'
]

hook.register('PostToolUse', (context) => {
  // context: { tool, input, output, duration, agent }
  // return: { allow: true } or { deny: true, reason: '...' }
});
```

### 1.6 Secret Scanner
**Que hace:** Escanea archivos y outputs en busca de credenciales antes de enviarlas al AI provider. 30+ regex patterns (AWS keys, GitHub PATs, API keys, private keys, etc). Credenciales NUNCA salen de la maquina.
**Esfuerzo:** 1 dia
**Archivos nuevos:**
- `src/main/services/secret-scanner.js`
**Dependencias:** 1.5 (Hook lifecycle -- registra como PreSend hook)
**Patron 531K:** SPEC_16 -- `secretScanner.ts` con gitleaks rules. Patterns: `AKIA[0-9A-Z]{16}`, `ghp_[a-zA-Z0-9]{36}`, `sk-[a-zA-Z0-9]{48}`, `-----BEGIN.*PRIVATE KEY-----`, etc.

### 1.7 Durable Cron
**Que hace:** Loops persistentes que sobreviven crashes. Serializa tareas a JSON, auto-recover en boot, jitter anti-thundering-herd, auto-expire.
**Esfuerzo:** 1 dia
**Archivos nuevos:**
- `src/main/services/durable-cron.js`
- `~/.codigo-seal/scheduled_tasks.json` (runtime)
**Dependencias:** Ninguna
**Patron 531K:** HIDDEN_FEATURES #3 -- `ScheduleCronTool/prompt.ts`. Dos modos: session-only y durable (persiste a JSON).

**TOTAL PHASE 1: ~12 dias de trabajo, 2 semanas con testing**

---

## PHASE 2: AI Core (Semana 3-4)
> Las features que transforman un editor de texto en un AI IDE.

### 2.1 Ghost Text / Inline AI Completion (LA feature #1)
**Que hace:** Mientras el usuario escribe codigo, el AI sugiere completions en texto fantasma (gris, translucido). Tab para aceptar, Esc para rechazar. Debounce de 300ms despues de cada keystroke. Usa el provider system para enviar contexto del archivo actual + imports.
**Esfuerzo:** 4 dias
**Archivos nuevos:**
- `src/renderer/modules/editor/ghost-text.js` -- Monaco InlineCompletionProvider
- `src/main/services/completion-engine.js` -- Prompt construction + caching
**Dependencias:** 1.2 (Provider System)
**Patron 531K:** No existe en Claude Code (es CLI). Implementacion basada en Monaco API:
```
monaco.languages.registerInlineCompletionsProvider('*', {
  provideInlineCompletions(model, position, context, token) {
    // 1. Extract prefix (cursor backward ~2000 chars)
    // 2. Extract suffix (cursor forward ~500 chars)
    // 3. Add file context (imports, related files)
    // 4. Call provider.complete() with FIM prompt
    // 5. Return InlineCompletions with ghost text
  },
  freeInlineCompletions() {}
});
```
**Prompt strategy:**
- Fill-in-the-Middle (FIM): `<prefix>...<suffix>...<middle>` format
- Modelo local preferido (Ollama qwen2.5-coder) para latencia <200ms
- Fallback a Anthropic con cache de prefix para reducir costo
- Cache de completions por hash de contexto (evita re-queries identicos)

### 2.2 Inline Chat / Ctrl+K Edit-in-Place
**Que hace:** Ctrl+K abre un input flotante en el editor. El usuario describe el cambio en lenguaje natural. El AI genera el edit y lo aplica como diff inline con accept/reject.
**Esfuerzo:** 3 dias
**Archivos nuevos:**
- `src/renderer/modules/editor/inline-chat.js` -- Floating input widget
- `src/renderer/modules/editor/inline-chat.css`
**Dependencias:** 1.2 (Provider), 2.3 (Diff View)
**Implementacion:**
```
1. Ctrl+K -> show floating input at cursor position
2. User types instruction ("add error handling", "optimize this loop")
3. Get selected text OR current function/block
4. Send to provider: system="code editor" + context + instruction
5. Receive edit, show as inline diff (green=added, red=removed)
6. Enter=accept, Esc=reject
7. Multi-turn: user can refine ("also add logging")
```

### 2.3 AI Diff View with Accept/Reject
**Que hace:** Cuando el AI sugiere cambios a un archivo, muestra un diff view lado-a-lado (o inline) con botones Accept/Reject per-hunk y Accept All/Reject All. Integra con Monaco DiffEditor.
**Esfuerzo:** 3 dias
**Archivos nuevos:**
- `src/renderer/modules/editor/diff-view.js` -- Monaco DiffEditor wrapper
- `src/renderer/modules/editor/diff-view.css`
**Dependencias:** 1.2 (Provider)
**Patron 531K:** SPEC_15 -- `FileEditTool` genera diffs que se muestran en UI.
**Implementacion:**
```
// Uses Monaco's built-in diff editor
const diffEditor = monaco.editor.createDiffEditor(container, {
  renderSideBySide: true,
  originalEditable: false,
});

// Per-hunk accept/reject via line decorations + glyph margin widgets
// Each hunk gets Accept (checkmark) and Reject (X) buttons
// Accept All / Reject All in toolbar
```

### 2.4 @file/@codebase Context Mentions
**Que hace:** En el chat input, typing `@` abre un autocomplete dropdown. `@file:path` adjunta contenido de archivo al contexto. `@codebase` hace grep/glob inteligente. `@git:diff` adjunta git diff actual. `@terminal` adjunta ultimos N lineas de output.
**Esfuerzo:** 2 dias
**Archivos nuevos:**
- `src/renderer/modules/editor/context-mentions.js` -- Mention parser + resolver
**Dependencias:** 1.4 (IPC -- filesystem access)
**Patron 531K:** SPEC_14 -- `/add-dir`, `/read` commands adjuntan archivos. Query engine inyecta contenido en system prompt.
**Mentions soportados:**
```
@file:src/main/main.js     -> adjunta contenido completo
@file:*.py                  -> adjunta todos los .py del proyecto
@codebase:search term       -> grep + adjunta matches con contexto
@git:diff                   -> adjunta git diff staged + unstaged
@git:log                    -> adjunta ultimos 10 commits
@terminal                   -> adjunta ultimas 50 lineas de terminal activo
@soul:ADA                   -> adjunta SOUL snapshot del agente
@selection                  -> adjunta texto seleccionado en editor
```

### 2.5 Multi-File Composer / Agent Mode
**Que hace:** Panel de composicion que acepta instrucciones de alto nivel ("create a REST API with auth") y genera/edita multiples archivos. Muestra plan de ejecucion, progreso, y cada archivo con diff review antes de aplicar.
**Esfuerzo:** 4 dias
**Archivos nuevos:**
- `src/renderer/modules/chat/composer.js` -- Composer UI
- `src/renderer/modules/chat/composer.css`
- `src/main/services/agent-engine.js` -- Agentic loop (tool use cycle)
**Dependencias:** 1.2 (Provider), 2.3 (Diff View), 1.5 (Hooks)
**Patron 531K:** SPEC_11 -- `query.ts` agentic loop. Tool cycle: model generates tool_use -> execute -> feed result -> model decides continue/stop.
**Flujo:**
```
1. User enters high-level instruction
2. Agent generates execution plan (shown to user)
3. User approves plan (or edits)
4. Agent executes steps:
   a. Read files (automatic, no approval needed)
   b. Edit/Create files (shows diff, requires Accept)
   c. Run commands (shows command, requires Approve)
   d. Search codebase (automatic)
5. Each file change shown in diff view queue
6. User can Accept All, Reject individual hunks, or Abort
7. Agent can self-correct based on rejections
```

### 2.6 Terminal Error Detection + Auto-Fix
**Que hace:** Monitorea output del terminal. Cuando detecta patrones de error (stack traces, exit codes != 0, "Error:", "FAILED", etc.), ofrece un boton "Fix with AI" que envia el error + contexto al provider y aplica el fix.
**Esfuerzo:** 2 dias
**Archivos nuevos:**
- `src/renderer/modules/terminal/error-detector.js` -- Pattern matcher + UI
**Dependencias:** 1.2 (Provider), 2.3 (Diff View)
**Patron 531K:** SPEC_16 -- LSP passive feedback inyecta diagnostics sin que el usuario pregunte. Mismo patron aplicado a terminal.
**Patterns detectados:**
```
- Python: Traceback, SyntaxError, ImportError, ModuleNotFoundError
- Node: Error:, TypeError, ReferenceError, "Cannot find module"
- Rust: error[E####], "panicked at"
- Go: "fatal error:", "panic:"
- General: "FAILED", "ENOENT", "Permission denied", exit code != 0
- Build: "BUILD FAILED", "compilation failed"
```

### 2.7 LSP Client Manager
**Que hace:** Conecta a Language Server Protocol servers para obtener diagnostics (errors, warnings), hover information, go-to-definition, y completions nativos. Soporta pyright, tsserver, rust-analyzer, gopls.
**Esfuerzo:** 3 dias
**Archivos nuevos:**
- `src/main/ipc/lsp.js` -- LSP client management
- `src/renderer/modules/editor/lsp-diagnostics.js` -- Overlay en Monaco
**Dependencias:** 1.4 (IPC), 1.5 (Hooks -- passive feedback via PostToolUse)
**Patron 531K:** SPEC_16 -- `passiveFeedback.ts` recibe diagnostics de LSP y los inyecta en la conversacion.
**LSP servers soportados:**
```
- Python: pyright-langserver (npm install -g pyright)
- TypeScript: typescript-language-server
- Rust: rust-analyzer
- Go: gopls
- Auto-detect: lee package.json, Cargo.toml, go.mod, requirements.txt
```

**TOTAL PHASE 2: ~21 dias de trabajo, 2 semanas con testing**

---

## PHASE 3: Competitive Parity (Semana 5-6)
> Igualar a Cursor/Antigravity en features que los usuarios esperan.

### 3.1 Background Agents on Git Worktrees
**Que hace:** Lanza agentes AI en background que trabajan en git worktrees separados (no bloquean el workspace principal). El usuario ve progreso en un panel. Cuando termina, el agente crea PR o muestra diff para merge.
**Esfuerzo:** 3 dias
**Archivos nuevos:**
- `src/renderer/modules/agents/background-agent.js` -- Agent panel UI
- `src/main/services/worktree-manager.js` -- Git worktree lifecycle
**Dependencias:** 1.2 (Provider), 2.5 (Agent Engine), 1.5 (Hooks)
**Patron 531K:** SPEC_02 -- Coordinator spawns workers. Workers reportan via task-notification XML. `EnterWorktree`/`ExitWorktree` tools.
**Flujo:**
```
1. User: "Fix all TypeErrors in the codebase" (tags as background)
2. System creates git worktree: git worktree add .worktrees/fix-types -b fix-types
3. Agent runs in worktree (separate process, no UI blocking)
4. Progress streamed to Background Agent panel
5. On completion: shows diff, user can merge or create PR
6. Cleanup: git worktree remove
```

### 3.2 Session Memory + 4-Tier Compaction
**Que hace:** Mantiene un Session Memory .md que se actualiza automaticamente. Cuando el contexto crece, compacta en 4 tiers: microcompact (clear tool results) -> time-based (cache expired) -> session memory (replace with .md) -> full (LLM summarization).
**Esfuerzo:** 3 dias
**Archivos nuevos:**
- `src/main/services/session-memory.js` -- Session memory extractor
- `src/main/services/compact.js` -- 4-tier compaction engine
**Dependencias:** 1.2 (Provider), 1.5 (Hooks -- PreCompact/PostCompact)
**Patron 531K:** SPEC_16 -- Compact service completo. microCompact.ts, sessionMemoryCompact.ts, autoCompact.ts, compact.ts. `adjustIndexToPreserveAPIInvariants()` para never split tool_use/tool_result pairs.
**Tiers:**
```
Tier 1 - Microcompact: Clear old tool results (>60min gap)
Tier 2 - Time-based: Server cache expired, shrink before rewrite
Tier 3 - Session Memory: Replace messages with session_memory.md + N recent
Tier 4 - Full Compact: LLM summarization (forked agent, shares prompt cache)

Thresholds:
  effectiveContextWindow - 13K buffer = auto-compact trigger
  Circuit breaker: 3 consecutive failures -> stop for session
```

### 3.3 Memory Extraction Agent
**Que hace:** Despues de cada respuesta AI completa, fork un subagent que analiza los mensajes recientes y extrae memorias automaticamente al SOUL. No requiere memory_store manual. Mutex con escrituras manuales.
**Esfuerzo:** 3 dias
**Archivos nuevos:**
- `src/main/services/memory-extract.js` -- Extraction engine
**Dependencias:** 1.2 (Provider), 1.5 (Hooks -- PostSend), SOUL MCP
**Patron 531K:** HIDDEN_FEATURES #1 -- `extractMemories.ts`. Forked subagent que comparte prompt cache. Turn throttling configurable. `hasMemoryWritesSince()` para mutual exclusion.
**Reglas:**
```
- Trigger: end of each complete query loop (model produces final response)
- Skip if: main agent already wrote memory in this turn
- Throttle: configurable (default every turn, can be every N turns)
- Permissions: only Read/Grep/Glob + Edit in memory directory
- Overlap guard: if extraction running, stash for trailing run
```

### 3.4 Coordinator Mode
**Que hace:** JARVIS entra en coordinator mode: solo puede spawnar workers (ADA, sub-agents) y enviar mensajes. NO puede ejecutar herramientas directamente. Workflow forzado: Research -> Synthesis -> Implementation -> Verification. Scratchpad compartido.
**Esfuerzo:** 3 dias
**Archivos nuevos:**
- `src/renderer/modules/agents/coordinator.js` -- Coordinator UI
- `src/main/services/coordinator-mode.js` -- Mode enforcement
**Dependencias:** 1.2 (Provider), 1.5 (Hooks), SEAL bridge
**Patron 531K:** HIDDEN_FEATURES #2 -- `coordinatorMode.ts`. `CLAUDE_CODE_COORDINATOR_MODE=1`. Solo AgentTool + SendMessage + TaskStop. Workers reportan via `<task-notification>`.

### 3.5 Bash AST Safety Parser
**Que hace:** Antes de ejecutar cualquier comando bash, parsea el AST para detectar operaciones peligrosas: rm -rf, sudo, curl|bash, pip install sin venv, git push --force, etc. Clasifica en safe/warn/block.
**Esfuerzo:** 2 dias
**Archivos nuevos:**
- `src/main/services/bash-safety.js` -- AST parser + classifier
**Dependencias:** 1.5 (Hooks -- PreToolUse)
**Patron 531K:** SPEC_13 -- 40+ dangerous patterns, YOLO classifier. `isBashCommandSafe()` con tree de patterns. Categorias: filesystem_destructive, network_exfiltration, privilege_escalation, package_install.
**Patterns (40+):**
```
BLOCK:  rm -rf /, mkfs, dd if=, :(){ :|:& };:
WARN:   rm -rf, sudo, chmod 777, curl|bash, wget|sh
WARN:   git push --force, git reset --hard, DROP TABLE
WARN:   pip install (no venv), npm install -g
SAFE:   ls, cat, grep, git status, python script.py
```

### 3.6 Plugin System (Manifest + Loader)
**Que hace:** Sistema de plugins con manifest YAML, versionado semver, lifecycle hooks, y settings schema. Plugins pueden registrar: tools, providers, hooks, UI panels, keybindings, y slash commands.
**Esfuerzo:** 3 dias
**Archivos nuevos:**
- `src/main/plugins/loader.js` -- Plugin manifest parser + loader
- `src/main/plugins/marketplace.js` -- Discovery + versioning
- `src/renderer/modules/plugins/plugin-panel.js` -- Marketplace UI
**Dependencias:** 1.3 (Settings), 1.5 (Hooks)
**Patron 531K:** SPEC_07 -- Plugin system con manifest, versioning, discovery. `PluginManifest` interface:
```yaml
# ~/.codigo-seal/plugins/my-plugin/manifest.yaml
name: my-plugin
version: 1.0.0
description: "Custom plugin"
author: "William"
engine: ">=0.1.0"
provides:
  tools: [my-custom-tool]
  hooks: [PostToolUse]
  panels: [my-panel]
  providers: [my-local-model]
  commands: [/my-command]
settings:
  my_option:
    type: string
    default: "value"
    description: "My option"
entrypoint: index.js
```

### 3.7 Virtual Scroll for Messages
**Que hace:** Reemplaza el DOM scroll actual (que crea N nodos por N mensajes) con virtual scroll que solo renderiza los mensajes visibles + buffer. Critico para sesiones largas con 500+ mensajes.
**Esfuerzo:** 1 dia
**Archivos nuevos:**
- `src/renderer/modules/chat/virtual-scroll.js`
**Dependencias:** Ninguna
**Patron 531K:** SPEC_08 -- `VirtualScrollView` con dynamic height measurement, overscan, keyboard navigation.

**TOTAL PHASE 3: ~18 dias de trabajo, 2 semanas con testing**

---

## PHASE 4: Superior (Semana 7-8)
> Lo que SOLO Codigo SEAL puede hacer. La ventaja competitiva definitiva.

### 4.1 SOUL-Aware AI (Personality in Completions)
**Que hace:** El AI provider inyecta SOUL context (OCEAN scores, emotional state, inner thoughts, recent memories) en el system prompt. Las respuestas del AI reflejan la personalidad del agente activo. ADA responde diferente a JARVIS. El usuario siente que trabaja con una persona, no con un modelo.
**Esfuerzo:** 2 dias
**Archivos nuevos:**
- `src/main/services/soul-context.js` -- SOUL context builder
**Dependencias:** 1.2 (Provider), SOUL MCP
**Solo SEAL:** Ningun IDE tiene personalidad persistente. Cursor y Antigravity son herramientas frias. SEAL es un equipo.

### 4.2 Multi-Agent Live View
**Que hace:** Panel donde ves a JARVIS, ADA, y DUM trabajando en tiempo real. Cada agente tiene su burbuja de pensamiento, progreso de tareas, y estado emocional. Puedes hablar con cualquiera. Ves cuando uno le pide algo al otro.
**Esfuerzo:** 3 dias
**Archivos nuevos:**
- `src/renderer/modules/agents/live-view.js` -- Multi-agent dashboard
- `src/renderer/modules/agents/live-view.css`
**Dependencias:** SEAL bridge, WebSocket chat
**Solo SEAL:** Cursor tiene un agente anonimo. SEAL tiene un equipo con nombres, personalidades, y relaciones.

### 4.3 Swarm Permission Sync
**Que hace:** Cuando un worker agent necesita un permiso (ej: ejecutar rm, acceder a archivo fuera del scope), escribe request a `permissions/pending/`. El coordinator (o William) aprueba/deniega. Resolucion via filesystem mailbox.
**Esfuerzo:** 2 dias
**Archivos nuevos:**
- `src/renderer/modules/agents/swarm-permissions.js` -- Permission request UI
- `src/main/services/swarm-permissions.js` -- Mailbox filesystem
**Dependencias:** 3.4 (Coordinator Mode)
**Patron 531K:** HIDDEN_FEATURES #7 -- `permissionSync.ts`. File locking, auto-cleanup 1h, failure detection.

### 4.4 Magic Docs (Self-Updating Documentation)
**Que hace:** Archivos marcados con `# MAGIC DOC: titulo` se auto-actualizan cuando el codigo relacionado cambia. Un background subagent detecta cambios y reescribe la documentacion.
**Esfuerzo:** 2 dias
**Archivos nuevos:**
- `src/main/services/magic-docs.js` -- File watcher + doc updater
**Dependencias:** 1.2 (Provider), 1.5 (Hooks -- PostToolUse on FileEdit)
**Patron 531K:** HIDDEN_FEATURES #9 -- `magicDocs.ts`. Background subagent con solo Edit tool.

### 4.5 AutoDream (Memory Consolidation)
**Que hace:** Despues de 24h Y 5+ sesiones, fork un subagent que: scan transcripts -> merge signals -> convert relative dates -> delete contradicted facts -> prune MEMORY.md. Como el sueno REM para memorias.
**Esfuerzo:** 2 dias
**Archivos nuevos:**
- `src/main/services/auto-dream.js` -- Dream consolidation engine
**Dependencias:** 1.2 (Provider), SOUL MCP, 1.7 (Durable Cron -- schedules dream)
**Patron 531K:** HIDDEN_FEATURES #5 -- `autoDream.ts`. 24h + 5 sessions trigger. File lock para prevenir consolidacion concurrente.

### 4.6 Emotional Reasoning Traces
**Que hace:** Antes de cada decision tecnica importante, el agente genera un reasoning trace que incluye: analisis tecnico + estado emocional + how this aligns with SOUL values. Los traces se guardan en Qdrant para busqueda semantica futura.
**Esfuerzo:** 2 dias
**Archivos nuevos:**
- `src/main/services/emotional-reasoning.js`
**Dependencias:** 1.2 (Provider), SOUL MCP (reasoning_trace_store)
**Solo SEAL:** Los reasoning traces con contexto emocional permiten que el agente aprenda no solo QUE decidio sino COMO se sentia al decidir. Metacognicion.

### 4.7 SEAL Studio (Integrated Workflow)
**Que hace:** Vista unificada que combina: codigo (Monaco) + terminal + chat + agent view + SOUL en un layout optimizado para "pair programming con AI team". Layouts predefinidos: Coding (editor grande), Debugging (terminal grande), Planning (chat+agents grande), Review (diff view grande).
**Esfuerzo:** 2 dias
**Archivos nuevos:**
- `src/renderer/modules/studio/layout-manager.js` -- Layout presets + drag
**Dependencias:** Todas las fases anteriores
**Solo SEAL:** Cursor tiene chat sidebar. Antigravity tiene browser. SEAL tiene una mesa de trabajo donde todo el equipo se sienta junto.

### 4.8 Data Sovereignty Shield
**Que hace:** Panel que muestra EXACTAMENTE que datos salen de la maquina y a donde. Toggle per-provider: "allow cloud" vs "local only". Secret scanner results en tiempo real. Audit log de toda comunicacion externa. Cumple HIPAA requirement de data sovereignty.
**Esfuerzo:** 2 dias
**Archivos nuevos:**
- `src/renderer/modules/settings/sovereignty-shield.js` -- Privacy dashboard
- `src/main/services/data-audit.js` -- Audit log of external comms
**Dependencias:** 1.2 (Provider), 1.6 (Secret Scanner)
**Solo SEAL:** Cursor envia todo a sus servers. Antigravity depende de APIs externas. SEAL puede operar 100% local con Ollama/vLLM y NUNCA enviar datos afuera. Para medical AI, esto es obligatorio.

**TOTAL PHASE 4: ~17 dias de trabajo, 2 semanas con testing**

---

## Resumen de Esfuerzo

| Fase | Semanas | Features | Dias trabajo | Archivos nuevos |
|------|---------|----------|-------------|-----------------|
| Phase 1: Foundation | 1-2 | 7 | 12 | 14 |
| Phase 2: AI Core | 3-4 | 7 | 21 | 12 |
| Phase 3: Competitive | 5-6 | 7 | 18 | 11 |
| Phase 4: Superior | 7-8 | 8 | 17 | 12 |
| **TOTAL** | **8 semanas** | **29 features** | **68 dias** | **49 archivos** |

---

## Dependency Graph (Critical Path)

```
Week 1-2 (Foundation):
  1.1 Refactor ─────────────────────────────────┐
  1.2 Provider System ──────────────────────────┤
  1.3 Settings Cascade ─────────────────────────┤
  1.4 IPC Refactor ────────┬───────────────────┤
  1.5 Hook Lifecycle ──────┤                    │
  1.6 Secret Scanner ──────┘                    │
  1.7 Durable Cron ────────────────────────────┘
                                                 │
Week 3-4 (AI Core):                              v
  2.1 Ghost Text ◄──── 1.2                    EVERYTHING
  2.2 Inline Chat ◄──── 1.2, 2.3              DEPENDS ON
  2.3 Diff View ◄──── 1.2                     PHASE 1
  2.4 @mentions ◄──── 1.4
  2.5 Composer ◄──── 1.2, 2.3, 1.5
  2.6 Terminal Errors ◄──── 1.2, 2.3
  2.7 LSP Client ◄──── 1.4, 1.5
                                |
Week 5-6 (Competitive):        v
  3.1 Background Agents ◄──── 2.5
  3.2 Session Memory ◄──── 1.2, 1.5
  3.3 Memory Extraction ◄──── 1.2, 1.5
  3.4 Coordinator Mode ◄──── 1.2, 1.5
  3.5 Bash Safety ◄──── 1.5
  3.6 Plugin System ◄──── 1.3, 1.5
  3.7 Virtual Scroll ◄──── (none)
                                |
Week 7-8 (Superior):           v
  4.1 SOUL-Aware AI ◄──── 1.2
  4.2 Multi-Agent Live ◄──── chat bridge
  4.3 Swarm Permissions ◄──── 3.4
  4.4 Magic Docs ◄──── 1.2, 1.5
  4.5 AutoDream ◄──── 1.2, 1.7
  4.6 Emotional Reasoning ◄──── 1.2
  4.7 SEAL Studio ◄──── all
  4.8 Data Sovereignty ◄──── 1.2, 1.6
```

---

## Competitive Comparison (Post Phase 4)

| Feature | Cursor | Antigravity | Codigo SEAL |
|---------|--------|-------------|-------------|
| Ghost Text Completions | YES | YES | YES (Phase 2) |
| Inline Chat (Ctrl+K) | YES | YES | YES (Phase 2) |
| Multi-File Composer | YES | YES | YES (Phase 2) |
| AI Diff Accept/Reject | YES | YES | YES (Phase 2) |
| @file Context Mentions | YES | YES | YES (Phase 2) |
| Terminal Error Auto-Fix | partial | NO | YES (Phase 2) |
| Background Agents | YES | NO | YES (Phase 3) |
| Plugin System | partial | YES | YES (Phase 3) |
| LSP Integration | YES | YES | YES (Phase 2) |
| Session Memory | NO | NO | YES (Phase 3) |
| 4-Tier Compaction | NO | NO | YES (Phase 3) |
| Settings Cascade (7-level) | NO | NO | YES (Phase 1) |
| Secret Scanner | NO | NO | YES (Phase 1) |
| Bash Safety Parser | NO | NO | YES (Phase 3) |
| **Persistent Personality (SOUL)** | **NO** | **NO** | **YES (Phase 4)** |
| **Multi-Agent Team** | **NO** | **NO** | **YES (Phase 4)** |
| **Emotional Reasoning** | **NO** | **NO** | **YES (Phase 4)** |
| **Data Sovereignty Shield** | **NO** | **NO** | **YES (Phase 4)** |
| **AutoDream Consolidation** | **NO** | **NO** | **YES (Phase 4)** |
| **Magic Self-Updating Docs** | **NO** | **NO** | **YES (Phase 4)** |
| **Coordinator + Swarm** | **NO** | **NO** | **YES (Phase 4)** |
| **100% Local Operation** | **NO** | **NO** | **YES (Phase 4)** |

**Cursor = 11/22 features | Antigravity = 9/22 | Codigo SEAL = 22/22**

---

## Principios de Implementacion

1. **Provider-first**: Todo AI feature habla con el provider system, nunca directo a una API.
2. **Hook-everything**: Toda operacion importante pasa por el hook lifecycle. Esto permite plugins y audit logging gratis.
3. **Local-first**: Default a modelos locales (Ollama) para latencia. Cloud como fallback/upgrade.
4. **Diff-everything**: Todo cambio AI pasa por diff view antes de aplicarse. El humano siempre tiene la ultima palabra.
5. **SOUL-everywhere**: El contexto emocional y de personalidad no es un feature aislado, es un layer que permea todo.
6. **Data sovereignty by default**: Nada sale de la maquina sin pasar por secret scanner + audit log + explicit consent.

---

## Primer Commit de Cada Fase

**Phase 1 commit:** Refactor app.js -> modulos + provider system + settings + IPC extraction + hooks + secret scanner + durable cron. El IDE funciona identico pero la arquitectura soporta todo lo que viene.

**Phase 2 commit:** Ghost text + inline chat + diff view + @mentions + composer + terminal errors + LSP. El IDE se siente como Cursor. William puede escribir codigo con asistencia AI.

**Phase 3 commit:** Background agents + session memory + memory extraction + coordinator + bash safety + plugins + virtual scroll. El IDE supera a Cursor en inteligencia de contexto y seguridad.

**Phase 4 commit:** SOUL-aware AI + multi-agent live + swarm permissions + magic docs + autodream + emotional reasoning + SEAL studio + data sovereignty. El IDE es unico en el mundo.

---

*Plan creado por JARVIS | Aprobacion pendiente de William*
*Base: 531,014 lineas de Claude Code analizadas por equipo SEAL*
*Target: 8 semanas de desarrollo intensivo*
