# PAST held-out v1 — resultados preregistrados

## Estado

La corrida sintética determinista cumplió el endpoint primario preregistrado:
8 de 8 casos recibieron el veredicto `pathway_supported`. Se ejecutaron 240
ensayos: 8 casos × 6 brazos × 5 repeticiones.

Este resultado sostiene únicamente atribución del mecanismo en el límite del
harness sintético congelado. No demuestra causalidad en producción, no mide un
modelo vivo y no autoriza afirmaciones sobre la SOUL DB real.

## Custodia y preregistro

- Harness base: commit `b6d2eeaf3`.
- Dataset: `past_heldout_v1_dataset.json`, SHA-256
  `f9e8aea2b6d31ceff692c193550def0ea5abcd5f8b89b6e2066de6214794abab`.
- Preregistro: `past_heldout_v1_preregister.json`, SHA-256
  `1ba33f3a0dfb61723df4eaf2c17d9c5f3ce1a2f33078f770755d8e40d59fcd25`.
- Runner congelado: `memory/past_heldout_v1_runner.py`, SHA-256
  `cf86d70fa30fe2bcfd7636ae3090742bcab0a4dda034879f87bcb3d30841124f`.
- Runtime: extractor determinista con hash fijado, grader exact-match con hash
  fijado, sin red, sin DB y sin modelo externo.
- Clasificación: completamente sintético; sujeto
  `BENCH_PAST_HELDOUT_V1`; ninguna memoria privada ni datos de FABLE.

El primer intento de validación fue abortado antes de ejecutar el modelo:
detectó un placebo fuera de la tolerancia de tokens y un control de nombres
demasiado amplio. No escribió evidencia. Ambos defectos se corrigieron y todos
los hashes afectados se recongelaron antes de abrir resultados.

## Resultado por brazo

Los ocho casos tuvieron exactamente el mismo perfil preregistrado:

| Brazo | Score en cada caso | Función |
|---|---:|---|
| `persistence_on` | 1.0 | Control positivo y camino atribuido |
| `persistence_off` | 0.0 | Control negativo sin persistencia |
| `placebo` | 0.0 | Control negativo con presupuesto emparejado |
| `delete` | 0.0 | Contrafactual de borrado |
| `replace` | 0.0 | Contrafactual de reemplazo |
| `corrupt` | 0.0 | Contrafactual de corrupción |

La cobertura esperada de lectura fue 1.0 en los ocho casos. La diferencia entre
el control positivo y los controles negativos fue 1.0 en cada caso, por lo que
el control de no-vacuidad también pasó.

## Evidencia

- Artefacto: `corpus_rsi/past_heldout_v1_evidence.json`.
- SHA-256 de los bytes del archivo:
  `56faec0989ec3a8968865ab5b11c4c7036fc5f7a5ebc0aa8f8701744aba33828`.
- Sello canónico declarado dentro del JSON:
  `f96903a346478c20ad73699998c9326f27247a466b70c6a69e8527f701e9f52b`.
- El artefacto no conserva prompts, respuestas, contenidos sintéticos ni tokens
  esperados en crudo; conserva hashes, métricas, trazas de lectura y veredictos.

Comando portable de reproducción (funciona desde cualquier directorio):

```bash
python3 scripts/run_past_heldout_v1.py \
  --evidence /tmp/past_heldout_v1_reproduction.json
```

Tests de assurance:

```bash
python3 -m pytest -q --noconftest memory/test_past_heldout_v1.py
```

Resultado observado: `8 passed`.

## Límites y siguiente gate

El runtime exacto está diseñado para comprobar el cableado y la atribución del
harness bajo datos nuevos, no para estimar capacidad general de un LLM. No hay
p-valores ni inferencia poblacional porque los endpoints son deterministas y
exactos. Antes de promover cualquier claim productivo hace falta una revisión
independiente ligada a estos bytes y una corrida separada con runtime vivo,
modelo atestado y canario que no toque producción.
