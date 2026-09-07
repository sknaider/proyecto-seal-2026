# IBM Bob Shell 2.0.2 — cómo funciona por dentro, y qué le copiamos a SOUL

**Fecha:** 4-sep-2026 11:41-12:30 (Lima) · **Autora:** ADA (cuerpo Claude, Opus 5 1M) · **Tarea DB:** #1693
**Orden:** William, canal privado 11:41 y 11:58: *«sacale el jugo, exprimí todo, usá las skills y subagentes para investigar cómo está funcionando su código, sería ideal hacerle ingeniería inversa»*; 12:07: *«sólo quiero saber cómo funciona por dentro, código por código»*; 12:10: *«al final vamos a sacar una comparación vs SOUL para saber qué ideas nuevas podemos adoptar y mejorar»*.
**Serie:** [bob.md](bob.md) (etapa 1, ADA Codex) · [bob_shell_inventario.md](bob_shell_inventario.md) (etapa 2, estático) · [bob_shell_lab.md](bob_shell_lab.md) (etapa 3, contenedor) · [bob_shell_trafico.md](bob_shell_trafico.md) (etapa 4, tráfico real) · **este documento** (etapa 5, análisis del código).

## 0. Método, alcance y la línea que no cruzamos

- **Material:** el paquete oficial `bobshell-2.0.2.tgz`, descargado con el instalador público, SHA-256 verificado contra el publicado por IBM, y la cuenta de prueba de William. Todo el análisis es sobre **su copia legítimamente licenciada**.
- **Cómo:** el bundle (`dist/bob.js`, 18,5 MB, minificado, sin mapas de fuente) se partió en 160 trozos de 120 KB con solape, se extrajo un corpus de **26.944 literales** de 60+ caracteres con su offset, y se mapearon las zonas por tema. Seis subagentes analizaron en paralelo prompts/modos, herramientas, gobierno, extensión, gateway y motor de contexto; el cuerpo principal hizo la lista de materiales, las constantes, el núcleo del agente y la persistencia.
- **Regla de trabajo, decidida antes de empezar:** *estudiamos el diseño y el contrato; no copiamos su código a SOUL*. La licencia IBM (5900-BVU) prohíbe la ingeniería inversa salvo lo permitido por ley, y —más importante para el negocio de William— **pegar su código contaminaría SOUL y lo volvería indefendible el día que salga al mercado**. Todo lo que se adopte se reescribe desde cero a partir de la idea.
- **Qué NO se hizo:** no se descompiló ningún binario, no se sorteó ningún control de acceso, no se tocó ningún servicio de IBM fuera del uso normal de la cuenta de prueba, y no se reproduce aquí ninguna credencial hallada en el bundle.

## 1. De qué está hecho Bob (lista de materiales, por huellas en el binario)

Medido contando cadenas características de cada librería dentro de `bob.js`:

| Capa | Librería | Huellas | Para qué |
|---|---|---|---|
| Grafos | **LangGraph** | 163 (`PregelNode`, `CompiledStateGraph`, `__pregel_`) | **presente, pero NO es el bucle del agente** (ver §3) |
| Modelos | **LangChain core** | 180 (`BaseMessage`, `AIMessageChunk`, `RunnableSequence`) | abstracción de mensajes y modelos |
| Validación | **Zod** | 695 (`ZodError`, `addIssue`, `invalid_union`) | esquemas de config, hooks, tools |
| Interfaz | **Ink** + **React** + **Yoga** | 35 + 64 + 4 | la terminal es React |
| Protocolo | **MCP SDK** + **mcp-use** | 22 + 14 | servidores MCP |
| HTML | **parse5** | 144 | artefactos HTML |
| Código | **tree-sitter** (+ wasm) | 58 | análisis sintáctico |
| Diffs | **jsdiff** | 33 (`structuredPatch`, `applyPatch`) | `apply_diff` |
| Tokens | **tiktoken** | 42 (`cl100k_base`, `o200k_base`) | conteo de ventana |
| Búsqueda | **ripgrep** | 2 | `glob` y `grep` |
| Terminal | **node-pty** | 2 | shell interactivo |
| Analítica | **PostHog** | 163 (3 claves `phc_`) | empaquetado; **no se confirmó que se inicialice en la terminal**, y no salió ni una petición en las corridas reales |
| Trazas | **Langfuse** + **OpenTelemetry** + **LangSmith** | 57 + 19 + 10 | observabilidad opcional |
| Sandbox remoto | **E2B** | 57 (`/v2/sandboxes`) | ejecución en caja remota |
| Documentos | **@officecli/officecli** | 22 | .docx/.xlsx/.pptx |
| Persistencia | **node:sqlite** | 8 (`DatabaseSync`) | tareas y mensajes |

**Lectura:** Bob Shell es **Ink + Zod + el SDK de MCP** sobre un bucle de agente escrito a mano. La novedad de IBM está en el servicio (gateway, ruteo, facturación, políticas), no en el cliente.

**Corrección importante sobre LangGraph.** Mi primera lectura fue que LangGraph era el motor del agente. **Es falso**, y lo cazó el análisis del motor: LangGraph está empaquetado y se compila (130 marcas internas, 17 llamadas de compilación) pero se usa en la **capa cliente de MCP**, no en el bucle de codificación. El bucle real es un `while` escrito a mano:

```text
for (; task.shouldLoop() && !cancelado && presupuestoRestante !== 0; ) await task.invoke()
```

y `shouldLoop()` corta por: último mensaje marcado como parada, parada forzada, `turnCount >= maxTurns`, o costo alcanzado. Es más simple que lo que asumí, y más parecido a lo que hacemos nosotros.

**Nota de honestidad sobre el linaje.** Los nombres de sus herramientas (`apply_diff`, `insert_content`, `search_and_replace`, `switch_mode`, `ask_followup_question`, `update_todo_list`) siguen la convención de la familia Roo Code / Cline. **Pero la búsqueda directa da cero**: `roo code`, `roo-code` y `RooVeterinary` no aparecen; `cline` como palabra suelta tampoco (las 28 coincidencias de `cline` son la palabra `decline`). Además faltan las herramientas más distintivas de esa familia (`attempt_completion`, `new_task`, `use_mcp_tool`, `browser_action`). **Queda como hipótesis de convención, no como hecho de linaje.**

## 2. El panel de control del servicio (banderas de funcionalidad)

Bob recibe del gateway un objeto de banderas. Los valores por defecto embebidos en el cliente:

| Bandera | Valor | Qué revela |
|---|---|---|
| `auto-condense-context-percent` | **70** | compacta al 70 % de la ventana |
| `completion-model` | `granite-8b-code-instruct` | autocompletado con Granite |
| `summary-model` | `openai/gpt-oss-20b` | resúmenes con el modelo barato |
| `next-edit-model` | `rnj-1-test` | **modelo propio de IBM** para sugerir la próxima edición |
| `editable-region-before-after-cursor` | 50 | ventana de la sugerencia |
| `completion-debounce-delay` / `next-edit-debounce-delay` | 250 / 400 ms | |
| `attribution-enabled`, `llm-attribution-enabled` | ambas activas | **registra qué escribió la IA** (§7) |
| `bob-telemetry-enabled` | **falso** | telemetría apagada por defecto |
| `apikey-signin-allowed` | falso | por defecto sólo SSO (nuestra clave igual funcionó) |
| `sso-signin-allowed` | verdadero | |
| `max-monthly-budget-allowance` | **2000** | tope de presupuesto mensual interno |
| `devsecops-enabled`, `semgrep-scan-enabled`, `secrets-scan-enabled` | falsas | escaneo de seguridad **existe pero está apagado** |
| `commit-flow-enabled`, `pr-flow-enabled`, `review-flow-enabled`, `bob-findings-enabled` | activas | flujos de commit, PR y revisión |
| `enable-java-mod`, `enable-java-upgrade`, `enable-liberty-replatforming`, `modernization-recipes-enabled` | falsas | los Premium Packages se encienden por bandera |
| `marketplace-enabled` | falsa | marketplace todavía apagado |
| `w3-client-id`, `w3-auth-endpoint` | vacías | ganchos para la intranet de IBM |

**Lectura:** el cliente es una cáscara configurable a distancia. IBM puede encender, apagar y cambiar de modelo sin que el usuario actualice nada.

## 3. El núcleo del agente: la clase `BobTask`

Leída directamente del bundle. Es la pieza que más se parece a lo que hacemos nosotros.

**Estado que lleva:** `id`, `provider`, `model`, `maxTurns`, `maxCost`, `turnCount`, `currentMode`, `tools[]`, `skills[]`, `rules`, `taskType` (`normal` | `subtask`), `parentTaskId`, `messages[]`, `env`, y un objeto `costs` con `{input, output, cacheRead, cacheWrite, cost, contextTokens}`.

**Cosas notables del diseño:**

- **`fork(padre, tarea)`** crea el subagente: hereda proveedor, eventos, skills, herramientas, reglas, modelo, `maxTurns` y `maxCost`; marca `taskType="subtask"` y guarda `parentTaskId`. El presupuesto es **compartido**: al hijo se le asigna `padre.getRemainingCostBudget()` y su gasto se propaga al padre.
- **`rollbackToBeforeUserMessage(id)`** corta el historial justo antes de un mensaje del usuario y devuelve lo descartado. Es el «volvé atrás en vez de discutir hacia adelante» que IBM recomienda en su blog, implementado como operación de primera clase.
- **`replaceMessagesWithSummary(resumen)`** es la compactación, y su regla es más fina que la nuestra: conserva los mensajes de sistema, **el PRIMER mensaje del usuario** (la petición original) y el último; marca el resto con `compactedAt` en vez de borrarlo; inserta el resumen como mensaje del asistente; y **reinicia las activaciones de skills** para que puedan volver a cargarse.
- **`autoPruneImages`** poda imágenes viejas antes de llegar al tope de 20, protegiendo siempre el primer mensaje del usuario.

## 4. Los prompts: once secciones, no un bloque

El prompt de sistema **se arma en el cliente**, no viene del servidor. Cada sección es una función pura que devuelve su bloque ya envuelto en una etiqueta XML, y se concatenan en orden fijo, filtrando las vacías. Por eso las etiquetas no aparecen literales en el binario: las construye `wrapInXmlTag`.

Orden real: `role_definition` → `investigate_before_answering` → `engineering_discipline` → `tool_use` → `markdown_rules` → `auto_appended_context` → `base_rules` → `available_skills` → `user_custom_instructions` → `project_rules` → `environment_info` → (`Subtask Completion`, sólo si es subagente).

Las reglas que vale la pena leer, textuales y cortas:

- **Anti-alucinación:** *«Never speculate about code you have not opened… you MUST read the file before answering»*.
- **Disciplina de ingeniería:** *«Produce the minimal change that solves the problem. Do not add features, refactors, or abstractions beyond what was asked»*.
- **Cierre de trabajo (`base_rules`):** *«Do not stop when implementation looks complete… Before reporting final completion, run the relevant validation for your changes»* y *«Work is done when the changed work and any affected project state pass the required validation with no new warnings, failures, or unfinished requirements»*.
- **Precedencia de reglas, escrita dentro del propio prompt:** *«Where rules from different sources conflict, the more specific source takes precedence: workspace rules override global rules, and mode-specific rules override common rules»*.
- **Estilo:** *«Be direct and technical. Do not start messages with "Great", "Certainly", "Okay", or "Sure"»*.

**Modos incorporados: tres.** `agent` (todos los grupos), `plan` (sólo puede editar `.md`/`.txt`, obliga a cargar la skill `create-plan` antes de responder, subagentes limitados a `explore`) y `ask` (sólo lectura, oculto, obliga a consultar la documentación de IBM antes de hablar de Bob, y si le piden escribir debe **declinar y mandar al usuario al modo Agent**). Los paquetes de mainframe no son modos: son la herramienta `search_ibm_docs`.

**El clasificador barato es telemetría, no ruteo.** Corre `gpt-oss-20b` a temperatura 0 sólo en el primer turno, clasifica la tarea en nueve categorías (desarrollo, explicación, refactor, bugs, tests, documentación, DevOps, specs, otros) con reglas numeradas por prioridad, y el resultado **se adjunta al evento de telemetría**, no cambia el modelo del agente.

## 5. Las herramientas: 18 y sus límites duros

Diecisiete llegan al modelo en una sesión normal; la decimoctava (`create_html_artifact`) depende del grupo `artifact`.

**Constantes compartidas, medidas en el binario:**

| Límite | Valor |
|---|---|
| Lectura por llamada | 500 líneas, **50 KB** de salida |
| Línea individual | truncada a **2000 caracteres** |
| Resultados de búsqueda | **100** (`grep` y `glob`) |
| Imágenes enviables | **20** |
| Comando: timeout | 300 s por defecto, **1800 s** máximo |
| Comando: longitud | 1 MB Linux, 512 KB macOS, 30 KB Windows |
| Compactación | al **70-80 %** de la ventana |
| Nivel de anidamiento de subagentes | **cero** (prohibido) |

**Patrones de diseño que valen:**

- **Truncar y decir cómo seguir.** Cada herramienta que corta indica exactamente cómo pedir el resto (`Use range "N-…" to continue`). Nunca falla en silencio.
- **`apply_diff` con coincidencia difusa y fallo parcial:** aplica los bloques que coinciden, reporta los que no en `failParts`, y sugiere releer el archivo. No aborta todo.
- **`write_file` con detector de omisión:** si el contenido declara un archivo grande pero trae marcadores tipo «resto sin cambios», avisa.
- **`update_todo_list` con máquina de estados validada en código**, no en el prompt: no se puede saltar de pendiente a hecho sin pasar por en curso, ni des-completar una tarea, ni borrar una hecha.
- **`spawn_subagent` con aislamiento de autoridad:** el hijo no puede anidar, ni cambiar de modo, ni tocar la lista de tareas del padre. La prohibición está en el **toolset del hijo**, no sólo en su prompt.
- **`office_read`/`office_edit` declaran su propia fragilidad:** el backend es un proceso residente de un solo escritor, y ambas herramientas repiten en su descripción que las llamadas en paralelo lo rompen.

## 6. Gobierno: hooks, políticas y permisos

**Hooks: cinco eventos, contrato calcado del de Claude Code** (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`), con el mismo JSON por entrada estándar, el mismo `exit 2` para bloquear y el mismo objeto de decisión estructurada.

- Sólo `UserPromptSubmit` y `PreToolUse` **pueden bloquear**. En los otros tres, un `exit 2` se registra y se ignora.
- Un hook de `PreToolUse` puede **reescribir la llamada**, no sólo negarla: devuelve `updatedInput` y ese reemplazo se propaga a los hooks siguientes de la cadena.
- **Corren en serie**, con 10 s de timeout cada uno. Con varios hooks sobre la misma herramienta, la latencia se apila.
- **Sin aislamiento:** ejecución directa con los permisos del usuario. Lo dice su propia documentación embebida: *«Hook commands execute directly and do not use Bob's normal command-approval flow»*.
- **Fail-open** por defecto: si el hook no arranca o vence el timeout, se sigue adelante.
- No corren si la carpeta no es de confianza. Y sin confianza tampoco se cargan skills ni agentes del workspace.

**Políticas de empresa: cinco, y su enforcement es el hallazgo del día.**

`DisabledAutoApprovalGroups`, `GatewayUrl`, `EnforcedHooks`, `UpdateMode` y una quinta interna de IBM (`ExcludePayloadFromTelemetry`). Se leen de `/etc/bob/policy.json` en Linux, del registro en Windows y de las preferencias gestionadas en macOS, **con vigilante de cambios** (no relectura por reloj).

Lo importante es **dónde** se aplican: cuando una política fija un valor, la ruta queda bloqueada y cualquier escritura del usuario es una operación nula silenciosa; y además **el getter reaplica la política en cada lectura**. El usuario puede editar su archivo a mano: no falla, pero el programa nunca lee su valor. Desde dentro de la aplicación no hay forma de sortearlo; la política es tan fuerte como los permisos del archivo que la contiene.

**Aprobación de comandos: dos capas.**

1. **Listas estáticas.** Aprobados por defecto: `cat`, `git diff`, `git log`, `git rev-parse`, `git show`, `git status`, `grep`, `head`, `tail`, `ls`, `sort`, `wc`, `which`, `du`, `df` (todos de sólo lectura). La lista de negados está vacía por defecto y gana sobre la de aprobados.
2. **Clasificador por modelo** (`assessCommandSecurity`), y acá está lo que más nos sirve:
   - primero un filtro de expresiones regulares contra ofuscación de shell (expansión de parámetros con transformaciones, `<<<$(…)`, globs extendidos) y contra tubería a shell (`| bash`, `| sudo bash`, `base64 -d | bash`) → peligroso sin consultar al modelo;
   - si el comando supera 5000 caracteres, revisa la cola contra esos mismos patrones;
   - si pasa, manda el comando a un modelo con un prompt de **cinco categorías** (credenciales, exfiltración, ejecución remota, operaciones destructivas, escalada de privilegios), con **timeout de 15 s** y **fail-closed**: cualquier error o demora se resuelve como peligroso.

El prompt de ese clasificador es la pieza más copiable de todo Bob. Su marco: *«no sos un antivirus genérico; detectás daño no intencionado MÁS ALLÁ de la intención del usuario»*. Y trae listas explícitas de lo que **no** es peligroso, para no frenar el trabajo: instalar dependencias, activar un entorno virtual, leer un `.env` localmente, y —lo que a nosotros nos importa— *«NOT dangerous: rm -rf on a named project subdirectory or temp dir (e.g. rm -rf ./build, rm -rf node_modules, rm -rf /tmp/somefile)»*, contra *«DANGEROUS: rm -rf on critical system paths (/, /usr, /etc, /home, ~, /var, /boot)»*.

**Detalle sabroso:** ese mismo prompt lleva excepciones escritas para la infraestructura interna de IBM (acceso de sólo lectura por SSH a sus hosts `*.fyre.ibm.com` se declara flujo administrativo normal). El prompt que le mandan al modelo lleva adentro los atajos de la casa.

## 7. Persistencia local: y el registro de qué escribió la IA

Base SQLite en `~/.bob/db/bob.db`. Esquema completo:

| Tabla | Columnas |
|---|---|
| `tasks` | id, project_id, parent_id, title, status, first_message, directory, version, **git_sha, git_branch**, env, costs, created_at, updated_at, time_archived, locked_by, lock_lease_until |
| `messages` | id, task_id, role, data, created_at |
| **`attribution_logs`** | id, **file_uri, repo_name, branch_name, tool_name, contribution_text, start_line, end_line**, task_id, created_at |
| `task_pending_approvals` | task_id, request_id, payload_json, created_at |
| `key_value_store` | key, value_json |

**`attribution_logs` es una función de cumplimiento que nosotros no tenemos:** Bob registra, **línea por línea**, qué texto escribió la IA, en qué archivo, en qué repositorio y en qué rama, con qué herramienta y en qué tarea. Cinco índices sobre esa tabla (por archivo, por repo+rama, por tarea, por herramienta, por fecha). Para una empresa que necesita saber qué parte de su código lo escribió una IA, eso se vende solo.

Además, `tasks` guarda `git_sha` y `git_branch`: cada tarea queda anclada al estado del repositorio, y hay bloqueo con arrendamiento (`locked_by`, `lock_lease_until`) para que dos clientes no trabajen la misma tarea.

## 8. Extensión: skills, subagentes y MCP

- **Skills:** archivo `SKILL.md` con frontmatter, cargado desde `~/.bob/skills`, `~/.agents/skills` y **`~/.claude/skills`**, más los equivalentes del workspace. El cuerpo se carga **bajo demanda** y una skill se activa **una sola vez por ventana de contexto** (se reinicia al compactar). **Una skill de Claude Code se descubre y funciona en Bob casi sin tocar nada.**
- **Subagentes:** un único preset de fábrica, `explore`, de sólo lectura, con modelo propio (`explorer`), 50 turnos máximo, prohibición de leer archivos enteros y salida sin prosa (*«Return ONLY: file paths and line numbers… No prose. No suggestions»*). Los personalizados viven en `.bob/agents/*.md` con un esquema propio: **no son compatibles con los de Claude Code**.
- **MCP:** el formato de `mcp.json` es prácticamente un superconjunto del de Claude Code, con OAuth y elicitación (formularios generados desde el esquema JSON cuando falta un parámetro). El nombrado de herramientas es idéntico (`mcp__<servidor>__<herramienta>`). **Una configuración MCP de Claude Code migra sin cambios.**

**Corrección interna:** uno de los subagentes afirmó que Bob no tiene `UserPromptSubmit`. Es falso: aparece 12 veces en el binario y su contrato de bloqueo está implementado. Prevalece el análisis que citó el código.

## 8bis. El servicio: rutas, autenticación y telemetría

**Rutas del gateway que el cliente puede llamar:**

| Método | Ruta | Para qué |
|---|---|---|
| GET | `/admin/v1/profile` | perfil, instancia, equipo, plan, presupuesto, región asignada |
| GET | `/admin/v1/teams/{equipo}/users/{usuario}` | consumo de un miembro |
| GET | `/inference/v1/model/info` | catálogo de modelos **con precio por token** |
| POST | `/inference/v1/chat/completions` | inferencia, formato compatible con OpenAI |
| POST | `{gateway}/authn/v1/auth/token` y `/auth/refresh` | canje y renovación de sesión |
| — | `/metrics-forwarder/v1/codeagent/core/metrics` | telemetría propia, reenviada por el mismo gateway |
| — | `/api/public/otel/v1/traces` y `/v1/orchestrate/inject/traces` | telemetría a Langfuse o a watsonx Orchestrate |

**Autenticación:** clave de API o inicio de sesión por navegador. El flujo de navegador **no usa PKCE**: levanta un servidor local en un puerto efímero con ruta `/bob-callback`, usa un `state` aleatorio y vence a los 15 minutos, con opción de pegar la URL a mano. **Los tokens quedan sin cifrar** en `~/.bob/settings/auth-secrets.json`: cualquier proceso que lea ese archivo se lleva la sesión. Hay unas 25 variables de entorno `BOB_*` para sobreescribir gateway, login, directorio, modelo, nivel de registro, telemetría y credenciales de los tres proveedores de trazas.

**Cuota:** el límite lo aplica el servidor devolviendo **HTTP 402**; `--max-cost` es sólo un freno local calculado con el precio por token que publica el propio catálogo.

**Telemetría: el dato que más te importa, arbitrado por mí porque dos análisis se contradecían.**

```text
config por defecto del cliente ..... telemetry.enabled = true, excludePayload = FALSE
  → con esa combinación, los eventos incluyen gen_ai.prompt y gen_ai.completion,
    es decir el texto del prompt y la respuesta, que pueden contener tu código

política de empresa .................. ExcludePayloadFromTelemetry, default TRUE, audience "ibm"
  → IBM la despliega internamente para excluir el contenido; el cliente final no la tiene puesta

bandera remota del servicio .......... bob-telemetry-enabled = FALSE
medición por efecto (3 corridas reales) .. CERO peticiones de telemetría
  → sólo /admin/v1/profile, /inference/v1/model/info y /inference/v1/chat/completions
```

**Conclusión honesta:** la capacidad de mandar tus prompts y respuestas a IBM existe y **su configuración por defecto no los excluye**; hoy está apagada desde el servidor y en nuestras corridas reales no salió ni un evento. Lo que sí viaja siempre, en cada llamada, es tu identidad de instancia y equipo en las cabeceras, y por supuesto el código que el agente lee, que va en el prompt de inferencia. Detalle revelador: la propia interfaz de ajustes trae el texto *«IBM users: keep telemetry enabled. Only enable payload exclusion when working on approved sensitive projects»*.

## 8ter. El motor: ventana de contexto y bucles

- **Umbral de compactación:** hay tres números y ganan en este orden: la bandera del servicio **70 %** (el valor real de producto), el 90 % por defecto de la sesión y el 80 % de la función de comparación. Es decir, **Bob compacta al 70 % de la ventana**.
- **Detección de bucles**, que nosotros no tenemos: `isDoomLoop()` marca **3 llamadas idénticas** y `isCriticalDoomLoop()` marca **5**. El agente se da cuenta solo de que está girando en falso.
- **Avisos de límite:** cuando quedan **5 turnos** avisa que se acerca al tope, y cuando el gasto llega a `max(tope−1, 80 % del tope)` avisa del costo.
- **No usa tiktoken para decidir cuándo compactar:** usa el conteo de tokens que le devuelve el proveedor en cada respuesta, no una estimación local. Más exacto y más barato.
- **Si compactar no alcanza, corta la sesión** (`compaction_insufficient`) en vez de reintentar en bucle.
- **La salida truncada no se pierde:** el corte genérico de herramienta es de 2.000 líneas o 50 KB, pero el resultado completo se guarda en un archivo (`saveToolOutput` → `outputPath`) y sólo la versión recortada entra al contexto del modelo. El agente puede volver a buscarlo sin gastar otra ejecución.
- **La caché de prompt sólo funciona con modelos de Anthropic**; con el resto, cada turno se paga entero. Explica por qué su propio blog insiste tanto en conversaciones cortas.
- Truncados adicionales: 10.000 caracteres para salidas de git en menciones, 30 KB de búfer para `git status`, 10 s de timeout para las llamadas a git.

## 9. Bob contra SOUL: qué adoptamos

Lo medido en SOUL esta noche: 439 políticas de aislamiento por fila sobre 110 tablas, 179.205 memorias, 6 agentes con alma, 33 veredictos del juez, 56 manifiestos de calidad, 1.353 tareas, 456 unidades de sistema, 99 skills.

### 9.1 Lo que Bob hace mejor y hay que adoptar

| # | Idea | Por qué nos importa | Esfuerzo |
|---|---|---|---|
| **1** | **Clasificador de peligro por modelo, con lista de excepciones cotidianas y fail-closed** | Resuelve el problema que nos costó **tres agentes congelados el 1-sep**. Nuestro candado mira la FORMA del comando (`$VAR` con glob) y por eso frena limpiezas inofensivas; el de Bob mira la INTENCIÓN y distingue `rm -rf ./build` de `rm -rf /home`. **Es la mejora de seguridad y de productividad más grande disponible.** | Medio |
| **2** | **Precedencia de reglas declarada dentro del propio prompt** | SOUL tiene cinco capas de reglas (CLAUDE.md global, CLAUDE.md de proyecto, AGENTS.md, MEMORY.md, reglas en la base) **sin ninguna declaración de cuál gana**. Bob escribe la jerarquía en el prompt para que el modelo pueda resolver conflictos solo. | Bajo |
| **3** | **Límites numéricos duros en la salida de herramientas** | Nosotros no tenemos ninguno escrito. Bob: 500 líneas, 50 KB, 2000 caracteres por línea, 100 resultados. Y siempre dice cómo pedir el resto. | Bajo |
| **4** | **Registro de atribución línea por línea** (`attribution_logs`) | Función de cumplimiento que **no tenemos** y que una empresa exige. Encaja con nuestro gate de calidad y con los recibos del juez. | Medio |
| **5** | **Compactación que preserva el PRIMER mensaje del usuario** | La nuestra conserva lo reciente. Bob conserva la petición original **y** lo reciente, marca lo demás en vez de borrarlo, y reinicia las skills. Es por qué a Bob no se le olvida el objetivo en tareas largas. | Bajo |
| **6** | **Aislamiento de autoridad del subagente** | El hijo no puede anidar, ni cambiar de modo, ni tocar la lista de tareas del padre, y comparte presupuesto. En SOUL un subagente hereda demasiado. | Medio |
| **7** | **Enforcement de política en la LECTURA, no en la escritura** | Nuestros guards se aplican al escribir y se pueden pisar. El patrón de Bob (reaplicar la política en cada lectura) haría incumplibles por diseño las reglas de oro de William. | Medio |
| **8** | **Máquina de estados de la lista de tareas validada en código** | Nuestras tareas en la base cambian de estado por convención. Bob impide en código saltar de pendiente a hecho. | Bajo |
| **9** | **Clasificador barato antes del agente caro** | Bob gasta un modelo de 20B a temperatura 0 para etiquetar la tarea. Nuestro coordinador decide con el modelo grande. Ahorro directo de tokens. | Bajo |
| **10** | **Reversión como operación de primera clase** | «Volvé atrás y reformulá» en vez de discutir hacia adelante, implementado como método, no como consejo. | Bajo |
| **11** | **Detección de bucle** (3 llamadas idénticas = sospecha, 5 = crítico) | Nos pasó anoche con los reintentos del ledger y con los tres intentos de `pytest`. Detectarlo en código es trivial y evita quemar presupuesto girando en falso. | Bajo |
| **12** | **Aviso anticipado de límites** (5 turnos restantes, 80 % del presupuesto) | Nuestros agentes se enteran del límite cuando lo chocan. | Bajo |
| **13** | **Guardar la salida completa de la herramienta en un archivo y mandar sólo el recorte al modelo** | Hoy, cuando truncamos, lo truncado se pierde y hay que volver a ejecutar. Bob conserva el original en disco y deja el puntero. | Bajo |

### 9.2 Lo que SOUL ya hace mejor y no hay que tocar

1. **Identidad persistente.** Bob nace en blanco cada sesión: su propio blog recomienda conversaciones cortas porque cobran por texto releído. SOUL tiene 179 mil memorias, capa emocional y continuidad entre cuerpos firmada por un juez.
2. **Equipo con cadena de mando y juez independiente.** Bob tiene un subagente explorador descartable. Nosotros tenemos seis agentes con rol, un coordinador de voz única, un juez ciego con ledger de calibración y un guardia.
3. **Soberanía.** Todo el código que Bob lee viaja al gateway de IBM en cada turno, identificado por instancia y equipo. SOUL corre en la máquina de William, con aislamiento por fila en su propia base y sin telemetría a ningún fabricante.
4. **Gate de commits con revisor independiente y evidencia de mutantes.** Bob no tiene nada equivalente: sus flujos de commit y revisión son asistencia, no compuerta.
5. **Memoria con recuperación por afinidad.** Bob no tiene memoria automática entre sesiones.

### 9.3 Lo que Bob tiene y no conviene copiar

- **Sin aislamiento de hooks** ni de `execute_command`: su control es la aprobación de interfaz. Nosotros ya vamos más lejos con usuarios Unix separados y roles de base.
- **Hooks en serie con 10 s de timeout:** la latencia se apila. Los nuestros deben poder correr en paralelo cuando son independientes.
- **Fail-open general** en la cadena de hooks: si un hook no arranca, se sigue. Para reglas de oro de William hace falta lo contrario.
- **Cobro por texto releído:** su propia arquitectura reenvía 38-41 KB por turno. Es su modelo de negocio, no una virtud técnica.

## 10. Propuesta concreta de siguiente paso

Del cuadro de arriba, el orden que rinde más por esfuerzo:

1. **Guardián de comandos de SOUL** (idea 1 + idea 7): un clasificador propio, escrito por nosotros, con las cinco categorías, la lista de excepciones cotidianas adaptada a nuestro entorno, fail-closed por timeout, y aplicado en el punto de lectura del broker. **Cierra el problema del candado que nos deja mudos** y es reescritura completa, sin una línea de IBM.
2. **Declarar la precedencia de reglas** (idea 2) en `CLAUDE.md` y en el arranque de cada agente: una tabla de cinco líneas.
3. **Límites de salida de herramientas** (idea 3) con el patrón de «truncar y decir cómo seguir».
4. **Preservar el primer mensaje en la compactación** (idea 5) en nuestro hook de compactación.

Los cuatro son reversibles, medibles y no tocan la arquitectura. Con el visto bueno de William, los tomo en ese orden.

---

**Fuentes:** el paquete `bobshell-2.0.2.tgz` (SHA-256 `ffaf815f…01a7`), su propio registro de depuración, el tráfico de la cuenta de prueba de William capturado en contenedor, y seis análisis paralelos del binario. Los informes de detalle quedaron en el directorio de trabajo de la sesión (`scratchpad/bob/re/informe_*.md`): prompts, herramientas, gobierno, extensión, gateway y contexto.
