"""Brazos del detector de oraculos circulares.

El caso REFUTADOR de este detector no es "encuentra el defecto conocido": es que NO grite
ante un assert legitimo. Un detector que marca todo no se mira, y el que no se mira no frena.
Todas las rutas son SENUELO bajo /tmp; ninguna toca el arbol real.
"""
from __future__ import annotations
import pathlib, subprocess, sys, textwrap

DETECTOR = pathlib.Path(__file__).resolve().parents[2] / "tools" / "seal_detector_oraculo_circular.py"
REAL = pathlib.Path(__file__).resolve().parents[2] / "tests" / "test_soul_runtime_orchestrator.py"


def _correr(*rutas: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(DETECTOR), *map(str, rutas)],
                          capture_output=True, text=True)


def _senuelo(tmp_path: pathlib.Path, sujeto: str, prueba: str) -> pathlib.Path:
    """Un sujeto y su test en un paquete SENUELO llamado 'memory' bajo /tmp."""
    paquete = tmp_path / "memory"
    paquete.mkdir()
    (paquete / "senuelo_sujeto.py").write_text(textwrap.dedent(sujeto), encoding="utf-8")
    destino = tmp_path / "test_senuelo.py"
    destino.write_text(textwrap.dedent(prueba), encoding="utf-8")
    return destino


def test_unit_encuentra_el_oraculo_circular_REAL_que_medi_hoy():
    """El caso que la mutacion destapo el 7-sep: assert env[PATH] == f2.TRUSTED_PATH."""
    r = _correr(REAL)
    assert r.returncode == 1
    assert "TRUSTED_PATH" in r.stdout
    assert ":100" in r.stdout, "debe senalar la linea exacta, no solo el archivo"


def test_qa_positive_marca_la_comparacion_contra_una_constante_de_TEXTO(tmp_path):
    prueba = '''
        import senuelo_sujeto as s
        def test_x():
            assert calcular() == s.RUTA_DE_CONFIANZA
    '''
    destino = _senuelo(tmp_path, 'RUTA_DE_CONFIANZA = "/usr/bin:/bin"\n', prueba)
    r = _correr(destino)
    assert r.returncode == 1
    assert "RUTA_DE_CONFIANZA" in r.stdout


def test_qa_negative_NO_marca_un_assert_con_el_valor_CABLEADO(tmp_path):
    """La forma correcta: el valor esperado escrito literal en el test. Si esto se marcara,
    el detector estaria pidiendo justo lo que hay que evitar."""
    prueba = '''
        import senuelo_sujeto as s
        def test_x():
            assert calcular() == "/usr/bin:/bin"
    '''
    destino = _senuelo(tmp_path, 'RUTA_DE_CONFIANZA = "/usr/bin:/bin"\n', prueba)
    r = _correr(destino)
    assert r.returncode == 0, r.stdout
    assert "ORACULO" not in r.stdout


def test_qa_negative_NO_marca_una_constante_NUMERICA(tmp_path):
    """`assert rc == EXIT_OK` tiene la MISMA FORMA y no es el mismo defecto: en un codigo
    simbolico importa la identidad, no el numero. Marcarlo seria ruido."""
    prueba = '''
        import senuelo_sujeto as s
        def test_x():
            assert correr() == s.EXIT_OK
    '''
    destino = _senuelo(tmp_path, 'EXIT_OK = 0\n', prueba)
    r = _correr(destino)
    assert r.returncode == 0, r.stdout
    assert "NUMERICO" in r.stdout, "debe verse en la seccion baja, no desaparecer"


def test_qa_negative_NO_marca_el_RETORNO_de_una_funcion_del_sujeto(tmp_path):
    """Comparar contra lo que el sujeto CALCULA es legitimo: eso es probar conducta."""
    prueba = '''
        import senuelo_sujeto as s
        def test_x():
            assert calcular() == s.normalizar("a")
    '''
    destino = _senuelo(tmp_path, 'def normalizar(x): return x.strip()\n', prueba)
    r = _correr(destino)
    assert r.returncode == 0, r.stdout


def test_qa_control_sin_archivos_que_mirar_NO_dice_limpio(tmp_path):
    """El unico modo de fallo silencioso: quedarse sin sujetos y devolver 0. Debe ser 2."""
    vacio = tmp_path / "no_existe_ningun_test.py"
    r = _correr(vacio)
    assert r.returncode == 2, r.stdout
    assert "SIN MIRAR" in r.stdout


def test_qa_control_un_test_que_NO_importa_del_repo_no_se_toca(tmp_path):
    """Control no vacuo: si marcara esto, marcaria por la forma del assert y no por el origen."""
    destino = tmp_path / "test_ajeno.py"
    destino.write_text(textwrap.dedent('''
        import os
        def test_x():
            assert os.sep == "/"
    '''), encoding="utf-8")
    r = _correr(destino)
    assert r.returncode == 0, r.stdout
    assert "ORACULO" not in r.stdout
