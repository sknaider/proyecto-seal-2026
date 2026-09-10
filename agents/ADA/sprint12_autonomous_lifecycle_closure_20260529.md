# Sprint 12 — Autonomous Lifecycle Closure (29-may-2026)

> **RECONSTRUIDO EL 9-sep-2026 DESDE LA BASE DE DATOS. No son los bytes originales.**
>
> El documento original se perdió en el borrado de `/home/dadito` del 7-sep-2026 a la
> 01:42:53. No estaba en git ni en el respaldo de `/mnt/spark-2/recuperacion_seal_7sep`.
> Lo que sigue se regeneró consultando `soul_v3.ada_evaluation_runs_v`, que **sí**
> conservó las 14 corridas íntegras: id, suite, puntaje, resultado y fecha.
>
> **El hallazgo que importa: la evidencia nunca se perdió, sólo el documento.** El
> test `v5.1 External Evidence Gate` existe justo para preguntar si una afirmación de
> sprint se puede replicar desde evidencia persistida en vez de creerle al chat. Esta
> reconstrucción es su respuesta: se pudo.
>
> Lo que NO se recuperó es la prosa original — el análisis, las decisiones y el
> contexto que ADA escribió ese día. Eso se perdió y no se inventa acá.

## Evidence Runs

| run id | suite | score | passed | fecha |
|---|---|---|---|---|
| 2589 | working_state_journal | 100 | sí | 2026-05-29 |
| 2590 | working_state_hook_activation | 100 | sí | 2026-05-29 |
| 2591 | working_state_hook_soak | 100 | sí | 2026-05-29 |
| 2592 | working_state_production_readiness | 100 | sí | 2026-05-29 |
| 2593 | working_state_live_capture | 100 | sí | 2026-05-29 |
| 2594 | agi_gap_ledger | 100 | sí | 2026-05-29 |
| 2595 | autonomous_lifecycle | 100 | sí | 2026-05-29 |
| 2596 | autonomous_lifecycle_persistence | 100 | sí | 2026-05-29 |
| 2597 | autonomous_lifecycle_delegation | 100 | sí | 2026-05-29 |
| 2598 | autonomous_lifecycle_review_gate | 100 | sí | 2026-05-29 |
| 2604 | skill_instinct_factory | 100 | sí | 2026-05-29 |
| 2605 | soul_autonomy_pipeline | 100 | sí | 2026-05-29 |
| 2606 | latent_graphmem_phase2 | 100 | sí | 2026-05-29 |
| 2607 | awareness_247_process | 100 | sí | 2026-05-29 |

**14 corridas, todas con puntaje 100 y `passed=true`.** Consultado el 9-sep-2026
con el rol de aplicación, contra `soul_v3.ada_evaluation_runs_v`.

## Procedencia de este archivo

```sql
SELECT id, suite_name, score, passed, run_at
FROM soul_v3.ada_evaluation_runs_v
WHERE id = ANY(ARRAY[2589,2590,2591,2592,2593,2594,2595,2596,2597,2598,
                     2604,2605,2606,2607]::bigint[])
ORDER BY id;
```

Los ids no se eligieron acá: son los que `memory/seal_bench_v5.py` declara en
`REQUIRED_RUN_IDS` desde antes del borrado. Este archivo no puede "aprobarse a sí
mismo" agregando ids convenientes — la lista vive en el test, no en el reporte.

