"""Brazos del calculador de manifest_digest.

Nace de un fallo repetido: dos agentes lo calcularon a mano y les dio mal cuatro
veces en dos dias, siempre por `json.dumps` sin `ensure_ascii=False`. Los
manifiestos llevan acentos y sin esa bandera el hash cambia.

El brazo que da sentido a todo el archivo es
`test_qa_negative_la_formula_SIN_ensure_ascii_da_OTRO_hash`: reproduce el error
real y exige que el nuestro NO lo cometa.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAIZ / "tools"))
import seal_manifest_digest as md  # noqa: E402


def _manifiesto(tmp_path, evidencia="texto del recibo", porque="por qué: cañón, ñandú, é"):
    # Los acentos van en el CUERPO (_por_que), no en el recibo: `_review_digest`
    # descarta el recibo ANTES de hashear, asi que un manifiesto cuyo unico
    # no-ASCII viva ahi da el MISMO hash con las dos formulas y el brazo del
    # ensure_ascii no probaria nada. Lo descubri al escribirlo: mi primera
    # version ponia los acentos en la evidencia y salia verde por casualidad.
    m = {"schema": "seal.quality-manifest.v1", "change_id": "x", "owner": "A",
         "independent_reviewer": "B", "subjects": ["a.py"], "tests": ["t.py"],
         "_por_que": porque,
         "review": {"status": "approved",
                    "receipt": {"reviewer": "B", "evidence": evidencia,
                                "manifest_digest": "sin-calcular"}}}
    p = tmp_path / "m.json"
    p.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return p


# ─────────────── qa_positive: coincide con el gate ───────────────

def test_qa_positive_devuelve_lo_mismo_que_el_gate(tmp_path):
    sys.path.insert(0, str(RAIZ))
    from quality_gate.gate import _review_digest
    p = _manifiesto(tmp_path)
    assert md.digest_de(p) == _review_digest(json.loads(p.read_text()))


def test_qa_positive_no_reimplementa_el_algoritmo():
    """Si algun dia el gate cambia su canonicalizacion, este script tiene que
    seguirlo solo. Por eso IMPORTA la funcion en vez de copiarla."""
    fuente = pathlib.Path(md.__file__).read_text(encoding="utf-8")
    assert "from quality_gate.gate import _review_digest" in fuente
    assert "sha256(" not in fuente, "no debe calcular el hash por su cuenta"


# ─────────────── qa_negative: el error real que motivo esto ───────────────

def test_qa_negative_la_formula_SIN_ensure_ascii_da_OTRO_hash(tmp_path):
    """El fallo exacto que cometieron ALICE y JARVIS. Si algun dia estos dos
    valores coincidieran, este brazo pierde sentido y hay que revisarlo."""
    p = _manifiesto(tmp_path)
    m = json.loads(p.read_text())
    m.get("review", {}).pop("receipt", None)
    mala = hashlib.sha256(json.dumps(m, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert md.digest_de(p) != mala, "el manifiesto de prueba debe tener no-ASCII"


def test_qa_negative_el_digest_IGNORA_el_texto_del_recibo(tmp_path):
    """Corrige un modelo mental equivocado que yo mismo publique: el recibo NO
    entra en el hash. Por eso un digest que no cambia tras re-firmar es correcto."""
    a = md.digest_de(_manifiesto(tmp_path, "un texto"))
    b = md.digest_de(_manifiesto(tmp_path, "OTRO TEXTO COMPLETAMENTE DISTINTO"))
    assert a == b


# ─────────────── el modo --verificar ───────────────

def test_verificar_sale_1_cuando_el_guardado_esta_mal(tmp_path):
    p = _manifiesto(tmp_path)
    assert md.main([str(p), "--verificar"]) == 1


def test_verificar_sale_0_cuando_coincide(tmp_path):
    p = _manifiesto(tmp_path)
    m = json.loads(p.read_text())
    m["review"]["receipt"]["manifest_digest"] = md.digest_de(p)
    p.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    assert md.main([str(p), "--verificar"]) == 0


def test_qa_control_un_archivo_inexistente_no_explota(tmp_path):
    assert md.main([str(tmp_path / "no-existe.json"), "--verificar"]) == 2


def test_cli_imprime_solo_el_hash(tmp_path):
    p = _manifiesto(tmp_path)
    r = subprocess.run([sys.executable, str(pathlib.Path(md.__file__)), str(p)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    salida = r.stdout.strip()
    assert len(salida) == 64 and all(c in "0123456789abcdef" for c in salida), \
        "la salida debe ser SOLO el hash, para poder usarla en un pipe"
