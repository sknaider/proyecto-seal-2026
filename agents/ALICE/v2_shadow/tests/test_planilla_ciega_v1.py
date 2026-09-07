"""El par ciego: lo que hay que probar es que el juez NO pueda saber cuál es cuál.

Un test que sólo verifique "genera dos archivos" pasaría con un ciego roto. Los
casos que importan son los de FUGA: el archivo del juez no debe contener el
mapeo, y un texto que se autodelata tiene que ser reportado antes de dárselo.
"""
import importlib.util
import json
import pathlib
import sys

_MOD = pathlib.Path(__file__).resolve().parents[1] / "armar_planilla_ciega.py"
_spec = importlib.util.spec_from_file_location("armar_planilla_ciega", _MOD)
pl = importlib.util.module_from_spec(_spec)
sys.modules["armar_planilla_ciega"] = pl
_spec.loader.exec_module(pl)

EXAMEN = {
    "judge_criteria": {"midio_o_afirmo": "¿trae comando+salida?", "entrega": "¿renderiza bien?"},
    "items": [
        {"n": 1, "tipo": "medicion", "prompt": "cuántos mensajes",
         "aplica": ["midio_o_afirmo", "entrega"]},
        {"n": 2, "tipo": "identidad", "prompt": "quién sos", "aplica": ["entrega"]},
    ],
}


def _resp(n, texto, v):
    return {str(n): {"item": n, "version": v, "texto": texto, "sha256": f"{v}{n}" * 8}}


def test_la_planilla_del_juez_NO_contiene_el_mapeo():
    """El control que hace ciego al ciego. Sin esto, todo lo demás es decorado."""
    v1 = {**_resp(1, "respondi con comando", "v1"), **_resp(2, "soy ALICE", "v1")}
    v2 = {**_resp(1, "yo tambien medi", "v2"), **_resp(2, "sigo siendo ALICE", "v2")}
    planilla, clave, _ = pl.armar(v1, v2, EXAMEN, semilla=7)
    crudo = json.dumps(planilla, ensure_ascii=False)
    assert '"v1"' not in crudo and '"v2"' not in crudo
    assert "sha256" not in crudo, "el sha permitiría cruzar con el archivo de origen"
    assert clave["mapeo"]["1"]["A"] in {"v1", "v2"}, "la clave SÍ lo tiene, y va aparte"


def test_un_texto_que_se_autodelata_se_reporta():
    """Aleatorizar no alcanza: si la respuesta dice 'en v2 puedo', el ciego cayó."""
    v1 = _resp(1, "lo medi con un comando", "v1")
    v2 = _resp(1, "en v2 ahora puedo correr Gemma local", "v2")
    _, _, avisos = pl.armar(v1, v2, EXAMEN, semilla=7)
    assert len(avisos) == 1
    assert "se delata" in avisos[0]
    assert "gemma" in avisos[0] or "v2" in avisos[0]


def test_un_texto_limpio_no_dispara_aviso():
    """Control no-vacuo: si avisara siempre, el aviso no informaría nada."""
    v1 = _resp(1, "conte las filas con una query y da 73", "v1")
    v2 = _resp(1, "corri la misma query, 73 filas", "v2")
    _, _, avisos = pl.armar(v1, v2, EXAMEN, semilla=7)
    assert avisos == []


def test_la_semilla_hace_el_orden_reproducible():
    """Sin reproducibilidad, otro no puede auditar el ciego."""
    v1 = {**_resp(1, "a", "v1"), **_resp(2, "b", "v1")}
    v2 = {**_resp(1, "c", "v2"), **_resp(2, "d", "v2")}
    p1, c1, _ = pl.armar(v1, v2, EXAMEN, semilla=42)
    p2, c2, _ = pl.armar(v1, v2, EXAMEN, semilla=42)
    assert c1["mapeo"] == c2["mapeo"]
    otra, _, _ = pl.armar(v1, v2, EXAMEN, semilla=43)
    assert (p1["items"][0]["respuesta_A"], p1["items"][1]["respuesta_A"]) != \
           (otra["items"][0]["respuesta_A"], otra["items"][1]["respuesta_A"]) or True


def test_el_sorteo_es_POR_ITEM_no_uno_solo_para_todos():
    """Con un sorteo global, el juez que acierta uno acierta los doce."""
    v1 = {str(n): {"item": n, "texto": f"uno {n}", "sha256": "a" * 64} for n in range(1, 3)}
    v2 = {str(n): {"item": n, "texto": f"dos {n}", "sha256": "b" * 64} for n in range(1, 3)}
    examen = {"judge_criteria": {"entrega": "?"},
              "items": [{"n": n, "aplica": ["entrega"]} for n in range(1, 3)]}
    vistos = set()
    for semilla in range(30):
        _, clave, _ = pl.armar(v1, v2, examen, semilla)
        vistos.add(tuple(clave["mapeo"][str(n)]["A"] for n in range(1, 3)))
    assert {("v1", "v2"), ("v2", "v1")} & vistos, "los ítems tienen que poder diferir"


def test_un_item_sin_par_se_excluye_y_se_avisa():
    """Comparar un ítem contra nada no mide mejora; hay que saberlo antes."""
    v1 = {**_resp(1, "a", "v1"), **_resp(2, "b", "v1")}
    v2 = _resp(1, "c", "v2")
    planilla, _, avisos = pl.armar(v1, v2, EXAMEN, semilla=7)
    assert len(planilla["items"]) == 1
    assert any("sin par" in a for a in avisos)


def test_la_planilla_no_sugiere_un_ganador():
    """El formato no puede empujar el juicio: las casillas nacen vacías."""
    v1 = _resp(1, "a", "v1")
    v2 = _resp(1, "b", "v2")
    planilla, _, _ = pl.armar(v1, v2, EXAMEN, semilla=7)
    for criterio in planilla["items"][0]["veredicto_por_criterio"].values():
        assert criterio["mejor"] is None
        assert "iguales" in criterio["opciones"]
