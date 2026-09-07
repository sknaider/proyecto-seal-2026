"""Brazos del detector de sujetos compartidos entre manifiestos.

Todo con manifiestos SENUELO bajo /tmp: ninguno toca quality/manifests del arbol real.
El brazo que mas importa es el negativo: NO debe gritar por compartir un archivo -eso es
normal y frecuente-, solo cuando ademas hay una firma que ya no cubre los bytes.
"""
from __future__ import annotations
import json, pathlib, subprocess, sys

DETECTOR = pathlib.Path(__file__).resolve().parents[2] / "tools" / "seal_detector_sujeto_compartido.py"


def _correr(carpeta: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(DETECTOR), str(carpeta)], capture_output=True, text=True)


def _manifiesto(carpeta: pathlib.Path, nombre: str, sujetos: list[str],
                firmado: dict[str, str] | None = None) -> None:
    m = {"change_id": nombre, "owner": "SENUELO", "subjects": sujetos, "tests": []}
    if firmado is not None:
        m["review"] = {"status": "approved",
                       "receipt": {"reviewer": "SENUELO", "reviewed_sha256": firmado}}
    (carpeta / f"{nombre}.json").write_text(json.dumps(m), encoding="utf-8")


def test_unit_encuentra_el_sujeto_declarado_por_DOS_manifiestos(tmp_path):
    _manifiesto(tmp_path, "carril-a", ["memory/senuelo.py"])
    _manifiesto(tmp_path, "carril-b", ["memory/senuelo.py"])
    r = _correr(tmp_path)
    assert "COMPARTIDO" in r.stdout and "memory/senuelo.py" in r.stdout
    assert "1 compartidos" in r.stdout


def test_qa_positive_avisa_cuando_la_firma_de_un_compartido_ya_no_cubre_los_bytes(tmp_path):
    _manifiesto(tmp_path, "carril-a", ["quality_gate/gate.py"],
                firmado={"quality_gate/gate.py": "0" * 64})   # firma que no corresponde a nada
    _manifiesto(tmp_path, "carril-b", ["quality_gate/gate.py"])
    r = _correr(tmp_path)
    assert r.returncode == 1, r.stdout
    assert "FIRMA VENCIDA EN ARCHIVO COMPARTIDO" in r.stdout
    assert "carril-b" in r.stdout, "debe decir CON QUIEN se comparte: eso es lo unico que agrega"


def test_qa_negative_NO_avisa_por_compartir_un_archivo_sin_firma_vencida(tmp_path):
    """Compartir sujeto es normal: 22 casos en el arbol real. Gritar por eso seria ruido."""
    _manifiesto(tmp_path, "carril-a", ["memory/senuelo.py"])
    _manifiesto(tmp_path, "carril-b", ["memory/senuelo.py"])
    r = _correr(tmp_path)
    assert r.returncode == 0, r.stdout
    assert "FIRMA VENCIDA" not in r.stdout


def test_qa_negative_NO_avisa_por_una_firma_vencida_en_archivo_NO_compartido(tmp_path):
    """Eso ya lo marca el gate manifiesto por manifiesto. Repetirlo seria pisarle el trabajo."""
    _manifiesto(tmp_path, "carril-solo", ["quality_gate/gate.py"],
                firmado={"quality_gate/gate.py": "0" * 64})
    r = _correr(tmp_path)
    assert r.returncode == 0, r.stdout
    assert "FIRMA VENCIDA" not in r.stdout


def test_qa_control_carpeta_sin_manifiestos_NO_dice_limpio(tmp_path):
    """Unico modo de fallo silencioso: quedarse sin nada que mirar y devolver 0."""
    r = _correr(tmp_path / "no_existe")
    assert r.returncode == 2, r.stdout
    assert "SIN MIRAR" in r.stdout
