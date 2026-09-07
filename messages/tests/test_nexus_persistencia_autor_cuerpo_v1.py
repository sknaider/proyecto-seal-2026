"""Quién queda escrito como AUTOR cuando publica el cuerpo de un agente.

POR QUÉ EXISTE ESTE ARCHIVO (7-sep-2026). El test que fijaba esto —
`test_persistencia_*`, de agosto— NO está en el árbol ni en el índice del 4-sep:
se perdió con el borrado del home. Lo detectó JARVIS re-mutando
`alice-cutover-exclusion-mutua` en la arena: **tres mutantes sobrevivieron**, no
porque el código fallara sino porque **ya no quedaba nadie que los matara**.

Un test perdido no deja rastro rojo: deja un mutante vivo. Por eso se reescribe
con un brazo POR MUTANTE, y cada uno dice cuál mata.

Lo que se protege, en una línea: **el nombre visible es el del AGENTE, la fila
sigue siendo de la CUENTA del cuerpo.** Si se guardara `ALICE-V2`, William vería
el nombre del cuerpo y el RLS de los DM compararía `ALICE-V2` con los
participantes `alice`/`william` y rechazaría el INSERT.
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("SEAL_PG_DSN", "postgresql://test:test@127.0.0.1:1/test")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import chat_server  # noqa: E402

SESION_CUERPO = {"username": "ALICE-V2", "user_id": 42}
SESION_AJENA = {"username": "henry", "user_id": 7}


def test_el_cuerpo_persiste_el_nombre_CANONICO_no_el_suyo():
    """Mata `cuerpo-persiste-su-propio-nombre` (devolver `usuario`).

    Es el caso que JARVIS midió en la fila 148366: el gate aceptaba `from=ALICE`
    desde la sesión ALICE-V2 y la persistencia guardaba `ALICE-V2`.
    """
    tipo, uid, nombre = chat_server._agent_send_db_actor("ALICE", SESION_CUERPO, None)
    assert nombre == "ALICE", f"se persistió '{nombre}': el nombre visible debe ser el del AGENTE"
    assert tipo == "user"


def test_la_fila_conserva_la_CUENTA_del_cuerpo():
    """Mata `se-pierde-la-autoria-real` (sender_id=None).

    El nombre se muestra con el del agente; la autoría real NO se borra, se
    audita por `sender_id` y por la metadata del cuerpo.
    """
    _, uid, _ = chat_server._agent_send_db_actor("ALICE", SESION_CUERPO, None)
    assert uid == 42, "sender_id nulo borra quién escribió de verdad"


def test_NEGATIVO_una_sesion_cualquiera_NO_persiste_el_nombre_aseverado():
    """Mata `cualquiera-persiste-el-aseverado` (`if True`).

    Sin este brazo, la excepción del cuerpo se vuelve la regla: cualquier sesión
    autenticada podría publicar con el nombre de cualquier agente.
    """
    _, uid, nombre = chat_server._agent_send_db_actor("ALICE", SESION_AJENA, None)
    assert nombre == "henry", f"'{nombre}': una sesión ajena no puede firmar como ALICE"
    assert uid == 7


def test_CONTROL_el_cuerpo_de_OTRO_agente_tampoco_pasa():
    """Sin este control, una tabla que aceptara cualquier '-V2' pasaría igual."""
    _, _, nombre = chat_server._agent_send_db_actor("JARVIS", SESION_CUERPO, None)
    assert nombre == "ALICE-V2", "ALICE-V2 no es cuerpo de JARVIS: debe persistir su propia cuenta"


def test_CONTROL_un_clon_sigue_publicando_como_agente_sin_sesion_humana():
    """El otro camino de la función, para que el arreglo no lo rompa."""
    tipo, uid, nombre = chat_server._agent_send_db_actor("ADA", None, 103)
    assert (tipo, uid, nombre) == ("agent", None, "ADA")
