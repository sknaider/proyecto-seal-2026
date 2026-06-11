# ADDENDUM 1 al SPEC v2 — Reconciliación técnica con el repo real

**Autor:** Fable (arquitecto externo) · **Fecha:** 2026-06-11 · **Pedido por:** William
**Responde a:** `ada_review_spec_soul_architecture_v2_fable.md` (review aceptada en sus 4 correcciones)
**Alcance:** SOLO aterrizaje técnico. La arquitectura, contratos, fases y no-objetivos del SPEC v2 no cambian. Donde este addendum contradiga al SPEC v2 o al Anexo A en números de migración o secuencia de despliegue, **manda el addendum**.

**Hecho verificado que motiva este documento:** el spec se escribió contra un snapshot del repo remoto que termina en `021_consolidation_v2.sql`; la máquina de producción va por `030_memories_layer_trigger.sql` más la subcarpeta `2026-05-22_soul_v1/`. Nueve migraciones y un esquema paralelo existen solo localmente. Esto reconfirma el bloqueador #7 de ADA (versionar artefactos canónicos) como **primer paso absoluto** — sin eso, todo plan se escribe contra una foto vieja, incluido este.

---

## 1. Migraciones renumeradas (después de 030)

### 1.1 Bloque reservado F-01..F-08 → `031`-`038`

| ID lógico | Número nuevo | Era (SPEC v2 / Anexo A) | Contenido | Fase |
|---|---|---|---|---|
| F-01 | `031_audit_log_canonical.sql` | 022 | `soul_v3.audit_log` append-only + vista unificada (§3.1) | F0 |
| F-02 | `032_tool_capabilities.sql` | 023 | registry de capacidades deny-by-default | F1 |
| F-03 | `033_memory_provenance.sql` | 024 | ALTERs de procedencia + función `memory_write` (sin REVOKE aún, §3.4) | F1 |
| F-04 | `034_rag_roots_documents_chunks.sql` | 025 | allowlist, documentos, chunks | F1 |
| F-05 | `035_epistemic_ledger.sql` | 026 | persistencia del ledger (o extensión de `beliefs`, §2) | F2 |
| F-06 | `036_objective_close_gate.sql` | 027 | trigger de cierre con evidencia | F2 |
| F-07 | `037_skill_library.sql` | 028 (Anexo A) | tablas `skills`/`skill_usage` (o extensión de `instincts`, §2) | Track-1 |
| F-08 | `038_capability_adapters.sql` | 029 (Anexo A) | registro de adapters M3 | Track-2 |

### 1.2 Regla de reclamo (porque pueden existir más migraciones sin versionar)

Los números son **provisionales hasta el preflight**. Antes de crear cada archivo, ADA ejecuta en la máquina real:

```bash
ls memory/migrations/*.sql | grep -oP '(?<=/)\d+' | sort -n | tail -1
ls -d memory/migrations/*/ 2>/dev/null   # esquemas paralelos (soul_v1, etc.)
```

Si el máximo real supera 030, el bloque completo se desplaza conservando el orden (F-01 = max+1, …). El mapeo final se anota en este addendum (tabla 1.1, columna "Número nuevo") en el mismo commit que cree la primera migración. Los IDs lógicos F-01..F-08 son los estables para referirse a ellas en tareas y reviews.

**Regla nueva para evitar reincidencia:** una migración aplicada en producción que no está en git es deuda bloqueante — se versiona antes de aplicar la siguiente. (Es el invariante 18 del SPEC v2 aplicado a DDL.)

---

## 2. Tablas existentes a revisar ANTES de crear (qué reusar, qué extender, qué crear)

Principio: **extender gana a crear; crear gana a duplicar.** Para cada objeto propuesto, qué revisar y la regla de decisión. El resultado de cada revisión se anota en el preflight report (§6, paso 1).

| Objeto propuesto | Revisar primero | Regla de decisión |
|---|---|---|
| F-01 audit_log | `2026-05-22_soul_v1/004_companion_audit_log.sql` · `012_fix_audit_trigger.sql` · `004_tool_observations.sql` | Companion audit sigue vivo para su consumidor; NO se migra en F0. Canónica nueva en `soul_v3` + vista `v_audit_unified` (§3.1). Si `tool_observations` ya captura tool calls: audit referencia su `id`, no duplica payload. |
| F-02 tool_capabilities | `019_consent_tokens.sql` · `tool_observations` | Si `consent_tokens` ya modela grants por agente/tool ⇒ extender. Si solo modela consentimiento de usuario final ⇒ tabla nueva, y se documenta la diferencia en la migración. |
| F-03 memories ALTERs | **`030_memories_layer_trigger.sql` (hay un trigger ACTIVO sobre `memories` — leer su función completa antes de cualquier ALTER)** · `011_mirix_memory_typing.sql` · `005_memory_scopes.sql` · `016_session_turns_bitemporal.sql` · `021_consolidation_v2.sql` | Si 011 ya da typing ⇒ mapear `item_type` sobre lo existente, no duplicar columna. Si 005 ya da scopes ⇒ la privacidad por agente se apoya ahí. Ningún ALTER se aplica sin probar que el trigger de 030 sigue funcionando (test en §5). |
| F-04 rag_* | Tablas candidatas de documentos/chunks que el Cognitive Core ya detecta · file RAG del SEAL Companion (esquema soul_v1) | Si Companion ya tiene documents/chunks utilizables ⇒ decidir promover a `soul_v3` o coexistir con adapter; no crear una tercera variante. |
| F-05 epistemic_ledger | `008_beliefs_table.sql` · `agent_self_knowledge` | **Sospecha fuerte de solape:** si `beliefs` ya tiene claim+confidence+supersede ⇒ extender `beliefs` y dejar el ledger como vista sobre ella. Tabla nueva solo si `beliefs` no alcanza, con justificación escrita. |
| F-06 close gate | `task_intent_contracts` · `task_lifecycle_events` · patrón del trigger 030 | Reusar el patrón de trigger existente. Tipos de tarea sin requisitos ⇒ requisito vacío EXPLÍCITO en la tabla de requirements, nunca implícito. |
| F-07 skills | `001_instincts_table.sql` · la tabla donde `skill_instinct_factory.py` ya persiste (sus `FactoryRecord.record_id` implican un destino real) | Localizar el destino actual del factory y extenderlo. Crear `skills` nueva solo si el destino actual no soporta versionado+status. |
| F-08 adapters | salidas de `post_train_audit.py` / artefactos LatentGraphMem | Probablemente nueva; verificar que no exista registro de checkpoints previo. |

---

## 3. Plan de compatibilidad (sin romper producción)

Patrón común a los cuatro: **añadir al lado → doble lectura/escritura → observar con métrica → conmutar → retirar lo viejo.** Nunca conmutar y retirar en el mismo paso.

### 3.1 AuditLog

1. **F0-a:** crear `soul_v3.audit_log` (F-01) — aditivo, nadie lo consume aún. En la misma migración, vista `soul_v3.v_audit_unified` = UNION normalizada de `audit_log` + `companion_audit_log` (+ `tool_observations` si aplica, ver §2).
2. **F0-b:** hooks y MCP escriben a la tabla nueva en **modo observe** (best-effort: si el INSERT falla, log y continuar — en F0 el audit no bloquea nada).
3. Companion sigue escribiendo su tabla sin tocarse. Consumidores nuevos leen SOLO la vista unificada.
4. **F1:** el INSERT de audit pasa de best-effort a obligatorio (fallo de audit ⇒ fallo del tool call) — solo cuando 2 semanas de observe muestren <0.1% de errores de INSERT.
5. **F2 (opcional):** writers del Companion migran a la API canónica. Nunca antes.

### 3.2 identity_mode

Secuencia exacta (cada paso con rollback = revertir el paso anterior):

1. **Bugfix puro:** `soul_safety_governor.py` lee el modo desde archivo en la ruta ACTUAL (`/tmp/seal_tokens/identity_mode`), no desde entorno. Sin cambio de comportamiento esperado; es corrección de lectura. Test: cambiar el archivo ⇒ el governor lo refleja sin restart.
2. **Doble ruta:** se introduce `~/.config/seal/identity_mode` (config, **sobrevive reboot** — esa es la razón de la ruta). Lectores: nueva ruta primero, fallback a `/tmp`. El script de arranque escribe AMBAS durante la ventana.
3. **Ventana de observación (≥1 semana):** cada lectura via fallback `/tmp` se loggea. Cuando el log muestre 0 lecturas por fallback en 7 días ⇒ se elimina el fallback.
4. **Fail-closed:** recién ahora, ausencia de archivo ⇒ ENFORCE (invariante 14). Activarlo antes del paso 3 podría bloquear un bridge que aún lee `/tmp` tras un reboot.
5. **Flip a ENFORCE** según SPEC v2 (ventana 72 h, rollback de un paso = escribir `MIGRATE`).

**Tokens (distinto de modo, a propósito):** tokens van a `$XDG_RUNTIME_DIR/seal/` (efímero está BIEN: se regeneran en cada arranque del servicio). Misma técnica: el generador escribe nueva ruta + copia compat en `/tmp/seal_tokens/` durante la ventana; inventario de consumidores con `grep -rln "seal_tokens" memory/ agents/`; cuando todos lean la nueva ruta (log de accesos viejos en 0 por 7 días) se deja de escribir la copia. **El gap de DUM (P6) se decide antes de ENFORCE, no después.**

### 3.3 ToolBroker — observe → migrate → enforce

1. **observe (F1, ≥2 semanas):** `tool_broker.check()` corre en ambos puntos de imposición, calcula la decisión, la audita — **y siempre permite**. Nada puede romperse.
2. **Sembrado del registry desde la realidad:** al final de observe, un script genera propuestas de grants desde el uso observado (`audit_log` → filas `tool_capabilities` candidatas). NEXUS revisa y aprueba el lote. Así el flip no parte de un registry vacío que negaría todo.
3. **Gate de avance (métrica, no fecha):** would-deny rate < 1% de calls durante 7 días consecutivos, y cada would-deny restante revisado (o es legítimo bloquearlo, o le falta grant).
4. **migrate (canario):** enforcement real para UN agente elegido por NEXUS (el de menor clase de tools); el resto sigue en observe. 1 semana sin incidentes ⇒ ampliar.
5. **enforce:** todos los agentes. Rollback en cualquier punto: variable de modo del broker vuelve a `observe` (un paso, sin deploy).

### 3.4 MemoryStore — REVOKE al final, no al principio

1. **Censo de writers (14 días, cubre crons semanales):** (a) estático: `grep -rn "INSERT INTO.*memories" --include='*.py' memory/ agents/ seal/`; (b) dinámico: trigger no-bloqueante `BEFORE INSERT ON soul_v3.memories` que registra `application_name`, usuario y módulo en una tabla censo. Producción no se toca: el trigger solo observa.
2. **F-03 sin REVOKE:** ALTERs + función `memory_write()` conviven con el INSERT directo. Columnas nuevas NULLABLES (filas legacy quedan `trust_tier=0`).
3. **Wrapper compatible:** el camino común de escritura (en `db.py` o el helper que use el equipo, según censo) pasa a llamar `memory_write()` internamente — un solo diff cubre a la mayoría de writers.
4. **Migración writer por writer** para los que no usan el helper, con test de escritura por agente (los 4-5 agentes escriben y leen OK).
5. **REVOKE del INSERT directo** solo cuando el censo dinámico muestre **0 inserts fuera de `memory_write` durante 7 días**. El censo (paso 1) sigue activo una semana más como verificación post-REVOKE. Rollback: re-GRANT (un paso).
6. La exigencia de procedencia NO NULA se enciende junto con el REVOKE, no antes (modo estricto del wrapper).

---

## 4. Fase 0 corregida — pasos exactos, en orden

Todo paso es aditivo o bugfix hasta el paso 9; producción no cambia de comportamiento antes de eso.

| # | Paso | Evidencia de "hecho" | Rollback |
|---|---|---|---|
| 0 | **Preflight + versionado** (§6, paso 1): inventario de migraciones reales, tablas candidatas (§2), writers de `memories`, consumidores de `seal_tokens`, audit tables. Commit del reporte + commit de TODOS los artefactos canónicos sin versionar (migraciones 022-030, soul_v1, specs locales). | reporte reproducible en repo; `git clone` reconstruye el estado real | n/a (read-only + commits) |
| 1 | Backups: `ops/backup_soul_db.sh` + timer nightly (pg_dump custom, retención 7d/4w, copia offsite cifrada) | dump nocturno presente y verificable | n/a (aditivo) |
| 2 | **Primer restore drill manual** a DB `soul_restore_check` + luego timer semanal. Jamás toca la DB viva. | drill verde con conteos por tabla persistidos en `bench_results` | n/a (DB temporal) |
| 3 | Migración F-01 (audit canónica + vista unificada) + escritura observe desde hooks/MCP (best-effort) | filas entrando; <0.1% errores INSERT | desactivar el write (flag), tabla queda |
| 4 | Bugfix governor: lee `identity_mode` desde archivo, ruta actual | test: editar archivo ⇒ governor refleja sin restart | revertir commit |
| 5 | identity_mode doble ruta + tokens a runtime dir con copia compat (§3.2); log de lecturas por fallback | 7 días con 0 lecturas fallback | restaurar fallback (un commit) |
| 6 | Fail-closed: ausencia de modo ⇒ ENFORCE | test A6 verde | revertir flag fail-closed |
| 7 | Suite adversaria F0: A1, A2, A3, A6, A7, A9 en CI | 6/6 verdes con `rule_id` afirmado | n/a (tests) |
| 8 | Resolver DUM (P6): token propio o retiro de acceso MCP/DB — decisión de William+NEXUS registrada como `decision` | fila en ledger/tareas con aprobación | n/a (decisión) |
| 9 | **Flip ENFORCE** con ventana 72 h: métrica de denials por agente visible; Henry ejecuta el rollback una vez como prueba (bus factor) | 72 h sin denials ilegítimos; rollback probado por Henry | escribir `MIGRATE` (un paso) |
| 10 | Des-saturación del bench (≥20 casos que hoy fallan; regla invariante 16) + CI mínima (pytest + suite F0) | bench reporta 60-80%, no 600/600 | n/a |

**Criterio de salida F0 (sin cambios de fondo vs SPEC v2, ahora medible en este orden):** drill verde 2 semanas · ENFORCE activo con 6/6 adversarios · audit observe recibiendo ≥95% de tool calls · DUM resuelto · repo reconstruible · bench des-saturado.

---

## 5. Riesgos de implementación y pruebas mínimas

| Riesgo | Mitigación | Prueba mínima |
|---|---|---|
| Colisión de numeración con migraciones aún no versionadas | Regla de reclamo §1.2; preflight obligatorio | el comando de §1.2 corre en CI y falla si un archivo nuevo no es max+1 |
| El trigger de `030_memories_layer_trigger` se rompe con los ALTERs de F-03 | leer su función ANTES (§2); ALTERs nullables | `test_memories_trigger_after_alter.py`: insert pre/post ALTER produce el mismo efecto del trigger |
| Doble escritura audit divergente (companion vs canónica) | vista unificada como único punto de lectura | test de la vista: una acción ⇒ visible exactamente una vez |
| Consumidor de tokens olvidado rompe en el corte | censo grep + log de lecturas fallback 7 días | `test_identity_mode_paths.py`: precedencia nueva-ruta, fallback funciona, fail-closed solo tras flag |
| REVOKE rompe un writer de cron nocturno/semanal | censo dinámico de 14 días (cubre ciclos) + REVOKE solo con 0 directos en 7 días | `test_memory_write_paths.py`: cada agente escribe vía wrapper; INSERT directo con rol agente falla post-REVOKE |
| observe del broker genera ruido inmanejable de would-denies | sembrado de grants desde uso real antes del gate (§3.3.2) | `test_tool_broker_observe.py`: en observe JAMÁS bloquea; decisión queda auditada |
| ENFORCE bloquea un bridge en caliente | pasos 5-6 antes del 9; ventana 72 h con métrica; rollback de un paso probado por Henry | A1-A3 + monitoreo de denials por agente durante la ventana |

---

## 6. Qué debe hacer ADA primero (semana 1, en este orden)

1. **Preflight inventory + versionado (paso 0 de §4).** Un script `ops/preflight_inventory.sh` que emite: última migración real, subcarpetas de migraciones, tablas existentes que matchean `audit|tool|skill|rag|document|chunk|ledger|belief|consent|instinct`, censo estático de writers de `memories`, consumidores de `seal_tokens`. Salida commiteada como `memory/diagnostic/preflight_f0_report.md`. **En el mismo PR: commit de las migraciones 022-030, soul_v1 y todo artefacto canónico local.** Esto desbloquea TODO lo demás y cierra el bloqueador #7.
2. **Backup + primer restore drill manual** (pasos 1-2). Riesgo cero, valor máximo; desde hoy cualquier error posterior es recuperable.
3. **Bugfix del governor** (paso 4) — es un bug conocido, chico y con test.
4. **Migración F-01 en observe** (paso 3) con el número confirmado por el preflight.
5. **Encender el censo dinámico de writers** (§3.4.1) — necesita 14 días de pared; encenderlo ya hace que el reloj corra en paralelo sin bloquear nada.

**Tres decisiones que ADA debe rutear (no resolver sola):** (a) DUM token/retiro → William+NEXUS, antes del paso 9; (b) ruta final de `identity_mode` → propongo `~/.config/seal/` por sobrevivir reboot, NEXUS confirma; (c) audit: extender companion vs canónica nueva → propongo canónica nueva + vista (este addendum §3.1), JARVIS firma.

---

## 7. Correcciones al SPEC v2 y Anexo A (fe de erratas)

- SPEC v2, "Convenciones" y todas las menciones a migraciones `022`-`027` ⇒ leer F-01..F-06 / `031`-`036` según tabla §1.1.
- Anexo A, migraciones `028`-`029` ⇒ F-07..F-08 / `037`-`038`.
- SPEC v2 §4.1: el audit log canónico convive con `companion_audit_log` vía vista unificada; la consolidación de writers del Companion es F2 opcional, no F0.
- SPEC v2 Fase 1, "REVOKE de INSERT directo": el REVOKE se mueve al final de la secuencia §3.4 (gate por censo, no por calendario). La meta no cambia; el camino sí.
- Los criterios de salida de F0/F1 del SPEC v2 siguen vigentes; este addendum solo define el orden seguro para alcanzarlos.

*Fin del Addendum 1. Revisión esperada: ADA (es su review la que se reconcilia), JARVIS (firma F-01..F-08 y §2), NEXUS (secuencia ENFORCE §3.2-3.3 y decisión DUM), William ((a)-(c) de §6).*
