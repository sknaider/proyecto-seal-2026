# Estado verificable de los derivados RSI — 2026-08-19

Este registro separa **infraestructura construida**, **evidencia offline** y
**enforcement productivo**. Ningún artefacto offline se presenta como control
vivo.

## Estado por incremento

| Incremento | Estado medido | Evidencia | Residuo real |
|---|---|---|---|
| Traza determinista del shield | **OFFLINE TEST-GREEN** | Regenera byte-idéntico; 35/39 detectados, 4 evasiones; controles positivo/negativo/no-vacuo | No cubre semántica ni el guard multi-turno; no es un cambio del shield vivo |
| T5 / extracción de memoria | **PROBE + POLICY SEAM TEST-GREEN** | Probe byte-idéntico; el shield deja pasar 5/5 vectores T5; policy seam 16/16 tests | **No cableado**: falta identidad autenticada del interlocutor, `owner_subject` autoritativo/inmutable y presupuesto durable entre workers/reinicios |
| RSI-T6 / reconstrucción del grafo | **OFFLINE TEST-GREEN** | Baseline stateless reconstruye 12/12 aristas; guarda acumulativa deja 5/12; benigno 2/2; 5/5 tests | No prueba ni modifica el connectome vivo; enforcement productivo requiere contadores atómicos y contrato de finalidad |
| PAST / atribución-ablation | **HARNESS TEST-GREEN, EXPERIMENTO PENDIENTE** | Harness + adapter read-only: 32/32 tests; seis brazos, metadata hash-only y fail-closed | Falta corpus assurance held-out bajo custodia de FABLE, corrida viva, evidencia JSON final y análisis preregistrado. No afirmar causalidad |
| E3 held-out | **PENDIENTE** | La batería E3 previa existe, pero no fue convertida retroactivamente en held-out | Curar y custodiar casos assurance nuevos fuera del alcance del constructor |

`TEST-GREEN` significa que la evidencia del constructor reproduce; el gate final
requiere además revisión independiente ligada a los bytes exactos.

## Gates reproducidos por ADA

```text
PYTHONPATH=corpus_rsi:memory:. python3 -m pytest -q \
  corpus_rsi/test_t6_graph_reconstruction_probe.py \
  corpus_rsi/test_absorbed_derivatives.py \
  memory/test_past_attribution_harness.py \
  memory/test_past_live_adapter.py
=> 40 passed

PYTHONPATH=soul-platform/src python3 -m pytest -q \
  soul-platform/tests/test_t5_memory_egress_v1.py
=> 16 passed

cd soul-platform && .venv-quality-040/bin/python -m pytest -q tests
=> 145 passed, 1 skipped
```

La corrida amplia anterior que usó `soul-platform/.venv` cargó Core 0.3.0 y dio
errores de incompatibilidad. El entorno correcto `.venv-quality-040` carga
Core 0.4.2 y deja la suite completa verde; por eso los errores del venv viejo
no se atribuyen a T5.

## Decisión de arquitectura T5

El punto futuro de integración es
`soul-platform/src/soul_platform/proxy.py::soul_context`, entre
`memory.search()` y la concatenación de `hit.memory.content`. No se conecta
todavía porque el token actual autentica la máquina, no al humano, y el modelo
de memoria expone `agent/scope/metadata` pero no un dueño humano autoritativo.
Derivar el dueño desde prompt, header libre o metadata escribible sería un
control falso.

La policy seam nueva queda lista para recibir esos datos cuando existan. Su
default es fail-closed y además limita extracción gradual multi-turno; un simple
filtro `owner == interlocutor` no habría cerrado la amenaza acumulativa.

## Veredicto

ALICE tenía razón en que T6 y el cableado T5 eran residuos, pero su cierre
omitía PAST y mezclaba evidencia local con versionado/enforcement. El estado
correcto es: **shield, T5 y RSI-T6 cerrados como evidencia offline; policy T5
construida pero no integrada; PAST y E3 held-out todavía no autorizan claims
finales.**
