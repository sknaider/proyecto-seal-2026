# PRE-REGISTRO — Ablation Harness de atribución (SEAL-Bench / Recall Router)
**Autor (VERIFICADOR independiente):** ALICE · **Fecha:** 18-ago-2026 · v0 (propuesta)
**Constructor (separado, por §2.1):** ADA (harness `memory/past_attribution_harness.py` construido).
**Verificador reservado por diseño de ADA:** FABLE (config línea 168). ALICE = revisión de código + 2do lente.
**Fuente:** PAST-Bench 2608.04003 §2.1 del dossier RSI.

> **Propósito del pre-registro:** fijar el análisis ANTES de ver resultados, para que
> ningún acierto se reclasifique post-hoc y el constructor no sesgue la lectura. El
> verificador escribe esto; el constructor construye para satisfacerlo, no al revés.

## 1. Afirmación bajo prueba
Los aciertos de SEAL-Bench (59/60) ocurren PORQUE el Recall Router recuperó e inyectó el
recuerdo correcto — no porque el modelo base habría acertado igual sin memoria.
**H0 (nula, la que hay que refutar):** el acierto NO depende de la memoria inyectada.

## 2. Diseño — 3 condiciones por caso (mismo N, mismos ítems)
- **Control:** memoria real (pipeline actual).
- **Placebo:** recuerdos IRRELEVANTES del mismo volumen de tokens (controla "más contexto
  ayuda" independiente del contenido).
- **Nulo:** sin inyección de memoria.
Requisito del constructor: instrumentar el Router para persistir, por turno, los IDs exactos
de memoria inyectados + su fuente (de las 5: duraderas / conversación / destilados /
continuidad / reglas activas).

## 3. Criterio de PATHWAY-EVIDENCE (la definición operativa, fijada ya)
**SUPERSEDIDO por el harness de ADA (más riguroso — revisado y aceptado por ALICE):**
- `control_upper_bound = max(nulo, placebo)` → el acierto debe superar el MEJOR de AMBOS controles
  (no un simple OR; es la elección conservadora correcta).
- ADEMÁS gate de `read_coverage`: aunque pase, si NO leyó el recuerdo inyectado → 'headline_gain
  _without_pathway_evidence'. Verifica el CAMINO causal, no solo el delta de score.
- + counterfactuals delete/replace/corrupt como gates extra.
Verdicts del harness: independent_of_persistence / no_headline_gain / headline_gain_without_
pathway_evidence / control_not_cleared / counterfactual_not_cleared / pathway_supported.
Un acierto que NO cambia entre las 3 → **SIN pathway-evidence** (el modelo acertó por
conocimiento propio, no por SOUL). Se cuenta y se CONSERVA, no se descarta.

## 4. Métricas de salida (desagregadas, NO una tasa global)
- `n_con_pathway` / `n_sin_pathway` / 60. (Prohibido sumarlas a una tasa nueva — §IV.5.)
- Por-fuente: apagar UNA de las 5 fuentes a la vez → delta de aciertos con pathway.
- Reportar Recall@k / MRR contra placebo si el harness lo permite (brecha §III.B del v9).

## 5. Análisis estadístico (fijado)
- N=60 por condición, ítems apareados → McNemar sobre Control-vs-Nulo y Control-vs-Placebo.
- Reportar el conteo crudo + IC; NO afirmar "causalidad" hasta correr las 3 condiciones con
  repeticiones y semillas fijadas. Antes de eso: "evidencia de atribución", no causalidad.
- Dependencia de modelo: correr con ≥1 modelo local además del principal si es viable (L5).

## 6. Compromiso de honestidad (pre-comprometido)
- **El número incómodo se publica.** Si k de 60 no tienen pathway-evidence, k se reporta.
  Estimación esperada declarada ANTES de medir: es plausible que sea NO trivial (§2.1 risk).
- Constructor ≠ verificador: el constructor NO ejecuta el análisis final; lo ejecuto yo
  (ALICE) o un tercero, sobre los bytes/artefactos congelados del constructor.
- Nivel objetivo: E2 con trazabilidad causal (no E3 automático).

## 7. Qué NO decide este harness
No mide si la memoria es "buena", solo si CAUSA el acierto. No sube el 59/60 a causal por
sí solo (corrección de ADA): habilita atribución; causalidad = tras intervenciones +
repeticiones + este pre-registro cumplido.
