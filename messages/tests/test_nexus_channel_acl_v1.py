"""ACL remitente→canal: la capa que convierte el aislamiento en frontera.

Las tres pruebas que pidió JARVIS para el asiento sombra. Los dos ROJOS son los
que importan: son lo único que prueba que el aislamiento es una frontera y no una
promesa. Los verdes solos pasarían con un puente bien intencionado y cero
contención.
"""
import pathlib
import sys

import pytest

_MESSAGES = pathlib.Path(__file__).resolve().parents[1]
if str(_MESSAGES) not in sys.path:      # nunca incondicional: mediría PRODUCCIÓN
    sys.path.insert(0, str(_MESSAGES))  # y dejaría vivos a los mutantes

import channel_acl as acl  # noqa: E402

# Medidos sobre 30 días de tráfico real en soul_v3.chat_messages.
REALES = ["JARVIS", "FABLE", "ADA", "NEXUS", "ALICE", "DUM",
          "William", "henry", "FAILOVER", "william2"]
CANALES_DE_HOY = ["web_chat", "dm:jarvis:nexus", "latidos", "user:3:gtl-sistemas"]


@pytest.mark.parametrize("canal", ["web_chat", "dm:william:alice-v2", "latidos"])
def test_ROJO_el_asiento_sombra_no_publica_fuera_de_su_canal(canal):
    """El caso que justifica todo el módulo: que no le hable a William por error."""
    assert acl.puede_escribir("ALICE-v2", canal) is False


def test_VERDE_el_asiento_sombra_publica_en_su_propio_canal():
    assert acl.puede_escribir("ALICE-v2", "shadow:alice-v2") is True


@pytest.mark.parametrize("sender", REALES)
@pytest.mark.parametrize("canal", CANALES_DE_HOY)
def test_VERDE_no_hay_regresion_para_quien_ya_publicaba(sender, canal):
    """Cero cambio para los diez remitentes medidos: si esto se pone rojo, el
    control rompió el sistema en vez de protegerlo."""
    assert acl.puede_escribir(sender, canal) is True


def test_una_identidad_NUEVA_queda_contenida_sin_configurar_nada():
    """La decisión de diseño, y lo que la hace estructural.

    Un asiento que nadie agregó a la lista NO puede publicar en el general. El
    olvido produce seguridad, no un agujero — al revés que `delivery_mode`, donde
    el marcado lo pone el publicador y por eso es convención: medido, 12 mensajes
    de 134.679 lo llevan y las 5 instancias de clon vivas no aparecen marcadas.
    """
    for inventado in ("ALICE-v3", "clone-9", "asiento-desconocido", "ADA-u999"):
        assert acl.puede_escribir(inventado, "web_chat") is False
        assert acl.puede_escribir(inventado, f"shadow:{inventado.lower()}") is True


def test_un_nombre_vacio_no_pasa():
    for vacio in ("", "   ", None):
        assert acl.puede_escribir(vacio, "shadow:x") is False


def test_el_canal_de_sombra_no_se_puede_falsificar():
    """`shadow:` tiene que ser el PREFIJO, no aparecer en cualquier lado."""
    assert acl.es_canal_de_sombra("shadow:alice-v2") is True
    assert acl.es_canal_de_sombra("web_chat:shadow:alice") is False
    assert acl.es_canal_de_sombra("dm:william:shadow") is False
    assert acl.es_canal_de_sombra("shadow:") is False
    assert acl.puede_escribir("ALICE-v2", "web_chat_shadow:alice") is False


def test_el_motivo_del_rechazo_no_filtra_la_lista():
    """Un 403 dice qué hacer, no quiénes están habilitados."""
    texto = acl.motivo_del_rechazo("ALICE-v2", "web_chat")
    assert "ALICE-v2" in texto and "web_chat" in texto
    for habilitado in ("JARVIS", "FABLE", "NEXUS"):
        assert habilitado not in texto


# ── El agujero del clone_identity ───────────────────────────────────────────
# `chat_server` permite, legítimamente, que la instancia `ALICE-u2` asevere
# `from: "ALICE"` (así hablan los clones de usuario). Sin mirar la instancia, el
# ACL veía "ALICE" —remitente pleno— y le abría `web_chat`: el asiento sombra
# evadía el control usando el nombre de su agente madre, y justo con la forma
# `X-uN` que la spec de F2 propone para instanciarlo.

def test_una_INSTANCIA_no_hereda_los_permisos_de_su_agente_madre():
    """El agujero, en una línea: quién escribe es la instancia, no el nombre."""
    assert acl.puede_escribir("ALICE", "web_chat", instance_id="ALICE-u2") is False
    assert acl.puede_escribir("ALICE", "dm:william:alice", instance_id="ALICE-u2") is False
    assert acl.puede_escribir("ALICE", "shadow:alice-v2", instance_id="ALICE-u2") is True


def test_el_agente_SIN_instancia_no_pierde_nada():
    """Control de no-regresión del mismo cambio: ALICE de carne y hueso sigue igual."""
    for canal in CANALES_DE_HOY:
        assert acl.puede_escribir("ALICE", canal) is True
        assert acl.puede_escribir("ALICE", canal, instance_id="") is True


def test_la_instancia_gana_sobre_el_nombre_aseverado():
    """Aunque el nombre aseverado sea de un remitente pleno."""
    assert acl.identidad_efectiva("ALICE", "ALICE-u2") == "ALICE-U2"
    assert acl.identidad_efectiva("ALICE", "") == "ALICE"
    assert acl.identidad_efectiva("ALICE", "   ") == "ALICE"
