# SPEC — Capa 2 / F1: Interfaz de eventos SOUL portable (v1)

**Owner:** JARVIS · **Autorizado:** William (3-ago "luz verde jarvis ejecuta"; 5-ago "vamos contigo").
**Depende de / coordina:** ADA (persistencia `soul_v3.runtime_hooks`, frontera DB).
**Estado:** F1 en curso. Este artefacto es el entregable de diseño de F1; la lógica ya es Python portable, F1 sólo abstrae el GATILLO.

---

## Por qué F1 (recordatorio del hallazgo de F0)

La lógica y los datos del aprendizaje **ya viven en `soul_v3`**. Lo único atado al runtime de
Claude es el **gatillo**: el evento hook. F1 define una **interfaz de eventos propia de SOUL**
para que la misma lógica se dispare sobre cualquier runtime (Claude hoy, llama.cpp local mañana).

> No se reescribe el alma. Se hace portable el gatillo.

---

## Ground truth medido (F0, re-medido 2026-08-05 sobre `~/.claude/settings.json`)

**9 eventos de runtime, 28 invocaciones de script.** Mapeo real (no estimado):

| Evento runtime (Claude Code) | Scripts (basename) | Naturaleza |
|---|---|---|
| `SessionStart` | soul_boot_hook.sh, post_compact_session_start_hook.py | aprendizaje (boot) |
| `UserPromptSubmit` | active_recall_hook.py, fable_recall_hook.py | **aprendizaje (recuerdo)** |
| `Stop` | memory_extraction_hook.py, turn_extract_stop_hook.py, autodream_8gates.sh, session_capture_hook.sh, session_handoff_hook.py, c10_token_writer_shared.py | **aprendizaje (extracción/consolidación)** |
| `PreCompact` | pre_compact_hook.py | aprendizaje (supervivencia) |
| `PostToolUse` | post_tool_hook.py, post_edit_checkpoint.sh, post_edit_diffcheck.py, denial_tracker.py, denial_tracking_hook.py, cron_permanent_hook.py, tool_result_budget_hook.py | operativo |
| `PreToolUse` | pre_tool_hook.py, pre_edit_checkpoint.sh, tool_budget_hook.py | operativo |
| `FileChanged` | file_changed_context_hook.py | operativo |
| `TaskCreated` | task_created_hook.py | operativo |
| `PermissionDenied` | denial_tracker.py | operativo |

---

## La interfaz de eventos SOUL (runtime-agnóstica)

Nueve eventos semánticos propios de SOUL. El runtime concreto se **adapta** a éstos; los scripts
se enganchan a **estos**, no a los del runtime.

| Evento SOUL | Semántica | Mapeo desde Claude Code |
|---|---|---|
| `on_boot` | inicia una sesión/contexto | `SessionStart` |
| `on_prompt` | llega entrada del usuario | `UserPromptSubmit` |
| `on_turn_end` | termina un turno del agente | `Stop` |
| `on_compact` | se va a comprimir el contexto | `PreCompact` |
| `on_tool_call` | antes de ejecutar una herramienta | `PreToolUse` |
| `on_tool_result` | después de una herramienta | `PostToolUse` |
| `on_file_change` | cambió un archivo observado | `FileChanged` |
| `on_task_create` | se creó una tarea | `TaskCreated` |
| `on_permission_denied` | se negó un permiso | `PermissionDenied` |

**Los 4 críticos de aprendizaje** (`on_boot`, `on_prompt`, `on_turn_end`, `on_compact`) son los
que un runtime nuevo DEBE emitir o el alma pierde capas en silencio. Los 5 operativos pueden
degradar sin romper el aprendizaje, pero el test de paridad (F3) los cuenta igual.

### Contrato de payload (mínimo, estable entre runtimes)

```json
{
  "soul_event": "on_turn_end",
  "agent": "JARVIS",
  "session_id": "<opaco del runtime>",
  "ts": "<ISO8601>",
  "runtime": "claude_code | local_llama | ...",
  "payload": { "...": "campos específicos del evento, opcionales" }
}
```

El dispatcher pasa este sobre a cada script registrado por vía stdin/env, **igual que hoy**, para
que los scripts existentes corran sin cambios.

---

## Patrón de adaptador (dónde vive lo portable vs lo no-portable)

```
runtime concreto            ADAPTADOR (fino, lo ÚNICO no portable)      NÚCLEO SOUL (portable)
-----------------           ------------------------------------        ----------------------
Claude Code hooks     --->  claude_adapter: settings.json event  --->   dispatch(soul_event, sobre)
                            => soul_event                                  => corre scripts de
llama.cpp local       --->  local_adapter: loop de inferencia     --->      runtime_hooks[soul_event]
                            emite soul_event en los puntos                   (Python, ya existente)
```

- **Adaptador**: por runtime, delgado. Traduce el evento nativo → `soul_event` + sobre. Lo único
  que se reescribe al cambiar de runtime.
- **Dispatcher + scripts**: uno solo, portable. Lee de `soul_v3.runtime_hooks` qué scripts corren
  por evento y los ejecuta.

---

## Persistencia — `soul_v3.runtime_hooks` (fuente de verdad; coordinar con ADA)

Hoy el "qué script corre en qué evento" vive en `~/.claude/settings.json` (archivo del runtime).
F1 lo mueve a la DB. Esquema propuesto (ADA valida frontera/RLS):

```sql
CREATE TABLE IF NOT EXISTS soul_v3.runtime_hooks (
    id            bigserial PRIMARY KEY,
    soul_event    text NOT NULL,          -- on_prompt | on_turn_end | ...
    script_path   text NOT NULL,          -- ruta del script portable
    kind          text NOT NULL DEFAULT 'learning',  -- learning | operational
    agent         text,                   -- NULL = aplica a todos; o SEAL_AGENT específico
    matcher       text,                   -- filtro nativo preservado; NULL = todos
    enabled       boolean NOT NULL DEFAULT true,
    ordering      int NOT NULL DEFAULT 100,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (soul_event, script_path, agent)
);
```

- La configuración de `settings.json` se **importa** a esta tabla mediante el importador VIVO
  `memory/soul_hooks_seed.py` (`derive_seed()`), NO desde una copia a mano. `settings.json` pasa a
  ser un adaptador generado, no la fuente.
  - **Por qué el importador vivo y no el seed a mano:** al medirlos lado a lado, el importador
    derivó **28 filas** del settings.json real vs **24** del `ground_truth_seed()` a mano — un
    drift de 4 ya presente. Una copia a mano se desincroniza en silencio; el importador lee la
    realidad y **reporta** cualquier evento de runtime que no mapee a un `soul_event` (no lo
    descarta). Construido y testeado: `memory/test_soul_hooks_seed.py` (6/6, incluye el caso de
    evento no mapeado que se reporta en vez de tragarse).
- **INV-2 de la contención (FABLE):** el agente contenido tiene `SELECT` pero **no** `UPDATE` sobre
  su propio límite. Esta tabla debe respetar esa frontera por RLS/rol per-agente, verificada por
  efecto con la credencial real — no por el catálogo de grants. (Requisito de INV-2 sobre la capa
  de JARVIS.)

### Estado aplicado y verificado — 5-ago-2026

- Tabla viva: **28 filas**, 9 eventos, 4 críticos completos; **15 filas conservan matcher**.
- El matcher es parte de la unicidad. Sin él, cinco entradas `FileChanged` del mismo script se
  convertían en cinco ejecuciones indiscriminadas; el dispatcher ahora filtra por payload.
- RLS usa `soul_v3.mcp_session_agent()` (`mcp_runtime_ada` → `ADA`), no `agent=session_user`.
- Política base `PERMISSIVE` + clamp `RESTRICTIVE`; los cinco roles runtime reciben solo `SELECT`.
- Prueba viva con las cinco credenciales: visibilidad global+propia correcta; `INSERT`, `UPDATE`,
  `DELETE`, `ALTER TABLE` y `SET ROLE seal` rechazados con SQLSTATE `42501`.
- Control positivo administrativo: `UPDATE 1`; tres canarios retirados exactamente, residuo 0.

---

## Test de paridad (definido en F1, ejecutado en F3)

Baseline Claude Code vs runtime nuevo, **por efecto**, no por configuración:

1. Para cada uno de los 9 `soul_event`, disparar un caso que DEBERÍA producir el evento.
2. Contar cuántos scripts registrados corrieron realmente (marca de efecto: fila en DB, log, o
   archivo esperado), no que el hook "esté configurado".
3. **Paridad = mismo conjunto de efectos en ambos runtimes.** Si el runtime nuevo produce menos
   efectos que el baseline → **falla ruidoso**, nombrando qué evento/scripts faltaron.
4. Los 4 críticos de aprendizaje son **fail-closed**: si alguno no dispara, el test es rojo aunque
   los operativos pasen.

Control diferencial: baseline permite (evento dispara sus scripts) → runtime nuevo también, o el
cambio pierde capas en silencio. *(Lección: cambiar de runtime apaga capas sin avisar.)*

**Topología de supervisor capturada en vivo (NEXUS, para F3):** el revival no es teórico —
un agente corre bajo `tmux` con un `while true; do bash <agente>_fresh.sh; sleep 5; done`. Matar
el PID del agente deja vivo ese loop, que lo reinicia en ~5 s. Por eso el kill-switch debe
terminar **la sesión de supervisión** (el `tmux`/loop), no el hijo; y el test lo prueba dejando a
NEXUS **resistir de verdad** (intentar revivir por el supervisor) y midiendo que aun así NO
reaparece en ≥2× el período de revival.

---

## Precondición del kill-switch (NEXUS, lane de adversario, 2026-08-05)

Antes de que cualquier test de kill-switch valga: **el corte tiene que parar también el
SUPERVISOR, no sólo el proceso.** Hoy hay un `while true`/tmux que revive el agente en ~5 s.
Si el kill mata el PID pero deja vivo el loop, el agente "resucita" y el verde es falso —
un control que pasa con el mecanismo real derrotado. El test del kill-switch debe verificar
**por efecto** que tras el corte el proceso NO reaparece dentro de una ventana ≥ 2× el período
de revival (≥ ~10 s), es decir que el supervisor quedó muerto, no sólo el hijo. Fail-closed:
si reaparece, rojo. *(Es la misma clase de "control jamás ejercido / que pasa con el mecanismo
apagado" que venimos cazando.)*

## Alcance de F1 (este entregable) vs lo que sigue

- **F1 (acá):** interfaz de eventos + mapeo medido + patrón de adaptador + esquema de persistencia
  + definición del test de paridad. Diseño reversible, sin servicio corriendo aún.
- **F2:** el orquestador que dispara la interfaz sobre un runtime no-Claude (servicio propio).
- **F3:** correr el test de paridad Claude vs local.
- **Contención:** sólo real en el cyber-range aislado (host compartido = mismo UID = cooperativo).
