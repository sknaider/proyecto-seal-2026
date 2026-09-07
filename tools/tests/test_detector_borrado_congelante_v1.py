"""Brazos del detector de borrados que congelan la sesión.

Todo con archivos SEÑUELO bajo /tmp: ninguno se ejecuta, sólo se leen. El brazo que más
importa es el negativo — un detector que marca las formas SEGURAS le enseña al equipo a
ignorarlo, y entonces la próxima sesión congelada no la evita nadie.
"""
from __future__ import annotations
import pathlib, subprocess, sys, textwrap

DETECTOR = pathlib.Path(__file__).resolve().parents[2] / "tools" / "seal_detector_borrado_congelante.py"


def _correr(*rutas: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(DETECTOR), *map(str, rutas)],
                          capture_output=True, text=True)


def _script(tmp_path: pathlib.Path, nombre: str, cuerpo: str) -> pathlib.Path:
    d = tmp_path / nombre
    d.write_text(textwrap.dedent(cuerpo), encoding="utf-8")
    return d


def test_unit_encuentra_el_glob_sobre_variable(tmp_path):
    """La forma exacta que congeló a NEXUS 2 h 5 min el 7-sep-2026."""
    s = _script(tmp_path, "congela.sh", '''
        M=$(mktemp -d /tmp/senuelo-XXXXXX)
        rm "$M"/*.json
    ''')
    r = _correr(s)
    assert r.returncode == 1
    assert "CONGELA LA SESION" in r.stdout
    assert ":3" in r.stdout, "debe señalar la línea exacta"


def test_qa_positive_tambien_sin_comillas_y_con_llaves(tmp_path):
    s = _script(tmp_path, "variantes.sh", '''
        rm -rf $DIR/*
        rm -f "${OTRO}"/*.log
    ''')
    r = _correr(s)
    assert r.returncode == 1
    assert r.stdout.count("CONGELA LA SESION") == 2, r.stdout


def test_qa_negative_NO_marca_las_formas_que_SI_se_pueden_escribir(tmp_path):
    """`find -delete` y el `rm -rf` dentro de un `case /tmp/*` son las formas aprobadas.

    Si el detector las marcara, estaría pidiendo justo lo que la regla manda escribir.
    """
    s = _script(tmp_path, "seguro.sh", '''
        D=$(mktemp -d /tmp/senuelo-XXXXXX)
        find "$D" -mindepth 1 -delete
        find "$D" -mindepth 1 -maxdepth 1 -type d -exec rm -rf {} +
        case "$D" in /tmp/*) rm -rf "$D" ;; esac
    ''')
    r = _correr(s)
    assert r.returncode == 0, r.stdout
    assert "CONGELA LA SESION" not in r.stdout


def test_qa_negative_NO_marca_el_patron_DENTRO_de_un_literal_de_python(tmp_path):
    """Documentar el patrón y usarlo como dato de prueba tiene que seguir siendo posible.

    Dos suites del repo lo llevan como cadena y no ejecutan nada; este propio detector lo
    cita en su docstring. Marcarlos impediría escribir sobre el problema.
    """
    s = _script(tmp_path, "documenta.py", '''
        """Ejemplo de lo que NO se debe escribir: rm "$M"/*.json"""
        CASOS = ['rm -rf "$V"/*/']
        def f():
            return CASOS
    ''')
    r = _correr(s)
    assert r.returncode == 0, r.stdout
    assert "CONGELA LA SESION" not in r.stdout


def test_qa_negative_NO_marca_un_comentario(tmp_path):
    s = _script(tmp_path, "comentado.sh", '''
        # nunca escribir  rm "$M"/*.json
        echo ok
    ''')
    r = _correr(s)
    assert r.returncode == 0, r.stdout


def test_qa_control_sin_scripts_NO_dice_limpio(tmp_path):
    r = _correr(tmp_path / "no_existe.sh")
    assert r.returncode == 2, r.stdout
    assert "SIN MIRAR" in r.stdout
