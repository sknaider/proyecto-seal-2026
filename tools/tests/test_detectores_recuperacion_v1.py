"""Brazos de calidad de los 4 detectores que ALICE entrego el 7-sep-2026.

Los cuatro brazos, y lo que prueba cada uno:

  unit          los scripts parsean (sintaxis), sin ejecutarlos sobre nada real
  qa_positive   con una bomba PLANTADA, el detector la encuentra
  qa_negative   con el arbol limpio, NO inventa hallazgos
  qa_control    el control que hace falta porque el positivo solo no basta:
                sobre la MISMA arena, al versionar la bomba el detector pasa
                a exit=0. Eso prueba que responde al HECHO y no al arbol.

Todo corre en arenas bajo /tmp; nada toca el repo ni la maquina.
"""
from __future__ import annotations
import ast, hashlib, pathlib, subprocess, tempfile

RAIZ = pathlib.Path(__file__).resolve().parents[2]
NO_VERSIONADO = RAIZ / "tools/seal_detector_no_versionado.py"
CREDENCIALES = RAIZ / "tools/seal_detector_credenciales_unidades.py"
MUTILADOS = RAIZ / "tools/seal_detector_paquetes_vacios.py"
EXISTENCIA = RAIZ / "tools/seal_detector_existencia.py"
CHEQUEO = RAIZ / "tools/seal_chequeo_integridad_recuperacion.sh"


def _arena() -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp(dir="/tmp", prefix="seal-arena-test-"))


def _git(d: pathlib.Path, *a: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *a],
                   cwd=d, check=True, capture_output=True)


def _corre(script: pathlib.Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["python3", str(script), *args],
                          capture_output=True, text=True, timeout=300)


# ---------------------------------------------------------------- unit
def test_unit_los_cinco_scripts_parsean():
    for py in (NO_VERSIONADO, CREDENCIALES, MUTILADOS, EXISTENCIA):
        ast.parse(py.read_text())          # lanza si hay error de sintaxis
    r = subprocess.run(["bash", "-n", str(CHEQUEO)], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()


# ------------------------------------------------------------ positivo
def test_qa_positive_no_versionado_encuentra_la_bomba():
    a = _arena(); _git(a, "init", "-q", ".")
    (a / "pkg").mkdir()
    (a / "pkg/main.py").write_text("import compa\n")
    (a / "pkg/compa.py").write_text("X = 1\n")      # LA BOMBA: no se versiona
    _git(a, "add", "pkg/main.py"); _git(a, "commit", "-q", "-m", "base")
    r = _corre(NO_VERSIONADO, str(a))
    assert r.returncode == 1 and "pkg/compa.py" in r.stdout


def test_qa_positive_credenciales_con_nombre_inocente():
    a = _arena()
    # el caso dificil: nombre de variable que NO delata, valor que si
    (a / "u.service").write_text(
        "[Service]\nEnvironment=CFG=postgres://usuario:REDACTADO@h/db\n")
    r = _corre(CREDENCIALES, str(a))
    assert r.returncode == 1 and "CFG" in r.stdout


def test_qa_positive_paquete_mutilado_reproduce_el_caso_del_mcp():
    a = _arena(); sp = a / "site-packages"; (sp / "pandas/_libs").mkdir(parents=True)
    (sp / "pandas/_libs/algos.cpython-312.so").write_bytes(b"\x00")
    r = _corre(MUTILADOS, str(sp))
    assert r.returncode == 1 and "pandas" in r.stdout


# ------------------------------------------------------------ negativo
def test_qa_negative_arbol_limpio_no_inventa_hallazgos():
    a = _arena(); _git(a, "init", "-q", ".")
    (a / "pkg").mkdir()
    (a / "pkg/main.py").write_text("import compa\n")
    (a / "pkg/compa.py").write_text("X = 1\n")
    _git(a, "add", "-A"); _git(a, "commit", "-q", "-m", "todo")
    assert _corre(NO_VERSIONADO, str(a)).returncode == 0

    b = _arena()
    (b / "u.service").write_text(
        "[Service]\n"
        "Environment=SEAL_PG_DSN_FILE=/home/x/.config/seal/env/a.env\n"
        "Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus\n"
        "Environment=SUIE_SHA256=3f786850e387550fdab836ed7e6dc881de23001b\n")
    assert _corre(CREDENCIALES, str(b)).returncode == 0

    c = _arena(); sp = c / "site-packages"; (sp / "numpy.libs").mkdir(parents=True)
    (sp / "numpy.libs/libopenblas.so").write_bytes(b"\x00")
    (sp / "numpy-2.5.3.dist-info").mkdir()
    (sp / "numpy-2.5.3.dist-info/RECORD").write_text("numpy.libs/libopenblas.so,sha256=x,1\n")
    assert _corre(MUTILADOS, str(sp)).returncode == 0


# ------------------------------------------------------------- control
def test_qa_control_el_hallazgo_desaparece_al_CORREGIR_el_hecho():
    """Sin esto, un detector que grita siempre pasaria el positivo.

    Misma arena, mismo comando: lo unico que cambia es versionar la bomba.
    Si el exit no pasa de 1 a 0, el detector no esta midiendo el hecho.
    """
    a = _arena(); _git(a, "init", "-q", ".")
    (a / "pkg").mkdir()
    (a / "pkg/main.py").write_text("import compa\n")
    (a / "pkg/compa.py").write_text("X = 1\n")
    _git(a, "add", "pkg/main.py"); _git(a, "commit", "-q", "-m", "base")
    antes = _corre(NO_VERSIONADO, str(a)).returncode
    _git(a, "add", "pkg/compa.py"); _git(a, "commit", "-q", "-m", "cierro la bomba")
    despues = _corre(NO_VERSIONADO, str(a)).returncode
    assert (antes, despues) == (1, 0), f"antes={antes} despues={despues}"


# --------------------------------------------- brazos que matan mutantes
# NEXUS reviso el 7-sep y encontro que la condicion central de
# paquetes_vacios (`if sos > 0 and pys == 0`) sobrevivia a dos mutaciones:
# mis tests no distinguian sus dos mitades. Estos dos casos las separan.
def test_qa_negative_mata_mutante_solo_binarios_ignora_las_fuentes():
    """Mata `if sos > 0:` — un paquete CON fuentes no esta mutilado.

    Un namespace legitimo con submodulos .py y ademas un .so compilado
    NO debe marcarse. Si alguien borra la mitad `pys == 0`, este caso
    se pone rojo.
    """
    a = _arena(); sp = a / "site-packages"; (sp / "plug/sub").mkdir(parents=True)
    (sp / "plug/sub/mod.py").write_text("X = 1\n")
    (sp / "plug/sub/acel.cpython-312.so").write_bytes(b"\x00")
    r = _corre(MUTILADOS, str(sp))
    assert r.returncode == 0, f"marco un paquete que SI tiene fuentes: {r.stdout}"


def test_qa_negative_mata_mutante_sin_fuentes_ignora_los_binarios():
    """Mata `if pys == 0:` — un directorio sin binarios NO es un paquete roto.

    Datos sueltos sin .py y sin .so no son un paquete mutilado: son datos.
    Si alguien borra la mitad `sos > 0`, este caso se pone rojo.
    """
    a = _arena(); sp = a / "site-packages"; (sp / "datos").mkdir(parents=True)
    (sp / "datos/tabla.csv").write_text("a,b\n1,2\n")
    r = _corre(MUTILADOS, str(sp))
    assert r.returncode == 0, f"marco un directorio de datos: {r.stdout}"


# ------------------------------------------- el detector de EXISTENCIA
# NEXUS, 7-sep 14:33: "protege el caso mas importante que tenemos y nada lo
# protege a el". Tenia razon: lo escribi por la condicion de FABLE -los
# detectores diferenciales dan verde con el arbol vaciado- y lo entregue sin
# un solo brazo. Estos tres lo cubren.
def _arena_existencia(rutas: dict[str, str]):
    """Arma un arbol con el detector, su lista y las rutas que se le pidan."""
    import json
    a = _arena()
    (a / "tools").mkdir(); (a / "quality").mkdir()
    (a / "tools/seal_detector_existencia.py").write_bytes(EXISTENCIA.read_bytes())
    lista = {"_doc": "prueba", "rutas": {}}
    for rel, contenido in rutas.items():
        p = a / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contenido)
        lista["rutas"][rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    (a / "quality/rutas-criticas.json").write_text(json.dumps(lista))
    return a


def test_qa_positive_existencia_grita_cuando_falta_una_ruta():
    """El caso del 7-sep 01:42: el arbol se vacia y hay que enterarse."""
    a = _arena_existencia({"algo/critico.py": "X = 1\n"})
    (a / "algo/critico.py").unlink()          # se borra lo declarado
    r = _corre(a / "tools/seal_detector_existencia.py")
    assert r.returncode == 1 and "FALTAN" in r.stdout and "critico.py" in r.stdout


def test_qa_control_existencia_no_grita_cuando_estan_todas():
    """Control: si gritara siempre, el positivo no probaria nada."""
    a = _arena_existencia({"algo/critico.py": "X = 1\n"})
    r = _corre(a / "tools/seal_detector_existencia.py")
    assert r.returncode == 0, f"grito sin faltantes: {r.stdout}"


def test_qa_negative_existencia_con_lista_VACIA_no_pasa():
    """Su unico modo de fallo silencioso: una lista vacia lo vuelve siempre-verde."""
    import json
    a = _arena_existencia({"algo/critico.py": "X = 1\n"})
    (a / "quality/rutas-criticas.json").write_text(json.dumps({"_doc": "x", "rutas": {}}))
    r = _corre(a / "tools/seal_detector_existencia.py")
    assert r.returncode == 2 and "VACIA" in r.stdout


def test_qa_negative_un_nombre_de_DIRECTORIO_relativo_no_es_credencial():
    """Falso positivo real, reportado por ADA el 7-sep-2026 leyendo next.config.ts.

    `STUDIO_BUILD_DIR=.next-clipboard-20260907a` mide 25 chars y da entropia 4.2, asi
    que caia en la regla de "valor opaco". No empieza con "/" ni "./", asi que la guarda
    de rutas no lo veia. Alimenta `distDir` de Next.js: es un nombre de carpeta de build.

    Un detector de credenciales que grita por un directorio ensena a ignorarlo.
    """
    import importlib.util, pathlib
    ruta = pathlib.Path(__file__).resolve().parents[2] / "tools" / "seal_detector_credenciales_unidades.py"
    spec = importlib.util.spec_from_file_location("det_cred", ruta)
    det = importlib.util.module_from_spec(spec); spec.loader.exec_module(det)

    es_cred, por_que = det.clasificar("STUDIO_BUILD_DIR", ".next-clipboard-20260907a")
    assert es_cred is False, f"marco un directorio como credencial: {por_que}"

    # CONTROL: la exencion se apoya en el nombre, asi que no debe tapar un secreto real
    # colgado de una variable que diga DIR. El valor manda; el nombre solo desempata.
    es_cred, _ = det.clasificar("STUDIO_BUILD_DIR", "postgresql://seal:clavereal@localhost/db")
    assert es_cred is True, "una URI con clave embebida NO se salva por llamarse _DIR"
    es_cred, _ = det.clasificar("BUILD_DIR", "sk-proj-AbCdEfGhIjKlMnOpQrStUvWx")
    assert es_cred is True, "un prefijo de credencial conocido NO se salva por llamarse _DIR"
