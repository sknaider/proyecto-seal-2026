"""El driver de la arena rechaza un spec['tests'] mal formado ANTES de correr el control.

Caso real (7-sep-2026): dos clones escribieron tests = [["python3","-m","pytest","-q",ruta]];
pytest buscó un archivo llamado `python3`, falló con "file or directory not found: python3" y
ambos lo diagnosticaron como PYTHONPATH roto del arnés. El error tiene que decir qué es.
"""
import importlib.util
import pathlib
import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[2]
ESPEC = importlib.util.spec_from_file_location("arena_remutar_revisor", RAIZ / "tools/arena_remutar_revisor.py")
mod = importlib.util.module_from_spec(ESPEC)
ESPEC.loader.exec_module(mod)


def test_rojo_un_argv_con_interprete_se_rechaza_con_mensaje_claro():
    with pytest.raises(SystemExit) as e:
        mod._validar_tests([["python3", "-m", "pytest", "-q", "tools/tests/test_arena_remutar_revisor_v1.py"]])
    assert "python3" in str(e.value) and "argv PARA pytest" in str(e.value)


def test_rojo_una_ruta_inexistente_se_rechaza():
    with pytest.raises(SystemExit) as e:
        mod._validar_tests([["tools/tests/no_existe_señuelo_v1.py"]])
    assert "inexistente" in str(e.value)


def test_rojo_una_lista_vacia_o_mal_tipada_se_rechaza():
    for malo in ([], [[]], "tools/tests/test_arena_remutar_revisor_v1.py", [["a.py"], "b.py"]):
        with pytest.raises(SystemExit):
            mod._validar_tests(malo)


def test_verde_rutas_nodeid_y_flags_pasan():
    mod._validar_tests([
        ["tools/tests/test_arena_remutar_revisor_v1.py"],
        ["tools/tests/test_arena_remutar_revisor_v1.py::test_verde_rutas_nodeid_y_flags_pasan", "-k", "verde", "-q"],
    ])


def test_control_no_vacuo_el_validador_existe_y_el_driver_lo_llama():
    fuente = (RAIZ / "tools/arena_remutar_revisor.py").read_text()
    assert fuente.count("\n    _validar_tests(tests)\n") == 1
    assert fuente.index("\n    _validar_tests(tests)\n") < fuente.index("if corre(tests) != 0")
