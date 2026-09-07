"""Brazos del respaldo cifrado del estado esencial (carril 4).

TODO con rutas SEÑUELO bajo /tmp y datos inventados. Ningún secreto real entra
en estas pruebas, y eso NO es una comodidad: el 7-sep ALICE probó este mismo
script con las 19 rutas reales y `pg_dumpall` contra la base viva, y dejó el
paquete cifrado junto a su clave en /tmp. Un archivo cifrado y su clave, juntos,
es un archivo en claro con pasos extra.

La prueba mide el MECANISMO —cifra, publica atómico, se niega sin clave— y para
eso no hace falta un solo secreto de verdad.
"""
from __future__ import annotations
import os, pathlib, subprocess, tempfile

RAIZ = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / "tools/seal_respaldo_secretos_cifrado.sh"


def _arena():
    a = pathlib.Path(tempfile.mkdtemp(dir="/tmp", prefix="seal-arena-secretos-"))
    # tres archivos SEÑUELO con contenido inventado
    secretos = a / "senuelos"; secretos.mkdir()
    for n, c in (("uno.env", "SEAL_FALSO_DSN=postgres://nadie:inventada@ejemplo/nada\n"),
                 ("dos.dsn", "postgres://tampoco:inventada@ejemplo/nada\n"),
                 ("tres_cred", "linea-inventada-sin-valor-real\n")):
        (secretos / n).write_text(c)
    lista = a / "rutas.txt"
    lista.write_text("\n".join(str(secretos / n) for n in ("uno.env", "dos.dsn", "tres_cred")) + "\n")
    clave = a / "clave"; clave.write_text("clave-de-prueba-larga-inventada-0000\n"); os.chmod(clave, 0o600)
    dest = a / "destino"; dest.mkdir()
    return a, lista, clave, dest


def _corre(lista, clave, dest, *args):
    env = dict(os.environ, SEAL_SECRETOS_KEYFILE=str(clave), SEAL_SECRETOS_DEST=str(dest))
    # la lista se inyecta copiando el script a la arena con LISTA redirigida
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True,
                          timeout=300, env=env, cwd=str(RAIZ))


def test_unit_el_script_parsea():
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True)
    assert r.returncode == 0, r.stderr.decode()


def test_qa_negative_sin_clave_se_niega():
    a, lista, clave, dest = _arena()
    env = dict(os.environ, SEAL_SECRETOS_KEYFILE="/tmp/no-existe-jamas-000.key",
               SEAL_SECRETOS_DEST=str(dest))
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=str(RAIZ))
    assert r.returncode == 2 and "no existe el archivo de clave" in r.stderr


def test_qa_negative_clave_con_permisos_flojos_se_niega():
    a, lista, clave, dest = _arena()
    os.chmod(clave, 0o644)
    env = dict(os.environ, SEAL_SECRETOS_KEYFILE=str(clave), SEAL_SECRETOS_DEST=str(dest))
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=str(RAIZ))
    assert r.returncode == 2 and "permisos" in r.stderr


def test_qa_negative_clave_vacia_se_niega():
    """Cifrar con clave vacía no cifra: es el modo de fallo silencioso."""
    a, lista, clave, dest = _arena()
    clave.write_text(""); os.chmod(clave, 0o600)
    env = dict(os.environ, SEAL_SECRETOS_KEYFILE=str(clave), SEAL_SECRETOS_DEST=str(dest))
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, cwd=str(RAIZ))
    assert r.returncode == 2 and "VACIO" in r.stderr


def test_qa_positive_probar_rechaza_clave_equivocada():
    """El modo --probar tiene que DISTINGUIR: con otra clave, no descifra."""
    a, lista, clave, dest = _arena()
    falso = dest / "paquete.tgz.enc"
    subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "600000", "-salt",
                    "-in", str(lista), "-out", str(falso), "-pass", f"file:{clave}"], check=True)
    otra = a / "otra"; otra.write_text("clave-distinta-inventada\n"); os.chmod(otra, 0o600)
    env = dict(os.environ, SEAL_SECRETOS_KEYFILE=str(otra))
    r = subprocess.run(["bash", str(SCRIPT), "--probar", str(falso)],
                       capture_output=True, text=True, env=env, cwd=str(RAIZ))
    assert r.returncode == 2 and "NO descifra" in r.stderr


def test_qa_control_con_la_clave_correcta_descifra_pero_detecta_paquete_invalido():
    """Control que separa dos fallos que se confunden: descifrar != abrir.

    Un archivo cifrado con la clave correcta que NO es un tar valido debe dar
    "descifra pero el paquete NO abre". Sin este control, "no descifra" y
    "copia corrupta" serian el mismo mensaje y nadie sabria cual es cual.
    """
    a, lista, clave, dest = _arena()
    falso = dest / "no_es_un_tar.enc"
    subprocess.run(["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "600000", "-salt",
                    "-in", str(lista), "-out", str(falso), "-pass", f"file:{clave}"], check=True)
    env = dict(os.environ, SEAL_SECRETOS_KEYFILE=str(clave))
    r = subprocess.run(["bash", str(SCRIPT), "--probar", str(falso)],
                       capture_output=True, text=True, env=env, cwd=str(RAIZ))
    assert r.returncode == 2 and "NO abre" in r.stderr, r.stderr
