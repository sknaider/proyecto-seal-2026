"""Contrato de seal_verificar_mensaje.py: ¿los hashes de un mensaje corresponden?

POR QUE EXISTE ESTE ARCHIVO: publique la herramienta y no tenia tests. Es el mismo
estandar que FABLE me aplico a mi -se nego a firmar `seal_bajo_firma.py` sin
tests- y que yo le aplique a el para el audit_anchor. Dejarla sin tests seria
repetir lo que critique dos veces en la misma noche.

QUE VERIFICA LA HERRAMIENTA: que cada par `<sha256>  <ruta>` del mensaje se
corresponda con los bytes de ESA ruta AHORA. Nacio porque mi gate anterior
CONTABA hashes -forma- y FABLE le encontro el borde: la cantidad correcta de
hashes de OTROS archivos pasaba limpio.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "seal_verificar_mensaje.py"


def _run(msg: str, raiz: Path):
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(msg)
        p = f.name
    return subprocess.run([sys.executable, str(TOOL), p, str(raiz)],
                          capture_output=True, text=True, timeout=60)


def _archivo(raiz: Path, nombre: str, contenido: str) -> str:
    p = raiz / nombre
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(contenido, encoding="utf-8")
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_par_CORRECTO_pasa():
    """CONTROL POSITIVO: sin esto, 'rechaza siempre' cumpliria todos los rojos."""
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        h = _archivo(r, "a.py", "hola\n")
        p = _run(f"```text\n{h}  a.py\n```\n", r)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "corresponden" in p.stdout, p.stdout


def test_hash_VIEJO_no_corresponde():
    """El caso real: publique un hash correcto de OTRO momento. Pasa forma y
    existencia -es un sha256 real de un archivo real- y solo lo delata comparar."""
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        viejo = _archivo(r, "a.py", "version vieja\n")
        _archivo(r, "a.py", "version nueva\n")          # el archivo cambio
        p = _run(f"```text\n{viejo}  a.py\n```\n", r)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "NO CORRESPONDE" in p.stdout, p.stdout


def test_hash_de_OTRO_archivo_no_corresponde():
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        _archivo(r, "a.py", "aaa\n")
        hb = _archivo(r, "b.py", "bbb\n")
        p = _run(f"```text\n{hb}  a.py\n```\n", r)      # el hash de b, la ruta de a
    assert p.returncode == 1, p.stdout + p.stderr


def test_ruta_INEXISTENTE_no_se_reporta_como_OK():
    """Una ruta que no resuelve NO es 'no aplica': es que no pude verificar."""
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        p = _run(f"```text\n{'a'*64}  no_existe.py\n```\n", r)
    assert p.returncode == 1, p.stdout + p.stderr
    assert "NO RESUELVE" in p.stdout, p.stdout


def test_hash_SUELTO_sin_ruta_falla_CERRADO():
    """Un hash sin ruta al lado NO se puede comprobar. Decir OK ahi seria dar por
    bueno lo que no se midio -la clase que nos costo la noche entera."""
    with tempfile.TemporaryDirectory() as t:
        p = _run(f"el digest es {'b'*64} y listo\n", Path(t))
    assert p.returncode == 2, f"un hash suelto no fallo cerrado. {p.stdout}{p.stderr}"
    assert "SIN ruta" in p.stderr or "sin ruta" in p.stderr, p.stderr


def test_mensaje_SIN_hashes_no_es_un_error():
    with tempfile.TemporaryDirectory() as t:
        p = _run("un mensaje comun, sin numeros\n", Path(t))
    assert p.returncode == 0, p.stdout + p.stderr


def test_DISCRIMINA_en_la_misma_corrida():
    """El control mas fuerte: OK y NO CORRESPONDE juntos. Si solo probaramos los
    rojos, 'rechaza todo' pasaria; si solo los verdes, 'acepta todo'."""
    with tempfile.TemporaryDirectory() as t:
        r = Path(t)
        ha = _archivo(r, "bien.py", "ok\n")
        viejo = _archivo(r, "mal.py", "antes\n")
        _archivo(r, "mal.py", "despues\n")
        p = _run(f"```text\n{ha}  bien.py\n{viejo}  mal.py\n```\n", r)
    assert p.returncode == 1, p.stdout
    assert "OK           bien.py" in p.stdout, p.stdout
    assert "NO CORRESPONDE  mal.py" in p.stdout, p.stdout


def test_python_disponible():
    """CONTROL DE LA FIXTURE: sin interprete todo lo demas seria un no-op verde."""
    p = subprocess.run([sys.executable, "-c", "print('ok')"], capture_output=True, text=True)
    assert p.returncode == 0 and "ok" in p.stdout
