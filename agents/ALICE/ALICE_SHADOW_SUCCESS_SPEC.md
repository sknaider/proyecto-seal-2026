# Shadow Router — Spec de Éxito (ALICE)
**Fecha:** 2026-04-12
**Autor:** ALICE
**Contexto:** Shadow mode logging del router híbrido LatentGraphMem V1.2 vs MAGMA. Decidir wire/freeze con datos reales del equipo.

## 1. Objetivo
Recolectar ≥200 queries reales del equipo en ventana de 48-72h post-fix del hook extendido, y dictar una decisión objetiva sobre si cablear el hybrid router a producción o congelar V1.2 donde está.

## 2. Muestra mínima aceptable
| Campo | Umbral mínimo | Justificación |
|---|---|---|
| Total queries reales | ≥200 | Poder estadístico para test de diferencia de medias |
| Queries por agente | ≥30 cada uno (JARVIS/ADA/ALICE) | Evitar sesgo por patrón individual |
| Distribución pred_type | ≥3 tipos distintos cubiertos | Multi_hop, factual, entity/causal/temporal |
| latent_status=ok | ≥90% | Si baja → problema de servicing no de modelo |

Si a las 48h no llegamos a 200, extender ventana hasta 7 días máximo.

## 3. Métricas de decisión

### 3.1 Calidad (retrieval)
- **agreement_top5** — % de queries donde latent y magma coinciden en top-5
- **agreement_top1** — % donde coinciden en top-1
- **disagreement_top20** — queries donde 0/5 ids coinciden (goldmine: donde cada retriever ve universos distintos)

### 3.2 Velocidad (latencia)
- **magma_p50 / latent_p50**
- **magma_p95 / latent_p95**
- **hybrid_router_p50 simulado** (min de ambos, con overhead de classify)

### 3.3 Per-tool breakdown (NUEVO con hook extendido)
- Distribución de calls por tool: `memory_search` vs `memory_hybrid_search` vs `magma_retrieve`
- Por cada tool: agreement, latencia, latent_status
- Por cada agente: patrón de uso

## 4. Umbrales de decisión (CRITERIO OBJETIVO)

### WIRE (cablear hybrid router a producción)
Cumplir ≥3 de 4:
- `agreement_top5 < 50%` — los retrievers ven distinto → router agrega valor real
- `latent_p50 < 1500ms` — latencia sustentable
- `latent_status=ok ≥ 95%` — estable
- En ≥30% de queries, top-1 latent ∉ top-5 magma → latent encuentra cosas que magma no ve

### FREEZE (congelar V1.2, no cablear)
Cumplir ≥1 de 3:
- `agreement_top5 ≥ 80%` — redundante, no vale la complejidad
- `latent_p50 > 3000ms` — muy lento para producción
- `latent_status=ok < 85%` — inestable

### EXTENDER (decisión diferida)
- Muestra <200 o cobertura pred_type <3 → esperar más datos
- Split mixto (criterios wire y freeze simultáneos) → investigar qué pred_type gana cada uno, posible wire selectivo

## 5. Wire selectivo (plan B si split)
Si latent gana en `multi_hop` + `causal` + `temporal` pero magma gana en `factual` + `entity`:
→ router híbrido con clasificador de intent dirigiendo la query al retriever ganador. ADA ya tiene el classifier rule-path + LLM fallback listos (14ms rule, 200-470ms LLM).

## 6. Análisis extendido — script requerido
`analyze_shadow_router.py` ya existe (JARVIS+ADA). Extensión necesaria:
- `--per-tool` → breakdown por tool (memory_search, hybrid, magma)
- `--per-agent` → breakdown por caller (requiere campo `agent` en hook)
- `--wire-decision` → emite WIRE / FREEZE / EXTEND con los umbrales arriba

## 7. Output final (reporte ALICE)
Al cierre de ventana, entregar a William:
1. Veredicto: WIRE / FREEZE / EXTEND
2. Tabla de métricas contra umbrales
3. Top-20 disagreements con ejemplos concretos
4. Costo estimado del wire (si aplica): LoC, tiempo ADA, riesgo de regresión
5. Un párrafo en humano (1 SMS) resumiendo la decisión

## 8. Riesgos conocidos
- **Hook no extendido a todas las tools** → muestra sesgada. Mitigación: ADA extiende hook a `memory_hybrid_search` (en curso).
- **Queries sintéticas de tests contaminan** → filtrar por substring `test` y por ts < T0_real.
- **Serving :8767 muere silenciosamente** → verify_shadow_live.py debe correr cada 6h como cron.

---
Owner: ALICE | Reviewer: JARVIS | Implementor del fix shadow: ADA
