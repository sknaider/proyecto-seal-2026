# Review JARVIS — Pack Restart-Loop (ADR + Cost Sheet)

**Revisor:** JARVIS
**Fecha:** 2026-04-18 22:53 Lima
**Documentos revisados:**
- `/agents/ADA/adr_restart_loop_pattern.md` (ADR-001)
- `/agents/ALICE/cost_sheet_restart_loop_20260418.md` (Cost Sheet v1)

**Veredicto:** ✅ **Aprobar con 3 observaciones menores (no bloqueantes).**

---

## 1. ADR de ADA — evaluación

### Fortalezas
- Requerimiento literal de William capturado textualmente (sección 1).
- Diagrama ASCII explícito de las 3 capas (kitty → bash_loop → claude).
- Invariantes formales y verificables (4 puntos, sección 2).
- Alternativas descartadas con justificación técnica real (systemd, tmux, kitty --hold, fork).
- Commits trazados en tabla (sección 3) — auditable.
- Validación empírica con tabla por agente (sección 6).

### Observaciones
**O1 — Umbral T_kill/h > 6 sin fuente (sección 5 "Negativas").**
El umbral "6 muertes/hora → overhead supera trabajo útil" está sin citar. Sugiero:
- Marcarlo como "heurística inicial, a recalibrar con cost_sheet de ALICE",
- o citar el cálculo: 6 muertes × ~14k tokens ≈ 84k tokens/h vs ~150k tokens/h de trabajo útil promedio.

**O2 — Alternativa híbrida no enumerada.**
"systemd --user unit + kitty child" no se consideró. No es bloqueante — el ADR es correcto para la decisión tomada — pero merece una línea en "Alternativas" para cerrar la puerta explícitamente.

## 2. Cost Sheet de ALICE — evaluación

### Fortalezas
- Unidad de medida clara (tokens + latencia).
- Datos del día reales (34 resurrecciones totales).
- Desglose por componente con transparencia sobre estimaciones (sección 3.1).
- Comparativa antes/después con métricas concretas (RAM zombie 1.6-2.4 GB/día → 0).
- Palancas de optimización priorizadas con estimaciones (sección 7).

### Observaciones
**O3 — Cache-miss multiplicador 2.5× puede sobreestimar.**
Anthropic factura el prefix cacheado a 0.1× del precio normal (no 0×). Con cache-hit, el system prompt de 8k tokens cuesta ~800 tokens equivalentes, no 0. El multiplicador real cache-miss vs cache-hit es ~2.0× (no 2.5×). Impacto: total día equipo podría bajar de ~725k a ~680k tokens. No altera la conclusión (ROI positivo día 1), sólo ajusta precisión.

### Palancas — prioridad recomendada
Coincido con ALICE. Ranking por ROI:
1. **Palanca #2 — gate active_recall** (-10-15% tokens/día, ~100k tokens ahorrados) — bajo riesgo, alto impacto.
2. **Palanca #3 — batchear nerves auto-fires** (-12k tokens/día) — trivial de implementar.
3. **Palanca #1 — aprovechar cache <5min** — ya la obtenemos gratis con restart-loop.
4. **Palanca #4 — compactación guiada @150k** — ya parcialmente implementada.

Recomiendo a William autorizar palancas #2 y #3 como siguiente incremento (+1% rule aplica).

---

## 3. Coherencia ADR ↔ Cost Sheet

- ADR sección 7 enumera métricas, Cost Sheet sección 4 las cuantifica. ✅ consistente.
- Umbral de alerta ADR (T_kill/h > 6) no aparece explícito en Cost Sheet. Sugiero a ALICE agregar fila en tabla 2 con columna "resurrecciones/hora peak" para validar el umbral con datos reales.
- Cost Sheet sección 5 coincide con consecuencia ADR 5 (RAM zombie eliminada). ✅

---

## 4. Recomendación final para William

**Aprobar pack tal cual para firma, con compromiso de revisión en 7 días:**
- ADR-001 → persistir en Soul DB como `project/architecture/adr-001`.
- Cost Sheet v1 → persistir en `/agents/ALICE/` como referencia base.
- Métricas ADR sección 7 → DUM debería emitir `system_alert` si T_kill/h > 6 (palanca accionable por DUM, sin tocar código Claude).
- Palancas #2 y #3 del Cost Sheet → solicitar luz verde separada tras firma inicial.

Las 3 observaciones son mejoras incrementales, no bloquean la aprobación.

---

**Firma:** JARVIS — 2026-04-18 22:53 Lima
**Estado:** listo para luz verde de William.
