"""Lectura compartida de la credencial del observer, y su uso en el exporter.

POR QUÉ CADA BRAZO CORRE EN SUBPROCESO CON HOME SANEADO: el 7-sep un test que
manejaba esta misma credencial la publicó TRES veces —por el diff de un assert,
por los locales de un traceback y por un default mal ligado— y costó tres
rotaciones. Acá el valor vivo no entra nunca al proceso de pytest: cada brazo
levanta un intérprete con HOME apuntando a un directorio señuelo bajo tmp_path y
sin ninguna variable POSTGRES_*/SEAL_*/PG*. Lo que se afirma son PROPIEDADES
(vacío / no vacío / qué excepción), nunca el contenido.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECOY = "postgresql://senuelo:no-es-una-credencial@127.0.0.1:1/senuelo"


def _corre(codigo: str, home, extra_env: dict[str, str] | None = None):
    """Ejecuta `codigo` en un intérprete limpio. Devuelve (rc, stdout, stderr)."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("POSTGRES", "SEAL_", "PG"))
    }
    env["HOME"] = str(home)
    env["PYTHONPATH"] = TOOLS
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra_env or {})
    p = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(codigo)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    return p.returncode, p.stdout.strip(), p.stderr.strip()


@pytest.fixture()
def home_con_credencial(tmp_path):
    """HOME señuelo con el archivo en modo 600 y un DSN que no sirve para nada."""
    d = tmp_path / "casa" / ".config" / "seal"
    d.mkdir(parents=True)
    f = d / "mcp_postgres_observer.env"
    f.write_text(f"POSTGRES_MCP_DSN={DECOY}\n", encoding="utf-8")
    f.chmod(0o600)
    return tmp_path / "casa"


@pytest.fixture()
def home_vacio(tmp_path):
    d = tmp_path / "casa_vacia"
    d.mkdir()
    return d


# ─────────────────────────── el módulo compartido ───────────────────────────

def test_qa_positive_lee_el_dsn_cuando_el_archivo_esta_en_600(home_con_credencial):
    rc, out, err = _corre(
        """
        import seal_observer_credencial as c
        v = c.leer_dsn_del_archivo()
        print("SENUELO" if v.endswith("/senuelo") else "OTRA-COSA")
        """,
        home_con_credencial,
    )
    assert rc == 0, err
    assert out == "SENUELO"


def test_devuelve_VACIO_y_no_levanta_cuando_el_archivo_no_existe(home_vacio):
    """El contrato que rompió al exporter: NO lanza, devuelve cadena vacía.

    Si algún día se cambia a que levante, este brazo se pone rojo y obliga a
    revisar a los llamadores, que hoy dependen de chequear el vacío.
    """
    rc, out, err = _corre(
        """
        import seal_observer_credencial as c
        v = c.leer_dsn_del_archivo()
        print("VACIO" if v == "" else "NO-VACIO")
        """,
        home_vacio,
    )
    assert rc == 0, err
    assert out == "VACIO"


def test_rechaza_un_archivo_con_permisos_flojos(home_con_credencial):
    (home_con_credencial / ".config/seal/mcp_postgres_observer.env").chmod(0o644)
    rc, out, err = _corre(
        """
        import seal_observer_credencial as c
        try:
            c.leer_dsn_del_archivo()
            print("ACEPTO-644")
        except RuntimeError:
            print("RECHAZO-644")
        """,
        home_con_credencial,
    )
    assert rc == 0, err
    assert out == "RECHAZO-644"


def test_qa_control_el_error_de_permisos_NO_incluye_el_dsn(home_con_credencial):
    """Control de fuga: el mensaje nombra modo y uid, jamás el contenido."""
    (home_con_credencial / ".config/seal/mcp_postgres_observer.env").chmod(0o644)
    rc, out, err = _corre(
        """
        import seal_observer_credencial as c
        try:
            c.leer_dsn_del_archivo()
        except RuntimeError as e:
            print("FUGA" if "senuelo" in str(e) or "postgresql://" in str(e) else "LIMPIO")
        """,
        home_con_credencial,
    )
    assert rc == 0, err
    assert out == "LIMPIO"


def test_una_ruta_explicita_no_se_pisa_con_la_de_por_defecto(tmp_path, home_con_credencial):
    """El default se resuelve EN CADA LLAMADA; una ruta explícita manda.

    Es el brazo que mató al mutante del 7-sep: con el default ligado en la firma,
    pasar una ruta distinta no cambiaba nada.
    """
    otro = tmp_path / "otro.env"
    otro.write_text("POSTGRES_MCP_DSN=postgresql://explicito/x\n", encoding="utf-8")
    otro.chmod(0o600)
    rc, out, err = _corre(
        f"""
        import pathlib, seal_observer_credencial as c
        v = c.leer_dsn_del_archivo(pathlib.Path({str(otro)!r}))
        print("EXPLICITO" if v == "postgresql://explicito/x" else "PISADO:" + ("vacio" if not v else "otro"))
        """,
        home_con_credencial,
    )
    assert rc == 0, err
    assert out == "EXPLICITO"


def test_acepta_las_claves_alternativas(tmp_path):
    otro = tmp_path / "alt.env"
    otro.write_text("SEAL_PG_DSN=postgresql://alterna/x\n", encoding="utf-8")
    otro.chmod(0o600)
    rc, out, err = _corre(
        f"""
        import pathlib, seal_observer_credencial as c
        v = c.leer_dsn_del_archivo(pathlib.Path({str(otro)!r}))
        print("ALTERNA" if v == "postgresql://alterna/x" else "NO")
        """,
        tmp_path,
    )
    assert rc == 0, err
    assert out == "ALTERNA"


def test_no_importa_nada_fuera_de_la_biblioteca_estandar(tmp_path):
    """La razón de existir del módulo: la unidad del exporter corre con
    /usr/bin/python3, donde `mcp` y `asyncpg` no están. Si alguien le agrega una
    dependencia de terceros, el servicio vuelve a romperse en producción y este
    brazo lo ve antes."""
    rc, out, err = _corre(
        """
        import ast, pathlib, sys, seal_observer_credencial as c
        arbol = ast.parse(pathlib.Path(c.__file__).read_text(encoding="utf-8"))
        mods = set()
        for n in ast.walk(arbol):
            if isinstance(n, ast.Import):
                mods.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                mods.add(n.module.split(".")[0])
        fuera = sorted(m for m in mods if m not in sys.stdlib_module_names)
        print("SOLO-STDLIB" if not fuera else "DEPENDE-DE:" + ",".join(fuera))
        """,
        tmp_path,
    )
    assert rc == 0, err
    assert out == "SOLO-STDLIB"


# ──────────────────────── el consumidor: el exporter ────────────────────────

def test_exporter_usa_el_archivo_cuando_no_hay_variable(home_con_credencial):
    rc, out, err = _corre(
        """
        import soul_metrics_exporter as e
        print("SENUELO" if e._dsn().endswith("/senuelo") else "OTRA-COSA")
        """,
        home_con_credencial,
    )
    assert rc == 0, err
    assert out == "SENUELO"


def test_exporter_da_precedencia_al_entorno(home_con_credencial):
    rc, out, err = _corre(
        """
        import soul_metrics_exporter as e
        print("ENTORNO" if e._dsn() == "postgresql://del-entorno/x" else "ARCHIVO")
        """,
        home_con_credencial,
        {"POSTGRES_MCP_DSN": "postgresql://del-entorno/x"},
    )
    assert rc == 0, err
    assert out == "ENTORNO"


def test_qa_negative_exporter_FALLA_CLARO_sin_variable_y_sin_archivo(home_vacio):
    """El defecto que encontró el control negativo: devolvía "" en silencio.

    Un DSN vacío no explota acá, explota más tarde disfrazado de error de
    conexión. Este brazo exige el RuntimeError explícito.
    """
    rc, out, err = _corre(
        """
        import soul_metrics_exporter as e
        try:
            v = e._dsn()
            print("DEVOLVIO-VACIO" if v == "" else "DEVOLVIO-ALGO")
        except RuntimeError:
            print("RUNTIMEERROR")
        """,
        home_vacio,
    )
    assert rc == 0, err
    assert out == "RUNTIMEERROR"


def test_exporter_carga_el_helper_por_ruta_y_no_por_el_cwd(home_con_credencial, tmp_path):
    """Portabilidad: el fallo real fue ModuleNotFoundError en la unidad, que
    arranca desde otro directorio. Se corre desde un cwd ajeno y SIN el
    directorio de tools en el path de importación por cwd."""
    ajeno = tmp_path / "cwd_ajeno"
    ajeno.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("POSTGRES", "SEAL_", "PG"))}
    env.update({"HOME": str(home_con_credencial), "PYTHONPATH": TOOLS, "PYTHONDONTWRITEBYTECODE": "1"})
    p = subprocess.run(
        [sys.executable, "-c", "import soul_metrics_exporter as e; print('OK' if e._dsn() else 'VACIO')"],
        capture_output=True, text=True, env=env, cwd=str(ajeno), timeout=120,
    )
    assert p.returncode == 0, p.stderr
    assert p.stdout.strip() == "OK"


def test_qa_control_el_dsn_vivo_no_entra_al_proceso_de_pytest():
    """Control del propio arnés: si este brazo falla, los demás no prueban lo
    que dicen —estarían leyendo la credencial real en vez del señuelo."""
    assert "POSTGRES_MCP_DSN" not in os.environ or os.environ.get("POSTGRES_MCP_DSN", "") == ""


# ───────── condición del veredicto de FABLE (8-sep, mutante C1) ─────────

def test_qa_control_una_linea_COMENTADA_no_se_toma_por_el_DSN(tmp_path):
    """Mutante C1 de FABLE: cambiar `startswith(clave+"=")` por `clave in linea`
    SOBREVIVÍA a los 25 brazos. El código ya era correcto —usa `startswith`—
    pero **ninguna prueba lo observaba**, así que nada impedía que alguien lo
    relajara. Con un `#` delante, el valor de al lado es un comentario, y
    tomarlo sería leer una credencial vieja o de ejemplo.
    """
    f = tmp_path / "c.env"
    f.write_text("# POSTGRES_MCP_DSN=postgresql://comentado/NO\n"
                 "POSTGRES_MCP_DSN=postgresql://real/si\n", encoding="utf-8")
    f.chmod(0o600)
    fpath = str(f)
    rc, out, err = _corre(f"""
import pathlib
import seal_observer_credencial as c
print(c.leer_dsn_del_archivo(pathlib.Path({fpath!r})))
""", home=tmp_path)
    assert rc == 0, err
    assert out == "postgresql://real/si"


def test_qa_control_la_clave_debe_estar_al_PRINCIPIO_de_la_linea(tmp_path):
    """Mismo mutante, el otro lado: una línea donde la clave aparece en medio
    no define nada."""
    f = tmp_path / "c.env"
    f.write_text("export OTRA=1 POSTGRES_MCP_DSN=postgresql://en-medio/NO\n", encoding="utf-8")
    f.chmod(0o600)
    fpath = str(f)
    rc, out, err = _corre(f"""
import pathlib
import seal_observer_credencial as c
print(c.leer_dsn_del_archivo(pathlib.Path({fpath!r})))
""", home=tmp_path)
    assert rc == 0, err
    assert out == ""
