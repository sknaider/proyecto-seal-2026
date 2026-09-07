"""El contrato entre `chat_server` y `response_lease`: lo que se llama, existe.

POR QUE ESTE TEST Y NO UNO DE LA FUNCION (NEXUS, 3-sep-2026)

El 3-sep un `git reset --hard` mio borro `release_db` de `response_lease.py`. El
call site en `chat_server.py:3116` sobrevivio porque vivia en OTRO commit. El
archivo quedo roto casi 5 horas sin sintoma: el proceso tenia el modulo bueno en
memoria desde el dia anterior. Al reiniciar, TODO envio con `--in-reply-to`
--o sea, la forma en que los cinco le respondemos a William-- devolvia HTTP 500.

Un test de `release_db` no habria servido: la funcion no existia, asi que el test
habria fallado con ImportError y se habria leido como "test roto", no como
"produccion rota". Lo que hay que fijar es la RELACION entre los dos archivos:

    todo `_response_lease.<algo>` que chat_server invoque
    TIENE que existir como atributo de response_lease

Es estatico, corre en milisegundos y cubre la clase entera --cualquier funcion
del modulo, no solo la que se perdio esta vez--.
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
SERVIDOR = RAIZ / "messages" / "chat_server.py"
MODULO = RAIZ / "messages" / "response_lease.py"


def atributos_invocados(fuente: pathlib.Path, alias: str) -> set[str]:
    """-> {'acquire_db','release_db'} leyendo el AST, no con grep.

    Con grep, un `_response_lease.release_db` dentro de un comentario o de un
    docstring contaria como call site y el test pediria una funcion que nadie
    llama. El AST solo ve codigo.
    """
    arbol = ast.parse(fuente.read_text(encoding="utf-8"))
    encontrados: set[str] = set()
    for nodo in ast.walk(arbol):
        if (isinstance(nodo, ast.Attribute)
                and isinstance(nodo.value, ast.Name)
                and nodo.value.id == alias):
            encontrados.add(nodo.attr)
    return encontrados


@pytest.fixture(scope="module")
def modulo():
    spec = importlib.util.spec_from_file_location("response_lease_bajo_prueba", MODULO)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# --- unit ----------------------------------------------------------------

def test_unit_el_lector_de_call_sites_lee_codigo_y_no_texto():
    """El instrumento antes que la medicion: si el lector se equivoca, todo lo
    que mida despues es ruido con forma de evidencia."""
    invocados = atributos_invocados(SERVIDOR, "_response_lease")
    assert invocados, "el lector no encontro NINGUN call site: sospechoso, no verde"
    assert "acquire_db" in invocados


# --- qa_positive ---------------------------------------------------------

def test_qa_positive_todo_lo_que_el_servidor_llama_existe_en_el_modulo(modulo):
    invocados = atributos_invocados(SERVIDOR, "_response_lease")
    faltantes = sorted(a for a in invocados if not hasattr(modulo, a))
    assert not faltantes, (
        f"chat_server llama a {faltantes} y response_lease no lo define. "
        "Esto es un HTTP 500 en produccion en cuanto el proceso reinicie."
    )


def test_qa_positive_release_db_sigue_siendo_una_corrutina(modulo):
    """El call site hace `await`: una funcion sincrona con el mismo nombre
    pasaria el chequeo de existencia y reventaria igual."""
    import inspect
    assert inspect.iscoroutinefunction(modulo.release_db)


# --- qa_negative ---------------------------------------------------------

def test_qa_negative_un_call_site_a_una_funcion_inexistente_se_detecta(modulo, tmp_path):
    """El mismo chequeo, contra un servidor de mentira que llama a algo que no
    existe: si esto NO falla, el positivo de arriba no prueba nada."""
    falso = tmp_path / "servidor_falso.py"
    falso.write_text("async def f():\n    await _response_lease.funcion_que_no_existe(1)\n")
    invocados = atributos_invocados(falso, "_response_lease")
    assert invocados == {"funcion_que_no_existe"}
    assert not hasattr(modulo, "funcion_que_no_existe")


def test_qa_negative_el_sql_libera_por_estado_y_no_solo_por_tiempo():
    """`release_db` marca el lease como 'released'; si la consulta de
    adquisicion solo mirase el TTL, soltarlo no serviria de nada y el turno
    seguiria retenido hasta que venciera."""
    fuente = MODULO.read_text(encoding="utf-8")
    assert "state = 'released'" in fuente


# --- qa_control ----------------------------------------------------------

def test_control_no_vacuo_el_test_puede_fallar(modulo):
    with pytest.raises(AssertionError):
        assert not hasattr(modulo, "acquire_db")


# ══════════════════════════════════════════════════════════════════════════
# BRAZOS DE CONDUCTA — agregados el 7-sep-2026 tras el hallazgo de ALICE.
#
# Ella re-corrio 6 mutantes de CONDUCTA y NINGUNO murio: rompio la exclusion
# mutua entera -el `return False` paso a `return True`- y esta suite siguio
# VERDE. Porque verificaba la FORMA del modulo -que los call sites existan, que
# release_db siga siendo corrutina, el texto del SQL- y **no llamaba a
# `acquire()` ni una sola vez**.
#
# Es el flood que William mando a arreglar, sin un solo test que lo probara.
# Un brazo de forma no es inutil; lo grave fue llamar "contrato" a un manifiesto
# que no ejercia el contrato.
# ══════════════════════════════════════════════════════════════════════════
import time as _time

from messages.response_lease import InMemoryLease, gate_allows, is_multi_response


def test_CONDUCTA_solo_UN_agente_gana_el_lease():
    """Mata `el-segundo-agente-tambien-gana`, que sobrevivio."""
    lease = InMemoryLease()
    assert lease.acquire("b1", "ALICE") is True
    assert lease.acquire("b1", "NEXUS") is False, (
        "un SEGUNDO agente gano el mismo turno: eso es el flood que el lease existe "
        "para impedir")


def test_CONDUCTA_el_dueno_conserva_su_turno():
    """Mata `el-dueno-pierde-su-propio-turno`. Idempotencia: el owner puede
    re-postear sin perder el lease que ya tiene."""
    lease = InMemoryLease()
    assert lease.acquire("b1", "ALICE") is True
    assert lease.acquire("b1", "ALICE") is True, "el dueño perdio su propio turno"


def test_CONDUCTA_el_lease_EXPIRA_y_hay_handoff():
    """Mata `el-lease-no-expira-nunca`. Sin handoff, un agente que se cuelga deja
    el turno tomado para siempre y William se queda sin respuesta."""
    lease = InMemoryLease()
    assert lease.acquire("b1", "ALICE", ttl=1) is True
    _time.sleep(1.1)
    assert lease.acquire("b1", "NEXUS", ttl=1) is True, (
        "el lease no expiro: un agente colgado bloquea el turno indefinidamente")


def test_CONTROL_el_lease_NO_expira_antes_de_tiempo():
    """Mata `el-lease-expira-al-instante` (TTL=0), el mutante espejo del anterior.

    Sin este control, un lease que expira SIEMPRE pasaria el test de handoff y
    romperia la exclusion mutua: los dos defectos se ven distintos y el mismo
    brazo no puede cazar a los dos.
    """
    lease = InMemoryLease()
    assert lease.acquire("b1", "ALICE", ttl=45) is True
    assert lease.acquire("b1", "NEXUS", ttl=45) is False, (
        "el lease expiro de inmediato: la exclusion mutua no dura nada")


def test_CONDUCTA_multivoz_no_aplica_lease():
    """Mata `ningun-mensaje-es-multivoz`. Cuando William pide varias voces, el
    lease NO debe silenciar a nadie."""
    lease = InMemoryLease()
    texto = "chicos, respondan todos"
    assert is_multi_response(texto) is True
    assert gate_allows(lease, "b1", "ALICE", texto) is True
    assert gate_allows(lease, "b1", "NEXUS", texto) is True, (
        "el lease silencio a un agente en un pedido MULTIVOZ")


def test_CONTROL_un_mensaje_normal_SI_aplica_lease():
    """Mata `todo-mensaje-se-declara-multivoz`, el espejo del anterior.

    Sin este control, declarar multivoz a TODO pasaria el test de arriba y
    desactivaria el lease por completo.
    """
    lease = InMemoryLease()
    texto = "nexus, revisa el gate"
    assert is_multi_response(texto) is False
    assert gate_allows(lease, "b1", "ALICE", texto) is True
    assert gate_allows(lease, "b1", "NEXUS", texto) is False, (
        "un mensaje normal no aplico lease: el turno unico dejo de existir")
