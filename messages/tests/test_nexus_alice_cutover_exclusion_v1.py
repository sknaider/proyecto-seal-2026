"""Exclusión mutua entre los dos cuerpos de ALICE en el ACL de canales.

Qué lo generó (4-sep-2026 02:21): William ordenó «pasala a v2 alice». El
reflejo era agregar `ALICE-V2` a `REMITENTES_PLENOS`, y eso habría creado un
SEGUNDO writer con la misma identidad — desactivando la propiedad que 3b
verificó (273 publicaciones de v2, cero fuera de su corral). NEXUS levantó el
punto, ADA lo fijó como exclusión mutua.

Cuatro brazos: unit (la lectura del switch) / positivo (el cuerpo activo
publica) / negativo (el inactivo NO publica fuera de su corral) / control
anti-vacuo (nunca los dos a la vez, y sin archivo gana v1).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import channel_acl  # noqa: E402


@pytest.fixture
def switch(tmp_path, monkeypatch):
    ruta = tmp_path / "alice_cuerpo_activo"
    monkeypatch.setattr(channel_acl, "_CUERPO_ALICE_PATH", ruta)
    return ruta


def test_unit_sin_archivo_gana_v1_el_olvido_conserva_lo_que_ya_andaba(switch):
    assert channel_acl.cuerpo_activo_de_alice() == "ALICE"


def test_unit_valor_desconocido_cae_a_v1_no_al_cuerpo_en_evaluacion(switch):
    switch.write_text("ALICE-V9\n", encoding="utf-8")
    assert channel_acl.cuerpo_activo_de_alice() == "ALICE"


def test_positivo_el_cuerpo_activo_publica_en_el_canal_general(switch):
    switch.write_text("ALICE-V2\n", encoding="utf-8")
    assert channel_acl.puede_escribir("ALICE-V2", "web_chat") is True


def test_negativo_el_cuerpo_INACTIVO_no_publica_fuera_de_su_corral(switch):
    switch.write_text("ALICE-V2\n", encoding="utf-8")
    # v1 sigue en REMITENTES_PLENOS y aun asi queda cercada: por eso el
    # chequeo de cuerpo va ANTES de la lista de plenos.
    assert channel_acl.puede_escribir("ALICE", "web_chat") is False
    assert channel_acl.puede_escribir("ALICE", "shadow:alice") is True


def test_control_NUNCA_los_dos_cuerpos_a_la_vez(switch):
    """Anti-vacuo: es la propiedad entera. Si los dos pueden, no hay cutover:
    hay dos ALICE publicando, que es lo que el ACL existe para impedir."""
    for valor in ("ALICE", "ALICE-V2", "", "basura"):
        switch.write_text(valor, encoding="utf-8")
        plenos = [
            c for c in ("ALICE", "ALICE-V2")
            if channel_acl.puede_escribir(c, "web_chat")
        ]
        assert len(plenos) == 1, f"con {valor!r} publican {plenos}"


def test_control_el_cutover_es_REVERSIBLE_borrando_el_archivo(switch):
    switch.write_text("ALICE-V2\n", encoding="utf-8")
    assert channel_acl.puede_escribir("ALICE-V2", "web_chat") is True
    switch.unlink()
    assert channel_acl.puede_escribir("ALICE", "web_chat") is True
    assert channel_acl.puede_escribir("ALICE-V2", "web_chat") is False


def test_control_los_demas_agentes_no_se_tocan(switch):
    switch.write_text("ALICE-V2\n", encoding="utf-8")
    for otro in ("JARVIS", "ADA", "NEXUS", "FABLE", "WILLIAM"):
        assert channel_acl.puede_escribir(otro, "web_chat") is True


def test_control_el_switch_DEPENDE_de_que_la_instancia_sea_visible(switch):
    """La precondición dura del cutover, medida y no supuesta.

    El ACL distingue los dos cuerpos por `identidad_efectiva`, que prefiere el
    `instance_id`. Si v2 publica aseverando `from="ALICE"` y SIN declarar
    instancia, es indistinguible de v1 — y con el switch en v2 el ACL la
    BLOQUEA, que es el peor resultado posible: el cutover deja muda justo a la
    que debía hablar.

    No se arregla acá: dos publicaciones idénticas no se pueden separar. Se
    arregla en el servidor, garantizando que el grant de v2 viaje siempre con
    su instancia. Este test existe para que esa dependencia no sea tácita.
    """
    switch.write_text("ALICE-V2\n", encoding="utf-8")
    assert channel_acl.puede_escribir("ALICE", "web_chat", "ALICE-V2") is True
    assert channel_acl.puede_escribir("ALICE-V2", "web_chat") is True
    # El caso que rompe: mismo cuerpo, sin instancia declarada.
    assert channel_acl.puede_escribir("ALICE", "web_chat") is False


def test_control_la_RUTA_REAL_del_interruptor_no_puede_volver_al_home():
    """El defecto que nos costó una hora, fijado para que no vuelva.

    Todos los tests de arriba monkeypatchean `_CUERPO_ALICE_PATH` a un tmp_path,
    así que NINGUNO miraba la ruta real: revertirla al home los dejaba a todos
    verdes. Lo medí sobre mi propio archivo y sobrevivió el mutante.

    Por qué importa: el otro lector es el broker de v2, que corre como root con
    `CapabilityBoundingSet=` vacío y NO atraviesa el 710 de `~/.config`. Una ruta
    bajo el home deja ciega a una de las dos piezas y el interruptor pasa a
    significar cosas distintas según quién lo lea.
    """
    ruta = str(channel_acl._CUERPO_ALICE_PATH)
    assert ruta == "/etc/seal/alice_cuerpo_activo", ruta
    assert "/.config/" not in ruta, (
        "el switch volvio a un directorio del home: el broker no puede leerlo"
    )
