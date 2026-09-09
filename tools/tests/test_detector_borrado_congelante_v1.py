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


def test_qa_positive_marca_rm_rf_sobre_variable_SIN_guarda(tmp_path):
    """Segunda forma, aportada por NEXUS el 7-sep tras borrar su detector duplicado.

    `rm -rf "$T"` es la otra mitad de su comando congelado y es EXACTAMENTE la forma que
    casi borra /home/dadito el 9-ago (`rm -rf "$HOME"`, un typo).
    """
    s = _script(tmp_path, "sin_guarda.sh", '''
        T=$(mktemp -d /tmp/senuelo-XXXXXX)
        rm -rf "$T"
    ''')
    r = _correr(s)
    assert r.returncode == 1
    assert "BORRA UNA VARIABLE SIN GUARDA" in r.stdout


def test_qa_negative_NO_marca_la_variable_con_ABORTO_si_esta_vacia(tmp_path):
    """`${VAR:?}` aborta cuando la variable está vacía: es más fuerte que el `case`.

    NEXUS lo encontró como falso positivo en su propia versión —2 de sus 17—. Un detector
    que cobra ruido donde alguien YA hizo lo correcto termina apagado, y apagado es peor
    que inexistente.
    """
    s = _script(tmp_path, "aborta.sh", '''
        rm -rf "${ROOT:?}/${name:?}"
    ''')
    r = _correr(s)
    assert r.returncode == 0, r.stdout
    assert "SIN GUARDA" not in r.stdout


def test_qa_negative_NO_marca_el_rm_rf_dentro_de_su_guarda_case(tmp_path):
    """La forma que la regla de oro manda escribir no puede aparecer marcada."""
    s = _script(tmp_path, "con_guarda.sh", '''
        D=$(mktemp -d /tmp/senuelo-XXXXXX)
        case "$D" in /tmp/*) rm -rf "$D" ;; esac
    ''')
    r = _correr(s)
    assert r.returncode == 0, r.stdout
    assert "SIN GUARDA" not in r.stdout


def test_brazo_guarda_sobre_otra_variable_no_cuenta(tmp_path):
    """BRAZO BC-c — `case "$OTRA"` cerca de `rm -rf "$DIR"` no guarda $DIR.

    La guarda de la línea 84 (g == var) compara la variable del case con la del rm.
    El mutante BC-c la relaja: cualquier case cercano cuenta → el rm pasa por guardado.
    Con el código correcto: `case "$OTRA"` no menciona `$DIR` → SIN GUARDA detectado.
    Con el mutante BC-c: no se detecta → brazo FALLA → mutante MUERTO.
    """
    s = _script(tmp_path, "guarda_otra.sh", '''
        OTRA=$(mktemp -d /tmp/senuelo-XXXXXX)
        DIR=/tmp/senuelo-objetivo
        case "$OTRA" in /tmp/*) echo "OTRA esta bajo /tmp" ;; esac
        rm -rf "$DIR"
    ''')
    r = _correr(s)
    assert r.returncode == 1, (
        f"rm -rf sobre $DIR sin guarda DEBE detectarse aunque $OTRA tenga case;\n"
        f"stdout:\n{r.stdout}"
    )
    assert "BORRA UNA VARIABLE SIN GUARDA" in r.stdout, (
        f"la linea del rm -rf debe decir SIN GUARDA (guarda sobre $OTRA no cubre $DIR);\n"
        f"stdout:\n{r.stdout}"
    )
