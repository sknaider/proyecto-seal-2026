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


# ---------------------------------------------- el freno de MECANISMO
# NEXUS, 7-sep 14:37: los brazos de arriba impiden que un TEST toque material
# real, pero el SCRIPT seguia permitiendo que cualquiera lo hiciera a mano con
# una clave improvisada — el vector exacto del incidente de las 14:23.
# "Una regla que deba cumplirse aunque el agente se equivoque va en una capa de
# MECANISMO, no en un archivo." Estos dos brazos ejercen esa capa.
def test_qa_negative_clave_no_designada_con_lista_REAL_se_niega():
    """Repite la invocación del 7-sep 14:23 y exige que hoy se frene sola."""
    a, lista, clave, dest = _arena()                 # clave propia, NO la designada
    env = dict(os.environ, SEAL_SECRETOS_KEYFILE=str(clave), SEAL_SECRETOS_DEST=str(dest))
    env.pop("SEAL_SECRETOS_LISTA", None)             # -> usa la LISTA REAL
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                       env=env, cwd=str(RAIZ), timeout=300)
    assert r.returncode == 2, "una clave improvisada con la lista REAL NO fue frenada"
    # el mensaje cambio al cerrar la evasion por CONTENIDO (14:40): ahora nombra
    # las rutas reales que la lista apunta, en vez de la identidad de la lista.
    assert "FUERA de /tmp" in r.stderr
    assert not list(dest.glob("*.enc")), "publico un paquete pese a frenarse"


def test_qa_control_ensayo_legitimo_corre_pero_SIN_roles():
    """Control: el freno no puede romper el ensayo legítimo.

    Con clave propia Y lista señuelo el script debe correr — y NO volcar los
    roles reales, porque sus verificadores no tienen por qué estar en un
    paquete cifrado con una clave improvisada.
    """
    a, lista, clave, dest = _arena()
    env = dict(os.environ, SEAL_SECRETOS_KEYFILE=str(clave), SEAL_SECRETOS_DEST=str(dest),
               SEAL_SECRETOS_LISTA=str(lista))
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                       env=env, cwd=str(RAIZ), timeout=300)
    assert r.returncode == 0, r.stderr
    assert "0 roles" in r.stdout, f"volco roles en un ensayo: {r.stdout}"
    assert list(dest.glob("*.enc")), "no publico nada en un ensayo legitimo"


def test_qa_negative_copiar_la_lista_real_a_otra_ruta_NO_evade_el_freno():
    """Evasión medida por NEXUS el 7-sep 14:40, cerrada por CONTENIDO.

    La versión anterior comparaba la RUTA de la lista, así que `cp lista /tmp/x`
    la volvía "distinta" llevando adentro los mismos 20 secretos reales.
    Ahora se exige que TODA ruta de la lista viva bajo /tmp: una copia de la
    lista real sigue apuntando a /home/dadito y se frena.
    """
    a, lista, clave, dest = _arena()
    copia = a / "copia_de_la_lista_real.txt"
    copia.write_text((RAIZ / "quality/estado_esencial_rutas.txt").read_text())
    env = dict(os.environ, SEAL_SECRETOS_KEYFILE=str(clave), SEAL_SECRETOS_DEST=str(dest),
               SEAL_SECRETOS_LISTA=str(copia))
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                       env=env, cwd=str(RAIZ), timeout=300)
    assert r.returncode == 2 and "FUERA de /tmp" in r.stderr
    assert not list(dest.glob("*.enc")), "publico pese a apuntar a rutas reales"


def test_qa_negative_enlace_bajo_tmp_que_apunta_afuera_NO_evade():
    """Cuarta evasión de NEXUS (7-sep 14:42), cerrada resolviendo la ruta.

    Un symlink bajo /tmp pasaba el filtro de TEXTO y `cp -p` seguía el enlace:
    el contenido real viajaba dentro del paquete. Ahora cada ruta se resuelve
    con readlink -f antes de decidir. El texto de la ruta no es la ruta.
    """
    a, lista, clave, dest = _arena()
    victima = a / "secreto_simulado.txt"
    victima.write_text("CONTENIDO-QUE-NO-DEBERIA-VIAJAR\n")
    fuera = pathlib.Path(tempfile.mkdtemp(dir="/var/tmp", prefix="seal-fuera-"))
    real = fuera / "real.txt"; real.write_text("CONTENIDO-QUE-NO-DEBERIA-VIAJAR\n")
    enlace = a / "parece_senuelo.txt"
    enlace.symlink_to(real)                      # bajo /tmp, apunta a /var/tmp
    lista.write_text(str(enlace) + "\n")
    env = dict(os.environ, SEAL_SECRETOS_KEYFILE=str(clave), SEAL_SECRETOS_DEST=str(dest),
               SEAL_SECRETOS_LISTA=str(lista))
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                       env=env, cwd=str(RAIZ), timeout=300)
    assert r.returncode == 2 and "FUERA de /tmp" in r.stderr, r.stderr
    assert not list(dest.glob("*.enc")), "publico siguiendo un enlace hacia afuera"


def test_qa_negative_enlace_DURO_no_evade_el_freno():
    """Condición de FABLE (7-sep 15:34), la residual que NEXUS dejó declarada.

    Un enlace DURO no tiene destino: ES el archivo, así que `readlink -f` no lo
    delata. Se mira `st_nlink`: más de un nombre para el mismo inodo puede ser
    un secreto real bajo otra ruta.
    """
    a, lista, clave, dest = _arena()
    original = a / "original.txt"; original.write_text("SECRETO-SIMULADO\n")
    duro = a / "parece_senuelo.txt"
    os.link(original, duro)
    lista.write_text(str(duro) + "\n")
    env = dict(os.environ, SEAL_SECRETOS_KEYFILE=str(clave), SEAL_SECRETOS_DEST=str(dest),
               SEAL_SECRETOS_LISTA=str(lista))
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True,
                       env=env, cwd=str(RAIZ), timeout=300)
    assert r.returncode == 2, "un enlace duro evadio el freno"
    # ADA (15:48): un negativo debe alcanzar LA GUARDA QUE VERIFICA, no fallar
    # antes por otro motivo. Por eso se exige el mensaje del enlace duro, no
    # cualquier rechazo.
    assert "enlace DURO" in r.stderr, r.stderr
    assert not list(dest.glob("*.enc"))


def test_qa_control_el_script_corre_desde_una_COPIA_fuera_de_su_ruta():
    """Regresión permanente pedida por ADA (15:48).

    El 7-sep el script cableaba REPO=/home/dadito/IA/proyecto-seal y moría en
    "no existe la lista" al correr desde cualquier otro lado — justo el caso
    para el que un script de recuperación existe. Este control lo fija.
    """
    a, lista, clave, dest = _arena()
    copia_dir = a / "copia" / "tools"; copia_dir.mkdir(parents=True)
    (copia_dir / SCRIPT.name).write_bytes(SCRIPT.read_bytes())
    (a / "copia" / "quality").mkdir()
    (a / "copia" / "quality" / "estado_esencial_rutas.txt").write_text("")
    env = dict(os.environ, SEAL_SECRETOS_KEYFILE=str(clave), SEAL_SECRETOS_DEST=str(dest),
               SEAL_SECRETOS_LISTA=str(lista))
    r = subprocess.run(["bash", str(copia_dir / SCRIPT.name)], capture_output=True, text=True,
                       env=env, cwd=str(a), timeout=300)
    assert r.returncode == 0, f"desde una copia no corre: {r.stderr}"
    assert list(dest.glob("*.enc")), "no publico corriendo desde la copia"
