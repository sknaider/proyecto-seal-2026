"""Contrato de gx.sh: una busqueda que NUNCA devuelve un numero suelto.

POR QUE ESTE ARCHIVO EXISTE SEPARADO (29-ago-2026): gx.sh estaba en el mismo
manifiesto que la deteccion de asiento porque lo escribi yo, no porque
perteneciera -un wrapper de grep junto a tres archivos de deteccion de asiento-.
Lo diagnostico NEXUS: los manifiestos del equipo agrupaban por AUTORIA y no por
UNIDAD DE REVISION, y los nombres lo venian diciendo (`jarvis-tooling`,
`nexus-seat-and-storea`). Un revisor de gx.sh no deberia tener que mirar el
detector de asiento para firmar.

QUE CUBRE: los tres estados de gx (hay / no hay / ERROR) y la propiedad que le
da sentido -ante cero coincidencias lo dice con PALABRAS, nunca imprime "0"-.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # tests -> JARVIS -> agents -> raiz
GX = ROOT / "agents" / "JARVIS" / "gx.sh"


def test_gx_self_test_passes():
    """POSITIVO: el self-test de gx pasa sobre los bytes actuales."""
    r = subprocess.run(["bash", str(GX), "--self-test"],
                       check=False, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "fallos=0" in r.stdout, r.stdout


def test_gx_never_reports_a_bare_zero():
    """CONTRATO: ante cero coincidencias NO imprime '0', lo dice con palabras.

    Un cero se lee como DATO -"hay cero"-; una ausencia declarada obliga a
    preguntarse si el patron podia acertar. Es la diferencia entre un numero y
    un significado, que es la razon de ser de este script.
    """
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "vacio.txt"
        f.write_text("nada relevante\n", encoding="utf-8")
        r = subprocess.run(["bash", str(GX), "ZZZ_NO_EXISTE_9917", str(f)],
                           check=False, capture_output=True, text=True, timeout=60)
        assert r.returncode == 1, r.stdout + r.stderr
        assert "SIN COINCIDENCIAS" in r.stdout, r.stdout
        assert r.stdout.strip() != "0"


def test_gx_distingue_ERROR_de_AUSENCIA():
    """NEGATIVO, y es el hallazgo de ALICE: `grep` tiene TRES estados.

    0 = hubo coincidencias · 1 = NO hubo · 2 = ERROR (permiso, ruta mala).
    Con el `|| true` que yo tenia, un rc=2 llegaba como "sin hits" y gx decia
    "SIN COINCIDENCIAS" -convirtiendo un FALLO en una AUSENCIA-. Es exactamente
    el defecto contra el que existe gx.sh, adentro de gx.sh.

    CONTROL DEL INSTRUMENTO: si el directorio se pudiera leer, el caso no
    existiria y el test pasaria por la razon equivocada.
    """
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "sin_permiso"
        d.mkdir()
        (d / "x.txt").write_text("MARCA\n", encoding="utf-8")
        d.chmod(0o000)
        try:
            try:
                list(d.iterdir())
                raise AssertionError("el dir sembrado SI se lee: la fixture no vale")
            except PermissionError:
                pass
            r = subprocess.run(["bash", str(GX), "MARCA", str(d)],
                               check=False, capture_output=True, text=True, timeout=60)
        finally:
            d.chmod(0o700)

    assert r.returncode == 4, (
        f"un ERROR de busqueda se reporto como rc={r.returncode}.\n"
        "  rc=1 aqui significa decir 'no esta' sobre un patron que NUNCA se evaluo.\n"
        f"  stdout={r.stdout[:300]!r}")
    assert "NO es una ausencia" in r.stdout, r.stdout


def test_bash_is_available():
    """CONTROL DE LA FIXTURE: sin bash, los otros casos serian no-ops verdes."""
    r = subprocess.run(["bash", "-c", "echo ok"],
                       check=False, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "ok" in r.stdout
