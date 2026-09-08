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


# ───── CABLEADO: que el handler PASE la instancia al ACL (JARVIS, 8-sep) ─────
#
# El mutante `_instancia_acl = ""` (chat_server.py:2663) SOBREVIVIA a los 12
# brazos de arriba, y la razon es la misma que ya me mordio hoy dos veces:
# **todos llaman a `puede_escribir()` pasando `instance_id` explicito**, asi que
# prueban la FUNCION y nunca el CABLEADO. El ACL puede estar perfecto y el
# handler pasarle cadena vacia: los clones que aseveran ALICE entran como ALICE
# canonica y quedan dos writers en el mismo canal.
#
# Este brazo va por el handler real `agents_send` con el ACL REAL. Lo unico
# simulado es la sesion verificada, que es la ENTRADA del cableado, no lo que
# se prueba.

import asyncio
import json as _json
import types


def _peticion(cuerpo: dict):
    """Request minimo: agents_send solo usa .json()/.body() y .client/.headers."""
    class R:
        headers = {}
        client = types.SimpleNamespace(host="127.0.0.1")
        async def json(self):
            return cuerpo
        async def body(self):
            return _json.dumps(cuerpo).encode()
    return R()


def _cargar_chat_server():
    import importlib
    import sys as _sys
    ruta = str(pathlib.Path(__file__).resolve().parents[1])
    if ruta not in _sys.path:
        _sys.path.insert(0, ruta)
    return importlib.import_module("chat_server")


def test_CABLEADO_el_handler_pasa_la_instancia_de_la_SESION_al_acl(monkeypatch):
    """Mutante `_instancia_acl = ""`: con la mutacion el ACL ve «ALICE sin
    instancia» -> True -> no hay 403 y el clon publica en web_chat.

    Se verifica por el ARGUMENTO que recibe el ACL, no por el 403: asi el brazo
    sigue siendo especifico si manana cambia el codigo de estado.
    """
    cs = _cargar_chat_server()
    visto = {}

    def espia(sender, channel, instancia=""):
        visto["sender"] = sender
        visto["canal"] = channel
        visto["instancia"] = instancia
        return acl.puede_escribir(sender, channel, instance_id=instancia)  # ACL REAL

    monkeypatch.setattr(cs, "_acl_puede_escribir", espia)

    async def sesion_falsa(_hash):
        return {"username": "ALICE-V2"}          # cuerpo verificado por la sesion

    # sin un pool "vivo" el handler NI SIQUIERA llama a validate_session
    # (`if chat_db.pool:`), la sesion queda None y corta en el gate de auth
    # antes de llegar al ACL. Fue lo primero que rompio este brazo.
    monkeypatch.setattr(cs.chat_db, "pool", object())
    monkeypatch.setattr(cs.chat_db, "validate_session", sesion_falsa)

    cuerpo = {"from": "ALICE", "channel": "web_chat", "message": "hola",
              "session_key": "no-importa", "type": "chat"}
    asyncio.run(cs.agents_send(_peticion(cuerpo)))

    assert visto.get("instancia") == "ALICE-V2", (
        "el handler NO paso la instancia de la sesion al ACL: recibio "
        f"{visto.get('instancia')!r}. Un clon aseverando ALICE entraria como ALICE."
    )


def test_CABLEADO_el_cuerpo_verificado_GANA_sobre_el_declarado_por_el_cliente(monkeypatch):
    """Segundo filo del mismo cableado: si el cliente miente el `instance_id`,
    manda la sesion. Sin esto la exclusion mutua vuelve a ser advisory."""
    cs = _cargar_chat_server()
    visto = {}

    def espia(sender, channel, instancia=""):
        visto["instancia"] = instancia
        return acl.puede_escribir(sender, channel, instance_id=instancia)

    monkeypatch.setattr(cs, "_acl_puede_escribir", espia)

    async def sesion_falsa(_hash):
        return {"username": "ALICE-V2"}

    # sin un pool "vivo" el handler NI SIQUIERA llama a validate_session
    # (`if chat_db.pool:`), la sesion queda None y corta en el gate de auth
    # antes de llegar al ACL. Fue lo primero que rompio este brazo.
    monkeypatch.setattr(cs.chat_db, "pool", object())
    monkeypatch.setattr(cs.chat_db, "validate_session", sesion_falsa)

    cuerpo = {"from": "ALICE", "channel": "web_chat", "message": "hola",
              "session_key": "x", "instance_id": "", "type": "chat"}   # el cliente NO la declara
    asyncio.run(cs.agents_send(_peticion(cuerpo)))
    assert visto.get("instancia") == "ALICE-V2"


def test_CABLEADO_la_instancia_DECLARADA_por_el_cliente_tambien_llega_al_acl(monkeypatch):
    """El brazo que de verdad mata a `_instancia_acl = ""`, y lo escribo despues
    de que la mutacion en arena me mostrara que los dos de arriba NO lo mataban.

    El handler hace:

        _instancia_acl = instance_id            <- la linea que el mutante borra
        if _es_cuerpo_de_agente(verificado, sender):
            _instancia_acl = verificado         <- la REASIGNA

    Con una sesion que SI es cuerpo conocido (ALICE-V2) la segunda linea repone
    el valor y el mutante queda tapado: mis dos brazos anteriores pasaban por
    ahi. **El unico camino que ejerce la linea mutada es una instancia que
    declara el CLIENTE y que no corresponde a un cuerpo verificado** — que es
    justo el clon del que hay que defenderse.
    """
    cs = _cargar_chat_server()
    visto = {}

    def espia(sender, channel, instancia=""):
        visto["instancia"] = instancia
        return acl.puede_escribir(sender, channel, instance_id=instancia)

    monkeypatch.setattr(cs, "_acl_puede_escribir", espia)
    monkeypatch.setattr(cs.chat_db, "pool", object())

    # La sesion ES el clon. Es el UNICO camino que llega vivo a la linea mutada:
    #  * si la instancia declarada no coincide con la sesion, el gate de auth
    #    corta antes con `agent_instance_mismatch` (lo comprobe: el ACL ni se
    #    llamaba);
    #  * si la sesion es un cuerpo de _CUERPOS_POR_AGENTE, la linea siguiente
    #    reasigna desde `_verificado` y tapa la mutacion.
    async def sesion_falsa(_hash):
        return {"username": "ALICE-u2"}   # clon: NO esta en _CUERPOS_POR_AGENTE

    monkeypatch.setattr(cs.chat_db, "validate_session", sesion_falsa)

    cuerpo = {"from": "ALICE", "channel": "web_chat", "message": "hola",
              "session_key": "x", "instance_id": "ALICE-u2", "type": "chat"}
    asyncio.run(cs.agents_send(_peticion(cuerpo)))

    assert visto.get("instancia") == "ALICE-u2", (
        "el handler descarto la instancia declarada: el ACL recibio "
        f"{visto.get('instancia')!r}. Un clon publica como el agente canonico."
    )
