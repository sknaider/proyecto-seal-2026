"""Brazos del detector de codigo vivo que no coincide con el repositorio.

Nace de un caso medido (8-sep-2026): `seal-chat` corria con codigo que no estaba
en git, y las 7 lineas de diferencia eran el FIX RLS de los canales `user:*`
—sin ellas la sala privada de William no guarda mensajes—. Un `git checkout` de
cualquiera lo borraba y ningun chequeo lo notaba.

Los brazos usan un repositorio git REAL bajo /tmp, no un doble: lo que se prueba
es «el contenido en HEAD difiere del contenido en disco», y eso no se puede
simular sin volver el test circular.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import seal_codigo_vivo_vs_git as det  # noqa: E402


@pytest.fixture()
def repo(tmp_path):
    """Repo real con un archivo commiteado."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for k, v in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(["git", "-C", str(tmp_path), "config", k, v], check=True)
    (tmp_path / "vivo.py").write_text("print('uno')\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "vivo.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "base"], check=True)
    return tmp_path


def _estado(filas, archivo):
    return next(f["estado"] for f in filas if f["archivo"] == archivo)


# ───────────────────────── qa_positive: coincide ─────────────────────────

def test_qa_positive_disco_igual_a_HEAD_es_OK(repo):
    assert _estado(det.revisar(["vivo.py"], repo), "vivo.py") == "OK"


# ────────────── qa_negative: los tres modos de no coincidir ──────────────

def test_qa_negative_una_sola_linea_distinta_ya_es_DIVERGE(repo):
    """El caso real: 7 lineas sobre 5.000. Si esto no dispara, el detector
    no sirve para lo que se hizo."""
    (repo / "vivo.py").write_text("print('uno')\nprint('la linea que falta en git')\n")
    assert _estado(det.revisar(["vivo.py"], repo), "vivo.py") == "DIVERGE"


def test_qa_negative_archivo_en_disco_que_git_no_tiene(repo):
    (repo / "suelto.py").write_text("x = 1\n")
    assert _estado(det.revisar(["suelto.py"], repo), "suelto.py") == "AUSENTE_EN_GIT"


def test_qa_negative_archivo_que_el_servicio_ejecuta_y_NO_esta_en_disco(repo):
    """El hallazgo mas grave del 8-sep: seis unidades vivas corriendo archivos
    inexistentes. Corren desde memoria y mueren en el proximo reinicio."""
    filas = det.revisar(["se_borro.py"], repo)
    assert _estado(filas, "se_borro.py") == "AUSENTE_EN_DISCO"
    assert "reinicio" in filas[0]["detalle"], "el informe debe decir POR QUE importa"


def test_AUSENTE_EN_DISCO_e_ILEGIBLE_no_son_el_mismo_rotulo(repo):
    """Conflar los dos esconde el caso grave entre problemas de permisos.
    Yo los tenia juntos y por eso casi paso de largo."""
    (repo / "sin_permiso.py").write_text("x = 1\n")
    (repo / "sin_permiso.py").chmod(0o000)
    try:
        e = _estado(det.revisar(["sin_permiso.py"], repo), "sin_permiso.py")
    finally:
        (repo / "sin_permiso.py").chmod(0o644)
    if e != "ILEGIBLE":                      # root ignora los permisos
        pytest.skip("el usuario puede leer igual (root): el brazo no aplica")
    assert e == "ILEGIBLE"


# ───────── el defecto que tuvo ESTE detector en su primera corrida ─────────

def test_unidades_vivas_consulta_SISTEMA_Y_USUARIO(monkeypatch):
    """En la primera corrida solo miraba unidades de SISTEMA: vio 4, ignoro 61
    de usuario y **no encontro `seal-chat`, que es el caso que lo motivo**.
    Igual devolvia hallazgos, o sea que se leia como exito.
    """
    vistos = []

    class R:
        returncode = 0
        stdout = "seal-x.service loaded active running x\n"

    def falso(cmd, **kw):
        vistos.append("--user" in cmd)
        return R()

    monkeypatch.setattr(det.subprocess, "run", falso)
    det.unidades_vivas("seal-")
    assert vistos == [False, True], "debe consultar systemctl y systemctl --user"


def test_las_unidades_de_usuario_se_interrogan_CON_user(monkeypatch):
    """No alcanza con listarlas: `systemctl show` sin `--user` sobre una unidad
    de usuario devuelve vacio, y el archivo desaparece del informe en silencio."""
    vistos = {}

    class R:
        returncode = 0
        stdout = ""

    def falso(cmd, **kw):
        vistos["user"] = "--user" in cmd
        return R()

    monkeypatch.setattr(det.subprocess, "run", falso)
    det.fuentes_de_unidad("seal-x.service", pathlib.Path("/tmp"), usuario=True)
    assert vistos["user"] is True


# ─────────────── el silencio nunca es el default ───────────────

def test_qa_control_sin_archivos_descubiertos_NO_dice_que_todo_esta_bien(capsys):
    """Un detector que no descubre nada informa salud que no midio."""
    rc = det.main(["--archivo"][:0] + ["--prefijo-unidad", "no-existe-este-prefijo-"])
    salida = capsys.readouterr().out
    assert rc == 2, "sin nada revisado el codigo de salida no puede ser 0"
    assert "NO es salud" in salida


def test_rc_1_cuando_hay_hallazgos_y_0_cuando_no(repo, capsys):
    assert det.main(["--raiz", str(repo), "--archivo", "vivo.py"]) == 0
    (repo / "vivo.py").write_text("cambiado\n")
    assert det.main(["--raiz", str(repo), "--archivo", "vivo.py"]) == 1


# ───────────── el informe se pega en el chat: sin contenidos ─────────────

def test_qa_control_el_informe_NO_lleva_el_CONTENIDO_del_archivo(repo, capsys):
    """Un sujeto puede ser un archivo con credenciales. Solo rutas y hashes."""
    secreto = "VALOR-SINTETICO-NO-ES-UN-SECRETO-b2c3d4"
    (repo / "vivo.py").write_text(f"clave = '{secreto}'\n")
    det.main(["--raiz", str(repo), "--archivo", "vivo.py", "--json"])
    salida = capsys.readouterr().out
    assert secreto not in salida
    assert "DIVERGE" in salida, "y sin embargo debe reportar el hallazgo"


def test_los_hashes_van_RECORTADOS(repo):
    filas = det.revisar(["vivo.py"], repo)
    assert len(filas[0]["sha"]) == 16
