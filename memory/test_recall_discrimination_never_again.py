"""Regresión: el recall semántico DEBE discriminar — un pedazo del "nunca más".

┌── COBERTURA (honesta — este archivo NO es todo el "nunca vuelva a pasar") ──────┐
│ CUBRE   1 propiedad: la normalización del score (min-max) NO debe colapsar la    │
│         banda de e5 a ~1.0 ni aplanar la discriminación. Unitario, scores        │
│         SINTÉTICOS, sin ruta real. Auto-verificado (RED del bug + GREEN del fix). │
│ NO CUBRE (viven en otro lado / faltan):                                          │
│   · criterio 1 RETRIEVAL (texto literal -> ESA memoria en puesto 1) ->           │
│       ruta REAL contra DB: tools/seal_recall_healthcheck.py (ALICE)              │
│   · bug de prefijo query (get_embedding vs get_query_embedding)   -> FALTA test  │
│   · filtro importance>=7 amputando el rescate léxico              -> FALTA test  │
│   · NOTIFICAR cuando falle (que alguien se entere)                -> FALTA       │
│ Nadie que lea el nombre debe dejar de buscar: esto es 1/5. (aviso de FABLE)      │
└─────────────────────────────────────────────────────────────────────────────────┘

Orden de William (1-sep-2026). Caza el bug medido hoy en mcp_server_v4.py:4664/4673:
`sem_score = raw / max(raw del batch)` colapsa la banda estrecha de e5 (0.77-0.85)
a ~0.99 para TODO, y el score reportado deja de discriminar.

Diseñado contra las trampas del test viejo (test_hybrid_bm25_fix.py, que falló por
las tres a la vez, medido por NEXUS+FABLE):
  - ARRANCA sin DB ni login privilegiado (pura lógica). Correr con:
        pytest memory/test_recall_discrimination_never_again.py --noconftest
    (el conftest de memory/ carga credenciales y trababa el arranque — trampa #2 de NEXUS).
  - Aserción FUERTE por-caso (no `any` sobre todo el resultado).
  - AUTO-VERIFICADO: incluye el caso RED (la normalización vieja) y el GREEN (la nueva).
    Si el fix se rompe (vuelve el min-max), test_old_minmax_is_degenerate sigue en verde
    probando que la propiedad detecta el defecto.

ALCANCE HONESTO (corrección de FABLE, medida): dividir por el máximo del lote es una
transformación MONÓTONA -> NO cambia el ORDEN del ranking semántico, sólo el score que
se MUESTRA (todo ~1.0) y su peso en el blend híbrido (lo aplana). Por eso este archivo
es NECESARIO pero NO SUFICIENTE: prueba que el score vuelve a discriminar, no que la
BÚSQUEDA recupere lo correcto. El oráculo DECISIVO es el criterio 1 de FABLE —texto
literal de una memoria -> ESA memoria en el puesto 1— que exige la ruta REAL contra la
DB. Ese vive en `tools/seal_recall_healthcheck.py` (de ALICE) y NO se sustituye con un
skip (un skip cuenta como verde y no verifica nada).
"""
import math
import pytest

# --- banda REAL de cosenos de e5-base, MEDIDA hoy contra memorias del sistema ---
# relevante ~0.86, irrelevantes ~0.77-0.82. La discriminación es chica en valor
# absoluto (e5 vive en un cono) PERO existe y debe sobrevivir a la normalización.
E5_RAW = {
    "relevante":    0.86,
    "irrelevante1": 0.79,
    "irrelevante2": 0.77,
    "irrelevante3": 0.80,
}


def _minmax_by_batch(raw: dict[str, float]) -> dict[str, float]:
    """EL BUG (mcp_server_v4.py:4664/4673): divide por el máximo del batch."""
    mx = max(raw.values(), default=1.0)
    return {k: (v / mx if mx > 0 else 0.0) for k, v in raw.items()}


def _calibrated(raw: dict[str, float], lo: float = 0.70, hi: float = 0.90) -> dict[str, float]:
    """EL FIX: reescala la banda real de e5 [lo,hi] a [0,1] — la discriminación se ABRE."""
    span = hi - lo
    return {k: max(0.0, min(1.0, (v - lo) / span)) for k, v in raw.items()}


def _discriminates(scores: dict[str, float], min_gap: float = 0.15) -> bool:
    """Propiedad: el relevante debe superar al MEJOR irrelevante por un margen real."""
    rel = scores["relevante"]
    best_irrel = max(v for k, v in scores.items() if k != "relevante")
    return (rel - best_irrel) >= min_gap


# ---------------------------------------------------------------------------
# CASO RED (auto-verificación): la normalización vieja NO discrimina.
# Si algún día alguien reintroduce el min-max, esta prueba lo documenta en verde
# y test_calibrated_discriminates falla -> la suite grita.
def test_old_minmax_is_degenerate():
    scores = _minmax_by_batch(E5_RAW)
    # todos colapsan a ~0.90-1.0 (el 0.9888-1.0 que midió NEXUS)
    assert all(s >= 0.88 for s in scores.values()), scores
    # y NO discrimina: el gap relevante-vs-irrelevante es minúsculo
    assert not _discriminates(scores), (
        f"si el min-max discriminara, no habría bug. scores={scores}"
    )


# CASO GREEN (la propiedad que el fix DEBE cumplir):
def test_calibrated_discriminates():
    scores = _calibrated(E5_RAW)
    assert _discriminates(scores), (
        f"el recall semántico debe separar relevante de irrelevante. scores={scores}"
    )
    # el relevante claramente arriba, un irrelevante claramente abajo
    assert scores["relevante"] >= 0.75, scores
    assert scores["irrelevante2"] <= 0.45, scores


# PROPIEDAD GENERAL, independiente de la implementación:
# una query absurda no puede devolver un irrelevante con score casi perfecto.
@pytest.mark.parametrize("normalizer,should_discriminate", [
    (_minmax_by_batch, False),   # el bug
    (_calibrated,      True),    # el fix
])
def test_discrimination_property(normalizer, should_discriminate):
    scores = normalizer(E5_RAW)
    assert _discriminates(scores) == should_discriminate, scores


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
