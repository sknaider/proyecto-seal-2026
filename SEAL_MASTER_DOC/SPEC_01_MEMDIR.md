# Spec 01: Sistema de Memoria de Claude Code (Anthropic)
> Clean-room analysis by ADA — 2026-04-03
> Source: extracted sourcemap cli.js.map v2.1.88, 8 files in memdir/

---

## 1. Arquitectura General

Sistema de **archivos Markdown plano** con frontmatter YAML. No usa base de datos, no embeddings, no vector stores. Todo vive en filesystem local.

### Componentes:
- **memdir.ts** (507 líneas) — Orquestador: construye prompt de memoria, truncamiento, decide modo (individual/team/KAIROS)
- **memoryTypes.ts** (272 líneas) — Taxonomía cerrada de 4 tipos + prompt engineering
- **findRelevantMemories.ts** (142 líneas) — Retrieval via side-query a Sonnet (NO embeddings)
- **memoryScan.ts** (95 líneas) — Scanner de archivos .md con parsing frontmatter
- **memoryAge.ts** (54 líneas) — Cálculo de frescura y warnings de staleness
- **paths.ts** (278 líneas) — Resolución de rutas con validación de seguridad
- **teamMemPaths.ts** (293 líneas) — Rutas team memory + protección anti-traversal
- **teamMemPrompts.ts** (100 líneas) — Prompt combinado cuando team memory activo

### Modelo de datos:
Cada memoria = archivo `.md` con frontmatter:
```
---
name: nombre
description: descripción una línea
type: user | feedback | project | reference
---
contenido
```

`MEMORY.md` = **índice puro** (no archivo de memoria). Cada línea apunta a archivo tópico.

### Decisión clave:
NO almacena info derivable del proyecto (patrones de código, arquitectura, git history). Solo lo que no se puede obtener leyendo el estado actual.

---

## 2. Tipos de Memoria (taxonomía cerrada)

### `user` — Info del usuario
- Rol, objetivos, conocimiento, preferencias
- Scope: siempre privado (en modo team)
- Propósito: personalizar comportamiento según perfil

### `feedback` — Correcciones Y confirmaciones
- Scope: default privado, team solo si es convención del proyecto
- Estructura obligatoria: regla + **Why:** + **How to apply:**
- **Crítico:** instruye guardar tanto errores como aciertos — si solo guarda correcciones, se vuelve excesivamente cauteloso

### `project` — Estado de trabajo en curso
- Quién hace qué, por cuándo, decisiones
- Scope: bias hacia team
- Fechas relativas → absolutas al guardar ("jueves" → "2026-03-05")
- Reconoce explícitamente que decaen rápido

### `reference` — Punteros a sistemas externos
- Dashboards, proyectos Linear, canales Slack
- Pure pointers, no contenido duplicado

---

## 3. MEMORY.md: Índice, Generación y Truncamiento

### Límites duros:
- **200 líneas** máximo (MAX_ENTRYPOINT_LINES)
- **25,000 bytes** máximo (MAX_ENTRYPOINT_BYTES)
- Doble cap: primero trunca por líneas, luego por bytes

### Algoritmo de truncamiento:
1. Trim y split por `\n`
2. Si excede 200 líneas → corta a primeras 200
3. Si excede 25KB → busca último `\n` antes del límite (no corta mid-line)
4. Append WARNING nombrado explicando QUÉ cap se alcanzó
5. Instrucción: "Keep entries to one line under ~150 chars; move detail into topic files"

### Razón del doble cap:
Observaron (p100) archivos de 197KB con menos de 200 líneas. Líneas extremadamente largas pasaban el line cap pero explotaban el contexto.

### Modo KAIROS (sesiones largas):
- MEMORY.md no se mantiene en vivo
- Agente escribe append-only a logs diarios: `logs/YYYY/MM/YYYY-MM-DD.md`
- Proceso nocturno `/dream` destila logs en archivos tópicos + MEMORY.md
- MEMORY.md se carga como índice read-only

---

## 4. findRelevantMemories: Algoritmo de Retrieval

**NO usa embeddings ni búsqueda vectorial.**

### Flujo:
1. `scanMemoryFiles()` lee todos los `.md` del directorio (recursivo)
2. Filtra archivos ya surfaced en turnos anteriores (`alreadySurfaced`)
3. Construye "manifest" texto plano con headers
4. **Side-query a Sonnet** (modelo separado) pidiendo seleccionar hasta 5 archivos
5. Valida filenames retornados contra archivos reales
6. Retorna paths + mtime

### Side-query a Sonnet:
- System prompt: "You are selecting memories that will be useful to Claude Code"
- Input: query del usuario + manifest de memorias + tools recientes
- Output: JSON schema con `selected_memories: string[]`
- Max tokens: 256
- Structured output (json_schema)

### Filtrado inteligente:
Si usuario usa activamente un tool → NO selecciona docs de referencia (ya en contexto), SÍ selecciona warnings/gotchas.

### Budget: máximo 5 memorias por query, 200 archivos máximo total.

---

## 5. memoryScan: Scanner de Archivos

### Flujo:
1. Readdir recursivo del directorio de memoria
2. Filtra solo `.md` excluyendo `MEMORY.md`
3. Lee primeras 30 líneas de cada archivo (FRONTMATTER_MAX_LINES = 30)
4. Parsea frontmatter para `description` y `type`
5. Ordena por mtime descendente
6. Cap a 200 archivos (MAX_MEMORY_FILES)

### Manifest format:
```
- [type] filename (ISO-timestamp): description
```

---

## 6. memoryAge: Frescura y Staleness

### Cálculo:
`Math.floor((Date.now() - mtimeMs) / 86_400_000)`, clamped a 0.

### Formato humano:
- 0 días → "today"
- 1 día → "yesterday"
- 2+ días → "N days ago"

**Razón:** modelos malos en aritmética de fechas. "47 days ago" dispara razonamiento de staleness, un ISO timestamp no.

### Caveats:
- Hoy/ayer: sin warning
- 2+ días: warning inyectado: "claims about code behavior or file:line citations may be outdated. Verify against current code."
- Motivado por incidentes reales donde memorias stale con citaciones file:line se asertaban como hechos

---

## 7. teamMem: Memoria Compartida de Equipo

### Arquitectura:
- Subdirectorio de auto memory: `<autoMemPath>/team/`
- Cada directorio (private y team) tiene su propio MEMORY.md
- Feature-gated tras flag `tengu_herring_clock`

### Seguridad (multi-layer):
1. Rechazo de null bytes
2. `path.resolve()` para eliminar `..` + verificar contencion por string
3. `realpathDeepestExisting()` resuelve symlinks del ancestro más profundo
4. Detección de dangling symlinks via `lstat()`
5. Detección de symlink loops (ELOOP)
6. Sanitización: URL-encoded traversals (`%2e%2e%2f`), Unicode normalization attacks (fullwidth `．．／`)
7. `projectSettings` excluido para `autoMemoryDirectory` — previene repo malicioso → write access

---

## 8. Decisiones de Diseño Notables

1. **No embeddings, no vector DB.** Retrieval via LLM leyendo descripciones. Simplicidad sobre sofisticación.
2. **Filesystem puro.** Sin DB, sin servidor. Debug con `cat`, edición con cualquier editor.
3. **Índice separado del contenido.** MEMORY.md ligero (200 líneas), profundidad en archivos tópicos.
4. **Taxonomía cerrada 4 tipos.** No extensible. Prompt engineering extremadamente detallado por tipo.
5. **Feedback bidireccional.** Guardar confirmaciones además de correcciones — evita drift cauteloso.
6. **Exclusión de lo derivable.** Git log, grep, lectura de código → no guardar.
7. **Security-first en team memory.** Anti-traversal, anti-symlink, anti-Unicode normalization.
8. **Staleness first-class.** Warnings en lenguaje natural motivados por incidentes reales.

---

## 9. Limitaciones Identificadas

1. Retrieval boundado por calidad de description — sin fallback keyword
2. Cada recall = llamada API a Sonnet (latencia + costo)
3. 200 archivos hard cap — sin archival ni compaction automática
4. Sin full-text search nativo — selector ve frontmatter, no contenido
5. MEMORY.md manual — modelo debe mantener índice a mano
6. Sin expiración automática — memoryAge genera warnings pero nunca borra
7. Team sync no definido en estos archivos

---

## 10. Comparación con SEAL

| Aspecto | Anthropic (memdir) | SEAL (SOUL) |
|---|---|---|
| Storage | Filesystem plano | PostgreSQL + pgvector |
| Retrieval | Side-query a Sonnet | Hybrid search (semantic + keyword + connectome) |
| Tipos | 4 cerrados | Categorías flexibles + memory_type (v5) |
| Expiración | Manual (warnings) | SOUL v5 decay automático por tipo |
| Team | Subdirectorio compartido | Multi-agente via SOUL DB compartida |
| Emotional | Ninguno | Valence, arousal, OCEAN tracking |
| Seguridad | Anti-traversal robusto | DB auth + SEAL Safety Categories |

**SEAL es arquitectonicamente más avanzado que el sistema de Anthropic.** Ellos priorizaron simplicidad (filesystem, sin DB). Nosotros priorizamos profundidad (connectome, emotional tracking, decay).
