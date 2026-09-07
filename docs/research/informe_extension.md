# IBM Bob Shell 2.0.2 — Sistema de extensión (skills, subagentes, MCP)

Fuente: bundle minificado en `/tmp/.../scratchpad/bob/re/chunks/bob_*.js` (160 chunks) y
`literals.txt` (extracción de literales de cadena). Evidencia citada como `literals.txt:<línea>`.
No se copia código minificado; se documentan contratos/formatos observados en las guías internas
(`CREATE_A_SKILL_CONTENT`, `CONFIGURE_MCP_CONTENT`, `BUILD_MCP_SERVER_CONTENT`,
`CREATE_A_MODE_CONTENT`, `CONFIGURE_HOOKS_CONTENT`) y en el código de carga/validación.

---

## 1. SKILLS

### Formato de `SKILL.md`
Frontmatter YAML + cuerpo Markdown. Parseado por `parseSkillFile()` (literals.txt:31958-31961).

Campos de frontmatter:
| Campo | Obligatorio | Notas |
|---|---|---|
| `name` | No (recomendado) | Si se omite, se infiere del **nombre del directorio** contenedor. El nombre canónico SIEMPRE es el directorio, no el frontmatter. |
| `description` | No (recomendado, es el más importante) | Si se omite, cae a la primera línea del cuerpo. Es el disparador de auto-activación por el modelo. |
| `metadata.user-invocable` | No (default `true`) | `false` ⇒ sin comando `/`, solo auto-invocable por el modelo. Así funcionan los builtins tipo `create-plan`. |
| `metadata.disable-model-invocation` | No (default `false`) | `true` ⇒ solo se dispara con `/<skill-name>`, nunca por el modelo. |
| `metadata.argument-hint` | No | Hint de autocompletado tras `/skill-name`. |
| `metadata.groups` | No | Array de strings (grupos de permisos asociados). |

Validación del **nombre** (=nombre de directorio): regex `^[a-z0-9]+(-[a-z0-9]+)*$`, máx 64
caracteres (literals.txt:31958, `isValidSkillName`). **La validación es silenciosa**: un nombre
inválido hace que la skill se omita sin error visible (documentado explícitamente en
`CREATE_A_SKILL_CONTENT`, Paso 2).

### Directorios de carga (confirmado en código, no solo doc)
Globales (`DEFAULT_GLOBAL_SKILL_DIRS`, literals.txt:31961):
```
~/.bob/skills
~/.agents/skills
~/.claude/skills
```
Workspace (`WorkspaceSkillsManager`, literals.txt:34271, patrones glob):
```
.bob/skills/*/SKILL.md
.bob/*/skills/*/SKILL.md        (skill root anidado bajo un prefijo dentro de .bob/)
.agents/skills/*/SKILL.md
.claude/skills/*/SKILL.md
```
Precedencia de escritura recomendada: `.bob` > `.agents` > `.claude` (el asistente solo usa
`.agents`/`.claude` si el workspace ya muestra evidencia de usar ese ecosistema).

### Deduplicación y resolución
"First-wins dedup by name: **workspace > global > builtin**" — una skill de workspace pisa
silenciosamente una global con el mismo nombre (nota de poder-usuario en la guía).

### Carga del cuerpo: bajo demanda
El cuerpo (`onActivate`) es una función que se resuelve solo cuando la skill se activa
(`onActivate:()=>Promise.resolve(...)` o `onActivate:()=>n.readFile(o)` para skills en disco,
literals.txt:31961 `buildSkill`). El listado de skills disponibles (nombre + descripción) sí vive
siempre en el system prompt (sección "skills" del prompt, ver `contextWindowUsage`,
literals.txt:22227 zona), pero el contenido completo del `SKILL.md` solo entra al contexto cuando
`use_skill` la activa — confirmado por el tracking de tokens que suma el contenido de cada mensaje
`toolUsage.signature.name==="use_skill"` por separado del bloque fijo de "skills".

### Herramienta `use_skill`
Clase `UseSkillTool` (literals.txt:22200 zona). Al llamarla:
- Resuelve la skill por nombre entre las registradas para el modo actual.
- Ejecuta `onActivate()`, inyecta el cuerpo como resultado de la tool call.
- Si la skill tiene `skillDir`, añade una instrucción para leer archivos con `read_file` con rutas
  relativas a ese directorio (soporte a "supporting files": scripts/plantillas junto al
  `SKILL.md`).
- Mantiene un set `activatedSkills` para no re-inyectar contenido ya cargado en la tarea.
- Si `hideResult:true` (todas las builtins lo usan) el resultado se oculta de la UI del usuario
  pero igual entra al contexto del modelo.
- Si no encuentra la skill: error `"...not found."`.

### Skills incorporadas de fábrica (`BUILTIN_SKILLS`, literals.txt:34271)
1. **`create-plan`** — workflow estructurado de planificación (requisitos → sub-agente de
   investigación de código → confirmación de diseño → archivo de plan). `groups:["plan"]`,
   `userInvocable:false` (solo el modelo la dispara).
2. **`create-skill`** — meta-guía para crear skills (la documentada arriba).
3. **`create-mode`** — guía para crear modos custom (`custom_modes.yaml`).
4. **`configure-hooks`** — guía para hooks de ciclo de vida.
5. **`configure-mcp`** — guía para agregar/diagnosticar servidores MCP.
6. **`build-mcp-server`** — guía para programar un servidor MCP desde cero.
7. **`office-insights`** — uso de `office_read`/`office_edit` (.docx/.xlsx/.pptx).
8. **`create-chart`** — esquemas de props para `create_chart` (LineChart, BarChart, PieChart,
   ScatterChart, RadarChart, BoxplotChart). `groups:["artifact"]`, `userInvocable:false`.

Todas las builtin tienen `scope:"builtin"` y `hideResult:true`.

---

## 2. SUBAGENTES

### Cómo se definen
Dos vías:
1. **Presets built-in**, `SUBAGENT_PRESETS` (literals.txt:18570-18602). Confirmado en código: solo
   existe **un preset built-in, `explore`** — perfil de solo-lectura ("Do NOT have access to file
   editing tools"), con reglas de búsqueda tipo grep-primero/leer-rangos/no leer archivos enteros,
   y contrato de salida ultra-comprimido (solo rutas+líneas+snippets, "No prose").
2. **Agentes custom de workspace**, archivos Markdown con frontmatter en:
   ```
   .bob/agents/*.md
   ```
   (`WorkspaceAgentsManager`, literals.txt:28349 — glob `agents/*.md` bajo `WORKSPACE_BOB_DIR`).
   **No hay agentes custom globales** — `getAgents()` solo se llama si
   `workspaceTrusted===true`, si no la lista es `{}` (literals.txt:18602 zona, `oOu`).

### Frontmatter de un agente custom (`parseAgentFile`, literals.txt:28349)
| Campo | Notas |
|---|---|
| `name` | Id del agente; si falta, se usa el nombre de archivo sin `.md`. |
| `description` | Label mostrado en la lista de subagentes disponibles. |
| `groups` | Array de grupos de permisos que hereda (si falta, usa un default interno). |
| `model` | String que debe pertenecer a `MODEL_TIERS`; si no matchea, se ignora. |
| `maxTurns` | Numérico. |
| `rawPrompt` | Boolean. |
| `allowForkContext` | Boolean — si `false`, bloquea que este preset reciba `fork_context=true`. |
| `allowTools` / `denyTools` | Arrays de nombres de tool — allowlist/denylist explícita por agente. |

El **cuerpo** del `.md` se separa en `systemPrompt` + `outputConstraints` (dos secciones, función
interna `AVs`), es decir el archivo tiene una convención de secciones dentro del Markdown (no solo
frontmatter) para distinguir el prompt de rol de las restricciones de formato de salida.

### Tipos disponibles en runtime
La tool `spawn_subagent` (`SubAgentTool`, literals.txt:18876+22200-21995) construye la lista de
`name` válidos como: presets (`explore`, + los `.bob/agents/*.md` cargados) más `"general"`
(hereda el modo/tools actuales del agente padre, modelo default) — salvo que el modo activo tenga
`allowedSubagents` restringiéndolo.

### Herencia de herramientas
- `general`: hereda **el toolset del modo actual** del agente padre.
- `explore` y agentes custom: reciben el toolset derivado de sus `groups` (+ `allowTools`/
  `denyTools` si están declarados).
- Un modo puede limitar qué subagentes están disponibles con `allowedSubagents` en
  `custom_modes.yaml` (documentado como "trap": si se define, **restringe** a solo esos nombres).

### Anidamiento y concurrencia
- **No pueden anidar**: "Subagents cannot spawn other subagents" (texto literal del
  `getDescription()` de la tool, literals.txt:22200).
- **Concurrencia**: "Multiple spawn_subagent calls in one turn run in parallel" (mismo texto) — sin
  límite numérico explícito encontrado en el texto de la tool; el límite práctico es "una call por
  subagente por turno", ejecutadas en paralelo.
- Presupuesto compartido: el subagente corre bajo `getRemainingCostBudget()` del padre (se corta si
  el padre se queda sin presupuesto de costo) y bajo su propio `shouldLoop()`/cancelación.

### Cómo vuelve el resultado
`SubAgentTool.call()` (literals.txt:22200-22227): crea una sub-tarea (`createSubTask`), le manda el
mensaje inicial (con o sin `extractParentContext()` según `fork_context`), la deja correr su loop
(`while R.shouldLoop()`), y al terminar arma `createSubagentMetadata` (spend, toolUseCount,
duración, `loopExitReason`, `changes`) y llama `collectResults()` para devolver un **resumen** al
padre — no vuelca los mensajes completos, salvo en caso de error donde adjunta
`setSubTaskMessages`. Hay eventos de telemetría `onSubagentStart`/`onSubagentEnd`/`onSubagentSpend`.

### Modelo/prompt propio
Sí: cada preset (built-in o custom) puede fijar su propio `model` (de un tier fijo) y su propio
`systemPrompt`/`outputConstraints`; si no se especifica modelo, usa el default del sistema.
`fork_context=true` inyecta el historial de conversación del padre al mensaje inicial del
subagente; por defecto (`false`) el subagente arranca solo con la `description` dada por el
llamador.

---

## 3. MCP

### Formato de `mcp.json`
Ubicación (`mcpServers` como clave raíz):
```
Global:    ~/.bob/settings/mcp.json     (todos los workspaces)
Workspace: .bob/mcp.json                (este proyecto; pisa global por nombre repetido)
```
Monorepos: un `.bob/mcp.json` más profundo pisa uno más superficial en el mismo workspace
(`calculateConfigDepth()`).

Campos por servidor (tabla de `CONFIGURE_MCP_CONTENT`, literals.txt:32449):
| Campo | Transporte | Notas |
|---|---|---|
| `command` | local (stdio) | Ejecutable a spawnear. |
| `args` | local | Array de argumentos. |
| `url` | remoto (HTTP) | Mutuamente excluyente con `command`. El transporte se infiere de cuál de los dos está seteado, no hay campo `type` explícito documentado. |
| `headers` | remoto | Headers HTTP (auth). |
| `env` | local | Variables de entorno inyectadas al proceso hijo. |
| `disabled` | ambos | `true` = no conecta, pero la entrada queda guardada. |
| `disabledTools` | ambos | Array de nombres de tool a suprimir de ese server. |
| `alwaysAllow` | ambos | Array de tool names que se auto-aprueban (saltan el prompt de confirmación). |
| `timeout` | ambos | ms; se "clampa" a uno de `5000,10000,30000,60000,120000,300000,600000,1800000,3600000`; valor inválido → default `60000`. |
| `groups` | ambos | Restringe la disponibilidad del server a ciertos modos. |
| `oauth` | remoto | `{clientId, clientSecret, scope}` — ver abajo. |

⚠️ **Secrets en `env`/`headers` se guardan en texto plano.** Bob **no** expande `${VAR}` en
`mcp.json` — lo que se escribe se persiste literal.

### stdio vs HTTP
- **stdio (local)**: Bob spawnea el proceso; requiere `command`+`args`, valida dependencias del
  sistema (node/uvx/python/docker/podman) antes de escribir la config.
- **HTTP (remoto)**: Bob conecta como cliente a un server corriendo; usa `url`+`headers`. Existe
  también soporte HTTP server-side documentado para quien construye un server propio
  (`NodeStreamableHTTPServerTransport`, transporte streamable-HTTP).
- No se encontró evidencia textual de SSE como transporte de servidor separado en la guía de
  usuario (el HTTP moderno del SDK v2 es streamable-HTTP), aunque el cliente subyacente sí soporta
  reconexión/streaming a nivel de transporte HTTP.

### OAuth
Dos caminos distintos, confirmados por evidencia separada:
1. **Servidor local (stdio) que el usuario mismo escribe** — la guía `build-mcp-server` indica que
   como el proceso corre no-interactivo, **no puede** abrir el navegador ni iniciar el flujo OAuth
   en runtime; el patrón recomendado es un script aparte de un solo uso
   (`get-refresh-token.js`) que el usuario corre manualmente con `execute_command`, y el refresh
   token termina pegado en el `env` de `mcp.json`.
2. **Servidor remoto (HTTP) al que Bob se conecta como cliente** — acá SÍ hay OAuth nativo real en
   el cliente Bob: `McpOAuthManager`, descubrimiento de metadata
   (`discoverOAuthProtectedResourceMetadata` → `authorization_servers`), flujo de autorización con
   ventana/popup de navegador (`BrowserOAuthClientProvider`, `openBrowser`), refresh automático de
   tokens (`refreshOAuthToken`, con `expires_at` calculado desde `expires_in`), y registro dinámico
   de cliente. Se configura vía el bloque `oauth` en `mcp.json` (`clientId`/`clientSecret`/`scope`
   opcionales — sin ellos intenta registro dinámico). Si el server no soporta el descubrimiento
   OAuth y responde 401, Bob cae a pedir un header `Authorization` manual.

### Elicitation (formularios para parámetros faltantes)
Confirmado con evidencia de código (`handleElicitationRequest`, `buildElicitationFormSpec`,
literals.txt: bloque `Rgu`/`Ogu`/`Pgu`). Bob soporta el mecanismo MCP `elicitation/create`:
- Dos modos: **`form`** (default) y **`url`** (para flujos tipo "completá esto en una URL externa",
  con error dedicado `UrlElicitationRequired`, código `-32042`, y lista de `elicitations`
  pendientes).
- En modo `form`, Bob construye dinámicamente un formulario de UI a partir del
  `requestedSchema` (JSON Schema) que manda el server: literales de componentes vistos incluyen
  `elicit-card` (Card), `elicit-message` (Text con el mensaje del server), campos de formulario
  generados por propiedad del schema, y botones `elicit-done`/`elicit-decline`/`elicit-cancel`
  ("Continue"/"Decline"/"Cancel").
- El input se valida contra el `requestedSchema` antes de aceptar (`Dgu`/`errors` de validación); si
  falla, se re-muestra el formulario con `elicit-error`.
- Si no hay UI disponible (`requestJsonRenderUi` ausente), la elicitation se cancela con log de
  advertencia — no se auto-completa nunca en silencio.
- El cliente reporta capacidades `sampling`/`elicitation`/`roots.listChanged` al conectar
  (`ClientCapabilities`), y también soporta `sampling` (el server le puede pedir al cliente que
  genere texto con el modelo del usuario) con callback `onSampling`.

### Nombrado de herramientas MCP en el prompt
Convención confirmada por código: **`mcp__<nombreServidor>__<nombreTool>`** — idéntica a la
convención de Claude Code (evidencia: `a.signature.name.startsWith("mcp__")` en telemetría de tool
use, literals.txt zona de `ckToolEvent`; y ejemplo de matcher de hook `^mcp__github__` en la guía
de hooks).

### Filtro/allowlist de herramientas
Sí, en tres capas:
1. Por servidor: `disabledTools` (denylist) y `alwaysAllow` (auto-aprobación, no denylist).
2. Por servidor: `groups` restringe en qué modos aparece ese server.
3. Por agente/subagente: `allowTools`/`denyTools` en el frontmatter del `.md` del subagente.
No se encontró un "allowlist" global aparte de estas tres capas.

### Timeouts
Ver tabla arriba — por conexión/llamada, configurable por servidor, valores discretos
predefinidos, default 60s.

### Recursos y prompts expuestos por un server
- **Resources**: Bob descubre `resources` y `resourceTemplates` (`listResourceTemplates`,
  `listAllResources`) y expone una tool dedicada **`read_mcp_resource(server, uri)`**
  (`BobMcpResourceTool`) — nombre propio de Bob, distinto de Claude Code pero mismo concepto
  ("Use resources for contextual data; use tools for actions", nota explícita en
  `build-mcp-server`). Soporta `resources/read` como método RPC subyacente y suscripción a cambios
  de recursos (`unsubscribeFromResource`).
- **Prompts**: Bob lista (`listPrompts`) y resuelve (`getPrompt`) los prompts que expone un server,
  y los convierte en comandos invocables (`getMcpCommand(serverName, config, promptDef)`) — es
  decir, un prompt de servidor MCP se vuelve algo parecido a un slash-command para el usuario, con
  soporte de `hint`/argumento nombrado y `fromServer` para pasarle contexto al prompt remoto.

---

## 4. Marketplace (`/api/marketplace/modes`, `/api/marketplace/mcps`)

No se encontró el path HTTP como literal de cadena exacto (probablemente se arma por
concatenación con una `bob-marketplace-url` configurable, ese literal sí existe:
`"bob-marketplace-url":""`, default vacío). Pero el **contrato de datos** está completo vía Zod
(literals.txt, zona `marketplaceItemSchema`):

- Feature flag: `"marketplace-enabled":!1` por defecto (deshabilitado salvo que se active).
- `marketplaceItemTypeSchema = enum(["mode","mcp"])` — dos catálogos, modos y servidores MCP.
- `modeMarketplaceItemSchema`: `{id, name, ...} + content` (el YAML/definición completa del modo
  lista para instalar).
- `mcpMarketplaceItemSchema`: `{..., content, parameters?, prerequisites?}` donde `content` es una
  plantilla de config MCP y `parameters` es un array de
  `mcpParameterSchema = {name, key, placeholder?, optional?}` — es decir, **antes de instalar un
  MCP del marketplace, el cliente le pide al usuario que rellene esos parámetros** (típicamente API
  keys) y los sustituye en la plantilla antes de escribirla a `mcp.json`.
- `installMarketplaceItemOptionsSchema`: `{target: "global"|"workspace", workspaceName?}` — el
  usuario elige alcance de instalación, igual que con skills/MCP manuales.
- Clases de error dedicadas: `MarketplaceError`, `InstallationError`, `NetworkError`.
- Telemetría: eventos `MARKETPLACE_INSTALL_BUTTON_CLICKED`, `MARKETPLACE_TAB_V...` — confirma que
  es una UI de catálogo con botón "instalar" por ítem, no solo una API de fondo.

En síntesis: el cliente usa el marketplace como **catálogo de plantillas** (modos y servidores MCP)
que se materializan en los mismos archivos (`custom_modes.yaml`, `mcp.json`) que la instalación
manual — no es un mecanismo runtime distinto, es un instalador de plantillas con relleno de
parámetros.

---

## Ejemplos redactados por mí (no copiados del bundle)

### `SKILL.md` válido para Bob
```
.bob/skills/pr-security-review/SKILL.md
```
```markdown
---
name: pr-security-review
description: Use when the user asks to review a pull request for security issues — walks through auth checks, input validation, and secrets handling before approval.
metadata:
  argument-hint: "<pr-number>"
---

# PR Security Review

1. Ask for the PR number if not given (`ask_followup_question`).
2. Use `execute_command` to fetch the diff (`gh pr diff <n>`).
3. Check, in order: authentication/authorization changes, unvalidated input reaching a
   sink, and any secret or credential committed in plaintext.
4. Report findings as a bullet list with file:line references. Do not approve or merge —
   only report.
```
(Carpeta = nombre canónico `pr-security-review`, cumple el regex; frontmatter mínimo
`name`+`description` como recomienda la guía; sin `metadata.disable-model-invocation`, así que
queda auto-invocable **y** disponible como `/pr-security-review`.)

### `mcp.json` válido para Bob
```
.bob/mcp.json
```
```json
{
  "mcpServers": {
    "internal-issue-tracker": {
      "command": "npx",
      "args": ["-y", "@example/mcp-issue-tracker"],
      "env": {
        "ISSUE_TRACKER_TOKEN": "REEMPLAZAR_CON_TOKEN_REAL"
      },
      "timeout": 30000,
      "alwaysAllow": ["list_issues"],
      "groups": ["default"]
    },
    "hosted-search": {
      "url": "https://mcp.example.com/mcp",
      "oauth": {
        "scope": "search.read"
      },
      "timeout": 60000
    }
  }
}
```
(Primer server: local/stdio, dependencia `npx` ya validada, secreto en `env` — con advertencia
al usuario de que queda en texto plano. Segundo: remoto/HTTP con bloque `oauth` sin `clientId`
explícito, para que Bob intente registro dinámico + flujo de navegador.)

---

## Resumen — compatibilidad con el ecosistema de Claude Code (15 líneas)

1. **Skills**: alto grado de compatibilidad estructural. Bob lee directamente `.claude/skills/*/SKILL.md` como directorio global y de workspace — una skill de Claude Code se **descubre** tal cual.
2. El frontmatter mínimo (`name`+`description`) es el mismo concepto en ambos; Bob agrega su propio namespace `metadata.*` (`user-invocable`, `disable-model-invocation`, `argument-hint`) que Claude Code no usa igual, pero son opcionales — una skill simple de Claude Code corre sin cambios.
3. Diferencia real: el nombre canónico en Bob es **siempre el directorio**, no el campo `name` del frontmatter (en Claude Code el nombre también deriva del directorio, así que esto coincide en la práctica).
4. La regla de nombre (`^[a-z0-9]+(-[a-z0-9]+)*$`, máx 64) es equivalente a la de Claude Code; una skill con nombre válido en un ecosistema es válida en el otro.
5. Precedencia de scope (workspace > global > builtin) es análoga a Claude Code.
6. **Subagentes**: NO son compatibles 1:1. Claude Code define subagentes en Markdown con frontmatter en `.claude/agents/`; Bob usa **`.bob/agents/*.md`** con un esquema de campos distinto (`groups`, `allowTools`/`denyTools`, `allowForkContext`, cuerpo dividido en `systemPrompt`+`outputConstraints`). Un archivo de agente de Claude Code no se carga desde `.claude/agents/` en Bob (no se encontró ese glob en el loader de agentes).
7. Bob solo trae **un** preset built-in (`explore`, solo lectura); todo lo demás requiere `.bob/agents/*.md` propios de workspace (no hay agentes custom globales).
8. Ambos productos prohíben anidar subagentes y corren llamadas paralelas de spawn en el mismo turno — mismo diseño conceptual, distinta tool (`spawn_subagent` vs. `Task`).
9. **MCP**: alta compatibilidad de formato. `mcp.json` de Bob es esencialmente superset del de Claude Code: mismos campos núcleo (`command`/`args`/`env`/`url`/`headers`/`timeout`), Bob agrega `groups`, `disabledTools`, `alwaysAllow`, `oauth`. Una config MCP simple de Claude Code (stdio con command+args+env) funciona sin editar en Bob.
10. El nombrado de tools MCP en el prompt es **idéntico**: `mcp__<server>__<tool>` en ambos — un hook o prompt que matchea ese patrón en Claude Code sirve igual en Bob.
11. Elicitation y OAuth remoto están implementados de forma nativa en Bob (formularios generados desde JSON Schema, descubrimiento OAuth con refresh automático) — funcionalidad equivalente a la de Claude Code para servidores conformes al spec MCP.
12. Recursos y prompts de servidor: mismo concepto MCP estándar, expuesto con nombres de tool propios de Bob (`read_mcp_resource`, comandos generados desde prompts) — no son nombres de tool intercambiables con los de Claude Code, pero el servidor MCP en sí no necesita cambios.
13. **Modos** (`custom_modes.yaml`) no tienen equivalente directo en Claude Code (que usa "modes"/permisos distintos) — no son portables entre ecosistemas.
14. **Hooks**: eventos parecidos en espíritu (`SessionStart`, `PreToolUse`, `PostToolUse`, `Stop`) pero Bob no soporta `${CLAUDE_PROJECT_DIR}` ni el mismo nombre de evento para todo (le falta un evento explícito de "UserPromptSubmit" con el mismo contrato exacto) — requieren adaptación, no son copy-paste.
15. **Conclusión**: skills y `mcp.json` migran de Claude Code a Bob casi sin tocar nada; subagentes, modos y hooks comparten el modelo mental pero usan esquemas de archivo propios de Bob y necesitan reescritura.
