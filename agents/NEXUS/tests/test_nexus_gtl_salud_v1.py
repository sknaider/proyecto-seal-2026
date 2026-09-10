"""Brazos del vigía de gtl.pe.

El brazo que da sentido a todo el archivo es
`test_qa_negative_un_envio_DUPLICADO_no_cuenta_como_entregado`: reproduce, con la
respuesta literal del servidor, el defecto que dejó a gtl.pe tres días sin vigilancia
mientras la alarma "funcionaba".

    {"ok":true,"id":"api_nexus_1788808768010947272","duplicate":true}

428 disparos, 1 entrega, y el emisor viendo `ok:true` cada vez.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import gtl_salud_daemon as g  # noqa: E402

AHORA = _dt.datetime(2026, 9, 10, 18, 30, tzinfo=_dt.timezone.utc)
DUPLICADA = '{"ok":true,"id":"api_nexus_1788808768010947272","duplicate":true}'
BUENA = '{"ok":true,"id":"api_nexus_1789065693735765711"}'


# ───────── el hallazgo, convertido en mecanismo: ok:true NO es entregado ─────────

def test_qa_negative_un_envio_DUPLICADO_no_cuenta_como_entregado():
    """LA razón de existir de este archivo. Con `ok:true` a secas, este brazo pasa
    y el vigía vuelve a poder quedar mudo en silencio."""
    assert g.entregado(DUPLICADA) is False


def test_qa_positive_un_envio_con_id_nuevo_SI_cuenta():
    assert g.entregado(BUENA) is True


@pytest.mark.parametrize("respuesta", [
    None, "", "no soy json", "[]", "{}",
    '{"ok":false,"error":"agent_auth_required"}',
    '{"ok":true}',                                   # ok sin id: no hay prueba de entrega
    '{"ok":"true","id":"x"}',                        # ok como CADENA, no como booleano
])
def test_qa_negative_ante_la_duda_el_vigia_asume_que_NO_hablo(respuesta):
    assert g.entregado(respuesta) is False


def test_qa_control_acepta_el_dict_ya_parseado():
    assert g.entregado({"ok": True, "id": "z"}) is True
    assert g.entregado({"ok": True, "id": "z", "duplicate": True}) is False


# ─────────────── la clave de idempotencia: derivada, nunca constante ────────────

def test_la_clave_CAMBIA_con_la_hora():
    a = g.clave_de("arriba-a-abajo", AHORA)
    b = g.clave_de("arriba-a-abajo", AHORA + _dt.timedelta(hours=1))
    assert a != b, "una clave que no cambia silencia todo episodio futuro"


def test_la_clave_AGRUPA_dentro_de_la_misma_hora():
    a = g.clave_de("arriba-a-abajo", AHORA)
    b = g.clave_de("arriba-a-abajo", AHORA + _dt.timedelta(minutes=20))
    assert a == b, "sin agrupar, un episodio largo inunda el canal"


def test_la_clave_DISTINGUE_incidentes_distintos_en_la_misma_hora():
    assert g.clave_de("arriba-a-abajo", AHORA) != g.clave_de("abajo-a-arriba", AHORA)


def test_qa_control_la_clave_NO_es_constante_en_el_codigo():
    """El defecto original vivía en el ExecStart, no en el código. Este brazo evita
    que alguien lo replique acá 'para simplificar'."""
    fuente = pathlib.Path(g.__file__).read_text(encoding="utf-8")
    assert "strftime" in fuente
    assert "NEXUS-watcher-alarma-1788367882" not in fuente


# ──────────────────────────────── el sondeo ────────────────────────────────────

class _Resp:
    def __init__(self, code): self.status = code
    def __enter__(self): return self
    def __exit__(self, *a): return False


def test_qa_positive_200_es_arriba():
    assert g.sondear("http://x", abrir=lambda u, timeout=None: _Resp(200))["estado"] == "arriba"


def test_qa_negative_500_es_abajo():
    assert g.sondear("http://x", abrir=lambda u, timeout=None: _Resp(500))["estado"] == "abajo"


def test_un_error_de_RED_no_afirma_que_el_sitio_este_caido():
    """El incidente del 28-jul: confundir «está caído» con «dejé de poder verlo».
    DNS que no resuelve desde esta máquina no dice nada del servidor."""
    def explota(u, timeout=None):
        raise OSError("Name or service not known")
    r = g.sondear("http://x", abrir=explota)
    assert r["estado"] == "NO_MEDIBLE"   # el nombre es de ALICE (colision del 10-sep)
    assert r["estado"] != "abajo"


# ─────────────── la corrida: avisa en transiciones, y falla ruidoso ────────────

def _corrida(tmp_path, estado, previo=None, salida=BUENA, ahora=AHORA):
    ep = tmp_path / "estado.json"
    if previo is not None:
        ep.write_text(json.dumps({"estado": previo}), encoding="utf-8")
    enviados = []
    def ejecutor(argv):
        enviados.append(argv); return salida
    r = g.corrida("http://x", ahora=ahora, estado_path=ep, latido_path=tmp_path / "latido.json",
                  sonda=lambda u: {"estado": estado, "codigo": 200, "detalle": ""},
                  ejecutor=ejecutor)
    return r, enviados


def test_sin_transicion_NO_avisa(tmp_path):
    r, enviados = _corrida(tmp_path, "arriba", previo="arriba")
    assert r["transicion"] is False and r["aviso"] is False
    assert enviados == [], "un vigia que habla en cada sondeo entrena a que lo ignoren"


def test_una_TRANSICION_avisa(tmp_path):
    r, enviados = _corrida(tmp_path, "abajo", previo="arriba")
    assert r["transicion"] is True and r["aviso"] is True and r["entregado"] is True
    assert len(enviados) == 1


def test_qa_negative_si_el_aviso_NO_llega_la_corrida_lo_DICE(tmp_path):
    """El caso que el vigía anterior no podía distinguir: canal muerto, `ok:true`."""
    r, _ = _corrida(tmp_path, "abajo", previo="arriba", salida=DUPLICADA)
    assert r["aviso"] is True
    assert r["entregado"] is False, "un aviso duplicado se estaba contando como entregado"


def test_qa_negative_main_SALE_CON_ERROR_si_el_aviso_no_se_entrego(tmp_path, monkeypatch):
    """Que la corrida lo sepa no alcanza: systemd tiene que verlo en el codigo de
    salida, o la unidad queda 'active' con el vigia mudo."""
    monkeypatch.setattr(g, "corrida", lambda *a, **k: {
        "estado": "abajo", "transicion": True, "aviso": True, "entregado": False})
    assert g.main([]) == 2


def test_qa_control_main_sale_0_cuando_el_aviso_SI_llego(monkeypatch):
    monkeypatch.setattr(g, "corrida", lambda *a, **k: {
        "estado": "abajo", "transicion": True, "aviso": True, "entregado": True})
    assert g.main([]) == 0


# ─────────────────────────────── el latido ─────────────────────────────────────

def test_el_latido_lleva_el_RESULTADO_no_solo_la_hora(tmp_path):
    """Un latido que sólo prueba que el proceso vive no distingue un vigía sano de
    uno colgado contra un socket."""
    _corrida(tmp_path, "abajo", previo="abajo")
    d = json.loads((tmp_path / "latido.json").read_text(encoding="utf-8"))
    assert d["estado"] == "abajo"
    assert "epoch" in d and "cuando" in d


def test_el_latido_se_escribe_AUNQUE_no_haya_transicion(tmp_path):
    """Si sólo latiera al avisar, un sitio sano durante días se veria igual que un
    vigia muerto."""
    _corrida(tmp_path, "arriba", previo="arriba")
    assert (tmp_path / "latido.json").exists()


def test_la_PRIMERA_corrida_no_avisa_pero_deja_estado(tmp_path):
    r, enviados = _corrida(tmp_path, "abajo", previo=None)
    assert r["primera"] is True and r["aviso"] is False
    assert enviados == [], "sin estado previo no hay transicion que reportar"
    assert json.loads((tmp_path / "estado.json").read_text())["estado"] == "abajo"
