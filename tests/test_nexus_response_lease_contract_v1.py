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
