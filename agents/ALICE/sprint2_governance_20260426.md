# Governance / Costo / Riesgo — Sprint 2-4 Mitigación de Corrupción por Delegación

**Autor:** ALICE (Sonnet)
**Fecha:** 2026-04-26 21:24 Lima
**Spec base:** JARVIS — `/home/dadito/IA/proyecto-seal/agents/JARVIS/spec_delegate_corruption_mitigation_20260426.md`
**Origen del riesgo:** arXiv:2604.15597 "LLMs Corrupt Your Documents When You Delegate"
**Modelo usado:** Sonnet 4.6 (análisis governance, no requiere Opus)
**Scope:** Sprints 2, 3, 4 (B + F, luego C, luego E). Sprint 1 (D + A) ya en producción canary.
**Audiencia:** William (decisión Go/No-Go por sprint) + JARVIS (ajuste de diseño) + ADA (implementación) + NEXUS (validación sandbox).

---

## 1. Resumen ejecutivo (para William)

Las seis mitigaciones suben la cobertura de corrupción silenciosa de **40% → 95%**. Sprint 1 ya está canary en ADA — los hooks de precisión y checkpoint git protegen los edits "obvios". Pero el riesgo real, el que pasa review humano, está en Sprint 2-4.

**Lo que protege cada bloque (lenguaje humano):**
- **Sprint 2 (B+F):** "el sistema sabe leer su propio diff y compararlo contra lo que el agente CREYÓ que iba a hacer". Cierra el ~20% más peligroso — drift semántico que parece bueno a simple vista.
- **Sprint 3 (C):** "ALICE no puede tocar mcp_server, ADA no puede tocar specs de JARVIS". Convierte la regla social en muro.
- **Sprint 4 (E):** "cada mes, otro modelo lee el paper ICTSE y el contrato de la API y dice qué cambió de fondo". Detecta drift acumulativo lento que ninguna otra capa ve.

**Mi recomendación:** Sprint 2 con prioridad alta, Sprint 3 después de validar Sprint 2 en producción 7+ días, Sprint 4 como tarea continua de baja frecuencia.

**Costo total estimado:** ver §6. Rango: USD 18-45 / mes operacional + ~12-20 horas-agente de implementación inicial.

---

## 2. Sprint 2 — B (diff-check) + F (Belief Inspector hash)

### 2.1 Mitigación B — Diff-check automático

**Qué protege (humano):**
Cuando un agente edita un archivo crítico, el sistema vuelve a leer el cambio y lo compara con lo que el agente dijo que iba a hacer. Si el cambio toca más líneas de lo esperado, elimina funciones, o cambia headings/llaves YAML, dispara alerta antes de que William lo vea corrupto.

**Vector cubierto:** drift semántico no detectado por review humano superficial — el riesgo principal del paper.

**Failure modes (dónde puede fallar):**
| Modo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Falsos positivos en refactors masivos | Alta primer mes | Paraliza al equipo | Modo `--report-only` 48h antes de `--block`; flag `--bulk-refactor` audit-logged |
| Parser AST cae en archivo malformado | Media | Skip de la verificación, edit pasa sin chequeo | Fallback a diff de líneas + alerta WARN; nunca bloquear por error del parser |
| Costo CPU en archivos >10k líneas (mcp_server_v2.py) | Baja | Latencia 200-500ms | Cache LRU de AST previo; dispatcher con timeout 1s |
| Adversario que edita justo bajo el umbral del 30% | Baja | Bypass detectable solo por F | Defensa en profundidad — F captura el SHA pre/post igual |
| Soft-failure: el hook se desactiva silenciosamente | Media | Cobertura cae sin aviso | Cron NEXUS verifica que hook está vivo cada hora |

**Qué monitorear (métricas de operación):**
| Métrica | Fuente | Verde | Rojo | Acción si rojo |
|---|---|---|---|---|
| Alertas/día | system_alerts canal seal_diagnostics | <5 | >20 | Subir umbral / revisar parser |
| Falsos positivos % | manual review semanal William | <5% | >15% | Recalibrar umbrales por tipo de archivo |
| Recall en sandbox NEXUS | benchmark mutación DELEGATE-52 | ≥0.85 | <0.70 | NO promover a producción |
| Latencia edit p95 | timing hook | <200ms | >500ms | Optimizar parser / cachear |

**Costo Sprint 2-B:**
- Implementación: ~4-6h ADA (parsers AST/MD/YAML + dispatcher).
- Operacional: ~0 tokens (parsing local, no LLM). CPU/RAM despreciable en RTX 5090/Spark.
- Validación NEXUS sandbox: ~2h (benchmark mutación adaptado).

### 2.2 Mitigación F — Belief Inspector con hash pre/post

**Qué protege (humano):**
Antes de cualquier edit crítico, el sistema saca una "huella digital" (SHA-256) del archivo. Después de editar, saca otra. Si el agente dice "edité X" pero la huella es la misma → marca `silent_noop` (el agente alucinó que editó). Si dice "edit OK" pero la huella cambió raro → marca `partial_failure`. La creencia del agente queda atada al cambio físico real, auditable.

**Vector cubierto:** desconexión entre la creencia del agente ("edité bien") y la realidad del filesystem. Cierra el caso más perverso del paper: corrupción + alucinación de éxito.

**Failure modes:**
| Modo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Migración schema reasoning_traces falla mid-flight | Baja | Soul DB inconsistente | Backup obligatorio + ventana mantenimiento + dry-run NEXUS sandbox |
| SHA collision (teóricamente posible) | Despreciable | Falso noop | SHA-256 collision rate: 1/2^128 — ignorable |
| Race condition: otro proceso modifica archivo entre pre y post | Baja | Falso `partial_failure` | scope restriction (Sprint 3) lo cierra; reportar como warning, no error |
| Wrappers no se llaman porque agente usa Edit directo sin trace | Alta inicial | Cobertura parcial | Instinct activado en boot: "antes de Edit crítico → reasoning_trace_store_with_target" + audit en daily_brief de edits sin trace |
| Belief loop infinito si el flag dispara más beliefs | Baja | DoS a sí mismo | Circuit breaker: máx 3 belief_query por trace |

**Qué monitorear:**
| Métrica | Fuente | Verde | Rojo |
|---|---|---|---|
| Edits críticos con trace + sha_pre/post | reasoning_traces JOIN hooks log | 100% | <95% |
| Flags `silent_noop_or_corruption` | reasoning_traces.flag | 0 | ≥1 |
| Flags `partial_edit_failure` | reasoning_traces.flag | <2/día | >5/día |
| Latencia adicional SHA256 | timing hook | <10ms | >50ms |
| Beliefs corregidos por F | belief_update events con reinforce=False | informativo | — |

**Costo Sprint 2-F:**
- Implementación: ~3-4h ADA (migración schema + wrappers + belief_inspector.py).
- Migración schema producción: ~5 min downtime con backup.
- Operacional: ~0 tokens. SHA-256 ~5ms/edit, despreciable.

### 2.3 Por qué B+F juntos (governance)

JARVIS agrupó B+F como Sprint 2 porque B alimenta `diff_score` que F persiste en reasoning_traces. **Mi validación financiera:** ROI combinado 80x-300x (estimado por reducción de incidentes Tier-1 y horas-agente de debugging). Coincido. Separar B y F en sprints distintos perdería sinergia y obligaría a doble migración schema.

**Riesgo de bloqueo cruzado:** F necesita A (Sprint 1) para tener commit de referencia. A ya está canary en ADA — verificar que está estable antes de Sprint 2. Si A falla en producción → posponer Sprint 2.

---

## 3. Sprint 3 — C (Scope restriction)

**Qué protege (humano):**
Hoy la regla "ALICE no toca mcp_server, ADA no toca specs de JARVIS" vive en CLAUDE.md como acuerdo social. Sprint 3 la convierte en muro técnico: el hook lee el path, lo cruza contra una tabla `agent_scope`, y bloquea/avisa antes del Edit. William sigue con override absoluto.

**Vector cubierto:** un agente confundido o con belief errado que edita zona ajena. Convierte cumplimiento social (frágil) en cumplimiento técnico (verificable).

**Failure modes:**
| Modo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Scope mal definido bloquea trabajo legítimo | Alta primer mes | Frustración + escalada | Default `review` en zonas grises; escalada <10min cuando William presente |
| Agente bypasea vía Bash + sed | Media | Bypass total | Hook también escucha Bash con patrones `sed/awk/cat >` sobre paths críticos |
| Seeds desincronizados entre archivos y DB | Media | Comportamiento inconsistente | `scope_seeds.yaml` requiere firma HMAC de William para mergear |
| William no está disponible para `review` | Alta nocturna | Trabajo bloqueado | Auto-aprobación tras 30min en horario laboral; queue para revisión next morning |
| Path renombrado y scope no actualizado | Media | Falso allow/deny | `scope_query` falla cerrado (deny por defecto si no hay match exacto) |

**Qué monitorear:**
| Métrica | Fuente | Verde | Rojo |
|---|---|---|---|
| Scope denials/semana | agent_scope log | <10 | >30 |
| `review` pendientes >30min | pending_review table | <2 | ≥5 |
| Tiempo medio aprobación review | timestamps | <10min | >2h |
| Incidentes "agente X tocó algo que no debía" | manual auditoría | 0 | ≥1 |

**Costo Sprint 3-C:**
- Implementación: ~3h ADA (migración tabla + scope_check.py + tools MCP).
- Definición seeds: ~2h ALICE+JARVIS+William sentados juntos para revisar zonas grises.
- Operacional: ~0 tokens. Query DB <5ms/edit.

**Por qué Sprint 3 después de Sprint 2 (no antes):**
- C añade fricción visible al equipo. Si lo desplegamos antes de tener detección automática (B+F), el equipo siente que "molesta" sin ver el beneficio. B+F primero da casos reales de corrupción que justifican el muro perimetral.
- C no es prerequisito de B/F (a diferencia de A → B/F). Se puede añadir encima sin migración cruzada.

---

## 4. Sprint 4 — E (Backtranslation review)

**Qué protege (humano):**
Cada vez que un documento crítico (paper ICTSE, contrato API, CLAUDE.md, specs de JARVIS) acumula >5 edits sin review canónico, el sistema:
1. Toma el doc actual y la última versión "canónica" tagueada.
2. Pasa cada uno por otro modelo (Opus o NEXUS local) → genera resumen ejecutivo.
3. Compara los dos resúmenes con embedding cosine.
4. Si divergen más de X% → POST a William con highlights del drift semántico.

**Vector cubierto:** drift acumulativo lento. Ningún edit individual es sospechoso, pero después de 20 edits el documento dice algo diferente al original. Las otras mitigaciones no ven esto porque miran un edit a la vez.

**Failure modes:**
| Modo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Modelo de revisión también alucina | Media | Falsa alarma o falso OK | Dos pasadas con modelos distintos; alertar solo si ambos coinciden |
| Costo de tokens explota | Media | Gasto inesperado | Lista cerrada de docs críticos + umbral 5 edits; máx 1 review/doc/día |
| Embeddings cambian de versión Qdrant y rompen comparación | Baja | Falso drift | Pin de versión modelo embeddings + recálculo total al cambiar |
| El "canónico" inicial está mal definido | Alta primer mes | Drift detectado contra basura | William firma cada canonical/* tag explícitamente |
| Cron timer falla silencioso | Media | Cobertura cae | NEXUS heartbeat verifica timer activo cada 6h |

**Qué monitorear:**
| Métrica | Fuente | Verde | Rojo |
|---|---|---|---|
| Docs críticos con review canónico <30 días | backtranslation_review log | 100% | <80% |
| Drift detectado y aprobado por William | review_request resoluciones | informativo | — |
| Tokens consumidos/mes en review | accounting Opus/Sonnet | <USD 15 | >USD 40 |
| Falsos positivos en drift | manual review William | <20% | >50% |

**Costo Sprint 4-E:**
- Implementación: ~2h ADA (script + timer + integración Qdrant).
- Operacional: ~USD 5-15/mes en tokens (Opus para resumen) si lista crítica = ~10 docs y trigger ~2-3 reviews/mes. Si NEXUS local hace el resumen → USD 0.
- **Mi recomendación de modelo:** NEXUS local primero. Si recall de drift <60% en sandbox, escalar a Opus solo para tier-1 docs (paper ICTSE, contrato API).

**Por qué Sprint 4 al final:**
- E es el más caro en tokens y el menos urgente.
- Sin C (Sprint 3) hay riesgo de que un agente edite docs críticos fuera de scope → E detecta drift pero la fuga ya ocurrió.
- E es continuo, no bloqueante. Puede arrancar tarde sin penalty.

---

## 5. Riesgos transversales (todos los sprints)

| Riesgo | Probabilidad | Impacto | Mitigación |
|---|---|---|---|
| Hooks acumulan latencia >500ms total/edit | Media | UX degradada | Budget global ≤300ms; benchmark NEXUS antes de cada despliegue |
| Equipo siente que el sistema "molesta" | Alta | Política/moral | Feedback semanal William + métrica "ediciones canceladas por hook" |
| Soul DB se llena de traces y ralentiza queries | Media a 90 días | Performance | Particionamiento por mes + retention 90d activos + cold archive |
| Migration Fase 3 (schema reasoning_traces) corrompe data | Baja | Catastrófico | Backup explícito + dry-run sandbox + ventana mantenimiento |
| Hooks se desactivan en alguno de los 4 settings.json | Media | Cobertura parcial silenciosa | Cron NEXUS audita los 4 settings cada hora; alerta si difference |
| Falsos positivos generan fatiga de alerta | Alta | Equipo ignora alertas reales | Tuning de umbrales con datos primer mes; revisión semanal con William |

---

## 6. Costos consolidados

### 6.1 Implementación inicial (one-time)

| Sprint | Componente | Horas-agente | Modelo dominante |
|---|---|---|---|
| 2 | B (parsers + dispatcher) | 4-6h ADA | Sonnet (código) |
| 2 | F (migración schema + wrappers) | 3-4h ADA | Sonnet |
| 2 | Validación sandbox | 2h NEXUS | local |
| 3 | C (tabla + hooks + tools) | 3h ADA | Sonnet |
| 3 | Definición seeds | 2h ALICE+JARVIS+William | conversación |
| 4 | E (script + timer) | 2h ADA | Sonnet |
| **Total** | | **16-19 horas-agente** | |

Tokens implementación: estimado USD 5-12 (Sonnet para código, sin Opus salvo specs).

### 6.2 Operacional mensual (post-deploy)

| Item | Tokens/mes | Cómputo | Costo USD |
|---|---|---|---|
| Hooks B+F+C (sin LLM) | 0 | CPU/RAM local | ~0 |
| E backtranslation con NEXUS local | 0 | GPU local | ~0 |
| E backtranslation con Opus tier-1 | ~50K | API | ~5-15 |
| Reviews semanales falsos positivos | manual William | tiempo William | — |
| Cron NEXUS auditoría | 0 | local | ~0 |
| **Estimado mensual** | | | **USD 0-15** |

**Comparación:** un solo incidente Tier-1 de corrupción del SEAL Memory API = **horas de debugging + posible breach de cliente + reputación**. ROI defensivo positivo desde el primer caso evitado.

### 6.3 ROI por sprint (estimación)

| Sprint | Reducción riesgo | Costo | Prioridad |
|---|---|---|---|
| 1 (D+A) | 32 pp | bajo | Crítica — base |
| 2 (B+F) | 20 pp | medio | **Alta** — mejor ROI |
| 3 (C) | 2 pp | medio | Media — defensa |
| 4 (E) | 1 pp | bajo recurrente | Baja — continuo |

Sprint 2 da más cobertura por dólar. Sprint 4 es marginal pero barato.

---

## 7. Métricas globales — Dashboard propuesto

Para que William vea el estado de un golpe:

```
SPRINT MITIGATION STATUS  [2026-04-26 21:24 Lima]
─────────────────────────────────────────────────
Sprint 1 (D+A)  ✓ canary ADA       cobertura ~72%
Sprint 2 (B+F)  pending             prox: cobertura ~92%
Sprint 3 (C)    pending             prox: cobertura ~94%
Sprint 4 (E)    pending             prox: cobertura ~95%

LIVE METRICS
  Edits críticos sin trace .......... 0     [verde]
  Auto-checkpoints generados/24h .... 12    [info]
  Diff-check alertas/24h ............ -     [pending B]
  Silent noop flags ................. -     [pending F]
  Scope denials/semana .............. -     [pending C]
  Drift en docs críticos ............ -     [pending E]

BUDGET MES ACTUAL
  Tokens operacional ................ USD 0  [verde]
  Horas-agente debugging corrupción . 0      [verde]
```

JARVIS revisa lunes (ciclo arquitectural). William revisa cuando quiere. NEXUS audita continuo.

---

## 8. Decisiones que necesito de William

1. **Sprint 2 ¿Go?** Spec JARVIS está completo, ROI alto, cobertura sube 20pp. Confirmación esperada.
2. **Modelo backtranslation Sprint 4:** ¿NEXUS local primero (USD 0) o Opus directo (USD 5-15/mes con mejor recall)? Mi voto: NEXUS local, escalar solo si recall <60%.
3. **Ventana de mantenimiento Sprint 2-F:** migración schema reasoning_traces requiere ~5min con agentes pausados. ¿Cuándo?
4. **Lista crítica Sprint 4:** propongo `paper/ictse_2026.md`, `agents/JARVIS/spec_*.md`, `CLAUDE.md` raíz, contrato API. ¿Apruebas o agregas?
5. **Failure budget:** ¿cuántos falsos positivos/semana toleramos en B antes de subir umbrales? Propongo: <10/semana = aceptable, >20 = retune.

---

## 9. Recomendación operativa final

- **Aprobar Sprint 2 ahora** (B+F juntos, mayor ROI).
- **Sprint 3 una vez Sprint 2 esté estable 7+ días** (sin spike de falsos positivos).
- **Sprint 4 como tarea continua** sin fecha fija — arranca cuando William quiera y NEXUS tenga capacidad.
- **Validación NEXUS en sandbox obligatoria** antes de producción cada sprint.
- **Modo `--report-only` 48h** antes de `--block` en B (evita parálisis de equipo).
- **Backup PostgreSQL :5433 explícito** antes de migración schema F (no negociable).

---

**Fin del documento. ALICE — listo para discusión técnica con JARVIS o decisión Go/No-Go con William.**
