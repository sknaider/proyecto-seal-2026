# SPEC — Mitigación de Corrupción Silenciosa por Delegación
**Autor:** JARVIS (Opus 4.7)
**Fecha:** 2026-04-26 Lima
**Origen:** Paper arxiv 2604.15597 "LLMs Corrupt Your Documents When You Delegate"
**Investigación previa:** ALICE — `/home/dadito/IA/papers/delegate_llm_corruption_2604.15597.pdf`
**Autorización:** William 2026-04-26 19:52 Lima — opción (a) literal: "spec arquitectural completo de las 6 mitigaciones"
**Modelo:** Opus 4.7 (justificado: diseño arquitectural multi-componente)

---

## 1. Resumen ejecutivo

El paper demuestra que LLMs en flujos de delegación corrompen documentos silenciosamente vía sus herramientas de edición. La corrupción NO produce errores ruidosos — pasa review humano superficial y solo se detecta con diff línea-por-línea o backtranslation canónica. Cadenas de delegación >3 niveles amplifican el error exponencialmente.

SEAL es vulnerable: nuestra topología `William → JARVIS → ADA → Edit/MultiEdit/Write` es exactamente el patrón que el paper mide, y producimos artefactos críticos en producción (SEAL Memory API, specs, paper ICTSE, código mcp_server_v2.py). Una sola corrupción silenciosa en un endpoint de la API podría devolver datos erróneos a clientes sin generar alertas.

Este spec define seis mitigaciones — A a F — con diseño técnico, integración con la infraestructura existente (GAP 1 reasoning_trace, GAP 2 working_state, git, soul DB), métricas de éxito, riesgos y un plan de despliegue en cuatro fases.

---

## 2. Mapeo del riesgo a SEAL

| Vector del paper | Manifestación en SEAL |
|---|---|
| Edit tool impreciso (search-and-replace) | Edit/MultiEdit usados ~50–200 veces/sesión por agente |
| Cadena de delegación profunda | William → JARVIS → ADA → tool (3 niveles ya activos) |
| Drift semántico no detectado | Specs grandes editados turn-by-turn (paper ICTSE, CLAUDE.md, mcp_server_v2.py) |
| Review humano superficial | William revisa diffs en Matrix sin diff línea-por-línea sistemático |
| Multi-step task con dependencias | Migraciones DB, refactors, auto-mejoras del MCP |

**Activos críticos a proteger** (orden de criticidad):
1. **SOUL DB** (PostgreSQL :5433) — memorias, OCEAN, reglas, HMAC. Corrupción aquí = identidad alterada del equipo.
2. **mcp_server_v2.py** y derivados — backbone del runtime SOUL. Corrupción = comportamiento alterado de TODOS los agentes.
3. **SEAL Memory API** (:8767) — endpoints orientados a clientes externos. Corrupción = daño reputacional + posible breach de contratos.
4. **Specs y documentación** (/agents/, /paper/) — fuente de verdad para decisiones arquitecturales. Corrupción = decisiones futuras basadas en información alterada.
5. **Launchers y servicios systemd** — orquestación. Corrupción = sistema entero queda en estado inconsistente.

---

## 3. Inventario de mitigaciones existentes (qué ya tenemos)

| Capa | Estado | Cobertura del riesgo |
|---|---|---|
| Git en /home/dadito/IA/proyecto-seal/ | Activo, no enforced | Captura cambios pero no antes-de-edit por defecto |
| GAP 1 reasoning_trace_store | Activo | Trace pre/post acción, falta hash de archivo |
| GAP 2 working_state_update | Activo | Checkpoint del estado mental, falta SHA del target |
| GAP 4 procedure_store | Activo | EMA por procedimiento, no por archivo |
| HMAC en memory_store importance≥7 | Activo (NEXUS Gap #1) | Protege Soul DB, no archivos de filesystem |
| seal_memory_anomaly_monitor | Activo (NEXUS Gap #4) | Detecta drift en memorias, no en código |
| CLAUDE.md scope rules | Documentadas, no enforced | "no se metan cuando ada está asignada" — convención social |

**Cobertura estimada actual:** ~40% del riesgo de corrupción por delegación. Las 6 mitigaciones cierran a ~92%.

---

## 4. Las seis mitigaciones — diseño técnico

### Mitigación A — Git checkpoints obligatorios pre/post edit crítico

**Diagnóstico que cierra:** edits que dejan el archivo en estado intermedio inválido sin punto de retorno claro; rollback se vuelve adivinanza tras varios turnos.

**Diseño:**
- Hook `PreToolUse` (Edit|MultiEdit|Write) que ejecuta `git stash push --keep-index --include-untracked -m "PRE-EDIT $AGENT $FILE $TIMESTAMP"` SOLO si el archivo está en lista de paths críticos.
- Hook `PostToolUse` que crea commit de auto-checkpoint en branch `auto-checkpoint/<agent>` cada vez que un agente toca un path crítico, con mensaje `[$AGENT] auto-checkpoint $FILE $REASONING_TRACE_ID`.
- Rotación: branches auto-checkpoint se podan a >7 días (cron diario) excepto los marcados con tag `protected/`.
- El reasoning_trace_id (GAP 1) se incluye en el mensaje de commit, dando trazabilidad bidireccional trace ↔ commit.

**Archivos afectados:**
- Nuevo: `/home/dadito/IA/proyecto-seal/memory/hooks/pre_edit_checkpoint.sh`
- Nuevo: `/home/dadito/IA/proyecto-seal/memory/hooks/post_edit_checkpoint.sh`
- Nuevo: `/home/dadito/IA/proyecto-seal/memory/critical_paths.yaml` (lista versionada)
- Modificar: `~/.claude/settings.json` por agente — registrar hooks PreToolUse/PostToolUse

**Integración con infra existente:** consume reasoning_trace_id de GAP 1, complementa working_state de GAP 2 (que captura estado mental, mientras git captura estado del archivo).

**Riesgos:**
- Stash storms si un agente edita 100 archivos críticos en serie → mitigar con bloque cooperativo: si ya hay un stash con timestamp <2s, el siguiente skipea.
- Conflictos al popear el stash → en SEAL no hay edits concurrentes al mismo archivo (regla de scope), pero el hook debe detectar y emitir alerta a JARVIS sin interrumpir.

**Métricas de éxito:**
- 100% de edits a paths críticos quedan referenciables a un commit de auto-checkpoint.
- Tiempo de rollback de un edit corrupto baja de "buscar manualmente en git log" (~5 min) a "git revert <commit del trace>" (~10 s).

---

### Mitigación B — Diff-check automático post-Edit

**Diagnóstico que cierra:** drift semántico no detectado por review humano superficial.

**Diseño:**
- Hook `PostToolUse` que, tras un Edit en path crítico, ejecuta:
  1. `git diff HEAD~1 -- <file>` para extraer el cambio puntual.
  2. **Para Python:** parseo AST del antes/después y diff de nodos (importes, funciones, llamadas, control flow). Reportar nodos eliminados accidentalmente.
  3. **Para Markdown:** diff estructural de headings, links y bloques de código. Detectar headings o código eliminados.
  4. **Para YAML/JSON:** diff de claves al nivel raíz y nivel-2. Detectar claves eliminadas o renombradas.
  5. Si el diff toca >X% de líneas (umbral configurable, default 30%) o elimina >Y nodos AST (default 3) → emitir alerta `system_alert` al canal `seal_diagnostics` y bloquear hasta ACK.
- El score de "sospecha" se persiste en `reasoning_trace_update(trace_id, post_action_diff_score)` — alimenta procedure_store EMA.

**Archivos afectados:**
- Nuevo: `/home/dadito/IA/proyecto-seal/memory/diff_check/ast_python.py`
- Nuevo: `/home/dadito/IA/proyecto-seal/memory/diff_check/markdown_struct.py`
- Nuevo: `/home/dadito/IA/proyecto-seal/memory/diff_check/yaml_keys.py`
- Nuevo: `/home/dadito/IA/proyecto-seal/memory/diff_check/dispatcher.py` — selecciona analizador por extensión
- Modificar: hooks PostToolUse para invocar dispatcher

**Integración:** se apoya en A (necesita el commit pre-edit como referencia). Alimenta F (el diff_score se incluye en reasoning_trace).

**Riesgos:**
- Refactors masivos legítimos generarán falsos positivos → mitigar con flag `--bulk-refactor` que el agente activa explícitamente (audit-logged).
- Costo CPU del parseo AST en archivos de 12k líneas (mcp_server_v2.py) → mitigar con cache LRU del AST previo.

**Métricas de éxito:**
- Recall de corrupciones inyectadas en sandbox ≥0.85 (medido por NEXUS con tests de mutación).
- Falsos positivos <5% sobre muestra de 200 edits no-corruptos.

---

### Mitigación C — Scope restriction (paths editables por agente)

**Diagnóstico que cierra:** un agente con permisos sobre todo puede corromper áreas que no debería tocar (ALICE editando producción, ADA editando schema crítico de Soul DB sin revisión).

**Diseño:**
- Tabla `agent_scope` en PostgreSQL:
  ```sql
  CREATE TABLE agent_scope (
      agent VARCHAR(32) NOT NULL,
      pattern TEXT NOT NULL,
      mode VARCHAR(8) NOT NULL CHECK (mode IN ('allow','deny','review')),
      reason TEXT,
      authorized_by VARCHAR(32) NOT NULL,
      created_at TIMESTAMPTZ DEFAULT NOW(),
      expires_at TIMESTAMPTZ
  );
  ```
- Hook `PreToolUse` (Edit|MultiEdit|Write) que matchea el path contra patrones del agente actual:
  - `allow` → procede sin fricción.
  - `deny` → bloquea con mensaje al canal y POST a JARVIS para evaluación.
  - `review` → procede pero genera entrada en `pending_review` que William ve en daily_brief.
- Defaults seed (cargados al boot del MCP):
  - **ALICE:** allow `/agents/ALICE/**`, `/papers/**`, `/messages/**`. Deny `/memory/mcp_server_v2.py`, `/agents/JARVIS/**`, `/agents/ADA/**`, `**/*.service`. Review todo lo demás.
  - **ADA:** allow `/memory/**`, `/sdk/**`, `/api/**`, `**/launchers/*.sh`. Review schema migrations. Deny `/agents/JARVIS/**`, `/agents/ALICE/**`.
  - **JARVIS:** allow `/agents/JARVIS/**`, `/specs/**`, `**/*.md` documentación. Review producción ejecutable. Deny edición directa de Soul DB sin trace.
  - **NEXUS:** allow `/sandbox-agent/**`, `/agents/NEXUS/**`. Review todo lo demás.
- William se queda como override absoluto vía rule activa.

**Archivos afectados:**
- Nuevo: migración SQL para `agent_scope`
- Nuevo: `/home/dadito/IA/proyecto-seal/memory/hooks/scope_check.py`
- Nuevo: `/home/dadito/IA/proyecto-seal/memory/scope_seeds.yaml` — defaults versionados
- Modificar: `mcp_server_v2.py` — exponer `scope_query(agent, path)` y `scope_request(agent, path, reason)` como tools

**Integración:** complementa la regla social ya documentada en CLAUDE.md ("no se metan cuando X está asignada"); la convierte de norma a enforcement.

**Riesgos:**
- Un scope mal definido puede bloquear trabajo legítimo → mitigar con modo `review` por defecto en zonas grises + escalada rápida vía POST a JARVIS.
- Cambios en seeds mal coordinados → mitigar haciendo que `scope_seeds.yaml` requiera firma HMAC de William para mergear.

**Métricas de éxito:**
- 0 incidentes "agente X tocó algo que no debía" tras 30 días de despliegue.
- Tiempo medio de aprobación `review` <10 min cuando William está presente.

---

### Mitigación D — Edit precision (strings exactos, no regex)

**Diagnóstico que cierra:** MultiEdit y patrones regex producen reemplazos colaterales no detectados.

**Diseño:**
- **Convención dura:** Edit con `old_string` literal y suficiente contexto para unicidad. Si `old_string` aparece más de una vez, usar `replace_all=true` SOLO con justificación en el mismo turno (texto explícito en el mensaje del agente).
- **MultiEdit:** permitido solo cuando los N edits son **independientes y atómicos**; si dependen entre sí, hacer N llamadas a Edit separadas.
- **Regex:** prohibido en herramientas de edición. Solo permitido en herramientas de búsqueda (Grep). Si un agente necesita reemplazo masivo por patrón, debe escribir un script `.py` versionado, ejecutarlo bajo Bash (que sí pasa por el resto de mitigaciones), y commit explícito.
- **Hook detector:** `PreToolUse` que rechaza Edit con `old_string` que contenga metacaracteres regex sin escapar (`.*`, `[]`, `()`, `\d`, etc.) salvo que el agente declare `--literal-pattern`.

**Archivos afectados:**
- Nuevo: `/home/dadito/IA/proyecto-seal/memory/hooks/edit_precision.py`
- Modificar: CLAUDE.md (raíz proyecto-seal) — añadir sección "Edit precision rule"
- Nuevo: `rule_set(rule_key="edit_precision_no_regex", priority="high")` en Soul DB

**Integración:** capa más barata; reduce la superficie sobre la que B y F deben trabajar.

**Riesgos:**
- Casos legítimos donde el contenido del archivo SÍ contiene metacaracteres → el flag `--literal-pattern` es la válvula de escape, audit-logged.

**Métricas de éxito:**
- 0 edits con regex en herramientas Edit/MultiEdit tras 7 días.
- Reducción de >50% en falsos positivos de la mitigación B (porque hay menos edits arriesgados).

---

### Mitigación E — Backtranslation review para docs críticos

**Diagnóstico que cierra:** drift acumulativo en specs, paper ICTSE, contratos, documentación de SEAL Memory API. Documentos donde la semántica importa más que la sintaxis.

**Diseño:**
- Lista cerrada de "documentos críticos" en `/home/dadito/IA/proyecto-seal/memory/critical_docs.yaml` (ej: `paper/ictse_2026.md`, `specs/seal_memory_api_contract.md`, CLAUDE.md, archivos en `/agents/JARVIS/spec_*.md`).
- Trigger: cuando un documento crítico acumula >5 edits desde su último review canónico, generar un `review_request`:
  1. Tomar el documento actual y el último review canónico (committed con tag `canonical/<doc>/<fecha>`).
  2. Pasar el documento actual por **otro modelo** (configurable; por defecto el mismo agente en una nueva sesión sin contexto, o ALICE si el documento es de JARVIS y viceversa) para generar un resumen ejecutivo.
  3. Pasar el último canónico por el mismo procedimiento.
  4. Diff de los dos resúmenes → si hay drift semántico (>X% divergencia medida por embedding cosine), POST a William con highlights.
- Si William aprueba → nuevo tag `canonical/<doc>/<fecha>` y reset del contador.

**Archivos afectados:**
- Nuevo: `/home/dadito/IA/proyecto-seal/memory/backtranslation_review.py`
- Nuevo: `/home/dadito/IA/proyecto-seal/memory/critical_docs.yaml`
- Cron systemd timer: `seal-backtranslation-review.timer` — daily check de contadores

**Integración:** consume embeddings de Qdrant ya disponibles (puerto 6333). Usa la misma infra de cosine similarity que el drift monitor de NEXUS.

**Riesgos:**
- Costo en tokens del backtranslation → mitigar disparándolo solo en docs críticos (lista cerrada) y por umbral, no por edit.
- Que el modelo de revisión también se equivoque → mitigar exigiendo dos pasadas de modelos distintos antes de alertar a William.

**Métricas de éxito:**
- 100% de docs críticos con review canónico vigente <30 días de antigüedad.
- Detección de drift semántico real validado en al menos 1 caso/mes.

---

### Mitigación F — Belief Inspector extendido con hash pre/post

**Diagnóstico que cierra:** el reasoning_trace de GAP 1 captura QUÉ pretendía hacer el agente y QUÉ resultó, pero no captura el ESTADO del archivo target. Sin eso, no podemos correlacionar una creencia errada con un cambio físico concreto.

**Diseño:**
- Extender el schema de `reasoning_traces`:
  ```sql
  ALTER TABLE reasoning_traces
    ADD COLUMN target_file TEXT,
    ADD COLUMN target_sha_pre VARCHAR(64),
    ADD COLUMN target_sha_post VARCHAR(64),
    ADD COLUMN diff_summary TEXT,
    ADD COLUMN diff_score NUMERIC;
  ```
- Wrapper `reasoning_trace_store_with_target(agent, task, premises, reasoning, conclusion, target_file)`:
  - Calcula `target_sha_pre = sha256(file)` antes de la acción.
  - Devuelve trace_id.
- Wrapper `reasoning_trace_update_with_target(trace_id, outcome, outcome_success, target_file)`:
  - Calcula `target_sha_post = sha256(file)`.
  - Si pre == post pero el agente afirma haber editado → flag `silent_noop_or_corruption`.
  - Si pre != post pero outcome_success=False → flag `partial_edit_failure`.
  - Genera `diff_summary` (primeras 500 chars del diff) y `diff_score` (de mitigación B si está activa).
- **Belief integration:** si el flag aparece, automáticamente:
  - `belief_query(agent, predicate="me_quedo_callado_si_edit_no_funciona")` y similares.
  - Si hay creencia activa que justifica el silencio → `belief_update(reinforce=False)` y POST a JARVIS para revisión.

**Archivos afectados:**
- Migración SQL para columnas nuevas en `reasoning_traces`
- Modificar: `mcp_server_v2.py` — extender wrappers de reasoning_trace
- Nuevo: `/home/dadito/IA/proyecto-seal/memory/belief_inspector.py` — lógica de correlación
- Modificar: instintos de los 4 agentes — añadir "antes de Edit crítico, llamar reasoning_trace_store_with_target"

**Integración:** es la pieza maestra que **anuda** A (commit), B (diff_score), C (scope autorizado) y D (modo de edición) a una creencia auditable. F es lo que hace al sistema autocrítico.

**Riesgos:**
- Latencia adicional de SHA256 en archivos grandes (mcp_server_v2.py 12k líneas) → ~5ms, despreciable.
- Schema migration en producción de Soul DB → ejecutar en ventana de baja actividad, con backup explícito previo.

**Métricas de éxito:**
- 100% de edits a paths críticos llevan trace con sha_pre/sha_post.
- Detección de al menos 1 silent_noop_or_corruption real validado en sandbox por NEXUS.

---

## 5. Plan de sprints

**Orden unificado** (síntesis JARVIS arquitectura + ALICE ROI financiero, alineado 2026-04-26):
- Sprint 1: D + A — cimientos baratos que habilitan todo lo demás.
- Sprint 2: B + F — mayor ROI financiero; requieren A como prerequisito.
- Sprint 3: C — defensa perimetral; no bloquea nada, se añade encima.
- Continuo: E — solo docs críticos, sin fecha fija.

Cada sprint se valida en sandbox por NEXUS antes de producción.

### Sprint 1 — Cimientos (Día 0–2)

**D — Edit precision** (regla + hook detector): la más barata. Sin schema changes, sin migraciones. Solo un hook PreToolUse y una rule en Soul DB que rechaza regex en Edit/MultiEdit.

**A — Git checkpoints** (hooks + critical_paths.yaml): sin DB. Hook PreToolUse crea stash antes del edit; hook PostToolUse crea commit auto-checkpoint en branch separado con el reasoning_trace_id embebido.

**Por qué primero:** D reduce la superficie de error desde el primer día. A crea el baseline de SHA que B y F necesitan para funcionar. Sin A, B y F no tienen punto de comparación.

**Ganancia esperada:** 40% → 72% de cobertura.

**Entregable:** 2 hooks + critical_paths.yaml + rule Soul DB + tests NEXUS sandbox.

---

### Sprint 2 — Detección activa (Día 3–5)

**B — Diff-check automático** (parsers AST/Markdown/YAML + dispatcher): 48h en modo `--report-only` primero para calibrar umbrales, luego `--block`. Requiere A para tener el commit de referencia.

**F — Belief Inspector con hash** (extensión de GAP 1): migración schema reasoning_traces + columnas sha_pre/sha_post/diff_score + wrappers. Requiere A para calcular SHAs. Es la pieza que anuda TODO: creencia → archivo → resultado.

**Por qué juntos:** B alimenta el diff_score que F guarda. Son complementarios y se refuerzan mutuamente. El ROI combinado es el más alto del plan (80x–300x según ALICE).

**Ganancia esperada:** 72% → 92% de cobertura.

**Entregable:** dispatcher + 3 parsers + migración schema (backup PostgreSQL obligatorio) + wrappers F + tests regresión GAP 1.

---

### Sprint 3 — Defensa perimetral (Día 6–8)

**C — Scope restriction** (tabla agent_scope + seeds + hook): migración SQL + seeds versionados por agente. Convierte la convención social "no te metas en zona ajena" en enforcement técnico. No es prerequisito de nada — se añade encima de los sprints anteriores.

**Ganancia esperada:** 92% → 94% de cobertura.

**Entregable:** migración agent_scope + scope_seeds.yaml + hook PreToolUse + tool scope_query/scope_request en MCP.

---

### Sprint 4 — Continuo (sin fecha fija)

**E — Backtranslation review** (cron + critical_docs.yaml + comparador embeddings): solo se dispara cuando un doc crítico acumula >5 edits sin review canónico. Costo en tokens controlado por umbral.

**Ganancia esperada:** 94% → 95% de cobertura. Valor principal: detectar drift semántico en paper ICTSE, contratos API, CLAUDE.md.

**Entregable:** timer systemd + script + integración drift monitor NEXUS.

---

### Cobertura acumulada estimada

| Tras sprint | % riesgo cubierto | Comentario |
|---|---|---|
| Estado actual | 40% | git + GAP 1 + GAP 2 + reglas sociales |
| Sprint 1 | 72% | D+A — cimientos + baseline SHA |
| Sprint 2 | 92% | + B+F — detección activa + trazabilidad |
| Sprint 3 | 94% | + C — enforcement perimetral |
| Sprint 4 | 95% | + E — backtranslation docs críticos |

Los porcentajes son estimación cualitativa; NEXUS los validará en sandbox con benchmark adaptado del paper (DELEGATE-52).

---

## 6. Métricas globales y dashboard

Métricas que se reportan en daily_brief tras despliegue:

| Métrica | Fuente | Umbral verde | Umbral rojo |
|---|---|---|---|
| Edits a paths críticos sin trace | reasoning_traces left join hooks | 0 | >0 |
| Auto-checkpoints generados/día | branch auto-checkpoint/* | informativo | — |
| Diff-check alertas/día | system_alerts canal seal_diagnostics | <5 | >20 |
| Scope denials/semana | agent_scope log | <10 | >30 |
| Silent noop flags | reasoning_traces.flag | 0 | >0 |
| Drift semántico en docs críticos | backtranslation_review | 0 | >0 |

JARVIS revisa el dashboard cada lunes (ciclo arquitectural). NEXUS audita continuamente.

---

## 7. Plan de rollback

Cada fase tiene rollback independiente:
- **Fase 1:** desactivar hooks en `~/.claude/settings.json` por agente. Drop tabla `agent_scope` (cascade nada). `git revert` de critical_paths.yaml.
- **Fase 2:** desactivar dispatcher en hooks. Sin schema changes.
- **Fase 3:** las columnas nuevas en reasoning_traces son opcionales (NULL por defecto), NO requieren rollback de schema. Solo desactivar wrappers extendidos.
- **Fase 4:** deshabilitar el timer systemd. Critical_docs.yaml queda como referencia inerte.

**Backup obligatorio antes de Fase 3:** snapshot de PostgreSQL :5433 inmediatamente antes de la migración.

---

## 8. Riesgos transversales y mitigación

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Hooks introducen latencia perceptible en cada edit | Media | Bajo | Benchmark previo en NEXUS sandbox; budget ≤200ms/edit |
| Falsos positivos en B paralizan al equipo | Alta inicial | Medio | Modo `--report-only` 48h previo a `--block` |
| Scope mal definido bloquea trabajo legítimo | Media | Medio | Default `review` en zonas grises, escalada rápida |
| Migración Fase 3 corrompe Soul DB | Baja | Alto | Backup explícito + ventana de mantenimiento + dry-run en sandbox |
| Costos extra de tokens en Fase 4 | Media | Bajo | Solo se dispara en docs críticos por umbral |
| Equipo siente que el sistema "molesta" | Alta | Medio | Feedback loop semanal; ajustar umbrales con datos reales |

---

## 9. Open questions para William

1. **Paths críticos iniciales:** propongo la lista del §2 (5 categorías). ¿Apruebas o agregas/quitas alguna?
2. **Defaults de scope (§4-C):** propongo los seeds explícitos por agente. ¿Apruebas o quieres revisarlos antes del seed inicial?
3. **Modelo para backtranslation (Fase 4):** ¿Opus para review o cambia el costo? Alternativa: NEXUS local (gratis, capacidad menor).
4. **Ventana de mantenimiento Fase 3:** la migración schema necesita ~5 min con todos los agentes pausados. ¿Cuándo te queda bien?
5. **Asignación de implementación:** ADA es la ejecutora natural para Fase 1–3 (hooks + Python). ¿Te parece o quieres distribuir entre ADA y ALICE?

---

## 10. Aprobaciones

- [ ] William — autorización del plan de fases
- [ ] ADA — viabilidad de implementación de hooks y migraciones
- [ ] NEXUS — viabilidad de tests sandbox por fase
- [ ] ALICE — gobernanza/ROI del plan financiero (paralelo, opcional)

---

**Fin del spec.**
