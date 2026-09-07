"""Brazos del detector de revisores que no pueden firmar.

Manifiestos SENUELO bajo /tmp y lista de vivos INYECTADA por argumento: el brazo no consulta
la base ni los tokens reales, para que la prueba no dependa de quien este vivo hoy.
"""
from __future__ import annotations
import json, pathlib, subprocess, sys

DETECTOR = pathlib.Path(__file__).resolve().parents[2] / "tools" / "seal_detector_revisor_inexistente.py"


def _correr(carpeta: pathlib.Path, vivos: str = "ADA,ALICE,NEXUS") -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(DETECTOR), str(carpeta), vivos],
                          capture_output=True, text=True)


def _manifiesto(carpeta: pathlib.Path, nombre: str, revisor: str | None) -> None:
    m = {"change_id": nombre, "owner": "SENUELO", "subjects": [], "tests": []}
    if revisor is not None:
        m["independent_reviewer"] = revisor
    (carpeta / f"{nombre}.json").write_text(json.dumps(m), encoding="utf-8")


def test_unit_encuentra_al_revisor_que_no_existe(tmp_path):
    _manifiesto(tmp_path, "carril-huerfano", "DARWIN")
    r = _correr(tmp_path)
    assert r.returncode == 1
    assert "NADIE PUEDE FIRMAR COMO  DARWIN" in r.stdout
    assert "carril-huerfano" in r.stdout, "debe nombrar el manifiesto, no solo contar"


def test_qa_positive_agrupa_varios_manifiestos_bajo_el_mismo_nombre(tmp_path):
    """Agrupar importa: el arreglo de a uno fue justo lo que dejo un caso suelto el 7-sep."""
    for n in ("uno", "dos", "tres"):
        _manifiesto(tmp_path, n, "WEGENER")
    r = _correr(tmp_path)
    assert "(3 manifiestos)" in r.stdout
    assert "3 manifiestos con un revisor que no puede firmar" in r.stdout


def test_qa_negative_NO_marca_a_un_revisor_vivo(tmp_path):
    _manifiesto(tmp_path, "carril-sano", "NEXUS")
    r = _correr(tmp_path)
    assert r.returncode == 0, r.stdout
    assert "NADIE PUEDE FIRMAR" not in r.stdout


def test_qa_negative_NO_marca_un_manifiesto_SIN_revisor_declarado(tmp_path):
    """Falta de campo no es revisor fantasma: eso ya lo reclama el gate por su cuenta."""
    _manifiesto(tmp_path, "carril-sin-campo", None)
    r = _correr(tmp_path)
    assert r.returncode == 0, r.stdout


def test_qa_control_sin_lista_de_vivos_NO_dice_limpio(tmp_path):
    """El modo de fallo peligroso: no poder derivar quien vive y contestar 'todo bien'."""
    _manifiesto(tmp_path, "carril-huerfano", "DARWIN")
    r = subprocess.run([sys.executable, str(DETECTOR), str(tmp_path), ""],
                       capture_output=True, text=True, env={"PATH": "/nonexistent"})
    assert r.returncode == 2, r.stdout
    assert "SIN MIRAR" in r.stdout


def test_qa_control_carpeta_sin_manifiestos_NO_dice_limpio(tmp_path):
    r = _correr(tmp_path / "no_existe")
    assert r.returncode == 2, r.stdout
