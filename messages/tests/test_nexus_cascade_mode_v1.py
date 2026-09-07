"""Contrato del modo CASCADA del coordinador (William, 2-sep-2026).

Orden: JARVIS -> ALICE -> NEXUS -> FABLE.  ADA fuera (runtime Codex).
Spec: agents/JARVIS/spec_cascade_mode_20260902.md

Lo que estos tests protegen, y por qué cada uno:
  - NUNCA dos voces a la vez  -> es el punto entero del modo
  - el turno AVANZA           -> si no avanza, la cascada cuelga al equipo
  - flag FAIL-CLOSED          -> un archivo ausente no estrena un modo nuevo
                                 sobre el coordinador de los cinco
  - NO-REGRESION con flag OFF -> el control que me refutaría: si mi cambio
                                 altera lo de hoy con el modo apagado, está mal
"""
import itertools

import pytest
import sys
from pathlib import Path

# NO insertar la ruta incondicionalmente: eso GANA sobre el PYTHONPATH y hace
# que el test mida SIEMPRE produccion, aunque un arnes ponga una copia mutada
# adelante. Lo detecte con 2 mutantes que sobrevivieron dando el MISMO resultado
# que el original — el sintoma de un test que cablea su sujeto. Es el defecto
# que yo mismo le bloquee a JARVIS esta manana (H1, 13:20).
try:  # el arnes de mutacion pone su copia primero en PYTHONPATH: que gane
    import soul_coordination as sc
except ModuleNotFoundError:  # ejecucion suelta desde el repo
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import soul_coordination as sc  # noqa: E402

ROSTER = ["JARVIS", "ALICE", "NEXUS", "FABLE", "ADA"]
CADENA = ["JARVIS", "ALICE", "NEXUS", "FABLE"]


@pytest.fixture(autouse=True)
def _aislar_del_estado_de_produccion(tmp_path, monkeypatch):
    """Los tests NO deben leer los archivos de control reales.

    Medido: al escribir `messages/.cascade_ada_ok` en produccion (decision de
    William, 14:12), 5 tests que asumian la cadena de cuatro se pusieron rojos.
    Un test que depende del estado del sistema no prueba el codigo: prueba el
    estado. Cada test declara el orden que necesita.
    """
    monkeypatch.setattr(sc, "_ADA_IN_CASCADE_PATH", str(tmp_path / "sin-recibo"))
    monkeypatch.setattr(sc, "_CASCADE_FLAG_PATH", str(tmp_path / "sin-flag"))


def _voces(published=None, text=""):
    filas = sc.build_assignments(
        "cascade", "JARVIS", text=text, candidates=ROSTER, published=published or []
    )
    return [f["agent"] for f in filas if f["public_write"]]


def test_nunca_dos_voces_en_ninguna_combinacion():
    for k in range(len(CADENA) + 1):
        for combo in itertools.combinations(CADENA, k):
            assert len(_voces(list(combo))) <= 1, f"dos voces con published={combo}"


def test_el_turno_avanza_en_el_orden_de_william():
    pub = []
    for esperado in CADENA:
        voces = _voces(pub)
        assert voces == [esperado], f"esperaba {esperado}, hubo {voces}"
        pub.append(esperado)
    assert _voces(pub) == [], "la cascada debe cerrarse tras el ultimo"


def test_ada_entra_en_la_cadena_cuando_su_prueba_paso(tmp_path, monkeypatch):
    """William fijo el orden CON ADA el 2-sep 14:12:59 tras la prueba de JARVIS.

    El recibo (`messages/.cascade_ada_ok`) es lo que la mete: sin el, la cadena
    vuelve a cuatro. No se activa por creerlo.
    """
    recibo = tmp_path / "ok"
    recibo.write_text("OK")
    monkeypatch.setattr(sc, "_ADA_IN_CASCADE_PATH", str(recibo))
    assert sc.cascade_order() == ("JARVIS", "ADA", "ALICE", "NEXUS", "FABLE")
    monkeypatch.setattr(sc, "_ADA_IN_CASCADE_PATH", str(tmp_path / "no-existe"))
    assert sc.cascade_order() == ("JARVIS", "ALICE", "NEXUS", "FABLE")


def test_ada_sin_voz_cuando_no_se_la_nombra_y_sin_recibo():
    for k in range(len(CADENA) + 1):
        for combo in itertools.combinations(CADENA, k):
            filas = sc.build_assignments(
                "cascade", "JARVIS", candidates=ROSTER, published=list(combo)
            )
            for f in filas:
                if f["agent"] == "ADA":
                    assert not f["public_write"], "ADA quedo fuera de la cadena"


def test_nombrado_abre_la_cascada():
    assert _voces(text="FABLE que opinas de esto") == ["FABLE"]


def test_ada_nombrada_SI_recibe_el_turno():
    """La exencion del agente NOMBRADO (22-jul) gana sobre la cadena.

    El test de arriba probaba solo el caso sin nombrar y su nombre prometia
    "nunca" — lo marco JARVIS revisando. Este es el caso positivo que faltaba:
    si William nombra a ADA, ADA abre, aunque este fuera del orden.
    """
    assert _voces(text="ADA revisa esto por favor") == ["ADA"]


def test_flag_es_fail_closed(tmp_path, monkeypatch):
    ausente = tmp_path / "no-existe"
    monkeypatch.setattr(sc, "_CASCADE_FLAG_PATH", str(ausente))
    assert sc.cascade_enabled() is False, "sin archivo NO debe activarse"
    invalido = tmp_path / "raro"
    invalido.write_text("quizas")
    monkeypatch.setattr(sc, "_CASCADE_FLAG_PATH", str(invalido))
    assert sc.cascade_enabled() is False, "un valor invalido NO debe activarse"
    prendido = tmp_path / "on"
    prendido.write_text("ON\n")
    monkeypatch.setattr(sc, "_CASCADE_FLAG_PATH", str(prendido))
    assert sc.cascade_enabled() is True


def test_sin_flag_la_clasificacion_es_la_de_siempre(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "_CASCADE_FLAG_PATH", str(tmp_path / "no-existe"))
    assert sc.classify_mode("que opinan del umbral del carril", to="equipo") == "discussion"
    assert sc.classify_mode("buenos dias", to="equipo") == "social"
    assert sc.classify_mode("chicos todos opinen", to="equipo") == "roundtable"


def test_con_flag_solo_discussion_se_vuelve_cascade(tmp_path, monkeypatch):
    on = tmp_path / "on"
    on.write_text("ON")
    monkeypatch.setattr(sc, "_CASCADE_FLAG_PATH", str(on))
    assert sc.classify_mode("que opinan del umbral del carril", to="equipo") == "cascade"
    # el saludo y el roundtable NO cambian: son las dos reglas de oro que el
    # modo nuevo no debe pisar (31-jul saludo, 30-jul audiencia grupal)
    assert sc.classify_mode("buenos dias", to="equipo") == "social"
    assert sc.classify_mode("chicos todos opinen", to="equipo") == "roundtable"


def test_la_cesion_avanza_el_turno_y_no_cuelga_la_cascada():
    """H1 de JARVIS (14:03): publicar y ceder son distintos y los dos cierran turno.

    Mi primera version mezclaba ambos en `published`: el agente sin nada que
    aportar no publicaba, y sin senal de cesion el turno NUNCA avanzaba —
    la cascada quedaba colgada en el primero que no tuviera aporte.
    """
    voces = _voces_full(published=["JARVIS"], yielded=["ALICE"])
    assert voces == ["NEXUS"], f"tras ceder ALICE el turno debe pasar a NEXUS, hubo {voces}"


def test_cascada_entera_por_cesion_no_se_cuelga():
    """Si TODOS ceden, la cascada termina; no se queda esperando para siempre."""
    assert _voces_full(yielded=["JARVIS"]) == ["ALICE"]
    assert _voces_full(yielded=["JARVIS", "ALICE"]) == ["NEXUS"]
    assert _voces_full(yielded=["JARVIS", "ALICE", "NEXUS"]) == ["FABLE"]
    assert _voces_full(yielded=list(CADENA)) == [], "con todos cedidos la cascada cierra"


def test_el_estado_de_cada_agente_es_distinguible():
    """publicado / cedido / esperando no pueden colapsar: el que CEDE queda
    registrado, no desaparece en silencio (requisito de ALICE, 13:56)."""
    filas = sc.build_assignments(
        "cascade", "JARVIS", candidates=ROSTER,
        published=["JARVIS"], yielded=["ALICE"],
    )
    estado = {f["agent"]: f.get("cascade_state") for f in filas}
    assert estado["JARVIS"] == "published"
    assert estado["ALICE"] == "yielded"
    assert estado["NEXUS"] == "open"
    assert estado["FABLE"] == "waiting"


def _voces_full(published=None, yielded=None, text=""):
    filas = sc.build_assignments(
        "cascade", "JARVIS", text=text, candidates=ROSTER,
        published=published or [], yielded=yielded or [],
    )
    return [f["agent"] for f in filas if f["public_write"]]
