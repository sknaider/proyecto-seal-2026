"""Timeout por eslabón: el respaldo para el agente TRABADO, que no puede ceder.

William, textual: "timeout por eslabon, si uno no publica en ~90 s, el turno
PASA al siguiente".

Este test existe porque la constante `CASCADE_TURN_TIMEOUT_S` estuvo definida y
SIN UN SOLO LECTOR: el único grep que la encontraba era su propia definición. El
comentario del código describía el mecanismo —"el timeout es el respaldo SOLO
para el trabado, que por definición no puede ceder"— y el mecanismo no existía,
que es peor que no documentarlo.

Se vio en vivo el 2-sep 22:58: la cadena quedó clavada en ADA (trabada, justo el
caso que el respaldo cubre) mientras otros publicaban fuera de orden.
"""
import pathlib
import sys

import pytest

_MESSAGES = pathlib.Path(__file__).resolve().parents[1]
if str(_MESSAGES) not in sys.path:      # nunca incondicional: mediría PRODUCCIÓN
    sys.path.insert(0, str(_MESSAGES))  # y dejaría vivos a los mutantes

import soul_coordination as sc  # noqa: E402

T = sc.CASCADE_TURN_TIMEOUT_S


def _abierto(**kw):
    filas = sc.build_assignments("cascade", "JARVIS", candidates=sc.DEFAULT_ROSTER, **kw)
    abierto = next((r["agent"] for r in filas if r.get("cascade_open")), None)
    estados = {r["agent"]: r.get("cascade_state") for r in filas if "cascade_state" in r}
    return abierto, estados


def test_sin_dato_de_tiempo_nadie_salta():
    """Compatibilidad: sin `turn_opened_at` se comporta como antes del timeout."""
    abierto, _ = _abierto()
    assert abierto == sc.cascade_order()[0]


@pytest.mark.parametrize(
    ("segundos", "esperado"),
    [
        (T - 1, "ADA"),      # todavía es su turno
        (T + 5, "ALICE"),    # venció uno
        (T * 2 + 5, "NEXUS"),
        (T * 4 + 5, "FABLE"),
    ],
)
def test_el_turno_pasa_al_siguiente_cada_90_segundos(segundos, esperado):
    """El caso REAL de las 22:58: JARVIS publicó y ADA quedó trabada."""
    abierto, _ = _abierto(published=["JARVIS"], turn_opened_at=0.0, now=float(segundos))
    assert abierto == esperado


def test_el_ultimo_eslabon_nunca_pierde_el_turno():
    """La cadena tiene que terminar en ALGUIEN, no en nadie.

    Sin este tope, un `now` muy grande deja `abierto=None` y el turno no es de
    nadie: el silencio total, que es el defecto opuesto y peor que el flood.
    """
    abierto, _ = _abierto(published=["JARVIS"], turn_opened_at=0.0, now=1e9)
    assert abierto is not None
    assert abierto == sc.cascade_order()[-1]


def test_vencer_no_es_ceder():
    """`timed_out` y `yielded` NO se colapsan: distinguen trabado de "no aporto".

    Un silencio no separa "no tengo nada que agregar" de "estoy trabado". Si el
    estado fuera el mismo, se perdería la única señal que delata a un agente
    caído — que fue exactamente lo que pasó con ADA: 1 h 56 min muda mientras su
    bridge respondía "te leí".
    """
    _, estados = _abierto(published=["JARVIS"], turn_opened_at=0.0, now=T + 5)
    assert estados["ADA"] == "timed_out"

    _, estados_cede = _abierto(published=["JARVIS"], yielded=["ADA"])
    assert estados_cede["ADA"] == "yielded"


def test_el_que_ya_publico_no_se_marca_vencido():
    """Un turno usado está cerrado; el reloj no puede reabrirlo ni ensuciarlo."""
    _, estados = _abierto(published=["JARVIS"], turn_opened_at=0.0, now=T * 3)
    assert estados["JARVIS"] == "published"
