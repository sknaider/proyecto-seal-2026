"""El bridge Codex declara QUÉ CUERPO habla en cada mensaje que publica.

Qué lo generó (ADA, 5-sep-2026, medido): a las 07:21 mi cuerpo Codex arregló y
arrancó `seal-alice-dm-poller` y publicó tres mensajes diciéndolo. Los tres salieron
sin `runtime_instance`, así que el cuerpo Claude —yo— no podía ver que ADA había
actuado, **negó en público haberlo hecho**, y ALICE se disculpó por una lectura que
era CORRECTA. Tres agentes reconstruyeron 15 minutos un arranque "sin autor".

Medido el mismo día, y por eso el arreglo es de adopción y no de mecanismo:

    ALICE   43 de 43 mensajes firmados ALICE_V2
    ADA     59 firmados ADA_CLAUDE  (cuerpo Claude, vía scripts/seal_send.py)
    ADA    181 SIN FIRMA            (cuerpo Codex: este bridge)

**EL TEST QUE IMPORTA es `test_exportar_la_variable_NO_ALCANZA_hay_que_LEERLA`.**
Mi primer arreglo fue sólo `Environment=SEAL_RUNTIME_INSTANCE=` en la unidad, y lo
di por hecho: la variable estaba en el entorno del proceso y el servicio decía
`active`. Pero este POST se arma a mano y **no pasa por `seal_send.py`**, que era la
única lectura que existía. Un dato disponible en el entorno que nadie lee es
idéntico a no tenerlo — y ese mutante lo caza este test, no los otros.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "messages"))


@pytest.fixture()
def bridge(monkeypatch):
    """Importa el módulo REAL y le corta la red, quedándose con el payload."""
    monkeypatch.chdir(RAIZ)
    import ada_codex_remote_bridge as B

    enviados: list[dict] = []

    class _Resp:
        def read(self): return json.dumps({"ok": True, "id": "api_ada_test"}).encode()
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def _urlopen(req, timeout=None):
        enviados.append(json.loads(req.data.decode("utf-8")))
        return _Resp()

    monkeypatch.setattr(B.request, "urlopen", _urlopen)
    monkeypatch.setattr(pathlib.Path, "read_text", lambda self, **k: "token-de-prueba"
                        if self.name == ".agent_session_token_ADA" else pathlib.Path.read_text(self, **k))
    B._enviados_de_prueba = enviados
    return B


def test_el_modulo_IMPORTA(bridge):
    """Compilar no es importar. `py_compile` daba OK con el módulo roto."""
    assert hasattr(bridge, "post_message")


def test_exportar_la_variable_NO_ALCANZA_hay_que_LEERLA(bridge, monkeypatch):
    """EL TEST DEL DEFECTO REAL: la variable en el entorno tiene que llegar al POST.

    Poner `Environment=` en la unidad dejaba la variable disponible y el payload
    seguía saliendo sin firma, porque nadie la leía en este camino.
    """
    monkeypatch.setenv("SEAL_RUNTIME_INSTANCE", "ADA_CODEX_BRIDGE")
    bridge.post_message(to="ADA", message="x")
    assert bridge._enviados_de_prueba[-1]["metadata"]["runtime_instance"] == "ADA_CODEX_BRIDGE"


def test_sin_variable_no_inventa_firma(bridge, monkeypatch):
    """Control negativo: sin la variable no se declara un cuerpo cualquiera."""
    monkeypatch.delenv("SEAL_RUNTIME_INSTANCE", raising=False)
    bridge.post_message(to="ADA", message="x")
    assert "metadata" not in bridge._enviados_de_prueba[-1]


def test_control_no_vacuo_el_test_anterior_puede_fallar(bridge, monkeypatch):
    """Sin esto, `test_sin_variable...` pasaría con un módulo que NUNCA firme."""
    monkeypatch.setenv("SEAL_RUNTIME_INSTANCE", "OTRO_CUERPO")
    bridge.post_message(to="ADA", message="x")
    assert bridge._enviados_de_prueba[-1].get("metadata", {}).get("runtime_instance") == "OTRO_CUERPO"


def test_una_variable_EN_BLANCOS_no_produce_firma(bridge, monkeypatch):
    """LO ENCONTRÓ UN MUTANTE DE NEXUS que sobrevivió: quitar el `.strip()`.

    Él ejecutó las dos versiones en vez de razonarlas, y con `SEAL_RUNTIME_INSTANCE="   "`
    el sujeto sano no firma y el mutante firma **con espacios**. Una firma que existe y
    no dice nada es peor que ausente: rompe la discriminación de mi propio ARM2 de
    entrega, cuyo control es «sin variable -> NULL».
    """
    monkeypatch.setenv("SEAL_RUNTIME_INSTANCE", "   ")
    bridge.post_message(to="ADA", message="x")
    assert "metadata" not in bridge._enviados_de_prueba[-1]


def test_la_firma_viaja_SIN_espacios_alrededor(bridge, monkeypatch):
    """La segunda consecuencia del mismo mutante: un valor con padding rompe cualquier
    filtro por igualdad exacta, que es el uso declarado de este campo."""
    monkeypatch.setenv("SEAL_RUNTIME_INSTANCE", "  ADA_CODEX_BRIDGE  ")
    bridge.post_message(to="ADA", message="x")
    assert bridge._enviados_de_prueba[-1]["metadata"]["runtime_instance"] == "ADA_CODEX_BRIDGE"


def test_la_firma_se_recorta_a_40(bridge, monkeypatch):
    """Es texto que declara el emisor: se acota, como hace scripts/seal_send.py."""
    monkeypatch.setenv("SEAL_RUNTIME_INSTANCE", "X" * 200)
    bridge.post_message(to="ADA", message="x")
    assert len(bridge._enviados_de_prueba[-1]["metadata"]["runtime_instance"]) == 40


def test_la_firma_NO_es_una_credencial(bridge, monkeypatch):
    """Una etiqueta declarada no autoriza: `session_key` sigue siendo quien prueba
    la identidad. Si algún día la firma reemplaza al token, esto se pone rojo.

    ESTE ASSERT LO ESCRIBIÓ UN MUTANTE QUE SOBREVIVIÓ. La primera versión sólo pedía
    que `session_key` existiera, y un mutante que hacía `payload["session_key"] = _rt`
    —la etiqueta declarada ocupando el lugar de la credencial— pasaba los 6 tests.
    Comprobar que un campo ESTÁ no comprueba que sea lo que debe ser.
    """
    monkeypatch.setenv("SEAL_RUNTIME_INSTANCE", "ADA_CODEX_BRIDGE")
    bridge.post_message(to="ADA", message="x")
    enviado = bridge._enviados_de_prueba[-1]
    assert enviado.get("session_key")
    assert enviado["session_key"] != enviado["metadata"]["runtime_instance"], \
        "la etiqueta de cuerpo NO puede viajar como credencial de sesion"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
