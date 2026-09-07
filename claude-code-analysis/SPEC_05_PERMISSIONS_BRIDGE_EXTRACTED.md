# SPEC_05_PERMISSIONS_BRIDGE — Extracted Analysis

I now have comprehensive coverage. Let me write the analysis.

---

# Analisis Tecnico: Sistemas de Permisos y Bridge de Claude Code

## 1. SISTEMA DE PERMISOS

### 1.1 Arquitectura General

El sistema de permisos es una pipeline multi-etapa que evalua cada invocacion de herramienta (tool call) del modelo y decide: **allow**, **deny**, o **ask** (pedir aprobacion al usuario). La funcion central es `hasPermissionsToUseTool` en `/home/dadito/IA/proyecto-seal/claude-code-analysis/full_src/utils/permissions/permissions.ts`.

### 1.2 Modos de Permiso (PermissionMode)

Definidos en `PermissionMode.ts`, existen estos modos:

| Modo | Comportamiento |
|---|---|
| `default` | Pide aprobacion para operaciones peligrosas |
| `plan` | Solo lectura, no ejecuta cambios |
| `acceptEdits` | Auto-aprueba ediciones de archivos dentro del working directory |
| `bypassPermissions` | Aprueba todo (YOLO mode), excepto safety checks inmutables |
| `dontAsk` | Convierte todo `ask` en `deny` automatico -- para agentes sin UI |
| `auto` | Usa un clasificador AI (ant-only feature gate `TRANSCRIPT_CLASSIFIER`) para decidir allow/deny sin prompt |
| `bubble` | Interno Anthropic |

Los modos se ciclan con Shift+Tab en el orden: default -> acceptEdits -> plan -> bypassPermissions -> auto -> default. Los usuarios Anthropic internos (ant) saltan directamente de default a bypassPermissions/auto.

### 1.3 Pipeline de Decision (hasPermissionsToUseToolInner)

La evaluacion sigue este orden estricto de pasos:

**Paso 1a -- Deny rules de herramienta completa.** Si existe una regla deny para la herramienta entera (ej: `deny: ["Bash"]`), se deniega inmediatamente. No hay override posible.

**Paso 1b -- Ask rules de herramienta completa.** Si existe una regla ask global, se pide aprobacion. Excepcion: si el sandbox esta habilitado con `autoAllowBashIfSandboxed`, los comandos sandboxed pasan sin prompt.

**Paso 1c -- Tool-specific checkPermissions.** Cada herramienta implementa su propio `checkPermissions()` que evalua el input concreto. Por ejemplo, BashTool evalua subcomandos individuales contra reglas de prefijo/wildcard. Retorna `passthrough` (sin opinion), `allow`, `deny`, o `ask`.

**Paso 1d -- Tool denied.** Si la herramienta misma deniega (subcomando en deny list), se respeta.

**Paso 1e -- requiresUserInteraction.** Si la herramienta requiere interaccion del usuario (ej: AskUserQuestion), siempre pide aprobacion incluso en bypass mode.

**Paso 1f -- Content-specific ask rules.** Reglas ask con contenido especifico (ej: `Bash(npm publish:*)`) tienen precedencia sobre bypass mode. Decision de diseno: el usuario configuro estas reglas explicitamente, asi que bypass no las sobreescribe.

**Paso 1g -- Safety checks (INMUTABLE).** Archivos en `.git/`, `.claude/`, `.vscode/`, configs de shell (`.bashrc`, `.zshrc`), y archivos de configuracion de Claude. Estos NUNCA se auto-aprueban, ni siquiera en `bypassPermissions`. El sistema llama `checkPathSafetyForAutoEdit` que retorna `{type: 'safetyCheck'}`.

**Paso 2a -- Bypass mode.** Si el modo es `bypassPermissions` (o `plan` con bypass disponible), se aprueba todo lo que sobrevivio los pasos 1a-1g.

**Paso 2b -- Allow rules de herramienta completa.** Si la herramienta esta en la lista always-allow, se aprueba.

**Paso 3 -- Passthrough to ask.** Si la herramienta retorno `passthrough` (sin opinion), se convierte a `ask`.

### 1.4 Sistema de Reglas

Las reglas se cargan desde multiples fuentes con esta jerarquia (definida en `permissionsLoader.ts`):

1. `policySettings` -- Politicas enterprise gestionadas
2. `projectSettings` -- `.claude/settings.json` (compartido via git)
3. `localSettings` -- `.claude/settings.local.json` (gitignored)
4. `userSettings` -- `~/.claude/settings.json` (global del usuario)
5. `flagSettings` -- Via `--settings` CLI flag
6. `cliArg` -- Via `--allowed-tools` / `--denied-tools`
7. `session` -- En memoria, temporales
8. `command` -- Frontmatter de slash commands

Hay un modo enterprise `allowManagedPermissionRulesOnly` que cuando esta activo, SOLO respeta reglas de `policySettings`, ignorando todas las demas fuentes. Esto permite a organizaciones forzar politicas estrictas.

Las reglas se parsean con formato `ToolName(ruleContent)`:
- `Bash` -- Regla de herramienta completa
- `Bash(npm install:*)` -- Prefijo: cualquier comando que empiece con `npm install`
- `Bash(git * --force)` -- Wildcard: patron glob
- `Bash(npm publish)` -- Match exacto

El parser en `permissionRuleParser.ts` maneja parentesis escapados (`\(`, `\)`) para contenido que contiene parentesis literales.

### 1.5 Auto Mode (Clasificador AI)

Guardado detras del feature gate `TRANSCRIPT_CLASSIFIER`, el auto mode usa un segundo modelo AI como clasificador de seguridad. Implementado en `yoloClassifier.ts`:

**Arquitectura del clasificador:**
- Un sistema de 2 etapas (XML classifier): Stage 1 es rapido (max_tokens=64) para decision inmediata. Si bloquea, Stage 2 corre con razonamiento completo.
- El transcript compacto se construye en `buildTranscriptEntries`: incluye mensajes del usuario y tool_use del asistente. **Importante: el texto del asistente se excluye deliberadamente** para evitar que el modelo influencie al clasificador.
- CLAUDE.md del usuario se incluye como contexto del clasificador (envuelto en tags `<user_claude_md>`) para que el clasificador entienda la intencion del usuario.
- Las reglas del clasificador son configurables via `settings.autoMode` con tres secciones: `allow`, `soft_deny`, `environment`.

**Fast paths antes del clasificador:**
1. Si `acceptEdits` mode lo aprobaria, se salta el clasificador (ahorra API call). Excepcion: Agent tool y REPL tool no usan este fast path.
2. Safe-tool allowlist (`classifierDecision.ts`): herramientas de solo lectura (Read, Grep, Glob, LSP, TodoWrite, TaskCreate, etc.) se aprueban sin clasificador.

**Denial tracking (`denialTracking.ts`):**
- Max 3 denials consecutivos -> fallback a prompting manual
- Max 20 denials totales en la sesion -> reset y prompt manual
- En modo headless (sin UI), exceder limites = abort

**Iron gate (fail-closed):**
- Si el clasificador no esta disponible (API error), comportamiento controlado por feature flag `tengu_iron_gate_closed`
- Con iron gate: deny con mensaje explicativo
- Sin iron gate: fallback a prompting manual

### 1.6 Proteccion contra Bypass del Clasificador

`permissionSetup.ts` implementa protecciones criticas:

**Dangerous permission stripping:** Al entrar en auto mode, el sistema escanea TODAS las reglas allow existentes y remueve las que permitirian bypass del clasificador:
- `Bash(*)` o `Bash` sin contenido -- permite TODOS los comandos
- `Bash(python:*)`, `Bash(node:*)`, etc. -- interpretes de codigo arbitrario
- `PowerShell(iex:*)`, `PowerShell(Invoke-Expression:*)` -- evaluadores de strings
- `Agent(*)` -- permite sub-agentes sin evaluacion
- Cualquier regla para `Tmux` (ant-only)

Las reglas removidas se guardan en `strippedDangerousRules` y se restauran al salir de auto mode via `restoreDangerousPermissions`.

**Patrones peligrosos completos** (`dangerousPatterns.ts`):
- Interpretes: python, node, ruby, perl, php, lua, deno, tsx
- Package runners: npx, bunx, npm/yarn/pnpm/bun run
- Shells: bash, sh, zsh, fish
- Evaluadores: eval, exec, env, xargs, sudo
- Red (ant-only): curl, wget, gh, gh api, git, kubectl, aws, gcloud

### 1.7 Seguridad de Paths (filesystem.ts, pathValidation.ts)

El sistema valida paths con multiples capas:

1. **Deny rules primero** -- siempre ganan
2. **Internal editable paths** -- archivos internos de Claude (planes, scratchpad) se permiten
3. **Safety checks** -- `.git/`, `.claude/`, `.vscode/`, shell configs, claude settings files
4. **Working directory** -- paths dentro del working dir se permiten para lectura; escritura requiere acceptEdits mode
5. **Sandbox write allowlist** -- paths configurados explicitamente en sandbox config
6. **Allow rules** -- reglas explicitas del usuario

**Anti-bypass de paths:**
- Bloqueados: UNC paths (`\\server\share`), tilde variants (`~root`, `~+`), shell expansion (`$VAR`, `%VAR%`, `$(cmd)`, Zsh `=cmd`)
- Case normalization: todo se compara en lowercase para evitar bypass en filesystems case-insensitive
- Symlink resolution: paths se resuelven via `realpathSync` antes de comparar
- Glob patterns bloqueados en operaciones de escritura (evita que `*.txt` valide solo el directorio padre)
- Removal path protection: `/`, `~`, root children, Windows drive roots siempre bloqueados

### 1.8 Shadowed Rule Detection

`shadowedRuleDetection.ts` detecta reglas que nunca se ejecutaran:
- Allow rule con contenido especifico (ej: `Bash(ls:*)`) shadowed por deny rule global (`deny: Bash`)
- Allow rule shadowed por ask rule global -- el usuario siempre vera el prompt
- Genera sugerencias de fix automaticas

### 1.9 Bypass Permissions Killswitch

`bypassPermissionsKillswitch.ts` implementa un killswitch remoto via GrowthBook (Statsig) que puede desactivar bypass mode para toda una organizacion. Tambien verifica gates para auto mode. Corre una vez por sesion y puede downgrade bypass -> default mode.

### 1.10 Hooks de Permisos

Para agentes headless/async que no pueden mostrar prompts:
- `PermissionRequest` hooks se ejecutan primero
- Si un hook retorna allow/deny, se respeta
- Si ningun hook decide, auto-deny con mensaje explicativo
- Hooks pueden modificar input (`updatedInput`) y agregar reglas (`updatedPermissions`)

---

## 2. SISTEMA BRIDGE

### 2.1 Proposito y Arquitectura

El bridge es el subsistema que permite "Remote Control" -- conectar una instancia local de Claude Code con claude.ai para que un usuario pueda controlar la sesion desde el navegador. Hay dos arquitecturas:

**Environment-based (v1):** `replBridge.ts` + `bridgeMain.ts`
- Registra un "environment" en el servidor
- Poll loop: pregunta periodicamente al servidor si hay trabajo
- Recibe work items con un `WorkSecret` (base64url-encoded JSON)
- Spawns child processes para cada sesion

**Env-less (v2):** `remoteBridgeCore.ts`
- Conexion directa sin layer de Environments API
- POST /v1/code/sessions -> session_id
- POST /v1/code/sessions/{id}/bridge -> worker_jwt + epoch
- SSE read stream + CCRClient write path
- Gated por `tengu_bridge_repl_v2`

### 2.2 Transporte

`replBridgeTransport.ts` define la abstraccion de transporte con dos implementaciones:

**v1 (HybridTransport):** WebSocket para lectura + HTTP POST para escritura al Session-Ingress.

**v2 (SSE + CCRClient):** SSE stream para lectura de eventos del servidor. CCRClient para escritura via /worker/* endpoints. Worker registration otorga un epoch number para heartbeats.

Funcionalidades del transporte:
- `write`/`writeBatch` -- enviar mensajes
- `reportState` -- informar al servidor si hay permission prompt pendiente (v2 only)
- `reportDelivery` -- tracking de procesamiento de eventos (v2 only)
- `flush` -- drain de write queue antes de cierre
- `getLastSequenceNum` -- para resumir stream sin replay completo
- `droppedBatchCount` -- deteccion de batches perdidos silenciosamente

### 2.3 Autenticacion y Seguridad

**Multiples capas de auth:**

1. **OAuth token** -- token de claude.ai subscription. Requerido. Verificado en `bridgeConfig.ts`.
2. **Session ingress JWT** -- token especifico de sesion con formato `sk-ant-si-{jwt}`. Contiene claims de session_id y role=worker. Emitido por el servidor al despachar trabajo.
3. **Trusted device token** -- token persistente (90 dias rolling) almacenado en keychain del OS. Enviado como `X-Trusted-Device-Token`. Enrollment via POST /auth/trusted_devices dentro de 10 minutos del login. Gated por `tengu_sessions_elevated_auth_enforcement`.
4. **Environment secret** -- secreto por-environment para autenticar polls y heartbeats.

**Token refresh:** `jwtUtils.ts` implementa refresh proactivo 5 minutos antes de expiracion. Max 3 failures consecutivos antes de rendirse. Fallback: 30 minutos si expiry desconocido.

**Seguridad de IDs:** `validateBridgeId` valida que IDs del servidor solo contengan `[a-zA-Z0-9_-]` para prevenir path traversal en URLs.

### 2.4 Session Management (bridgeMain.ts)

El bridge loop principal maneja multiples sesiones concurrentes:

- **SpawnMode:**
  - `single-session`: una sesion en cwd, bridge termina cuando termina
  - `worktree`: cada sesion en un git worktree aislado
  - `same-dir`: todas las sesiones comparten cwd (riesgo de colision)

- **Timeout:** Default 24 horas por sesion. Watchdog kills sesiones que exceden timeout.
- **Heartbeat:** Extiende lease de work items via Session Ingress auth (JWT, sin DB hit).
- **Capacity wake:** Signal para despertar el sleep de at-capacity cuando una sesion completa, permitiendo aceptar nuevo trabajo inmediatamente.
- **Backoff:** Exponential backoff con caps configurables. Connection: 2s initial, 120s cap, 600s give-up. General: 500ms initial, 30s cap, 600s give-up.
- **Sleep detection:** Threshold = 2x connection backoff cap. Detecta sleep/wake del sistema para resetear error budget.

### 2.5 Messaging (bridgeMessaging.ts)

**Echo dedup:** `BoundedUUIDSet` (bounded ring buffer de UUIDs) filtra:
- Ecos de mensajes propios (recentPostedUUIDs)
- Re-deliveries de mensajes ya procesados (recentInboundUUIDs)

**Control requests del servidor:** Manejados por `handleServerControlRequest`:
- `initialize` -- setup inicial
- `set_model` -- cambiar modelo
- `can_use_tool` -- permission decision remota
- `set_permission_mode` -- cambiar modo de permisos

**Message eligibility:** Solo user/assistant turns y system events de slash commands se envian al bridge. Tool results, progress, y mensajes virtuales se filtran.

### 2.6 Permission Callbacks via Bridge

`bridgePermissionCallbacks.ts` define la interfaz para permisos remotos:

- `sendRequest` -- envia control_request al servidor (tool name, input, suggestions)
- `sendResponse` -- envia control_response con allow/deny + optional updatedInput/updatedPermissions
- `cancelRequest` -- cancela un prompt pendiente
- `onResponse` -- subscribe a respuestas del servidor para un requestId

El bridge actua como proxy de permisos: cuando el CLI local necesita aprobacion, envia un control_request al servidor (claude.ai), el usuario aprueba/deniega en la web, y la respuesta llega como control_response que el bridge reenvía al CLI.

### 2.7 WorkSecret Protocol

`workSecret.ts` -- el servidor envia trabajo como base64url JSON con:
- `version: 1` (obligatorio)
- `session_ingress_token` -- JWT para auth
- `api_base_url` -- endpoint de la sesion
- `sources` -- git info para clonar/checkout
- `auth` -- tokens adicionales
- `claude_code_args` -- argumentos CLI override
- `mcp_config` -- config de MCP servers
- `environment_variables` -- env vars inyectadas
- `use_code_sessions` -- selector CCR v2

SDK URLs se construyen diferente segun v1 (WebSocket: `wss://host/v1/session_ingress/ws/{id}`) o v2 (HTTP: `https://host/v1/code/sessions/{id}`).

### 2.8 Session Runner

`sessionRunner.ts` -- spawns child Claude Code processes:
- Parsea stdout del child para detectar tool_use (actividad)
- Detecta permission requests del child y los forwarda al bridge
- Ring buffers de actividad (10 entries) y stderr (10 lines)
- Filename sanitization: `safeFilenameId` reemplaza todo caracter no `[a-zA-Z0-9_-]`

---

## 3. PATRONES DE SEGURIDAD Y ANTI-BYPASS

### 3.1 Defense in Depth

1. **Safety checks son bypass-immune** -- ni bypassPermissions ni auto mode pueden saltarlos. Protegen .git, .claude, shell configs.
2. **Deny siempre gana** -- evaluado primero en la pipeline, sin override posible.
3. **Classifier transcript filtering** -- texto del asistente se excluye del transcript del clasificador para prevenir prompt injection.
4. **Dangerous permission stripping** -- al entrar en auto mode, reglas que permitirian bypass se remueven automaticamente y se restauran al salir.
5. **Fail-closed gate** -- clasificador no disponible = deny (configurable via feature flag).
6. **Denial limits** -- max 3 consecutivos o 20 totales antes de fallback a prompt manual. En headless: abort.
7. **Path validation anti-tricks** -- shell expansion, tilde variants, UNC paths, case normalization, symlink resolution.
8. **Enterprise lockdown** -- `allowManagedPermissionRulesOnly` descarta todas las reglas no-enterprise.

### 3.2 Anti-Escalation

- `bypassPermissions` killswitch remoto via GrowthBook
- Auto mode circuit breaker (`autoModeCircuitBroken`)
- PowerShell requiere permiso explicito en auto mode (no pasa por clasificador a menos que `POWERSHELL_AUTO_MODE` este activo)
- Agent/REPL tools excluidos del fast-path de acceptEdits (previene bypass del clasificador via sub-agentes)

### 3.3 Observabilidad

- Cada decision se loguea con telemetria detallada: classifier model, tokens, duration, cost, stage info
- `permissionExplainer.ts` usa un side-query al modelo para generar explicaciones humanas de risk (LOW/MEDIUM/HIGH)
- Denial limit events logueados con counts y tool names
- Error dumps del clasificador guardados en directorio temporal por sesion

### 3.4 Bridge Security

- Validated IDs en URLs (anti path traversal)
- OAuth retry con refresh automatico en 401
- Trusted device enrollment time-boxed (10 min post-login)
- Session ingress tokens son JWTs con session_id claim -- no reutilizables entre sesiones
- Worker epoch tracking previene replay de workers obsoletos

---

## 4. DECISIONES DE DISENO NOTABLES

1. **Reglas ask explicitas del usuario > bypass mode.** Si el usuario configuro `Bash(npm publish:*)` como ask, ni siquiera bypassPermissions lo salta. Razon: el usuario deliberadamente quiso ese checkpoint.

2. **Clasificador como side-query.** El auto mode classifier no usa el main loop del agente. Es una API call separada con su propio presupuesto de tokens. Esto evita que el classifier cost se mezcle con session cost.

3. **Two-tier JSONL transcript.** El transcript para el clasificador es compacto (JSONL de tool calls y user text), no el transcript completo. Cada herramienta tiene `toAutoClassifierInput` que controla como se representa ante el clasificador.

4. **Separator entre env-based y env-less bridge.** El env-less bridge elimina toda la capa de Environments API (poll/ack/stop/heartbeat/deregister) porque el endpoint /bridge puede emitir directamente worker JWTs. Esto simplifica significativamente el path REPL.

5. **Immutable safety layer.** La decision de hacer los safety checks inmutables a todos los modos es un principio arquitectonico fundamental: ningun modo de conveniencia puede comprometer archivos de configuracion del sistema.

---

