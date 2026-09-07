# SPEC_09_COMMANDS — Extracted Analysis

I now have a comprehensive understanding of all 207 files. Here is the complete technical report.

---

# Reporte Tecnico: Claude Code Slash Commands (207 archivos)

## Estructura de Directorios

**75 directorios** de comandos + **14 archivos standalone** en la raiz.

---

## CATEGORIA 1: CORE / SESION

### /clear
- **Que hace:** Limpia la conversacion y/o caches internos
- **Archivos:** clear.ts, conversation.ts, caches.ts, index.ts
- **Subcomandos:** `conversation` (limpia mensajes), `caches` (limpia caches de archivos/permisos)
- **Feature:** Soporta DISABLE_COMPACT env var

### /compact
- **Que hace:** Compacta la conversacion reduciendo tokens via summarization
- **Archivos:** compact.ts, index.ts
- **Parametros:** Args opcionales para instrucciones de compactacion
- **Features:** Integra microcompact, session memory compaction, reactive compact (feature flag), pre-compact hooks. Usa `bun:bundle` feature flags. Merge de hook instructions en el summary.

### /exit
- **Que hace:** Sale de Claude Code
- **Tipo:** local-jsx

### /resume
- **Que hace:** Reanuda una sesion anterior
- **Archivos:** resume.tsx, index.ts
- **Feature:** Selector interactivo de sesiones previas

### /session
- **Que hace:** Muestra info de la sesion remota actual (URL, QR code)
- **Gate:** Solo habilitado en modo remoto (`getIsRemoteMode()`)
- **Hidden:** Se oculta cuando no esta en modo remoto

### /rename
- **Que hace:** Renombra la sesion actual
- **Archivos:** rename.ts, generateSessionName.ts
- **Feature:** Si no se pasa argumento, genera nombre automatico via Haiku (modelo ligero) con formato kebab-case. Bloquea rename si la sesion es un "teammate" (swarm).

### /rewind
- **Que hace:** Abre un selector de mensajes para retroceder en la conversacion
- **Tipo:** local con `openMessageSelector()`

### /branch
- **Que hace:** Crea un branch de la conversacion (fork del historial)
- **Archivos:** branch.ts, index.ts

---

## CATEGORIA 2: MODELO Y RENDIMIENTO

### /model
- **Que hace:** Cambia el modelo de inferencia de la sesion
- **Archivos:** model.tsx, index.ts
- **Features:** Model picker interactivo, validacion de modelo, aliases (MODEL_ALIASES), chequeo de acceso a 1M context (Opus/Sonnet), Fast Mode integration, billing extra usage check

### /effort
- **Que hace:** Ajusta el nivel de esfuerzo del modelo (min/low/medium/high/max)
- **Archivos:** effort.tsx, index.ts
- **Parametros:** Valor de esfuerzo o "help"
- **Feature:** Persiste en userSettings. Detecta conflicto con env var CLAUDE_CODE_EFFORT_LEVEL.

### /fast
- **Que hace:** Toggle de Fast Mode (modelo mas rapido, menor costo)
- **Availability:** claude-ai, console
- **Gate:** `isFastModeEnabled()` feature flag
- **Feature:** Cambia modelo automaticamente si el actual no soporta fast mode. Muestra pricing comparativo. Cooldown system.

### /advisor
- **Que hace:** Configura un modelo advisor secundario (ej: Opus como advisor de Sonnet)
- **Parametros:** `<model>` para set, `unset`/`off` para desactivar
- **Gate:** `canUserConfigureAdvisor()` - requiere que el usuario pueda configurarlo
- **Feature:** Valida que el modelo base soporte advisors. Persiste en userSettings.

### /brief
- **Que hace:** Toggle del modo brief (respuestas mas cortas)
- **Gate:** Feature flag `tengu_kairos_brief` via `isBriefEnabled()`

---

## CATEGORIA 3: CONTEXT Y ARCHIVOS

### /context
- **Que hace:** Visualiza el uso actual del contexto como grid coloreado
- **Archivos:** context.tsx, context-noninteractive.ts, index.ts
- **Feature:** Dos implementaciones: interactiva (JSX con grid visual) y non-interactive (texto plano). Aplica las mismas transformaciones que query.ts (projectView, microcompact) para mostrar lo que el modelo realmente ve.

### /add-dir
- **Que hace:** Agrega un directorio de trabajo adicional
- **Archivos:** add-dir.tsx, validation.ts, index.ts
- **Parametros:** `<path>`
- **Validacion:** Verifica existencia, que sea directorio, que no sea subdirectorio de uno existente

### /files
- **Que hace:** Lista archivos actualmente en contexto
- **Gate:** **ANT-ONLY** (`USER_TYPE === 'ant'`)
- **Feature:** Muestra paths relativos al cwd de los archivos cacheados en readFileState

### /diff
- **Que hace:** Muestra diff de cambios realizados en la sesion
- **Tipo:** local-jsx, renderiza DiffDialog

---

## CATEGORIA 4: CONFIG Y SETTINGS

### /config
- **Que hace:** UI interactiva para configurar Claude Code
- **Archivos:** config.tsx, index.ts
- **Tipo:** local-jsx

### /permissions
- **Que hace:** Gestiona permisos de herramientas
- **Archivos:** permissions.tsx, index.ts

### /hooks
- **Que hace:** Configura hooks (pre/post tool execution)
- **Tipo:** local-jsx, renderiza HooksConfigMenu con lista de tools disponibles

### /sandbox-toggle (a.k.a. /sandbox)
- **Que hace:** Configura sandboxing de comandos
- **Archivos:** sandbox-toggle.tsx, index.ts
- **Subcomandos:** Sin args = menu interactivo, `exclude <pattern>` = excluir comando
- **Gate:** Solo macOS, Linux, WSL2 (no WSL1). Verifica dependencias, platform enablelist, policy locks.
- **Hidden:** Se oculta en plataformas no soportadas

### /privacy-settings
- **Que hace:** Configura opciones de privacidad
- **Gate:** Feature flag dinamico

### /theme
- **Que hace:** Cambia el tema visual (light/dark/etc)
- **Archivos:** theme.tsx, index.ts

### /color
- **Que hace:** Configura colores del terminal
- **Archivos:** color.ts, index.ts

### /keybindings
- **Que hace:** Personaliza atajos de teclado
- **Gate:** `isKeybindingCustomizationEnabled()` feature flag

### /output-style
- **Que hace:** **DEPRECADO** - redirige a /config
- **Hidden:** true

### /vim
- **Que hace:** Toggle del modo vim para edicion
- **Archivos:** vim.ts, index.ts

---

## CATEGORIA 5: MCP (Model Context Protocol)

### /mcp
- **Que hace:** Gestiona servidores MCP (add/enable/disable/reconnect)
- **Archivos:** mcp.tsx, addCommand.ts, xaaIdpCommand.ts, index.ts
- **Subcomandos interactivos:**
  - Sin args: MCPSettings UI (ant redirige a plugins)
  - `no-redirect`: Fuerza MCPSettings sin redireccion
  - `reconnect <name>`: Reconecta un servidor
  - `enable/disable [name|all]`: Toggle de servidores
- **CLI (no interactivo):** `mcp add <name> <url> [args...]` con opciones:
  - `--scope` (local/user/project)
  - `--transport` (stdio/sse/http)
  - `--env` (variables de entorno)
  - `--header` (headers HTTP)
- **XAA IdP:** Subcomando `mcp xaa` para gestionar conexion IdP (SEP-990):
  - `xaa setup --issuer <url> --client-id <id>` - configura IdP
  - Valida URL del issuer, permite http solo para loopback
  - Guarda client secret en keychain

---

## CATEGORIA 6: PLUGIN SYSTEM

### /plugin (aliases: /plugins, /marketplace)
- **Que hace:** Sistema completo de gestion de plugins
- **Archivos:** 14 archivos (el mas complejo de todos los comandos)
  - plugin.tsx, index.tsx, parseArgs.ts
  - BrowseMarketplace.tsx, AddMarketplace.tsx, ManageMarketplaces.tsx
  - DiscoverPlugins.tsx, ManagePlugins.tsx
  - PluginSettings.tsx, PluginOptionsDialog.tsx, PluginOptionsFlow.tsx
  - PluginTrustWarning.tsx, PluginErrors.tsx, ValidatePlugin.tsx
  - UnifiedInstalledCell.tsx, usePagination.ts, pluginDetailsHelpers.tsx
- **Features:** Marketplace browsing, plugin install/uninstall/enable/disable/update, trust warnings, pagination, unified installed view
- **Flag `immediate: true`** - se ejecuta sin esperar input adicional

### /reload-plugins
- **Que hace:** Recarga plugins instalados
- **Archivos:** reload-plugins.ts, index.ts

### /skills
- **Que hace:** Menu de skills disponibles
- **Tipo:** local-jsx, renderiza SkillsMenu

---

## CATEGORIA 7: GIT Y GITHUB

### /commit
- **Que hace:** Prompt command que genera un commit
- **Tipo:** prompt
- **Source:** builtin

### /commit-push-pr
- **Que hace:** Commit + push + create PR en un flujo

### /review
- **Que hace:** Review local de PR via `gh pr diff`
- **Tipo:** prompt con instrucciones para code review
- **Feature:** Prompt detallado para code quality, conventions, performance, security, test coverage

### /ultrareview
- **Que hace:** Review remota profunda de bugs (~10-20 min)
- **Gate:** `isUltrareviewEnabled()` via GrowthBook config `tengu_review_bughunter_config`
- **Feature:** Lanza sesion CCR (Claude Code on the web). Overage gate con 3 estados: not-enabled, low-balance ($10 min), needs-confirm. Billing como Extra Usage.

### /pr-comments (pr_comments/)
- **Que hace:** Obtiene comentarios de un PR de GitHub
- **Feature:** **Migrado a plugin** via `createMovedToPluginCommand`. Ant users ven instruccion de instalar plugin; external users obtienen prompt inline con instrucciones de `gh api`.

### /security-review
- **Que hace:** Review de seguridad del branch actual
- **Tipo:** prompt con instrucciones detalladas
- **Allowed tools:** Bash(git diff/status/log/show/remote), Read, Glob, Grep, LS, Task
- **Categorias de seguridad:** SQL injection, command injection, XXE, template injection, path traversal, etc. Umbral: >80% confianza de exploitabilidad.

### /init
- **Que hace:** Inicializa CLAUDE.md y configuracion del proyecto
- **Tipo:** prompt

### /init-verifiers
- **Que hace:** Crea skills de verificacion automatica para el proyecto
- **Tipo:** prompt
- **Feature:** Auto-detecta stack (web/CLI/API), sugiere Playwright/Tmux/HTTP verifiers, instala dependencias. Excluye unit tests y typechecking.

---

## CATEGORIA 8: REMOTE / BRIDGE / CLOUD

### /remote-control (alias: /rc) [bridge/]
- **Que hace:** Conecta terminal para sesiones de control remoto
- **Gate:** Feature flag BRIDGE_MODE + `isBridgeEnabled()`
- **Archivos:** bridge.tsx, index.ts
- **Feature:** QR code para URL de sesion, toggle connect/disconnect, prerequisite checks

### /bridge-kick
- **Que hace:** **ANT-ONLY** - Inyecta fallos en el bridge para testing de recovery
- **Gate:** `USER_TYPE === 'ant'`
- **Subcomandos detallados:**
  - `close <code>` - simula ws_closed con codigo especifico
  - `poll <status> [type]` - simula error en polling (404, 401, transient)
  - `register fail [N]` - N registros fallan transitoriamente
  - `register fatal` - registro 403 terminal
  - `reconnect-session fail` - POST /bridge/reconnect falla
  - `heartbeat <status>` - heartbeat falla
  - `reconnect` - fuerza doReconnect
  - `status` - imprime estado del bridge
- **Feature:** Permite secuencias compuestas de fallos para reproducir escenarios de BQ data (147K/week states)

### /remote-env
- **Que hace:** Dialogo de entorno remoto
- **Gate:** Solo en modo Claude AI con entornos personalizados habilitados

### /remote-setup
- **Que hace:** Setup de entorno remoto (importar GitHub token, crear env)
- **Availability:** claude-ai only
- **Feature:** Detecta `gh` CLI, importa token, crea default environment

### /desktop (alias: /app)
- **Que hace:** Continua la sesion actual en Claude Desktop
- **Availability:** claude-ai only
- **Gate:** Solo macOS y Windows x64

### /mobile (aliases: /ios, /android)
- **Que hace:** Muestra QR code para descargar la app movil
- **Feature:** Toggle entre iOS/Android, genera QR codes para App Store y Play Store

---

## CATEGORIA 9: BILLING Y USAGE

### /usage
- **Que hace:** Muestra estadisticas de uso
- **Availability:** claude-ai only

### /cost
- **Que hace:** Muestra costo de la sesion actual
- **Hidden:** Condicionalmente oculto

### /extra-usage
- **Que hace:** Habilita/gestiona Extra Usage (billing adicional)
- **Archivos:** extra-usage.tsx, extra-usage-core.ts, extra-usage-noninteractive.ts, index.ts
- **Gate:** `isExtraUsageAllowed()`
- **Feature:** Dos versiones (interactiva y non-interactive). Maneja Team/Enterprise admin requests. Invalidates overage credit grant cache.

### /rate-limit-options
- **Que hace:** Opciones cuando se alcanza rate limit
- **Hidden:** true (solo uso interno)
- **Feature:** Ofrece Extra Usage o upgrade segun contexto

### /passes
- **Que hace:** Comparte semanas gratis de Claude Code con amigos (referral)
- **Hidden:** Condicionalmente (requiere eligibilidad y cache)
- **Feature:** Tracking de first visit, reward para referrer

### /upgrade
- **Que hace:** Muestra opciones de upgrade de plan
- **Availability:** claude-ai only

---

## CATEGORIA 10: AGENT SYSTEM

### /agents
- **Que hace:** Menu de agentes disponibles
- **Tipo:** local-jsx, renderiza AgentsMenu con lista de tools

### /tasks (alias: /bashes)
- **Que hace:** Lista y gestiona tareas en background
- **Tipo:** local-jsx, renderiza BackgroundTasksDialog

### /plan
- **Que hace:** Gestiona planes de ejecucion
- **Archivos:** plan.tsx, index.ts
- **Subcomandos:** Sin args = muestra plan actual, `open` = edita en editor externo
- **Feature:** Plan mode con contexto de permisos especifico

### /ultraplan
- **Que hace:** Planificacion avanzada multi-agente via CCR (Claude Code on the web)
- **Gate:** `isEnabled: () => "external" === 'ant'` - **SIEMPRE DESHABILITADO para external users** (compile-time guard)
- **Features:**
  - Timeout 30 min
  - Usa modelo Opus 4.6 (configurable via GrowthBook)
  - Lanza sesion remota CCR con polling para aprobacion
  - Fases: running, needs_input
  - Soporta execution_target: "remote" o local
  - Prompt inyectado desde archivo .txt bundled
  - Dev override: ULTRAPLAN_PROMPT_FILE env var (ant-only)

---

## CATEGORIA 11: DEBUG / DIAGNOSTICS (Mayoria ANT-ONLY)

### /doctor
- **Que hace:** Diagnostico del sistema
- **Tipo:** local-jsx, renderiza pantalla Doctor
- **Gate:** `DISABLE_DOCTOR_COMMAND` env var

### /heapdump
- **Que hace:** Genera heap dump para diagnostico de memoria
- **Hidden:** true (siempre oculto)

### /version
- **Que hace:** Muestra version y build time
- **Gate:** **ANT-ONLY** (`USER_TYPE === 'ant'`)
- **Feature:** Usa MACRO.VERSION y MACRO.BUILD_TIME (compile-time defines)

### /stats
- **Que hace:** Muestra estadisticas de la sesion
- **Tipo:** local-jsx, renderiza componente Stats

### /status
- **Que hace:** Muestra estado general del sistema
- **Archivos:** status.tsx, index.ts

### /tag
- **Que hace:** Etiqueta la sesion actual con un tag
- **Gate:** **ANT-ONLY** (`USER_TYPE === 'ant'`)
- **Feature:** Confirma antes de remover tag existente. Sanitiza Unicode. Persiste con `saveTag()`.

---

## CATEGORIA 12: STUBS (Compilados como deshabilitados, implementacion removida)

Todos estos exportan `{ isEnabled: () => false, isHidden: true, name: 'stub' }`:

| Comando | Proposito inferido |
|---|---|
| **/ant-trace** | Tracing interno de Anthropic |
| **/autofix-pr** | Auto-fix automatico de PRs |
| **/backfill-sessions** | Backfill de sesiones historicas |
| **/break-cache** | Romper cache de prompts (testing) |
| **/bughunter** | Busqueda automatizada de bugs |
| **/ctx_viz** | Visualizacion avanzada de contexto |
| **/debug-tool-call** | Debug de tool calls individuales |
| **/env** | Variables de entorno |
| **/good-claude** | Feedback positivo (Easter egg?) |
| **/issue** | Gestion de issues |
| **/mock-limits** | Simular limites de rate (testing) |
| **/oauth-refresh** | Refresh de OAuth tokens |
| **/onboarding** | Onboarding flow |
| **/perf-issue** | Reportar problemas de performance |
| **/share** | Compartir sesion |
| **/summary** | Resumen de sesion |
| **/teleport** | Teleport a sesion remota |

Nota especial: **/reset-limits** tiene un stub ligeramente diferente: `const stub = { isEnabled: () => false, isHidden: true, name: 'stub' }` - mismo efecto.

---

## CATEGORIA 13: COMUNICACION Y EXPORT

### /copy
- **Que hace:** Copia respuesta del modelo al clipboard
- **Archivos:** copy.tsx, index.ts
- **Feature:** Extrae code blocks del markdown, permite seleccionar que copiar, exporta a archivo temporal en /tmp/claude/

### /export
- **Que hace:** Exporta la conversacion
- **Archivos:** export.tsx, index.ts
- **Feature:** Genera filename sanitizado con timestamp, renderiza mensajes a plain text

### /feedback
- **Que hace:** Envia feedback a Anthropic
- **Archivos:** feedback.tsx, index.ts
- **Parametros:** Descripcion inicial opcional
- **Gate:** Feature flag dinamico

---

## CATEGORIA 14: AUTH

### /login
- **Que hace:** Inicia sesion en Claude AI
- **Gate:** `DISABLE_LOGIN_COMMAND` env var

### /logout
- **Que hace:** Cierra sesion
- **Gate:** `DISABLE_LOGOUT_COMMAND` env var

---

## CATEGORIA 15: INSTALL / SETUP

### /install
- **Que hace:** Instala/actualiza Claude Code binario nativo
- **Archivos:** install.tsx (standalone)
- **Feature:** Cleanup de instalaciones npm previas, cleanup de shell aliases, instala en `~/.local/bin/claude`

### /install-github-app
- **Que hace:** Setup de Claude GitHub Actions para un repo
- **Archivos:** 14 archivos con flujo multi-step (OAuth, choose repo, create workflow, set secret)
- **Availability:** claude-ai, console
- **Feature:** Soporta API key y OAuth token. Crea workflow file via GitHub API. Detecta workflow existente. Maneja code review plugin workflow.

### /install-slack-app
- **Que hace:** Setup de Claude para Slack
- **Availability:** claude-ai only

### /terminalSetup
- **Que hace:** Configura integracion con terminal (shell integration)
- **Archivos:** terminalSetup.tsx, index.ts
- **Hidden:** Condicionalmente (se oculta si el terminal ya tiene soporte nativo CSIU)

---

## CATEGORIA 16: FEATURES EXPERIMENTALES / ESPECIALES

### /thinkback
- **Que hace:** Genera "Year in Review" animado (retrospectiva de uso)
- **Archivos:** thinkback.tsx, index.ts
- **Gate:** Feature flag + marketplace plugin requerido
- **Feature:** Instala plugin desde marketplace si no existe. Genera animacion con data del usuario. Usa node subprocess para player.js.

### /thinkback-play
- **Que hace:** Reproduce la animacion de thinkback directamente
- **Gate:** Feature flag
- **Hidden:** true

### /voice
- **Que hace:** Toggle de modo voz (STT)
- **Availability:** claude-ai only
- **Gate:** `isVoiceGrowthBookEnabled()` feature flag
- **Feature:** Pre-flight checks: microphone access, voice stream API. Configura idioma STT. Hint de idioma con max 2 shows.

### /btw
- **Que hace:** "By the way" - pregunta lateral sin interrumpir flujo principal
- **Archivos:** btw.tsx, index.ts
- **Feature:** Ejecuta `runSideQuestion()` con el contexto completo. Renderiza respuesta en scroll box con spinners. Usa el system prompt completo pero como pregunta separada.

### /chrome
- **Que hace:** Gestiona Claude in Chrome extension
- **Availability:** claude-ai only
- **Feature:** Menu para instalar extension, reconectar, gestionar permisos, toggle default. Detecta si extension esta instalada via MCP clients. QR codes.

### /insights
- **Que hace:** Analisis narrativo de uso historico de Claude Code
- **Feature:** Recolecta datos de todas las sesiones (homespace data). Usa **Opus** para extraccion de facetas y generacion de narrativa. Para ant users, detecta Coder workspaces remotos.

### /stickers
- **Que hace:** Abre pagina de stickers de Claude Code en browser
- **URL:** stickermule.com/claudecode

### /statusline
- **Que hace:** Renderiza statusline del terminal (archivo standalone)

---

## CATEGORIA 17: INFRAESTRUCTURA DE COMANDOS

### /help
- **Que hace:** Muestra ayuda general
- **Archivos:** help.tsx, index.ts

### /release-notes
- **Que hace:** Muestra notas de la version actual
- **Archivos:** release-notes.ts, index.ts

### createMovedToPluginCommand.ts
- **Que hace:** Factory function para crear comandos migrados a plugins
- **Feature:** Para ant users, genera prompt que instruye instalar plugin del marketplace. Para external users, usa `getPromptWhileMarketplaceIsPrivate()` con prompt inline.

---

## RESUMEN DE ACCESO / GATES

### Comandos ANT-ONLY (Anthropic internal)
1. `/bridge-kick` - testing de bridge recovery
2. `/files` - listar archivos en contexto
3. `/tag` - etiquetar sesiones
4. `/version` - mostrar version+build

### Comandos COMPILE-TIME DISABLED para external
1. `/ultraplan` - guard `"external" === 'ant'` siempre false

### Comandos con Feature Flags (GrowthBook)
- `/fast` - `isFastModeEnabled()`
- `/brief` - `isBriefEnabled()`
- `/voice` - `isVoiceGrowthBookEnabled()`
- `/ultrareview` - `tengu_review_bughunter_config`
- `/keybindings` - `isKeybindingCustomizationEnabled()`
- `/thinkback`, `/thinkback-play` - feature flag + plugin

### 17 Stubs (implementacion removida)
ant-trace, autofix-pr, backfill-sessions, break-cache, bughunter, ctx_viz, debug-tool-call, env, good-claude, issue, mock-limits, oauth-refresh, onboarding, perf-issue, reset-limits, share, summary, teleport

### Availability Restrictions
- `claude-ai` only: chrome, desktop, install-slack-app, mobile, remote-setup, upgrade, usage, voice
- `claude-ai + console`: fast, install-github-app

---

## TIPOS DE COMANDO (Command System)

1. **`local`** - Funcion sincrona, retorna texto
2. **`local-jsx`** - Componente React/Ink, renderiza UI interactiva
3. **`prompt`** - Genera prompt que se inyecta en la conversacion

### Propiedades del Command:
- `name` - nombre del slash command
- `aliases` - nombres alternativos
- `description` - descripcion para help
- `argumentHint` - hint de argumentos
- `availability` - donde esta disponible
- `isEnabled()` - gate dinamico
- `isHidden` - getter para ocultar de help
- `immediate` - ejecutar sin esperar input
- `supportsNonInteractive` - funciona en modo SDK/headless
- `load()` - dynamic import del modulo real (lazy loading)
- `source` - 'builtin' para prompts
- `contentLength` - tamano esperado del prompt
- `progressMessage` - mensaje durante ejecucion

---

## HALLAZGOS CLAVE PARA CLEAN-ROOM SPEC

1. **Lazy loading universal:** Todos los comandos usan `load: () => import('./module.js')` - zero startup cost.

2. **React/Ink UI:** Los comandos interactivos usan React con Ink para terminal. React Compiler runtime (`_c` function) esta presente en todos los JSX compilados.

3. **Feature flags via GrowthBook:** Sistema centralizado `getFeatureValue_CACHED_MAY_BE_STALE()` para gates en runtime.

4. **Build-time defines:** `process.env.USER_TYPE` se resuelve en build time (`"external"` hardcoded), no en runtime. Esto hace que guards como `"external" === 'ant'` sean compile-time DCE.

5. **`bun:bundle` feature flags:** Usados para conditional compilation (BRIDGE_MODE, CONTEXT_COLLAPSE, REACTIVE_COMPACT).

6. **Analytics pervasivo:** `logEvent('tengu_*', ...)` en practicamente todos los comandos. Namespace `tengu_` es el identificador interno del producto.

7. **Dual non-interactive support:** Algunos comandos (context, extra-usage) tienen implementaciones separadas para modo interactivo y SDK/headless.

8. **Plugin migration pattern:** `createMovedToPluginCommand()` es el patron para migrar funcionalidad built-in a plugins del marketplace.

9. **CCR (Claude Code on the web):** ultraplan y ultrareview usan sesiones remotas CCR con polling, overage gates, y timeout de 30min.

10. **MACRO system:** VERSION y BUILD_TIME son compile-time macros inyectadas por el bundler.

---

