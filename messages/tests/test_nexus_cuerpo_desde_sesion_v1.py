"""El CUERPO de un agente lo dicta la sesión, nunca el body.

Qué lo generó (JARVIS revisando mi ACL, 4-sep-2026 02:26): la exclusión mutua
entre ALICE y ALICE-V2 se decidía con el `instance_id` que venía del BODY, así
que era ADVISORY para quien tuviera el token canónico:

    token ALICE + instance_id=ALICE-V2  ->  quien=ALICE-V2  (exclusión funciona)
    token ALICE + SIN instance_id       ->  quien=ALICE     (DOS writers)

Es la lección de 3b un nivel más arriba: la identidad la fija el servidor desde
la sesión, no la asevera el cliente.

Cuatro brazos: unit (la tabla de cuerpos) / positivo (la sesión del cuerpo puede
firmar con el nombre canónico) / negativo (nadie más puede, y un instance_id sin
respaldo se RECHAZA en vez de ignorarse) / control anti-vacuo (un patrón no
sustituye a la lista).
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("SEAL_PG_DSN", "postgresql://test:test@127.0.0.1:1/test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chat_server  # noqa: E402


def test_unit_la_tabla_lista_cuerpos_por_NOMBRE_no_por_patron():
    assert chat_server._CUERPOS_POR_AGENTE["ALICE"] == frozenset({"ALICE-V2"})


def test_positivo_la_sesion_del_cuerpo_firma_con_el_nombre_canonico():
    assert chat_server._es_cuerpo_de_agente("ALICE-V2", "ALICE") is True
    assert chat_server._es_cuerpo_de_agente("alice-v2", "alice") is True


def test_negativo_nadie_mas_puede_firmar_como_ALICE():
    for intruso in ("JARVIS", "ALICE-V3", "ALICE-u2", "NEXUS", "", "ALICE"):
        assert chat_server._es_cuerpo_de_agente(intruso, "ALICE") is False, intruso


def test_negativo_el_cuerpo_no_habilita_a_OTRO_agente():
    assert chat_server._es_cuerpo_de_agente("ALICE-V2", "JARVIS") is False


def test_control_un_patron_generico_NO_habria_pasado_este_test():
    """Anti-vacuo: `ALICE-V3` y `ALICE-V9` son exactamente la forma que un
    patrón `ALICE-V\\d+` dejaría entrar. La lista por nombre los rechaza, y ese
    es el punto entero de listar en vez de matchear."""
    for inventado in ("ALICE-V3", "ALICE-V9", "ALICE-V0"):
        assert chat_server._es_cuerpo_de_agente(inventado, "ALICE") is False


# ── El CABLEADO, no el helper ────────────────────────────────────────────────
#
# Lo pidió JARVIS revisando: los cinco tests de arriba prueban
# `_es_cuerpo_de_agente` y NINGUNO llama a `_agent_auth_gate`. Un helper puede
# ser perfecto y no estar conectado -- es el mismo mutante que sobrevivió en mi
# dedup de idempotencia hace dos horas, cometido de nuevo.

class _Req:
    """Lo mínimo que `_agent_auth_gate` toca de una Request."""
    client = None


def _gate(sesion: str | None, aseverado: str, instancia: str = "", accion: str = "send"):
    ses = {"username": sesion} if sesion else None
    return chat_server._agent_auth_gate(ses, aseverado, accion, _Req(), instance_id=instancia)


def _rechazo(resp):
    if resp is None:
        return None
    import json
    return json.loads(bytes(resp.body).decode())["error"]


def test_cableado_positivo_sesion_del_cuerpo_firma_como_ALICE_sin_declararse():
    """El caso que motivó todo: v2 publica como ALICE y NO manda instance_id.
    Antes pasaba como v1 (dos writers); ahora la sesión la identifica igual."""
    assert _gate("ALICE-V2", "ALICE") is None
    assert _gate("ALICE-V2", "ALICE", "ALICE-V2") is None


def test_cableado_negativo_el_token_canonico_NO_puede_aseverar_otra_instancia():
    """`token ALICE + instance_id=ALICE-V2` era el agujero: el cliente elegía su
    propio cuerpo. Ahora se rechaza en vez de ignorarse."""
    assert _rechazo(_gate("ALICE", "ALICE", "ALICE-V2")) == "agent_instance_mismatch"


def test_cableado_negativo_una_sesion_ajena_no_firma_como_ALICE():
    assert _rechazo(_gate("JARVIS", "ALICE")) == "agent_sender_mismatch"
    assert _rechazo(_gate("ALICE-V9", "ALICE")) == "agent_sender_mismatch"


def test_cableado_control_lo_que_ya_andaba_sigue_andando():
    """Anti-vacuo: si este gate empezara a rechazar el tráfico normal, el
    sistema queda mudo y el cutover sería el menor de los problemas."""
    assert _gate("ALICE", "ALICE") is None
    assert _gate("JARVIS", "JARVIS") is None
    assert _gate("ALICE-V2", "ALICE-V2") is None
    # clon de usuario: la regla vieja, intacta
    assert _gate("ALICE-u2", "ALICE", "ALICE-u2") is None


def test_cableado_control_upload_conserva_SU_error_mas_especifico():
    """Mi rechazo nuevo pisaba `agent_upload_instance_unsupported` y rompió dos
    tests ajenos (63 -> 65 fallos). La guarda por acción es lo que lo evita."""
    assert _gate("JARVIS", "JARVIS", "JARVIS-u103", accion="upload") is None
