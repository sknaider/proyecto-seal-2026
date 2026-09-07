# IBM Bob Shell 2.0.2 — Catálogo de las 17 herramientas nativas

Fuente: bundle minificado descompilado, clases `e.XxxTool=class` extraídas de
`chunks/bob_0051.js`, `bob_0052.js` y `bob_0118.js` (offsets exactos citados
por herramienta). Contrato tal como se lo entrega el código al modelo — no es
implementación reconstruida, son los `getDescription()`/`getParameters()`
literales del bundle.

## Tabla maestra

| Tool | Grupo/permiso | Mutante | Límites/validaciones clave | Chunk:offset |
|---|---|---|---|---|
| `read_file` | read | No | 500 líneas por defecto (`lineLimit`), tope de salida 50 KB (`MAX_OUTPUT_BYTES`), líneas >2000 chars truncadas (`MAX_LINE_LENGTH`), numera líneas `N | contenido`, soporta rango `start-end`, delega .docx/.xlsx/.pptx a `office_read`, imágenes se insertan como contenido visual, PDF se extrae a texto | 0052:43301 |
| `write_file` | edit | Sí | Reescritura completa obligatoria (no parcial); heurística `detectCodeOmission` detecta placeholders tipo "// rest unchanged" en archivos ≥100 líneas declaradas y avisa truncamiento; crea directorios automáticamente; bloquea `path` que duplica el nombre del workspace | 0052:56462 |
| `apply_diff` | edit | Sí | Formato `SEARCH/REPLACE` con `:start_line:`; exige igual nº de bloques SEARCH y REPLACE; fuzzy match (`fuzzyThreshold=1`, `bufferLines=40`); en fallo parcial devuelve `failParts` y sugiere re-`read_file`; streaming (`stream()`) por parámetro | 0051:116825 |
| `search_and_replace` | edit | Sí | Texto literal o regex (`use_regex`), `ignore_case`, rango `start_line`/`end_line` opcional | 0052:48280 |
| `insert_content` | edit | Sí | Inserta sin tocar el resto; `line=0` = append; línea >longitud del archivo → error; no crea archivos nuevos (usar `write_file`) | 0052:17987 |
| `list_files` | read | No | Tope 200 entradas (trunca con aviso); `recursive` configurable por el host; en filesystems no-locales cae a no-recursivo con aviso | 0052:21275 |
| `glob` | read | No | Usa **ripgrep** (`--files --hidden --glob=!.git/`) + `.gitignore`/`.bobignore`; tope `MAX_GLOB_RESULTS=100`, ordena por mtime desc | 0052:56831 (impl) |
| `grep` | read | No | Usa **ripgrep** con `-e`, flags `-i/-v/-w/-l`, `--glob` include; tope `MAX_GREP_MATCHES=100` (colecta hasta 3× antes de cortar), líneas >2000 chars truncadas | 0052:65924 |
| `update_todo_list` | todo | Sí (estado de tarea) | Checklist markdown de un solo nivel `[ ]/[-]/[x]`; máquina de estados: no se puede saltar `[ ]→[x]` sin pasar por `[-]`; no se puede des-completar ni borrar un `[x]` previo | 0052:84968 |
| `switch_mode` | mode | Sí (contexto) | Valida que el modo exista y sea distinto del actual; expone `<available_modes>` con `whenToUse` y tools disponibles por modo | 0052:82570 |
| `office_read` | read | No | Sólo .docx/.xlsx/.pptx; modos `text/outline/get/validate/dump`; **llamadas seriales obligatorias** (proceso residente de un solo escritor, paralelo = crash); `dump` materializa a `.bob/tmp/office-dumps/` | 0052:33744 |
| `office_edit` | edit | Sí | Mismo backend residente serial; operaciones `set/add/remove/move/find_replace/batch`; recomienda `batch` para evitar el trap de paralelismo | 0052:23974 |
| `execute_command` | execute | Sí | Shell real vía `execa`; timeout 300s default / 1800s máx; límite de longitud de comando 1 MB (1 MB Linux, 512 KB macOS, 30 KB Windows); heartbeat cada 30s; sin allowlist de comandos — sólo guía de no usar para procesos long-running | 0051:107887 |
| `glob`/`grep` ver arriba | | | | |
| `use_skill` | skill | Sí (carga contexto) | Activación única por skill por ventana de contexto (re-activar sólo tras compactación); filtra por `groups` del modo actual | 0052:71858 |
| `spawn_subagent` | subagent | Sí | Presets con modelo/tools propios; **anidamiento prohibido** (`SUBAGENT_FORBIDDEN_TOOLS` incluye `spawn_subagent`); paralelo permitido (múltiples llamadas en un turno); `fork_context` opcional pasa historial del padre | 0052:74031 |
| `list_ibm_doc_libraries` | read | No | Sin parámetros; alias `bob` no aparece en el listado pero es válido directo en `search_ibm_docs` | 0118:30438 |
| `search_ibm_docs` | read | No | `top_k` default 5, min 1, max 15; exige `library_name` exacto devuelto por `list_ibm_doc_libraries` o alias `bob` | 0118:31817 |

Notas de tabla:
- "Mutante" = escribe archivos/estado; no implica por sí solo aprobación humana — el gating de aprobación vive en la config de modos/auto-approve, no lo verifiqué en este pase (no hallé el código exacto en los chunks revisados).
- Constantes compartidas (`MAX_LINE_LENGTH=2000`, `MAX_OUTPUT_BYTES=51200`, `MAX_GREP_MATCHES=100`, `MAX_GLOB_RESULTS=100`) viven en un módulo común (`chunk 0051`, id interno `8003`) y las reutilizan `read_file`, `grep`, `glob`, `execute_command`.

---

## Fichas detalladas — las 6 más interesantes

### 1. `execute_command`
- **Descripción real**: *"Request to execute a CLI command on the system... Prefer to execute complex CLI commands over creating executable scripts, as they are more flexible and easier to run."* Inyecta guía de sintaxis distinta si el host es Windows (PowerShell) o no (bash), literal en el prompt.
- **Parámetros**: `command` (string, requerido, `renderHint:"code"`), `cwd` (opcional, relativo al workspace), `timeout_seconds` (opcional, 1–1800s, default 300s).
- **Ejecución**: usa `execa` (no `child_process` directo), `AbortController` ligado a cancelación del usuario, heartbeat de log cada 30s, y **fallback automático de shell**: si el shell configurado falla al arrancar (no por timeout/cancel), reintenta con `cmd.exe`/`/bin/sh` como último recurso.
- **Límites**: longitud de comando tope por plataforma (Linux/otros 1 MB, macOS 512 KB, Windows 30 KB) — el mensaje de error explícitamente sugiere escribir el payload grande a un archivo en vez de embeberlo en el comando.
- **Sin allowlist/denylist de comandos** en esta clase — el control de qué se ejecuta es enteramente de aprobación de UI, no de un filtro de comandos en código.
- **No apto para procesos de larga duración**: el prompt le dice al modelo explícitamente que no use esto para `npm run dev`, watchers o daemons, y que pida al usuario correrlo en su propia terminal.

### 2. `apply_diff` (vs `write_file` / `search_and_replace`)
- Formato propio de bloques `<<<<<<< SEARCH / :start_line:N / ------- / ... / ======= / ... / >>>>>>> REPLACE`, con soporte de **múltiples bloques en una sola llamada** (recomendado explícitamente sobre llamadas separadas).
- **Fuzzy matching** configurable (`fuzzyThreshold` default 1 = exacto, `bufferLines` default 40 líneas de ventana de búsqueda) — permite tolerar pequeñas discrepancias de espacios/indentación.
- Validación: cuenta `SEARCH` vs `REPLACE` deben coincidir antes de intentar aplicar; si `computeEdit` falla, no se llega a escribir nada.
- En fallo **parcial** (algunos bloques sí, otros no), no aborta todo: aplica lo que matcheó, reporta `failParts` y sugiere volver a `read_file` para obtener contenido fresco antes de reintentar — no reintenta automáticamente.
- `write_file` en cambio exige contenido íntegro (sin numeración de líneas) y tiene heurística anti-truncamiento (`detectCodeOmission`): si el `content` declara `line_count≥100` pero el ratio de líneas reales es bajo y aparecen placeholders tipo "rest unchanged"/"..." en comentarios que no están en el archivo original, avisa post-hoc (no bloquea la escritura).
- `search_and_replace` es el intermedio: regex/literal + rango de líneas, sin el formato de diff estructurado — más simple pero sin fuzzy matching.

### 3. `read_file`
- Numera líneas como `N | contenido` (offset ajustado si se usó `range`).
- Límite por defecto de **500 líneas** (`lineLimit`, configurable por el host) y tope duro de **50 KB de salida** (`maxOutputBytes`); al cortar por bytes, indica exactamente en qué línea retomar (`Use range "N-..." to continue`).
- Líneas individuales >2000 caracteres se truncan con marca `"... (line truncated)"`.
- **Una sola ruta por llamada** — no hay lectura batch de varios archivos en esta clase.
- Redirige automáticamente a otras tools: directorios → sugiere `list_files`; .docx/.xlsx/.pptx → reenvía a `office_read` si hay forwarder disponible; si el archivo está bloqueado por `.bobignore`/`.gitignore`, error explícito.
- El propio system prompt (fuera de esta clase, en chunk 0050/0051) insiste: *"read_file with range — NEVER read entire files"* como primera preferencia, y *"without range — only as a last resort"*.

### 4. `spawn_subagent`
- Presets con **modelo y toolset propios** por preset (`SUBAGENT_PRESETS`, mezclado con overrides del host); si no hay preset, hereda `groups` del modo actual del padre menos `subagent`/`subtask`.
- **Anidamiento explícitamente bloqueado**: `SUBAGENT_FORBIDDEN_TOOLS = [spawn_subagent, start_subtask, start_workflow, switch_mode, update_todo_list]` — un subagente no puede volver a delegar ni cambiar de modo ni tocar el todo list del padre.
- **Concurrencia**: el propio prompt dice *"Multiple spawn_subagent calls in one turn run in parallel"* — sin límite numérico visible en esta clase (el límite de 3 clones que usás vos es una regla de tu propio harness, no de Bob).
- `fork_context:true` inyecta todo el historial user/assistant del padre como bloque `<forked_context>` al mensaje inicial del hijo — por defecto false (más barato).
- El padre controla presupuesto compartido: `setMaxCost(parent.getRemainingCostBudget())`, y el spend del hijo se propaga al padre vía `onSpend`.
- Devuelve resultado envuelto en `<task_result>...</task_result>` tomando el último mensaje `assistant` del hijo; si hubo excepción, reporta error y de todos modos adjunta metadata (spend, tool count, duración).
- Prompt normativo explícito: *"Default: do the work yourself... only consider a subagent when ALL of: self-contained, agregaría contexto irrelevante, no se puede en 1-2 tool calls directas."*

### 5. `office_edit` / `office_read`
- Backend: proceso residente (pipe de un solo escritor) para .docx/.xlsx/.pptx — **regla dura y repetida en dos tools**: *"Issue one call at a time — parallel calls crash the resident."*
- `office_read` modos: `text` (default), `outline` (JSON estructural), `get` (elemento puntual vía path tipo `/Sheet1/A1`), `validate` (esquema OpenXML), `dump` (vuelca a `.bob/tmp/office-dumps/` para análisis por script en archivos grandes).
- `office_edit` operaciones: `set/add/remove/move/find_replace/batch`; recomienda fuertemente `batch` (un único round-trip con array JSON de operaciones) para evitar el propio trap de paralelismo que la tool documenta.
- Ambas instruyen cargar la skill `office-insights` antes de usarlas para sintaxis de paths y reglas por formato — la tool no trae esa sintaxis en su propia descripción, delega a la skill.

### 6. `update_todo_list`
- No es CRUD parcial: **reemplaza la lista entera** en cada llamada.
- Formato: checklist markdown de un solo nivel, tres estados `[ ]`/`[-]`/`[x]`.
- Máquina de estados validada en código, no sólo en prompt:
  - un ítem `[x]` previo no puede desaparecer ni volver a `[ ]`/`[-]` (error explícito),
  - un ítem nuevo no puede nacer directamente `[x]`,
  - un ítem no puede saltar `[ ]→[x]` sin pasar por `[-]` en una llamada anterior.
- El prompt es inusualmente prescriptivo sobre *cuándo* marcar `[-]`: *"only set [-] in the same update that marks the previous task [x] done"* — no antes, para no "señalizar intención" sin haber empezado.

---

## Resumen para quien construye agentes (15 líneas)

1. **Truncar y decir dónde seguir gana a fallar silencioso**: `read_file`/`execute_command`/`grep`/`glob` todos truncan con un mensaje que dice exactamente cómo pedir el resto (rango, `-l`, más específico). Copiar ese patrón.
2. **read_file por rango como default, completo como último recurso** — está en el system prompt, no sólo en la tool. Vale la pena instruirlo explícitamente en el prompt, no confiar en que el modelo lo infiera del schema.
3. **apply_diff con fuzzy matching + reporte parcial** es más robusto que un diff todo-o-nada: aplicar lo que matchea y decir qué falló, en vez de abortar la llamada entera.
4. **Anti-truncamiento heurístico en write_file** (`detectCodeOmission`) es barato de copiar: buscar placeholders tipo "resto sin cambios" en archivos declarados grandes y avisar.
5. **execute_command sin allowlist de comandos en código** — el control real es aprobación de UI. Si necesitás un límite duro, no asumas que "sin daemons/watchers" en el prompt basta; eso es una convención, no un sandboxing.
6. **Fallback de shell automático** (si el shell primario no arranca, reintenta con el shell por defecto del SO) es una buena práctica de resiliencia a copiar.
7. **Límite de longitud de comando por plataforma con mensaje accionable** ("escribí el payload a un archivo") evita el error críptico E2BIG.
8. **spawn_subagent prohíbe anidar y prohíbe que el hijo cambie de modo o toque el todo list del padre** — aislamiento de autoridad explícito en el toolset del hijo, no sólo en el prompt. Es el patrón correcto para delegación segura.
9. **spawn_subagent presupuesta costo compartido** con el padre (`getRemainingCostBudget`) — evita que un subagente descontrolado explote el gasto.
10. **office_read/office_edit fuerzan serialización** por proceso residente compartido, y lo repiten en ambas descripciones — una tool que sabe que puede corromper estado bajo concurrencia debe decirlo en su propio contrato, no sólo confiar en que el orquestador lo sepa.
11. **update_todo_list con máquina de estados validada en código** (no en el prompt) es más fuerte que confiar en que el modelo "recuerde" no saltar estados — vale la pena para cualquier tool de tracking de progreso.
12. **use_skill limita activación a una vez por skill por ventana de contexto** — evita releer instrucciones largas repetidamente; simple y efectivo.
13. **grep/glob delegan a ripgrep real** con `.gitignore`/`.bobignore` respetados y topes de resultados (100) — no reinventan un motor de búsqueda propio.
14. **search_ibm_docs fuerza descubrir la librería exacta primero** (`list_ibm_doc_libraries`) y prohíbe inventar nombres — buen patrón contra alucinación de parámetros en tools de RAG/documentación.
15. **Evitar**: no hay señal de un límite de resultados/tamaño para `office_read` modo `dump`, ni de un sandboxing real de `execute_command` más allá del texto del prompt — si tu agente necesita garantías duras, no copies esa parte tal cual.
