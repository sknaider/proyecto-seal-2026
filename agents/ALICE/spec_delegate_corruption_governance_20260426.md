# SPEC — Governance, Costos & ROI: Mitigación de Corrupción Silenciosa en Workflows Delegados

**Autor:** ALICE (Analytical Ledger & Intelligence for Cost Engineering)
**Fecha:** 2026-04-26 Lima
**Modelo:** Claude Opus 4.7 (autorizado por William, vigente)
**Referencia técnica complementaria:** Spec arquitectural de JARVIS (en paralelo)
**Paper origen:** arXiv:2604.15597 — "LLMs Corrupt Your Documents When You Delegate"
**Estado:** Borrador para review de William

---

## 1. Resumen Ejecutivo

El paper documenta que **LLMs en cadenas de delegación corrompen silenciosamente documentos editados** mediante tools tipo Edit/MultiEdit/Write. La corrupción no produce errores ruidosos: es semántica, gramaticalmente válida, y pasa review humano superficial. El benchmark DELEGATE-52 mide tasas de corrupción no triviales incluso en frontier models en tareas de edición multi-step.

**El stack SEAL es prácticamente un caso de estudio del paper:**
- Cadenas profundas: William → JARVIS → ADA → tools.
- Tools afectadas en uso diario: `Edit`, `MultiEdit`, `Write`.
- Producción: SEAL Memory API ya en operación (Fase 2).
- Documentos críticos: specs, paper ICTSE, contracts, código de producción.

**La pregunta financiera no es "¿implementamos mitigaciones?" sino "¿cuál pagamos primero y con qué orden de ROI?"**

---

## 2. Decisión Recomendada

**Implementar en este orden por ROI descendente:**

| Rank | Mitigación | Implementación | ROI | Fase |
|---|---|---|---|---|
| 1 | F — Belief Inspector + hash pre/post | Extender `reasoning_trace` con `file_hash_before/after` | ★★★★★ | Sprint inmediato (1-2 días) |
| 2 | B — Diff-check post-Edit automático | Hook `PostToolUse` con AST/textdiff sobre archivos críticos | ★★★★★ | Sprint inmediato (2-3 días) |
| 3 | A — Git checkpoints atómicos | Wrapper sobre `working_state_update` que toma snapshot SHA antes de Edit crítico | ★★★★ | Sprint inmediato (1 día) |
| 4 | C — Scope restriction por agente | Whitelist de paths editables por agente (config + enforcement runtime) | ★★★★ | Sprint 2 (3-4 días) |
| 5 | D — Edit precision (no regex) | Regla de equipo + linting de patrones de edición | ★★★ | Documentación + revisión (0.5 día) |
| 6 | E — Backtranslation review humano | Solo para docs críticos identificados (specs, paper, contracts) | ★★ | Continuo, on-demand |

**Total Sprint inmediato (F+B+A): 4-6 días-agente. Cobertura estimada: ~80% del riesgo del paper.**

---

## 3. Costo de Implementar — Detalle

### 3.1 Cómputo del costo unitario

- **Hora-agente Sonnet:** Asumimos throughput efectivo ~150 tokens/seg input + ~50 tokens/seg output, costo blended ~$3.75/M tokens efectivos. Una hora-agente productiva ≈ $0.40-0.80 en tokens (excluyendo overhead de tools).
- **Hora-agente Opus:** ~5x el costo de Sonnet → ~$2-4 por hora-agente productiva.
- **Día-agente:** 4-6 horas productivas reales (resto: overhead, espera, coordinación).

### 3.2 Costo por mitigación

#### F — Belief Inspector + hash pre/post (★★★★★)
- **Implementación técnica:** Añadir campos `file_path`, `file_hash_before`, `file_hash_after` al schema de `reasoning_trace_store` y `reasoning_trace_update`. Wrapper Python que computa SHA256 antes y después de cada Edit/Write.
- **Esfuerzo:** ADA 1-2 días (schema + wrapper + tests).
- **Costo $:** ~$2-5 en tokens. Marginal.
- **Costo ongoing:** ~negligible (1 hash SHA256 por edit ≈ µs CPU, 64 bytes/trace).
- **Beneficio:** Detección retroactiva 100% de cambios no autorizados o silenciosos. Audit trail forense.

#### B — Diff-check post-Edit automático (★★★★★)
- **Implementación técnica:** Hook `PostToolUse` que ejecuta `git diff --no-index` o `difflib.unified_diff` sobre archivos en whitelist crítica. Si diff > umbral (líneas o ratio), notifica al agente y a William.
- **Esfuerzo:** ADA 2-3 días (hook + whitelist config + thresholds + integración con Matrix alerts).
- **Costo $:** ~$5-10 en tokens.
- **Costo ongoing:** ~5-15ms por Edit (depende del archivo). Negligible para SEAL.
- **Beneficio:** Detección **sincrónica** de drift. El agente ve el diff y puede abortar/revertir antes de continuar la cadena.

#### A — Git checkpoints atómicos (★★★★)
- **Implementación técnica:** Extensión de `working_state_update` que en `task_name` con flag `critical=true` ejecuta `git stash create` o `git commit --no-verify` automáticamente sobre el archivo target.
- **Esfuerzo:** ADA 1 día.
- **Costo $:** ~$2-3 en tokens.
- **Costo ongoing:** disco — ~1KB por checkpoint, despreciable.
- **Beneficio:** Rollback instantáneo a estado pre-acción. Combinado con F, da forensics + recovery.

#### C — Scope restriction por agente (★★★★)
- **Implementación técnica:** Archivo `/home/dadito/IA/proyecto-seal/config/agent_scopes.yaml` con whitelist/blacklist de paths por agente. Hook `PreToolUse` que valida `file_path` contra el scope antes de ejecutar Edit/Write/MultiEdit. Bloquea con error explícito si está fuera de scope.
- **Esfuerzo:** ADA 3-4 días (diseño config + hook + tests + migración de excepciones documentadas en CLAUDE.md).
- **Costo $:** ~$8-15 en tokens.
- **Costo ongoing:** ~1ms por tool call. Negligible.
- **Beneficio:** **Defensa en profundidad.** Aunque el LLM se equivoque o un prompt injection redirija intención, el filesystem nunca es tocado fuera del scope autorizado. Prevención > detección.

#### D — Edit precision (no regex) (★★★)
- **Implementación técnica:** Regla en CLAUDE.md + pre-commit hook en repos críticos que detecta uso de `replace_all=true` sin justificación documentada. Uso obligatorio de Edit con `old_string` exacto y único.
- **Esfuerzo:** Documentación (0.5 día) + hook simple (0.5 día).
- **Costo $:** ~$1 en tokens.
- **Beneficio:** Reduce vector de error humano/LLM en ediciones masivas. Bajo costo, alto valor cultural.

#### E — Backtranslation review humano (★★)
- **Implementación técnica:** Workflow operativo: para docs en lista crítica (specs, paper ICTSE, contracts, prompts de producción), la edición pasa por: (1) cambio del agente → (2) re-síntesis del documento por un segundo LLM independiente → (3) comparación semántica → (4) review humano de divergencias.
- **Esfuerzo:** No es código — es proceso. Documentar workflow + identificar lista de "docs críticos" (estimado: <20 archivos).
- **Costo $:** Variable. ~$2-10 por documento revisado (depende de tamaño).
- **Costo ongoing:** Tiempo humano de William ~5-15 min/doc.
- **Beneficio:** Last line of defense. Para los pocos archivos donde la corrupción silenciosa sería catastrófica (paper publicado con errores semánticos, spec de producción que cliente firma).

### 3.3 Tabla resumen costos

| Mitigación | Esfuerzo (días-agente) | Costo $ implementación | Costo ongoing | Cobertura riesgo |
|---|---|---|---|---|
| F | 1-2 | $2-5 | ~0 | ~30% |
| B | 2-3 | $5-10 | ~0 | ~35% |
| A | 1 | $2-3 | ~0 | ~15% |
| C | 3-4 | $8-15 | ~0 | ~25% (defensa) |
| D | 1 | $1 | 0 | ~10% (cultural) |
| E | continuo | $2-10/doc | tiempo humano | ~5% (residual) |

**Total Sprint 1 (F+B+A): 4-6 días-agente, $9-18 en tokens. Cobertura ~80%.**

---

## 4. Costo de NO Implementar — Escenarios

### 4.1 Escenario CR-1: Corrupción en SEAL Memory API en producción

**Setup:** Cliente enterprise paga por SEAL Memory API. ADA en mantenimiento edita endpoint de query. Edit con `replace_all` cambia silenciosamente la lógica de `scope=team` a `scope=private` por error en pattern matching.

**Impacto:**
- Cliente recibe 0 resultados en queries que antes funcionaban.
- Tiempo de detección estimado: 4-72h (depende de telemetría del cliente).
- SLA breach: dependiendo de contrato, $500-$5000 USD en penalidades.
- **Costo reputacional:** un solo cliente que pierde confianza en una API "memory" no la recupera. Memory APIs venden trust, no features.

**Probabilidad anual sin mitigaciones:** Estimo 15-30% (basado en frecuencia actual de edits + paper baseline). Con F+B implementadas: <2%.

**Expected loss anual sin mitigación:** $500-$1500 (penalidades) + churn no contabilizable.

### 4.2 Escenario CR-2: Paper ICTSE con corrupción semántica

**Setup:** Edición colaborativa del paper para ICTSE. Agente ejecuta Edit con `old_string` ambiguo. Cambia un número de tabla, una conclusión, o un método citado sin que William o JARVIS lo noten.

**Impacto:**
- Si pasa review interno: paper publicado con error.
- Si reviewers detectan: rechazo o major revision. Atraso 6-12 meses.
- Daño reputacional académico: alto. Los papers se quedan online.

**Costo:** No directo en $, pero **costo de oportunidad masivo** (visibilidad académica de SEAL en LATAM).

### 4.3 Escenario CR-3: Contract/Pitch corrompido

**Setup:** Edición de un pitch o contrato cliente (ej. ESAN, USIL). Agente ajusta términos comerciales. Edit silencioso cambia un porcentaje, una fecha, o una cláusula.

**Impacto:**
- Si cliente firma sin detectar: SEAL queda atado a términos no intencionales.
- Si se detecta post-firma: renegociación o pérdida de confianza.

**Costo:** Variable, potencialmente $1000-$10000 USD.

### 4.4 Escenario CR-4: Auto-corrupción del propio sistema SOUL

**Setup:** ADA edita su propio runtime, MCP server, o schema de SOUL DB. Edit con drift introduce bug que no aparece en tests (semánticamente válido) pero altera comportamiento.

**Impacto:**
- Memorias del equipo se corrompen silenciosamente.
- Síntomas aparecen días después: "ALICE no recuerda X", "JARVIS dio una respuesta extraña".
- Diagnóstico extremadamente difícil sin checkpoints + diff history.

**Costo:** Pérdida de continuidad cognitiva del equipo. Difícil de cuantificar — pero es exactamente lo que el paper advierte.

### 4.5 Suma de exposición

**Sin mitigaciones, exposición anual estimada:**
- Hard $: $1,500 - $15,000 USD/año.
- Soft (reputación, oportunidad académica, churn): mucho mayor, no cuantificable.

**Con F+B+A implementadas (Sprint 1, $9-18):** exposición baja a <$300/año hard + casi cero soft.

**ROI Sprint 1 = ~80x-300x en el primer año.** No requiere análisis financiero sofisticado para ver que es decisión obvia.

---

## 5. Governance — Quién Audita, Cómo, Cuándo

### 5.1 Roles

| Rol | Responsable | Frecuencia |
|---|---|---|
| **Auditor primario** | DUM (monitoreo continuo) | 24/7 |
| **Reviewer técnico de drift** | JARVIS (análisis arquitectural) | On-demand cuando alerta |
| **Reviewer financiero/proceso** | ALICE (impacto, costo, ROI) | Semanal + on-incident |
| **Decisor final de rollback** | William | On-incident crítico |

### 5.2 Métricas reportadas a William

**Dashboard semanal (lunes a primera hora):**

1. `corruption_alerts_count`: alertas de drift detectadas en la semana.
2. `corruption_alerts_severity_breakdown`: low / medium / high / critical.
3. `mean_time_to_detect (MTTD)`: tiempo promedio entre Edit y alerta.
4. `mean_time_to_resolve (MTTR)`: tiempo promedio entre alerta y fix.
5. `rollback_count`: cuántas veces se hizo rollback en la semana.
6. `false_positive_rate`: cuántas alertas resultaron benignas.
7. `agent_scope_violations`: intentos bloqueados por scope restriction (C).
8. `critical_doc_edits_count`: ediciones a docs en lista crítica + revisor humano asignado.

**Reporte mensual a William:**
- Trend de las métricas anteriores.
- Top 5 archivos con más drift detectado.
- Recomendación de ajuste de thresholds o whitelist.
- Costo en tokens del overhead de mitigación (debe quedar <2% del total).

### 5.3 Política de transparencia

- Toda alerta de drift se publica en canal `seal-diagnostics` (Matrix room).
- Williams recibe notificación push solo para severidad `high`/`critical`.
- DUM mantiene histórico de 90 días en Postgres.

---

## 6. Process — Workflow de Drift Detection → Rollback

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│  AGENTE EJECUTA Edit/Write/MultiEdit en archivo crítico     │
│                          │                                  │
│                          ▼                                  │
│  PreToolUse hook → valida scope (C). Si fuera → bloqueo.    │
│                          │                                  │
│                          ▼                                  │
│  Hash pre-acción (F) → working_state_update (A: snapshot)   │
│                          │                                  │
│                          ▼                                  │
│  Edit ejecuta                                               │
│                          │                                  │
│                          ▼                                  │
│  PostToolUse hook → diff-check (B) + hash post-acción (F)   │
│                          │                                  │
│             ┌────────────┴────────────┐                     │
│             ▼                         ▼                     │
│       diff < threshold          diff >= threshold           │
│             │                         │                     │
│             ▼                         ▼                     │
│       continúa normal           ALERTA                      │
│                                       │                     │
│                                       ▼                     │
│                          DUM publica en seal-diagnostics    │
│                                       │                     │
│                                       ▼                     │
│                          JARVIS evalúa severidad            │
│                                       │                     │
│                       ┌───────────────┴────────────┐        │
│                       ▼                            ▼        │
│                 low/medium                   high/critical  │
│                       │                            │        │
│                       ▼                            ▼        │
│              ALICE registra                  Notif William  │
│              en log semanal                        │        │
│                                                    ▼        │
│                                          William decide:    │
│                                          - aceptar          │
│                                          - rollback (A)     │
│                                          - investigar       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 6.1 SLAs internos

- Detección: <5 segundos post-Edit (sincrónico vía hook).
- Notificación a William si crítico: <30 segundos.
- Rollback ejecutable: <60 segundos desde decisión (git stash apply o checkpoint restore).

### 6.2 Definición "archivo crítico"

Lista inicial (revisable mensualmente):
- `/home/dadito/IA/proyecto-seal/CLAUDE.md`
- `/home/dadito/IA/proyecto-seal/memory/mcp_server_v2.py`
- Schemas de SOUL DB (`agents/<agent>/schema/*.sql`)
- Specs en `agents/*/spec_*.md`
- Paper ICTSE (en cuanto se cree el archivo final)
- Pitches y contracts en `agents/ALICE/seal_memory_pitch_*.md`
- Cualquier archivo en `/home/dadito/IA/proyecto-seal/seal-memory-api/` (producción)

### 6.3 Exclusiones (no requieren diff-check estricto)

- Logs (`*.log`, `*.jsonl` de mensajes).
- Checkpoints autogenerados.
- Cache (`/tmp/*`, `*_cache.json`).
- Daily briefs (sobreescritura intencional diaria).

---

## 7. Plan de Fases

### Fase 0 — Pre-implementación (esta semana)
- [ ] William aprueba este spec + spec arquitectural de JARVIS.
- [ ] ADA agenda Sprint 1 para F+B+A.
- [ ] DUM prepara dashboard semanal vacío con las métricas definidas.

### Fase 1 — Sprint Inmediato (próximos 4-6 días)
- [ ] F: schema extension en `reasoning_trace` + wrapper hash.
- [ ] B: hook PostToolUse + whitelist crítica + alertas Matrix.
- [ ] A: extensión `working_state_update` con snapshot.
- [ ] Tests E2E: simular drift artificial en archivo crítico, verificar detección + alerta + rollback.
- [ ] Smoke test con William: editar deliberadamente CLAUDE.md con un cambio sutil, ver que el sistema lo detecta.

### Fase 2 — Sprint 2 (siguiente semana)
- [ ] C: config `agent_scopes.yaml` + hook PreToolUse.
- [ ] Migración de excepciones (paths que cada agente legítimamente edita) desde CLAUDE.md.
- [ ] D: regla en CLAUDE.md + linter pre-commit.

### Fase 3 — Continuo
- [ ] E: identificación de docs críticos + workflow backtranslation.
- [ ] Reportes semanales a William.
- [ ] Revisión mensual de thresholds y whitelist.

### Fase 4 — Iteración
- Si MTTD > 10 segundos en producción → investigar latencia hooks.
- Si false_positive_rate > 15% → ajustar thresholds.
- Si rollback_count == 0 durante 90 días en archivos críticos → considerar el sistema validado.

---

## 8. Riesgos del Plan

1. **Hook overhead:** si los hooks de F+B son lentos, podrían frenar el throughput del equipo. **Mitigación:** medir en Sprint 1, abortar si overhead >50ms por tool call.
2. **False positives:** edits legítimos masivos (refactor) podrían disparar alertas constantes. **Mitigación:** flag `--bulk-refactor` autorizado por William que bypassa diff-check temporalmente con audit log obligatorio.
3. **Scope restriction muy estricto:** podría bloquear trabajo legítimo. **Mitigación:** Sprint 2 incluye fase de "shadow mode" — el hook reporta violaciones pero no bloquea durante 1 semana, así calibramos la whitelist.
4. **Backtranslation costoso en tokens:** revisar 10 docs/semana con segundo LLM ≈ $20-50/mes. **Mitigación:** lista crítica corta (<20 docs) + revisión solo cuando hay edit, no continua.

---

## 9. Recomendación Final (ALICE)

**Aprobar Sprint 1 (F+B+A) inmediatamente.** Esfuerzo bajo (4-6 días-agente, $9-18 en tokens), cobertura del 80% del riesgo, ROI 80x-300x el primer año. Sin debate financiero — es la decisión obvia.

**Sprint 2 (C+D) condicionado a:** que Sprint 1 demuestre overhead aceptable y false_positive_rate <15%.

**E queda como capa opcional sobre los 4-6 documentos verdaderamente críticos** (paper ICTSE, contratos firmados, CLAUDE.md cuando se modifica protocolo).

**Decisión que necesito de William:**
1. Aprobación del spec.
2. Confirmación de la lista inicial de "archivos críticos" (sección 6.2).
3. Designación de ADA para implementar Sprint 1 (asumido pero requiere luz verde).

---

## 10. Apéndice — Datos Base del Paper

- **DELEGATE-52:** 52 tareas de delegación realista (edit, refactor, summarize, translate).
- **Round-trip primitive backtranslation:** método de detección — reconstruir documento canónico y comparar.
- **Failure modes documentados:**
  - Drift semántico no ruidoso (output gramaticalmente válido pero significado alterado).
  - Pérdida de información en cadenas largas (>3 niveles de delegación).
  - Edición silenciosa de campos numéricos/fechas/nombres por matching impreciso.
- **Frontier models afectados:** todos los probados, en distinto grado. **No es un problema de modelo, es un problema de protocolo de delegación.**

---

**Fin del spec.**
*Listo para review de William junto con el spec arquitectural técnico de JARVIS.*
