# SPEC — Actualización de Absorción Hermes: v0.11 → v0.21 (JARVIS, 1-sep-2026)

> **Actualiza** `agents/ADA/spec_hermes_absorption_20260428.md` (v0.11) y se apoya en
> la **línea base fijada por NEXUS** en `HERMES_ABSORPTION_CHECKLIST.md`.
> Orden de William (1-sep): *"analiza a fondo y actualiza el spec de hermes que tenemos"*.

## 0. Anclaje (medido, no de memoria)

```text
BASELINE de absorción   hermes-agent v0.11.0 · commit df51ad7 · 28-abr-2026
ESTADO tras el pull     hermes-agent v0.21.0 · commit 180291162 · 2026-09-02
delta                   git log df51ad7..HEAD  ->  26.959 commits · 3.689 feat()
                        10.859 archivos cambiados · +2.67M / -318k líneas · 15 releases
```
**Método (de FABLE):** el análisis a fondo NO es leer 17.724 archivos — es `git log df51ad7..HEAD`.
La lista de cambios ES el análisis. `df51ad7` sigue siendo ancestro de HEAD, así que el rango es reproducible.

## 1. Qué teníamos absorbido (baseline v0.11, honesto)

Reimplementado NATIVO en SEAL (0 deps de Hermes, con tests) — de `HERMES_ABSORPTION_CHECKLIST.md`:
Signal/gateway, Spotify/YouTube skills, y el **modelo de mensajes** de la TUI (`tools/tui/app.py`, 288 líneas, visor curses).
**Lo que NO se absorbió:** la interfaz real de Hermes (`cli.py` REPL 11.455 líneas + `ui-tui/` React+Ink 3 MB).
Portamos el transcript+input, no la UI ni el agente interactivo.

## 2. Capacidades NUEVAS desde el baseline (v0.11 → v0.21)

### 2.1 Subsistema de ESTADO/BÚSQUEDA — `hermes_state_*` (ALTA relevancia SOUL)
Nuevo `SessionDB` modular: `hermes_state_portability` (export/import de sesiones),
`hermes_state_registry` (registro compartido process-wide, #90837),
`hermes_state_search` (**FTS + trigram + CJK** sobre mensajes), `hermes_state_schema`
(DDL/reconciliación de columnas + FTS).
> **Paralelo directo con el bug de recall que diagnosticamos HOY:** Hermes construyó una
> capa de búsqueda FTS/trigram madura para sesiones. Vale estudiarla contra nuestro
> `active_recall`/hybrid (similitud degenerada + piso `importance>=7` amputando el léxico).

### 2.2 Compaction al commit boundary (relevancia SOUL: continuidad)
`feat(compaction)`: el system prompt **se reconstruye en el límite de commit** — las
actualizaciones "por fin llegan a sesiones de larga vida" (#98426).
> Paralelo con nuestro dolor de post-compactación / `boot_context`. Diseño a mirar.

### 2.3 `evals/` — framework de evaluación (relevancia: nuestro gate cognitivo)
Nuevo dir `evals/`: `browser_use`, `compaction`, `readtool`, `session_search_schema`.
> Paralelo con el corpus cognitivo v2 + el gate autoritativo que curé hoy.

### 2.4 Skills — ecosistema maduro
`optional-skills` catálogo con install de un click; `skills-hub` con contenido en vivo desde upstream;
`skill_manage.operations[]` atómico con rollback cross-skill (#97295); `/plan` graduado a comando built-in;
**`grill-me`/`plan-interrogation`** (entrevista adversarial del plan antes de codear) y `decision-questionnaire`.
> `grill-me` es notablemente parecido a nuestra cultura de revisor-adversarial (FABLE sobre mi curación hoy).

### 2.5 Providers — refactor grande
Nuevos proveedores LLM (Alibaba CN, Nebius Token Factory, Ramp Router/router.com, tokenplan),
picker curado por variante, User-Agent Hermes-Agent en Router.

### 2.6 MCP — `optional-mcps/`
Docenas de servidores MCP (airtable, asana, atlassian, aws-knowledge, canva, cloudflare, circleci, clickup…);
MCP OAuth de escritorio contra backends remotos (relay de callback).

### 2.7 Otros
`apps/`, `native/`, `locales/` (i18n), `hermes_bootstrap.py`, `hermes_startup_watchdog.py`,
`registration_lifecycle.py`, `kanban` (export/import de board como archivo portable),
web search Tavily, voice-bubble transcode universal, y un **`SOUL.md`** propio (su system prompt:
"be direct, match length to the ask, no filler" — irónicamente alineado con las reglas de William).

## 3. Recomendación de absorción (para decisión de William)

```text
PRIORIDAD ALTA (estudiar YA, tocan bugs/frentes vivos)
  - hermes_state_search (FTS/trigram)  -> contra nuestro recall roto
  - compaction al commit boundary       -> contra nuestra continuidad
  - evals/                              -> contra el gate cognitivo v2

MEDIA (valor claro, sin urgencia)
  - grill-me / plan-interrogation        -> formalizar revisor-adversarial
  - optional-mcps + MCP OAuth remoto     -> ampliar ecosistema de tools
  - state portability (export/import)    -> respaldo/migración de sesiones

BAJA / YA CUBIERTO o no-nativo por diseño
  - providers picker (tenemos routing propio) · ui-tui (decisión aparte de William:
    correr original vs portar nativo) · locales/kanban/desktop (no es nuestro foco)
```

## 3.5 Barra de "ABSORBIDO" para el paso 3 (de FABLE — corrige el error de abril)

En abril se declaró absorción con tests unitarios verdes, y **6 de 7 módulos nunca se
conectaron** (medido hoy por NEXUS). Para no repetirlo, "absorbido" exige las TRES:

```text
ABSORBIDO =  código propio + path + test verde        (regla de William, abril)
          +  AL MENOS UN LLAMADOR REAL fuera de sus tests   (lo que faltó en abril)
          +  un caso que William pueda correr en 30 s y ver el resultado  (= DEMOSTRABLE)
```
Sin llamador real, "funciona" y "se usa" no son lo mismo. Ningún ítem de §3 se marca
absorbido hasta cumplir las tres.

## 4. Estado y límites de este análisis
- Delta contado por comando (medido). Los "paralelos SOUL" son señales de dónde mirar,
  **no** afirmaciones de que el diseño de Hermes sea mejor — eso exige leer cada módulo.
- No se ejecutó Hermes v0.21 (agente que llama LLMs; requiere config/keys; correrlo va al lab aislado).
- La absorción concreta de cualquier ítem de §3 es un frente nuevo que decide William.
