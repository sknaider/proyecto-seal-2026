"""Brazos de `tools/seal_detector_credencial_sin_fuente.py`.

Los tres estados que el detector distingue, cada uno con su brazo, y el
control que mata el sobre-reporte: una variable leida CON valor por defecto
es opcional y NO debe marcarse. Mi version 1 las contaba y daba 19 donde hay 2.

Todo en arenas bajo /tmp: ninguna unidad real se toca.
"""
from __future__ import annotations
import os, pathlib, subprocess, tempfile

RAIZ = pathlib.Path(__file__).resolve().parents[2]
DET = RAIZ / "tools/seal_detector_credencial_sin_fuente.py"


def _arena() -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp(dir="/tmp", prefix="seal-arena-cred-"))


def _unidad(a: pathlib.Path, nombre: str, cuerpo: str) -> None:
    (a / "u").mkdir(exist_ok=True)
    (a / "u" / nombre).write_text(cuerpo)


def _script(a: pathlib.Path, nombre: str, cuerpo: str) -> pathlib.Path:
    (a / "bin").mkdir(exist_ok=True)
    p = a / "bin" / nombre
    p.write_text(cuerpo)
    return p


def _corre(a: pathlib.Path) -> subprocess.CompletedProcess:
    return subprocess.run(["python3", str(DET), str(a / "u")],
                          capture_output=True, text=True, timeout=300)


def test_unit_el_detector_parsea():
    import ast
    ast.parse(DET.read_text())


def test_qa_positive_sin_fuente_se_marca():
    a = _arena()
    s = _script(a, "x.py", 'import os\nDSN = os.environ["SEAL_MI_DSN"]\n')
    _unidad(a, "bomba.service", f"[Service]\nExecStart=/usr/bin/python3 {s}\n")
    r = _corre(a)
    assert r.returncode == 1 and "SIN FUENTE" in r.stdout and "bomba.service" in r.stdout


def test_qa_positive_desajuste_de_nombre_se_marca():
    """El caso que casi mata el poller de ALICE el 7-sep: hay fuente, con OTRO nombre."""
    a = _arena()
    s = _script(a, "y.py", 'import os\nDSN = os.environ["SEAL_DB_URL"]\n')
    (a / "mi.env").write_text("SEAL_POLLER_DSN=algo\n")
    _unidad(a, "desajuste.service",
            f"[Service]\nEnvironmentFile={a}/mi.env\nExecStart=/usr/bin/python3 {s}\n")
    r = _corre(a)
    assert r.returncode == 1 and "DESAJUSTE" in r.stdout


def test_qa_negative_con_fuente_correcta_no_se_marca():
    a = _arena()
    s = _script(a, "z.py", 'import os\nDSN = os.environ["SEAL_MI_DSN"]\n')
    (a / "ok.env").write_text("SEAL_MI_DSN=algo\n")
    _unidad(a, "sana.service",
            f"[Service]\nEnvironmentFile={a}/ok.env\nExecStart=/usr/bin/python3 {s}\n")
    assert _corre(a).returncode == 0


def test_qa_control_variable_OPCIONAL_no_se_marca():
    """Mata el sobre-reporte: leida CON default, su ausencia no rompe nada.

    Sin este brazo, un detector que ignora la diferencia da el MISMO numero
    que uno que la distingue, y no hay forma de notarlo.
    """
    a = _arena()
    s = _script(a, "opt.py",
                'import os\nDSN = os.environ.get("SEAL_MI_DSN", "postgres://x:y@h/db")\n')
    _unidad(a, "opcional.service", f"[Service]\nExecStart=/usr/bin/python3 {s}\n")
    r = _corre(a)
    assert r.returncode == 0, f"marco una variable OPCIONAL: {r.stdout}"


def test_qa_negative_default_VACIO_si_se_marca():
    """Caso de ADA (13:01): `get("X", "")` NO es opcional.

    El default vacio es el modismo de "esto lo tenes que proveer" seguido de
    un chequeo que aborta. Mi filtro los descartaba y perdia justo los
    obligatorios mejor escritos.
    """
    a = _arena()
    s = _script(a, "vac.py",
                'import os\nDSN = os.environ.get("SEAL_MI_DSN", "")\n'
                'if not DSN:\n    raise SystemExit("falta")\n')
    _unidad(a, "vacio.service", f"[Service]\nExecStart=/usr/bin/python3 {s}\n")
    r = _corre(a)
    assert r.returncode == 1 and "SIN FUENTE" in r.stdout


def test_qa_control_entorno_ILEGIBLE_no_cuenta_como_sano():
    """La tercera categoria: no poder leer != estar bien.

    Mi primer barrido se cayo con PermissionError sobre /etc/... y la
    tentacion era saltear esos en silencio. Un barrido que esconde sus
    puntos ciegos da un numero mas lindo y menos cierto.
    """
    a = _arena()
    s = _script(a, "il.py", 'import os\nDSN = os.environ["SEAL_MI_DSN"]\n')
    secreto = a / "nopuedo.env"
    secreto.write_text("SEAL_MI_DSN=algo\n")
    os.chmod(secreto, 0o000)
    _unidad(a, "ilegible.service",
            f"[Service]\nEnvironmentFile={secreto}\nExecStart=/usr/bin/python3 {s}\n")
    r = _corre(a)
    os.chmod(secreto, 0o600)
    if os.geteuid() == 0:
        return                      # root lee igual: el caso no se puede montar
    assert "NO PUDE LEER" in r.stdout and "ilegible.service" in r.stdout


def test_qa_negative_un_INTERRUPTOR_con_TOKEN_en_el_nombre_no_es_credencial():
    """ADA, 7-sep 14:48: SEAL_DISABLE_LEGACY_TMP_TOKENS lleva TOKENS y es un flag.

    Su ausencia no rompe nada. Clasificar por el nombre convierte cada
    interruptor de configuracion en una falsa credencial faltante.
    """
    a = _arena()
    s = _script(a, "flag.py", 'import os\nX = os.environ["SEAL_DISABLE_LEGACY_TMP_TOKENS"]\n')
    _unidad(a, "flag.service", f"[Service]\nExecStart=/usr/bin/python3 {s}\n")
    r = _corre(a)
    assert r.returncode == 0, f"marco un interruptor como credencial: {r.stdout}"


def test_qa_negative_CREDENTIALS_DIRECTORY_la_pone_systemd():
    """ADA: si la unidad usa LoadCredential, systemd provee esa variable.

    Que no figure en Environment= no acredita desajuste.
    """
    a = _arena()
    s = _script(a, "cred.py", 'import os\nX = os.environ["CREDENTIALS_DIRECTORY"]\n')
    _unidad(a, "cred.service",
            f"[Service]\nLoadCredential=algo:/tmp/x\nExecStart=/usr/bin/python3 {s}\n")
    r = _corre(a)
    assert r.returncode == 0, f"marco CREDENTIALS_DIRECTORY pese a LoadCredential: {r.stdout}"


def test_qa_negative_EnvironmentFile_en_un_DROP_IN_no_se_marca():
    """Refutador de JARVIS (7-sep 14:48), cerrado leyendo drop-ins del disco.

    `systemctl cat` sólo conoce unidades INSTALADAS; una unidad de arena con su
    EnvironmentFile en <unidad>.d/10-env.conf caía al archivo principal y se
    marcaba como sin fuente. Ahora los drop-ins se leen por los dos caminos.
    """
    a = _arena()
    s = _script(a, "d.py", 'import os\nDSN = os.environ["SEAL_MI_DSN"]\n')
    _unidad(a, "refuta-d.service", f"[Service]\nExecStart=/usr/bin/python3 {s}\n")
    dropin = a / "u" / "refuta-d.service.d"; dropin.mkdir()
    (a / "mi.env").write_text("SEAL_MI_DSN=algo\n")
    (dropin / "10-env.conf").write_text(f"[Service]\nEnvironmentFile={a}/mi.env\n")
    r = _corre(a)
    assert r.returncode == 0, f"marco una unidad cuya fuente vive en un drop-in: {r.stdout}"


def test_qa_control_el_drop_in_no_silencia_una_credencial_REALMENTE_faltante():
    """Control de ADA: leer drop-ins no puede volverse un silenciador.

    Misma unidad con drop-in, pero el drop-in NO define la variable que el
    script exige: tiene que seguir marcandose.
    """
    a = _arena()
    s = _script(a, "d2.py", 'import os\nDSN = os.environ["SEAL_OTRA_DSN"]\n')
    _unidad(a, "sigue-rota.service", f"[Service]\nExecStart=/usr/bin/python3 {s}\n")
    dropin = a / "u" / "sigue-rota.service.d"; dropin.mkdir()
    (a / "otra.env").write_text("VARIABLE_QUE_NO_ES=algo\n")
    (dropin / "10-env.conf").write_text(f"[Service]\nEnvironmentFile={a}/otra.env\n")
    r = _corre(a)
    assert r.returncode == 1 and "DESAJUSTE" in r.stdout, r.stdout


def test_qa_negative_seal_observer_credencial_va_a_VERIFICAR_A_MANO():
    """Caso que NEXUS corrigio el 7-sep: soul_metrics_exporter usa importlib para
    cargar seal_observer_credencial.py como su fuente de DSN. El patron no estaba
    en OTRA_FUENTE, asi que la unidad aparecia en SIN FUENTE de forma erronea.

    Un script que menciona 'seal_observer_credencial' tiene otra fuente posible:
    debe ir a VERIFICAR A MANO, no a SIN FUENTE DE CREDENCIAL.
    """
    a = _arena()
    cuerpo = (
        'import os, importlib.util\n'
        'DSN = os.environ.get("SEAL_DB_URL", "")\n'
        'if not DSN:\n'
        '    _spec = importlib.util.spec_from_file_location("seal_observer_credencial", "/ruta/seal_observer_credencial.py")\n'
        '    _mod = importlib.util.module_from_spec(_spec)\n'
        '    DSN = _mod.leer_dsn_del_archivo()\n'
    )
    s = _script(a, "obs.py", cuerpo)
    _unidad(a, "observer.service", f"[Service]\nExecStart=/usr/bin/python3 {s}\n")
    r = _corre(a)
    assert r.returncode == 0, f"marco como SIN FUENTE una unidad con seal_observer_credencial: {r.stdout}"
    assert "SIN FUENTE" not in r.stdout, r.stdout
    assert "VERIFICAR A MANO" in r.stdout, r.stdout
