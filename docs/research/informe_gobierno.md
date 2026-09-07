# Bob Shell 2.0.2 (IBM) — capa de gobierno: hooks, políticas y permisos

Método: extracción de literales embebidos en el bundle minificado (`literals.txt`,
16 MB) más lectura directa de los chunks fuente cuando el extractor de literales
cortaba un string. La documentación de hooks (`CONFIGURE_HOOKS_CONTENT`) vive
completa, en texto plano, embebida como contenido de skill dentro del propio
bundle — no hubo que inferir nada de código minificado para esa parte.

## 1. Hooks

### 1.1 Los 5 eventos

| Evento | Cuándo dispara | Matcher | stdin extra | Puede bloquear |
|---|---|---|---|---|
| `SessionStart` | arranca o resume una task | `startup` / `resume` (regex sobre `source`) | `source` | No (exit 2 se ignora con warning) |
| `UserPromptSubmit` | antes de procesar el prompt | sin matcher (n/a) | `prompt` | Sí — exit 2 |
| `PreToolUse` | antes de aprobación+ejecución de la tool | regex sobre `tool_name` | `tool_name`, `tool_input`, `tool_use_id` | Sí — exit 2 o JSON estructurado |
| `PostToolUse` | tras éxito de la tool (se salta si la tool falló) | regex sobre `tool_name` | los de PreToolUse + `tool_response` (string) | No (exit 2 se loguea, ignorado — la tool ya corrió) |
| `Stop` | al terminar el loop raíz de la task | sin matcher | `last_assistant_message` (string o null) | No |

Payload común a los 5: `session_id`, `cwd`, `hook_event_name`. Se manda **un
único JSON por stdin**, no streaming.

### 1.2 Motor de ejecución (código real, `runHooks`/`gOu`, chunk `bob_0122.js`)

```js
async function runHooks(hooksByEvent, payload) {
  for (const group of hooksByEvent[payload.hook_event_name] ?? [])
    if (matcherMatches(group.matcher, payload))
      for (const hook of group.hooks) {
        if (hook.disabled) continue;
        const result = await execHook(hook, payload);   // <- await dentro del for
        ...
        if (result.blocked) return { blocked: true, ... };  // corta acá
      }
  return { blocked: false, ... };
}
```

- **Serie, no paralelo.** Es un `for...of` con `await` adentro, tanto entre
  grupos-matcher como entre hooks dentro de un grupo. No hay `Promise.all`.
- **Ejecución del proceso:** `child_process.exec(hook.command, {cwd: payload.cwd,
  timeout: (hook.timeout ?? 10) * 1000, maxBuffer: 1 MiB, windowsHide: true})`.
  `exec` = shell real (`/bin/sh -c` / `cmd.exe`), **mismo usuario/UID/permisos
  del proceso de Bob**. Nada de sandbox, contenedor, seccomp, ni usuario
  separado. **NO hay aislamiento — confirmado en el propio código, no inferido.**
- **Timeout:** default 10 s por hook (`pOu=10` en el bundle), configurable por
  handler (`timeout`, positivo). Es por-hook, no hay timeout de evento agregado.
- **Interpretación de exit code** (función `yOu`):
  - `exitCode === null` (falló al arrancar / timeout) → warning, se trata como
    **no bloqueado** (fail-open).
  - `exitCode === 2` → sólo `PreToolUse` y `UserPromptSubmit` bloquean
    (razón = stderr, con stdout como fallback). En `SessionStart`,
    `PostToolUse`, `Stop` un exit 2 se loguea con *"hooks cannot block"* y se
    ignora.
  - Cualquier otro exit ≠ 0 → warning, fail-open.
  - `exitCode === 0` → stdout (trim) se usa como `additionalContext` para
    `SessionStart` / `UserPromptSubmit` / `PostToolUse`; para `PreToolUse` se
    intenta parsear como JSON estructurado (ver 1.3).
- **Encadenado de reescrituras:** si un `PreToolUse` hook devuelve
  `updatedInput`, el `tool_input` del payload se reemplaza ANTES de invocar al
  siguiente hook — cada hook subsiguiente ve la versión más reciente. Confirmado
  en `runHooks`: `a = {...a, tool_input: o}` dentro del loop.

### 1.3 Salida estructurada de `PreToolUse` (compatible con formato Claude)

```json
{ "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny" | "allow",
    "permissionDecisionReason": "...",
    "updatedInput": { ... }
} }
```

`permissionDecision:"deny"` bloquea con la razón dada; `"allow"` **no salta el
flujo normal de aprobación de Bob** (no es un "siempre sí", según la propia
doc). `updatedInput` reemplaza el `tool_input` antes de que Bob valide, apruebe
y ejecute la tool — **un hook SÍ puede reescribir la llamada, no sólo
bloquearla.** Confirmado por código y por doc.

### 1.4 Formato del `settings.json`

```json
{ "hooks": {
    "SessionStart":  [ { "matcher": "^startup$", "hooks": [ { "type": "command", "command": "...", "timeout": 10, "disabled": false } ] } ],
    "UserPromptSubmit": [ ... ], "PreToolUse": [ ... ], "PostToolUse": [ ... ], "Stop": [ ... ]
} }
```

- Ubicaciones: workspace `.bob/settings.json`; global `~/.bob/settings/settings.json`.
  Se combinan (no se pisan) — un hook global no reemplaza uno workspace igual.
  `disableGlobalHooks` (bool) puede apagar los globales desde el workspace.
- Schema real (zod, chunk `bob_0065.js`): `hooksSchema` valida sólo las 5
  claves de evento; cada handler exige `command` no vacío y `timeout` positivo
  opcional; arrays de matcher-group y de hooks no pueden estar vacíos
  (`.strict()`, rechaza claves extra).
- **No existe `${CLAUDE_PROJECT_DIR}` ni equivalente** — el propio doc lo dice
  explícito: usar el cwd del payload o rutas absolutas para hooks globales.
- **Los hooks corren SÓLO si el workspace está "trusted"** (`isWorkspaceTrusted()`
  en el runtime, ver sección 3). Workspace no confiable = cero hooks de
  ninguno de los dos scopes.

## 2. Políticas empresariales

### 2.1 Ubicación y mecanismo de lectura

- **Linux / os390 / os400:** archivo plano `/etc/bob/policy.json`
  (`LINUX_POLICY_FILE_PATH`, chunk `bob_0123.js`, string verificada en disco).
  Vigilado con un `WorkspaceConfigService` apuntado a ese directorio+filename
  — es decir, **hay watcher de filesystem real**, no sólo lectura al boot.
- **Windows:** registro nativo, clave `Software\Policies\IBM\Bob`
  (`WINDOWS_REGISTRY_POLICY_PATH`), vía un watcher nativo (`vscode-policy-watcher.node`,
  el mismo módulo nativo que usa VS Code para Group Policy / Intune).
- **macOS (y cualquier no-Linux/os390/os400):** mismo watcher nativo, con
  `productName = "com.ibm.bob"` (managed preferences / MDM, patrón estándar
  macOS).
- Reintento/relectura: en Linux vía evento de filesystem (`onFilesChange`,
  debounce implícito del `WorkspaceConfigService`); en Windows/macOS vía
  callback nativo del watcher — **ambos son push, no polling con intervalo
  fijo**.

### 2.2 Las políticas (5 definidas, no 4 — hay una quinta interna)

| Nombre | Tipo | Mapea a (`configPath`) | Default | Notas |
|---|---|---|---|---|
| `DisabledAutoApprovalGroups` | string CSV | `approval.forbiddenApprovalGroups` | `"edit,execute"` | IDs válidos: read, edit, execute, mcp, skill, todo, subtask, subagent, mode |
| `GatewayUrl` | string | `gatewayUrl` (raíz) | `https://bob.gateway.example.com` | *"users and CLI flags cannot override this value"* — literal del propio string de descripción |
| `EnforcedHooks` | string JSON | (especial, no es un configPath simple) | ejemplo: hook `PostToolUse` que loguea cada uso de tool | *"Policy hooks run before any user-defined hooks"* |
| `UpdateMode` | string (sólo variante VS Code) | `update.mode` | `"none"` | valores: default / start / manual / none |
| `ExcludePayloadFromTelemetry` | boolean | `telemetry.excludePayload` | `true` | `audience:"ibm"` — no es una política documentada para el cliente final, es interna de telemetría IBM |

`EnforcedHooks` se parsea con el mismo `hooksSchema` que valida `settings.json`
de usuario; si no matchea, se ignora con warning (fail-safe, no rompe el
arranque).

### 2.3 Choque política vs configuración de usuario — y si se puede sortear

Código real de `BobUserConfig.applyPolicyOverrides` (chunk `bob_0004.js`):

```js
async applyPolicyOverrides(policyValues) {
  this._lockedPaths = new Set(Object.keys(policyValues));
  this._policyValues = new Map(Object.entries(policyValues));
  ...
}
updateAt(path, value) {
  if (this._lockedPaths.has(path)) return;              // no-op silencioso
  for (const locked of this._lockedPaths)
    if (locked.startsWith(path + ".") || path.startsWith(locked + ".")) return;
  ...
}
get values() {
  ...
  return this.applyPolicyValues(deepMerge(defaults, storage));  // reaplica SIEMPRE
}
```

- Un `configPath` bloqueado por política queda en `_lockedPaths`: cualquier
  `update()`/`updateAt()` del usuario sobre esa ruta (o un ancestro/hijo de
  ella) es un **no-op silencioso** — ni tira error, simplemente no escribe.
- Aunque el usuario edite el `settings.json` a mano, el getter `values` vuelve
  a aplicar `_policyValues` **encima** del merge default+storage en cada
  lectura. El archivo en disco puede tener el valor "deseado" por el usuario,
  pero el runtime nunca lo ve — siempre gana la política.
- **Conclusión sobre "sortear":** desde dentro de la app (UI, settings.json,
  flags CLI para `gatewayUrl`) **no se puede**, está reforzado en el punto de
  lectura, no sólo en el de escritura. La única vía de bypass real es *fuera*
  del control de Bob: borrar/editar `/etc/bob/policy.json` (requiere el mismo
  privilegio que puso la política — típicamente root) o la clave de registro
  equivalente en Windows/macOS. Es la misma superficie que cualquier MDM: la
  política es tan fuerte como la protección del archivo/registro que la porta.

## 3. Permisos y aprobación

### 3.1 Grupos de auto-aprobación

`read`, `edit`, `execute`, `mcp`, `skill`, `todo`, `subtask`, `subagent`,
`mode` — confirmado tanto en la descripción de `DisabledAutoApprovalGroups`
como en el registro de grupos de permiso de la UI (`id:"read"`, `id:"edit"`,
`id:"execute"`, ...).

- Config por defecto: `approval:{autoApprovalEnabled:true, outsideWorkspaceAllowed:false,
  allowed_permissions:[...]}`. Es decir, Bob **arranca con auto-aprobación
  general habilitada** y una whitelist de grupos ya permitidos por defecto
  (edit/execute quedan fuera si la política `DisabledAutoApprovalGroups` los
  fuerza).
- `forbiddenApprovalGroups` (población desde la política, sección 2.2) tiene
  **precedencia dura**: `requiresExplicitApproval()` chequea primero esta
  lista — si el grupo de la tool está ahí, exige aprobación explícita sin
  importar qué diga `allowed_permissions`.
- "Always allow" (`applyApprovalResponse`, chunk `bob_0123.js`):
  - Para permisos de tool nativa: agrega el grupo a `allowed_permissions` y
    persiste vía `updateApprovalConfig` — **es a nivel de config del usuario**
    (global o workspace según scope activo del `BobUserConfig`), no
    por-sesión.
  - Para comandos de shell específicos ("allow this exact command always"):
    `addTaskCommandApprovals` — pero este es **task-scoped**
    (`taskCommandApprovals`), no global; vive junto al estado de la tarea.
  - Para tools MCP: `updateMcpAlwaysAllow` — agrega a `taskAllowedMcpTools`
    (task-scoped) y también putea el permission group a `allowed_permissions`
    si corresponde.
  - Conclusión: hay **dos niveles de persistencia distintos** mezclados bajo
    el mismo botón "always allow" — grupo de permiso amplio (persistente,
    fuera de la task) vs. comando/tool MCP puntual (persistente sólo dentro
    de esa task). Puede sorprender a un admin que asuma que todo "always
    allow" es global.

### 3.2 Comandos peligrosos

Dos capas, no una:

1. **Lista estática** — `deniedCommands` (vacía por defecto) y
   `DEFAULT_APPROVED_COMMANDS = ["cat","git diff","git log","git rev-parse",
   "git show","git status","grep","head","tail","ls","sort","wc","which","du","df"]`
   (todos de sólo lectura). Deny list gana sobre approved list vía
   `getBestCommandMatch` (longest-prefix match).
2. **Clasificador por LLM** (`assessCommandSecurity`, chunk `bob_0051.js`,
   activo si `isCommandSecurityEnabled`): antes de eso corre un precheck
   regex contra patrones de inyección shell conocidos (`${...@P}` transform
   attacks bash, `<<< $(...)`/backtick process substitution, glob extendido
   `*(...)`) y contra pipe-to-shell (`| bash`, `| sudo bash`,
   `base64 -d | bash`) — si matchea, es `dangerous:true` sin llamar al LLM.
   Si no matchea nada de eso, el comando (truncado a 5000 chars) se manda a
   un modelo con un prompt explícito: *"you are detecting commands that cause
   unintended harm BEYOND the user's intent — exfiltración, borrado no
   intencional, escalada de privilegios"*, con timeout de 15 s y fail-closed
   (`dangerous:true`) si el LLM tira error o timeoutea.
   **Esto es un diseño distinto al de Claude Code**: no es sólo heurística
   estática, hay un juicio semántico por modelo en el camino crítico de
   aprobación de cada comando.

### 3.3 Workspace trust (folder trust)

- Config: `security.folderTrust.enabled` (default `true`), label en UI
  *"Prompt to configure trust level before starting Bob Shell in an
  unfamiliar folder"*.
- Persistencia: `TrustStore` — un store por carpeta (`folders[directory] =
  trustLevel`), resuelto con herencia de directorio padre
  (`resolveFolderTrust`).
- Efecto de no confiar en el workspace (todo verificado en código, no en doc):
  - Cero hooks (ni workspace ni global) — sección 1.4.
  - Skills de scope `"workspace"` se filtran, no se cargan (`getSkills`,
    `populateCoreTask`).
  - Agentes custom del workspace no se listan (`getAgents` devuelve `{}`).
  - `isWorkspaceTrusted()` es simplemente `options.workspaceTrusted !== false`
    — el runtime host decide el valor real; el motor sólo consume el booleano.

## 4. Aislamiento de hooks y reescritura de tool calls

Ambas preguntas, respondidas por código real (no inferidas):

- **Aislamiento: NO.** `child_process.exec()` sin contenedor, sin usuario
  separado, sin filtro de syscalls, `windowsHide` es la única bandera
  "de seguridad" (oculta la ventana en Windows, no aísla nada). Mismo
  proceso/permisos que Bob mismo. Confirmado también por la propia doc del
  bundle: *"Hook commands execute directly and do not use Bob's normal
  command-approval flow. Never add a command the user has not reviewed."*
- **Reescritura: SÍ.** Un `PreToolUse` hook con exit 0 y stdout JSON
  (`hookSpecificOutput.updatedInput`) reemplaza `tool_input` antes de que Bob
  valide/apruebe/ejecute — y ese reemplazo se propaga a los hooks
  subsiguientes en la cadena (sección 1.2/1.3).

---

## Resumen (comparado con hooks/permisos de Claude Code)

1. **Mismos 5 eventos que Claude Code**, mismo contrato stdin/exit-code/JSON
   estructurado — es casi un clon deliberado de la superficie de Claude Code
   Skills/Hooks (hasta el formato `hookSpecificOutput.permissionDecision`).
2. **PEOR — sin aislamiento**: confirmado en el código (`child_process.exec`
   directo, mismo usuario, cero sandbox). Claude Code documenta lo mismo
   ("hooks run with full user permissions"), así que en esto están a la par
   en riesgo, no hay diferencia real.
3. **PEOR — hooks estrictamente en serie**, un solo `for` con `await`, sin
   `Promise.all` en ningún nivel (ni entre matcher-groups ni entre hooks del
   mismo grupo). Con 10 s de timeout default por hook y varios hooks en la
   misma tool, la latencia se apila linealmente.
4. **MEJOR — políticas empresariales reales de fábrica**: `/etc/bob/policy.json`
   + registro de Windows + managed prefs macOS, con watcher push, 5 políticas
   tipadas, y sobre todo **enforcement en el punto de LECTURA** (`get values()`
   reaplica la política en cada acceso, no sólo al guardar) — Claude Code no
   tiene un mecanismo equivalente de policy-as-code nativo de esta forma.
5. **MEJOR — `EnforcedHooks` como política**: un admin puede inyectar un hook
   obligatorio (JSON en policy.json/registro) que corre **antes** que
   cualquier hook de usuario, sin que el usuario pueda desactivarlo desde su
   `settings.json`.
6. **MEJOR (matiz) — clasificador de comandos por LLM** (`assessCommandSecurity`)
   antes de la aprobación, con fail-closed en error/timeout, sumado a
   precheck regex de patrones de inyección conocidos — Claude Code se apoya
   más en heurística estática y en el propio LLM del turno.
7. **MEJOR — separación fina de "always allow"**: grupo de permiso amplio
   (persistente, cross-task) vs. comando puntual o tool MCP (persistente
   sólo dentro de la task) — reduce el radio de un "sí" accidental, aunque a
   costa de previsibilidad para el usuario.
8. **Igual — un `PreToolUse` puede bloquear O reescribir el `tool_input`**
   (no sólo aprobar/negar), con la misma semántica que Claude Code
   (`updatedInput`/`permissionDecision`).
9. **Igual/menor riesgo — `PostToolUse`/`Stop`/`SessionStart` no pueden
   bloquear nunca** (exit 2 se loguea y se ignora explícitamente) — mismo
   diseño que Claude Code, coherente porque la tool ya corrió.
10. Fail-open por defecto en casi todo (timeout de hook, exit code no
    reconocido, hook que no arranca) salvo el clasificador de comandos
    peligrosos, que es fail-closed — es una asimetría de diseño consciente:
    conveniencia por default, seguridad reforzada sólo donde el costo de un
    falso negativo es alto.

Archivo completo: `/tmp/claude-1000/-home-dadito-IA-proyecto-seal/ba53b9c8-12c5-4f76-b1ae-de28b582b00c/scratchpad/bob/re/informe_gobierno.md`
