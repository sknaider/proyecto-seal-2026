# IBM Bob Shell 2.0.2 — arquitectura de prompts (ingeniería inversa del bundle)

Fuente: `chunks/bob_00NN.js` (extraído del bundle minificado) y `literals.txt` (corpus de literales con offsets). Todas las citas están entrecomilladas y localizadas por offset/chunk. Producto legítimamente licenciado, análisis de diseño únicamente.

## 1. El system prompt del agente principal

El ensamblado real vive en `BobTask.buildSystemPromptParts()` / `getSystemPrompt()` (chunk 50, offset ~5785541). Cada sección es una función pura que devuelve un bloque XML ya envuelto en su tag, y `getSystemPrompt()` las concatena con `\n\n` **en este orden exacto**:

```js
[roleDefinition, antiHallucination, engineeringDiscipline, toolUseGuidelines,
 markdownRules, contextualInstructions, baseRules, skills, customInstructions,
 projectRules, environment, subtaskCompletion?]
```

| # | Tag XML real | Función fuente | Palabras aprox. | Regla más notable (cita) |
|---|---|---|---|---|
| 1 | `<role_definition>` | `wrapInXmlTag(mode.roleDefinition,"role_definition")` | 20–55 (varía por modo) | Es literalmente el `roleDefinition` del modo activo — ver tabla de modos abajo |
| 2 | `<investigate_before_answering>` | `getAntiHallucinationSection()` | 55 | *"Never speculate about code you have not opened... give grounded and hallucination-free answers."* |
| 3 | `<engineering_discipline>` | `getEngineeringDisciplineSection()` | 60 | *"Produce the minimal change that solves the problem. Do not add features, refactors, or abstractions beyond what was asked."* |
| 4 | `<tool_use>` | `getToolUseGuidelinesSection()` | 90 | *"Tools can be called in parallel when independent, but avoid running operations together that have dependencies."* + prioriza herramientas nativas de archivo sobre shell |
| 5 | `<markdown_rules>` | `markdownFormattingSection()` | 35 | Todo link/referencia de código debe ser `[texto](ruta:línea)` clicable; Mermaid se renderiza nativo |
| 6 | `<auto_appended_context>` | `getContextualInstructionsSection()` | 65 | Explica el bloque `<environment_details>`/`<external_changes>` que el sistema inyecta en cada mensaje de usuario; ordena chequearlo antes de editar |
| 7 | `<base_rules>` | `getRulesSection()` | 110 | *"Do not stop when implementation looks complete... Before reporting final completion, run the relevant validation."* Prioriza `apply_diff`/`search_and_replace`/`insert_content` sobre reescribir archivo entero |
| 8 | `<available_skills>` | `getSkillsPrompt(mode)` | variable (1 línea por skill) | Solo lista skills habilitadas para el modo/entorno; *"Only call use_skill for skills listed here"* |
| 9 | `<user_custom_instructions>` | `addCustomInstructions()` | variable | Envuelve instrucciones globales + del modo dadas por el usuario; incluye preferencia de idioma si está seteada |
| 10 | `<project_rules>` | `formatFileRulesSection()` | variable | *"take precedence over your training defaults... do not revert to your defaults as the conversation grows longer"*; jerarquía: reglas de workspace > reglas globales, reglas de modo > reglas comunes |
| 11 | `<environment_info>` / `## Workspace Info` | `formatEnvironmentInfo()` | variable | Workspace activo, plataforma/shell/arch; si es un playground temporal instruye organizar en subcarpetas |
| — | *(condicional)* `## Subtask Completion` | inline en `getSystemPrompt()` | 45 | Solo si `taskType==="subtask"`: *"you MUST call the end_subtask tool... Do not end your response without calling end_subtask."* |

Nota de nomenclatura: el equipo que encargó la tarea listaba 11 tags esperados incluyendo `available_modes`, pero **`available_modes` no es una sección fija del prompt** — es un fragmento que la propia *tool* `switch_mode` inyecta vía `getSystemPromptPart()` (ver §2), condicionado a qué modos están disponibles. Del mismo modo, otras tools (p. ej. `search_ibm_docs`, `spawn_subagent`) añaden sus propios bloques de instrucciones vía el mismo mecanismo — son "secciones de tool", no del núcleo de 11.

## 2. Modos incorporados

`DEFAULT_MODES` (chunk 51, offset ~5889828) define exactamente **3 modos built-in**: `agent`, `plan`, `ask`. No se halló ningún modo custom "Z Architect" ni "IBM i" en el bundle — el soporte IBM i/Z/COBOL existe como **tool** (`search_ibm_docs`), no como modo.

| slug | name | whenToUse (resumen) | groups | notas |
|---|---|---|---|---|
| `agent` | Agent | *"write, modify, or refactor code... implementing features, fixing bugs, creating new files"* | `read, edit, execute, browser, mcp, skill, todo, artifact, subagent, mode` | Modo por defecto, sin restricciones de archivo |
| `plan` | Plan | *"plan, design, or strategize before implementation... breaking down complex problems"* | `read, edit, browser, mcp, skill, todo, subagent, mode` | `restrictions: edit solo *.md/*.txt`; `allowedSubagents:["explore"]`; obliga `use_skill("create-plan")` antes de responder |
| `ask` | Ask | *"the user is asking about Bob itself... general technical questions... without making code changes"* | (sin `edit`/`execute` — solo lectura) | Read-only reforzado: *"decline clearly and tell the user to switch to Agent mode"* si piden escribir/ejecutar; obliga `search_ibm_docs` antes de responder preguntas sobre el propio Bob |

`roleDefinition` de cada modo (citas cortas):
- Agent: *"You are Bob, a highly skilled software engineer with extensive knowledge in many programming languages, frameworks, design patterns, and best practices."*
- Plan: *"an experienced technical leader who is inquisitive and an excellent planner"*, cuyo objetivo es usar la skill `create-plan`.
- Ask: *"an AI coding partner and product built by IBM. You are knowledgeable about... the Bob product."*

El tool `switch_mode` construye el bloque `<available_modes>` dinámicamente (chunk offset ~5999541) listando solo modos con `whenToUse` definido y no ocultos, con las tools habilitadas por modo. Regla explícita de uso: *"Stay in your current mode unless there is an explicit, compelling reason to switch. When in doubt, do not switch."*

## 3. Clasificador barato de intención (telemetría, no ruteo)

Prompt completo localizado en chunk 51/122 (offset ~14134543, función `classifyIntent`/`OOu`). Se ejecuta con `openai/gpt-oss-20b`, `temperature:0, maxTokens:500`, **solo en el primer turno de una tarea nueva** (`getMessageCount(e)===0`) y solo si `telemetry.isEnabled()`.

Categorías (9, letra→propósito):
- **A** Feature Development, **B** Code Explanation, **C** Refactoring, **D** Bug Detection & Fixing, **E** Test Generation, **F** Documentation Tasks, **G** DevOps/CI, **H** Specs & Planning, **I** Other (ack, continuación, intención ambigua).

Reglas de decisión: 10 reglas numeradas evaluadas **en orden, gana la primera que matchea** (p. ej. Docker/Terraform/K8s → G antes que cualquier otra). Distinciones clave citadas: *"C vs A: Refactoring (C) = same behavior after, just cleaner. Feature (A) = behavior or functionality changes."* Input: primeros y últimos 1000 caracteres del mensaje (`truncatePrompt`, `ibn=1000`), extraídos de dentro de `<user_query>` si existe. Salida: una sola letra A–I vía regex `[A-I]`, sin explicación.

**Uso del resultado:** no afecta el enrutamiento ni el modelo del agente — se adjunta como propiedad `requestType` al evento de telemetría `TASK_CREATED` (analítica de producto pura, con `try/catch` silencioso si falla).

## 4. Otros prompts de sistema secundarios

| Prompt | Dónde vive | Disparador | Detalle |
|---|---|---|---|
| **Compactación de contexto** (`COMPACTION_PROMPT`) | chunk 122, offset ~14088480 | `shouldCompact(tokens, max, 0.8)` → se dispara al 80% del context window (o `compactionThresholdPercent`, default 90% en `HarnessSession`) | Plantilla con 4 secciones fijas: `## Goal / ## Instructions / ## Discoveries / ## Accomplished / ## Relevant files`. Admite `customPrompt` del usuario, apendeado como *"Additional instructions from the user:"*. El resumen generado reemplaza mensajes viejos preservando el/los últimos mensajes de usuario reales (`replaceMessagesWithSummary`). |
| **Analizador de seguridad de comandos** | chunk 51, offset ~5817518 | Antes de ejecutar `execute_command`, si `securityEnabled` | Dos capas: (1) regex heurística instantánea contra ofuscación de shell (parameter expansion, `<<<`, extglob) → bloquea sin LLM; (2) si el comando es largo (>5000 chars) revisa el resto contra 3 regex de pipe-to-shell; (3) llamada `generateStructuredObject` con prompt de 5 categorías de amenaza (credenciales, exfiltración, RCE, destructivo, escalada de privilegios), timeout 15s. **Fail-closed**: cualquier error o timeout devuelve `{dangerous:true}`. El prompt aclara contexto para evitar falsos positivos: *"You are NOT a generic malware scanner — you are detecting commands that cause unintended harm BEYOND the user's intent."* Incluye lista extensa de excepciones "NOT dangerous" (npm install, activar venv, `oc login --token`, SSH read-only a hosts Fyre de IBM, etc.). |
| **Subagente "explore"** | chunk 50, offset ~5772163 | Preset fijo dentro de `SUBAGENT_PRESETS` | *"You are a fast codebase exploration agent. Your only job is to find and return relevant code context."* Solo lectura (sin edición), 3 niveles de profundidad (`medium`/`thorough`), prohíbe leer archivos completos salvo último recurso, salida sin prosa: *"Return ONLY: File paths and line numbers... No prose. No suggestions."* |
| **Tool `spawn_subagent` genérico** | chunk 50, offset ~5991046 | Se inyecta como sección extra del prompt (`getSystemPromptPart`) cuando el modo permite `subagent` | *"Default: do the work yourself. Most tasks are faster and cheaper without a subagent."* Lista 3 condiciones que deben cumplirse TODAS antes de usarlo; `fork_context:true` opcional para pasar el historial de conversación al hijo; subagentes **no pueden anidar** subagentes. |
| **Guía "Build MCP Server"** | chunk ~50, offset ~7591234–7598304 (skill embebida `BUILD_MCP_SERVER_CONTENT`) | Se activa como una skill más (`use_skill`) | Guía paso a paso (auth API key vs OAuth, build, registro en `mcp.json`); advierte *"Bob cannot expand `${VAR}` references in mcp.json — values are stored literally."* |
| Título de sesión / mensaje de commit | — | no encontrado | No hay prompt LLM dedicado a generar títulos de tarea ni mensajes de commit; el título de la tarea es simplemente el primer mensaje de usuario (o `title` explícito), y la generación de commits queda a criterio del propio agente en modo Agent usando `execute_command`. |

## 5. Cómo se arma el prompt (mecánica de ensamblado)

1. **Cada sección es una función pura sin estado** (`getXSection()`), evaluada por turno — no hay caching de contenido salvo el hash usado para telemetría de tokens (`contextWindowUsage`, chunk 122).
2. **Condicionalidad real:**
   - `skills`, `customInstructions`, `projectRules`, `environment` pueden ser cadena vacía y se filtran (`.filter(k=>!!k.trim())`) antes del join final — solo aparecen si hay contenido.
   - El bloque `## Subtask Completion` solo se agrega si `taskType==="subtask"`.
   - Reglas de proyecto (`project_rules`) se resuelven por `mode.id`: reglas de modo pisan reglas comunes, reglas de workspace pisan reglas globales — la jerarquía está escrita explícitamente en el propio prompt, no solo en el código.
3. **Secciones "de tool" son un canal aparte:** herramientas individuales (`switch_mode`, `spawn_subagent`, `search_ibm_docs`) exponen `getSystemPromptPart()` y solo se registran si esa tool está habilitada en el modo activo — esto es lo que en la práctica materializa el bloque `<available_modes>`, la guía de subagentes y las instrucciones de documentación IBM. `getSystemPromptParts()` (plural) es la función que efectivamente reúne núcleo + `toolSystemPrompts` para contar tokens por sección (usado en la UI de contexto), confirmando que el prompt real enviado al modelo es núcleo (11 tags) + N bloques de tool, no un prompt monolítico fijo.
4. **Modo determina el propio `roleDefinition`** (tag 1) y qué *tools/skills* existen para calcular el resto — es decir, cambiar de modo re-dispara la construcción completa del prompt (`changeMode()` compara `mode.id` contra el último modo usado y solo reconstruye si cambió).
5. **Idioma:** si el usuario tiene `language` configurado, se antepone un párrafo *"You should always speak and think in the '{idioma}' language unless the user gives you instructions below to do otherwise"* dentro de `user_custom_instructions`.

---

## Resumen para quien construye su propio sistema de agentes (15 líneas)

1. El prompt no es un bloque monolítico: son ~11 funciones puras, cada una envuelta en su propio tag XML, concatenadas en un orden fijo y filtradas si están vacías — fácil de testear y versionar por separado.
2. `role_definition` es lo único que cambia por completo entre modos; el resto de las 10 secciones es compartido por los tres modos.
3. Cada *tool* puede inyectar su propio fragmento de prompt (`getSystemPromptPart()`) condicionado a si está habilitada en el modo actual — así el prompt "available_modes" o las instrucciones de documentación IBM no viven en el núcleo, viven en la tool misma.
4. Disciplina de ingeniería como regla explícita y corta: *"minimal change... every changed line should trace directly to the user's request"* — vale la pena copiarla literal.
5. Regla anti-alucinación: nunca opinar sobre código no leído; forzar lectura antes de afirmar.
6. `base_rules` cierra con un gate de calidad obligatorio: no declarar terminado sin correr lint/tests/build relevantes.
7. Modo `ask` es de solo lectura reforzado por instrucción explícita, no solo por falta de tools — incluso si el usuario insiste, debe declinar y redirigir a Agent.
8. El clasificador barato de intención es puramente analítico (telemetría), corre en un modelo chico (`gpt-oss-20b`), temperatura 0, con reglas de prioridad numeradas y ejemplos — no participa del ruteo del agente.
9. Compactación de contexto usa una plantilla de resumen estructurada de 4 secciones fijas (Goal/Instructions/Discoveries/Accomplished/Files), disparada al 80–90% del context window.
10. El validador de seguridad de comandos es de dos capas: regex instantánea para ofuscación + LLM con 5 categorías de amenaza, **fail-closed** en timeout/error (15s) — decisión de diseño defendible para un ejecutor de shell.
11. El prompt de seguridad invierte el marco de "malware scanner" a "daño no intencionado más allá de la intención del usuario", con listas extensas de falsos positivos conocidos (npm install, venv, tokens OAuth) para no frenar operaciones rutinarias.
12. Subagentes tienen preset de "explorador" de solo lectura con salida sin prosa, forzando formato "solo archivos/líneas/snippet" — reduce contexto devuelto al padre.
13. Regla de oro para delegar a subagentes: "por defecto hacé el trabajo vos mismo", con 3 condiciones obligatorias antes de spawnear uno — antipatrón de over-delegation evitado por prompt, no por código.
14. No hay generación de títulos ni de mensajes de commit vía LLM dedicado — se apoya en el propio agente o en el primer mensaje del usuario; simplicidad deliberada donde no hace falta una llamada extra.
15. Precedencia de reglas está declarada dentro del propio prompt (workspace > global, modo > común) para que el modelo pueda razonar sobre conflictos sin necesitar lógica externa.

Archivo completo: `/tmp/claude-1000/-home-dadito-IA-proyecto-seal/ba53b9c8-12c5-4f76-b1ae-de28b582b00c/scratchpad/bob/re/informe_prompts.md`
