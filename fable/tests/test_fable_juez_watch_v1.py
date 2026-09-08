"""Brazos del vigilante del juez.

El defecto que motivó estos brazos no era sutil y estuvo horas: la lista de
canales era literal (`{"fable-juez", "dm:fable:william"}`), así que FABLE sólo
veía los DM de William. El 8-sep el caso de NEXUS y los archivos de JARVIS
quedaron guardados en la base sin que su sesión los viera nunca — y los tres
veredictos que faltaban no faltaban por lentitud del juez.

Por eso los brazos de abajo separan dos cosas que la lista mezclaba: **qué le
corresponde al juez** y **quién se lo escribe**.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import fable_juez_watch as w  # noqa: E402


# ───────────────── qa_positive: lo que el juez DEBE recibir ─────────────────

@pytest.mark.parametrize("canal", [
    "fable-juez",
    "dm:fable:william",
    "dm:fable:nexus",     # el caso que quedó sin ver el 8-sep
    "dm:fable:jarvis",    # los archivos que quedaron sin ver el 8-sep
    "dm:fable:alice",
    "dm:fable:ada",
    "dm:fable:dum",
])
def test_qa_positive_todo_DM_dirigido_a_fable_le_corresponde(canal):
    assert w.escucha(canal) is True


def test_qa_positive_un_agente_NUEVO_no_necesita_tocar_el_codigo():
    """La razón de usar prefijo y no lista: una lista se queda vieja en cuanto
    entra un agente, y el síntoma es silencio, no un error."""
    assert w.escucha("dm:fable:agente-que-no-existe-todavia") is True


def test_qa_positive_no_distingue_mayusculas():
    assert w.escucha("DM:FABLE:ALICE") is True
    assert w.escucha("Fable-Juez") is True


# ─────────────── qa_negative: lo que NO debe entrar al juez ────────────────

@pytest.mark.parametrize("canal", [
    "web_chat",
    "latidos",
    "dm:alice:nexus",        # DM entre otros dos: no es asunto del juez
    "dm:jarvis:william",
    "user:3:tareas",
    "internal:algo",
    "",
])
def test_qa_negative_no_escucha_lo_ajeno(canal):
    assert w.escucha(canal) is False


def test_qa_negative_un_canal_que_solo_CONTIENE_fable_no_alcanza():
    """`dm:fable:` es un PREFIJO, no una subcadena. Si se relajara a `in`,
    el juez empezaría a recibir conversaciones que no le tocan."""
    assert w.escucha("registro-dm:fable:nexus") is False
    assert w.escucha("copia-de-fable-juez") is False


# ───────────────────── control: robustez de la entrada ─────────────────────

def test_qa_control_entradas_raras_no_explotan():
    assert w.escucha(None) is False          # type: ignore[arg-type]
    assert w.escucha("   ") is False
    assert w.escucha("  dm:fable:nexus  ") is True   # con espacios sigue siendo suyo


def test_qa_control_el_anuncio_de_arranque_se_deriva_de_la_regla(capsys):
    """Un anuncio fijo mentiría si mañana cambia el criterio. Debe nombrar el
    prefijo, no una lista de personas."""
    fuente = pathlib.Path(w.__file__).read_text(encoding="utf-8")
    assert 'escuchando fable-juez y dm:fable:william' not in fuente, \
        "el anuncio volvio a ser una lista literal"
    assert "PREFIJO_DM" in fuente


def test_el_filtro_de_autoescucha_sigue_puesto():
    """FABLE no debe reaccionar a sus propios mensajes: sin esto, publicar un
    veredicto en `fable-juez` lo despierta con su propio texto."""
    fuente = pathlib.Path(w.__file__).read_text(encoding="utf-8")
    assert 'str(d.get("from", "")).upper() == "FABLE"' in fuente


# ───────────────── el evento completo, como llega en el jsonl ──────────────

def _pasa(evento: dict) -> bool:
    """Reproduce la decisión del bucle: canal correcto y no escrito por FABLE."""
    ch = str(evento.get("channel", "")).lower()
    if not w.escucha(ch):
        return False
    return str(evento.get("from", "")).upper() != "FABLE"


def test_el_caso_real_del_8_sep_ahora_pasa():
    evento = json.loads(json.dumps({
        "id": "api_nexus_1788858429066813403",
        "from": "NEXUS", "to": "FABLE",
        "channel": "dm:FABLE:NEXUS",
        "message": "CASO PARA EL JUEZ — expediente ...",
    }))
    assert _pasa(evento) is True


def test_un_veredicto_propio_de_fable_sigue_sin_despertarlo():
    assert _pasa({"from": "FABLE", "channel": "fable-juez", "message": "VEREDICTO"}) is False


# ───── el hueco que FABLE midió sobre mi propio arreglo (8-sep): DM cifrado ─────

def _cifrado(mensaje: str, clave=None):
    """Evento tal como lo escribe chat_server: `message` cifrado y `_encrypted`."""
    from cryptography.fernet import Fernet
    clave = clave or Fernet.generate_key()
    return {"from": "NEXUS", "channel": "dm:fable:nexus", "_encrypted": True,
            "message": Fernet(clave).encrypt(mensaje.encode()).decode("ascii")}, clave


def test_qa_positive_un_DM_CIFRADO_se_entrega_en_claro(tmp_path, monkeypatch):
    """Pasar el filtro no es poder leerse. Con el prefijo arreglado el DM llegaba
    como `gAAAAABq…` y el juez seguía sin poder leerlo: ninguno de los 22 brazos
    anteriores ejercía un DM cifrado, así que nadie lo vio."""
    evento, clave = _cifrado("CASO PARA EL JUEZ — expediente X")
    k = tmp_path / "clave"; k.write_bytes(clave)
    monkeypatch.setattr(w, "CLAVE_DM", str(k)); monkeypatch.setattr(w, "_fernet", None)
    assert w.descifra(evento)["message"] == "CASO PARA EL JUEZ — expediente X"


def test_qa_control_sin_clave_NO_rompe_el_vigilante(tmp_path, monkeypatch):
    """Falla ABIERTO: un juez sin vigilante es peor que un mensaje ilegible."""
    evento, _ = _cifrado("lo que sea")
    monkeypatch.setattr(w, "CLAVE_DM", str(tmp_path / "no-existe")); monkeypatch.setattr(w, "_fernet", None)
    salida = w.descifra(evento)
    assert "no hay clave" in salida["message"]
    assert not salida["message"].startswith("gAAAA"), "no debe entregar el ciphertext crudo"


def test_qa_control_con_la_clave_EQUIVOCADA_lo_dice(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    evento, _ = _cifrado("secreto")
    otra = tmp_path / "otra"; otra.write_bytes(Fernet.generate_key())
    monkeypatch.setattr(w, "CLAVE_DM", str(otra)); monkeypatch.setattr(w, "_fernet", None)
    salida = w.descifra(evento)
    assert "no coincide" in salida["message"]


def test_qa_negative_un_mensaje_EN_CLARO_no_se_toca(tmp_path, monkeypatch):
    """Sin la marca `_encrypted` el texto pasa igual: los mensajes de
    `fable-juez` no van cifrados y no deben tocarse."""
    monkeypatch.setattr(w, "_fernet", None)
    e = {"from": "NEXUS", "channel": "fable-juez", "message": "texto plano"}
    assert w.descifra(e)["message"] == "texto plano"


def test_qa_control_el_descifrado_NO_deja_la_clave_en_la_salida(tmp_path, monkeypatch):
    evento, clave = _cifrado("hola")
    k = tmp_path / "clave"; k.write_bytes(clave)
    monkeypatch.setattr(w, "CLAVE_DM", str(k)); monkeypatch.setattr(w, "_fernet", None)
    salida = json.dumps(w.descifra(evento))
    assert clave.decode() not in salida
    assert "_encrypted" not in salida, "la marca se quita tras descifrar"
