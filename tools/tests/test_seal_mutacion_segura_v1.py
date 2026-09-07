"""Los dos frenos del arnes de mutacion deben EJERCERSE, no estar escritos.

Cada freno tiene su brazo positivo (frena lo que debe) y su CONTROL (deja pasar
lo legitimo). Sin el control, "arreglarlo" negandose a todo pasaria en verde y
el arnes seria inservible — que es el modo de fallar que ya me mordio hoy.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import seal_mutacion_segura as sms  # noqa: E402
from seal_mutacion_segura import (    # noqa: E402
    ArnesInseguro, MARCA_GUARDA, _verificar_entorno, verificar_mutacion, mutar,
)

FUENTE_CON_GUARDA = f"""#!/usr/bin/env bash
borrar() {{
  {MARCA_GUARDA}: sin esto, el find de abajo apunta a cualquier lado
  case "$ruta" in /tmp/arena-*) : ;; *) exit 2 ;; esac
  find "$ruta" -mindepth 1 -delete
}}
saludar() {{ echo hola; }}
"""


# ── freno 2: no se muta una guarda ────────────────────────────────────────
def test_RECHAZA_mutar_la_linea_de_la_guarda():
    """El caso EXACTO del 7-sep: quitar el `case` que protege el find."""
    with pytest.raises(ArnesInseguro, match="GUARDA-DESTRUCTIVA"):
        verificar_mutacion(FUENTE_CON_GUARDA, 'case "$ruta" in /tmp/arena-*) : ;; *) exit 2 ;; esac')


def test_CONTROL_deja_mutar_una_linea_normal():
    """Sin esto, un arnes que rechaza TODO pasaria por seguro."""
    verificar_mutacion(FUENTE_CON_GUARDA, "echo hola")  # no levanta


def test_rechaza_un_ancla_ambigua():
    """Un mutante que no sabe que muta no prueba nada (me paso hoy: `19 passed`
    cuatro veces sin haber tocado un byte)."""
    fuente = "x = 1\nx = 1\n"
    with pytest.raises(ArnesInseguro, match="2 veces"):
        verificar_mutacion(fuente, "x = 1")


def test_rechaza_un_ancla_inexistente():
    with pytest.raises(ArnesInseguro, match="no aparece"):
        verificar_mutacion(FUENTE_CON_GUARDA, "esto no esta")


# ── freno 1: no arranca con permiso de escritura donde vive el trabajo ────
def test_el_freno_de_entorno_SE_EJERCE(tmp_path):
    """Contra un directorio escribible: debe negarse. Se ejerce el permiso
    creando un temporal, no se lee `stat` (el camino entero manda)."""
    with pytest.raises(ArnesInseguro, match="ESCRIBIR"):
        _verificar_entorno(raices=(str(tmp_path),))


def test_CONTROL_entorno_sin_escritura_pasa(tmp_path):
    solo_lectura = tmp_path / "ro"
    solo_lectura.mkdir()
    solo_lectura.chmod(0o500)
    try:
        _verificar_entorno(raices=(str(solo_lectura),))  # no levanta
    finally:
        solo_lectura.chmod(0o700)


def test_CONTROL_raiz_inexistente_no_bloquea(tmp_path):
    """Una raiz que no existe no puede ser escribible; no debe frenar."""
    _verificar_entorno(raices=(str(tmp_path / "no_existe"),))


def test_mutar_aplica_LOS_DOS_frenos(tmp_path, monkeypatch):
    """La via autorizada no puede saltarse ninguno."""
    # La lista real incluye el HOME desde el 7-sep 13:43, asi que el freno de
    # entorno bloquea SIEMPRE en este asiento. Para probar el freno 2 aislado se
    # sustituye por una raiz inexistente: aca se prueba `mutar`, no `verificar_entorno`
    # -que tiene sus propios brazos, incluido uno por efecto-.
    monkeypatch.setattr("seal_mutacion_segura._RAICES_PROHIBIDAS", (str(tmp_path / "nada"),))
    monkeypatch.setattr("seal_mutacion_segura._verificar_entorno",
                        lambda raices=(str(tmp_path / "nada"),): None)
    # Idem con el freno 3: tiene sus propios brazos y en este asiento -el host-
    # niega siempre, con razon. Aca se prueba que `mutar` los INVOCA a los tres.
    invocados = []
    monkeypatch.setattr("seal_mutacion_segura.verificar_montajes",
                        lambda arena, raices=(): invocados.append(arena))
    arena = str(tmp_path)
    salida = mutar(FUENTE_CON_GUARDA, "echo hola", "echo chau", arena)
    assert "echo chau" in salida and "echo hola" not in salida
    assert invocados == [arena], "mutar() debe pasarle el area de trabajo al freno 3"
    with pytest.raises(ArnesInseguro):
        mutar(FUENTE_CON_GUARDA,
              'case "$ruta" in /tmp/arena-*) : ;; *) exit 2 ;; esac', ":", arena)


# ── El freno debe mirar el HOME REAL, no su padre ──────────────────────────
# DEFECTO MEDIDO el 7-sep 13:43: la lista decia "/home" -el padre, que nadie
# puede escribir- y NO el home del usuario, que es lo que se borro a la 01:42.
# El freno pasaba en verde justo para el caso que existe para frenar.
def test_la_lista_incluye_el_HOME_DEL_USUARIO_no_solo_su_padre():
    import seal_mutacion_segura as m
    home = str(pathlib.Path.home())
    assert home in m._RAICES_PROHIBIDAS, (
        f"{home} no esta vigilado. Vigilar '/home' (el padre) no protege nada: "
        "ningun usuario puede escribir ahi, asi que el freno pasa siempre."
    )


def test_el_freno_SIGUE_al_entorno_real_sea_cual_sea_el_asiento():
    """Por efecto, contra el entorno real, y valido en CUALQUIER asiento.

    Antes esto afirmaba "en este asiento BLOQUEA", que es cierto en el host y
    falso dentro de la arena -donde no hay ninguna raiz protegida escribible y
    el freno debe dejar pasar-. Ese brazo ponia el control en rojo justo en el
    lugar donde el arnes tiene que correr (medido 14:04), o sea prohibia su
    unico uso legitimo.

    El contrato no es un veredicto fijo: es que el veredicto SIGA al entorno.
    Asi conserva los dientes en los dos lados, y ademas es mas fuerte -detecta
    tanto un freno que nunca bloquea como uno que bloquea siempre-.
    """
    import seal_mutacion_segura as m
    escribibles = [r for r in m._RAICES_PROHIBIDAS
                   if pathlib.Path(r).is_dir() and m._puede_escribir(r)]
    if escribibles:
        with pytest.raises(ArnesInseguro, match="ESCRIBIR"):
            m._verificar_entorno()
    else:
        m._verificar_entorno()  # no hay nada que proteger: debe dejar pasar


def test_CONTROL_una_raiz_de_solo_lectura_no_bloquea(tmp_path):
    """Sin esto, 'arreglarlo' bloqueando siempre pasaria en verde."""
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        _verificar_entorno(raices=(str(ro),))
    finally:
        ro.chmod(0o700)


# --- FRENO 3: el contenedor NO alcanza si le montaste el host con escritura ---
#
# Estos brazos existen porque el 7-sep 13:45 medi que `docker run --user 1000:1000`
# con el repo montado rw PISO un archivo del host mientras el arnes decia
# "habilitado". Lo unico que frenaba era el uid, no este codigo.

def _mountinfo(origen: str, montaje: str, opciones: str = "rw,relatime") -> str:
    return f"36 35 259:2 {origen} {montaje} {opciones} - ext4 /dev/x rw"


def test_niega_un_montaje_ESCRIBIBLE_cuyo_origen_esta_bajo_el_home(tmp_path, monkeypatch):
    contenido = _mountinfo("/home/dadito/IA/proyecto-seal", str(tmp_path))
    monkeypatch.setattr(sms.pathlib.Path, "read_text", lambda self: contenido)
    with pytest.raises(sms.ArnesInseguro) as e:
        sms.verificar_montajes("/tmp/seal-arena-x")
    assert "/home/dadito/IA/proyecto-seal" in str(e.value)


def test_CONTROL_un_montaje_escribible_desde_tmp_NO_bloquea(tmp_path, monkeypatch):
    """Sin este control, un freno que niega TODO se ve igual que uno que anda."""
    contenido = _mountinfo("/tmp/seal-arena-x/copia", str(tmp_path))
    monkeypatch.setattr(sms.pathlib.Path, "read_text", lambda self: contenido)
    # Con la lista blanca el area se declara donde esta MONTADA, no en el host.
    sms.verificar_montajes(str(tmp_path))


def test_CONTROL_el_mismo_origen_peligroso_montado_ro_NO_bloquea(tmp_path, monkeypatch):
    contenido = _mountinfo("/home/dadito/IA/proyecto-seal", str(tmp_path), "ro,relatime")
    monkeypatch.setattr(sms.pathlib.Path, "read_text", lambda self: contenido)
    sms.verificar_montajes("/tmp/seal-arena-x")


def test_un_home_degenerado_NO_convierte_todo_en_prohibido(monkeypatch):
    """Dentro de un contenedor, `Path.home()` de un uid sin passwd da "/".

    Como raiz, "/" es prefijo de todo y el freno niega hasta el arbol legitimo.
    """
    monkeypatch.setattr(sms.pathlib.Path, "home", classmethod(lambda cls: sms.pathlib.Path("/")))
    assert sms._home_real() == ()


def test_la_plomeria_de_docker_no_dispara_el_freno(tmp_path, monkeypatch):
    """resolv.conf/hosts/hostname vienen de /var/lib/docker y son ARCHIVOS."""
    archivo = tmp_path / "resolv.conf"
    archivo.write_text("nameserver 1.1.1.1")
    contenido = _mountinfo("/var/lib/docker/containers/abc/resolv.conf", str(archivo))
    monkeypatch.setattr(sms.pathlib.Path, "read_text", lambda self: contenido)
    sms.verificar_montajes("/tmp/seal-arena-x")


# --- LISTA BLANCA: las dos condiciones, cada una necesaria (medido 13:57/13:59) ---

def _parchear(monkeypatch, tmp_path, *lineas):
    contenido = "\n".join(lineas)
    monkeypatch.setattr(sms.pathlib.Path, "read_text", lambda self: contenido)


def test_niega_un_montaje_ESCRIBIBLE_fuera_del_area(tmp_path, monkeypatch):
    """El respaldo NFS: no esta bajo ninguna raiz que se me hubiera ocurrido."""
    _parchear(monkeypatch, tmp_path, _mountinfo("/", "/respaldo"),
              _mountinfo("/tmp/seal-arena-x", "/trabajo"))
    monkeypatch.setattr(sms, "_es_directorio", lambda m: True)
    with pytest.raises(sms.ArnesInseguro) as e:
        sms.verificar_montajes("/trabajo")
    assert "/respaldo" in str(e.value)


def test_niega_montar_el_ARBOL_VIVO_como_area_de_trabajo(tmp_path, monkeypatch):
    """Mirar solo el punto de montaje dejaba pasar esto: el area ERA el repo."""
    _parchear(monkeypatch, tmp_path, _mountinfo("/home/dadito/IA/proyecto-seal", "/trabajo"))
    monkeypatch.setattr(sms, "_es_directorio", lambda m: True)
    with pytest.raises(sms.ArnesInseguro) as e:
        sms.verificar_montajes("/trabajo", ("/home/dadito",))
    assert "arbol vivo" in str(e.value)


def test_CONTROL_area_desde_una_copia_en_tmp_HABILITA(tmp_path, monkeypatch):
    _parchear(monkeypatch, tmp_path, _mountinfo("/tmp/seal-arena-x", "/trabajo"),
              _mountinfo("/mnt/spark-2", "/respaldo", "ro,relatime"))
    monkeypatch.setattr(sms, "_es_directorio", lambda m: True)
    sms.verificar_montajes("/trabajo", ("/home/dadito",))


def test_un_fs_sin_estado_del_host_no_dispara_el_freno(tmp_path, monkeypatch):
    """La capa efimera y /proc aparecen rw con origen "/" y ahogaban la senal."""
    virtual = "36 35 0:1 / /proc rw,relatime - proc proc rw"
    _parchear(monkeypatch, tmp_path, virtual, _mountinfo("/tmp/seal-arena-x", "/trabajo"))
    monkeypatch.setattr(sms, "_es_directorio", lambda m: True)
    sms.verificar_montajes("/trabajo", ("/home/dadito",))


def test_un_fs_DESCONOCIDO_si_dispara_el_freno(tmp_path, monkeypatch):
    """Fail-closed: la lista es de excepciones, no de peligros."""
    raro = "36 35 0:1 /datos /afuera rw,relatime - unfsdesconocido x rw"
    _parchear(monkeypatch, tmp_path, raro, _mountinfo("/tmp/seal-arena-x", "/trabajo"))
    monkeypatch.setattr(sms, "_es_directorio", lambda m: True)
    with pytest.raises(sms.ArnesInseguro):
        sms.verificar_montajes("/trabajo", ("/home/dadito",))


def test_la_puerta_VIEJA_no_habilita_nunca_mas():
    """ALICE, 13:56: su banco llamo a `verificar_entorno` y obtuvo verde dentro
    de un contenedor con /home montado rw -- y escribio en el home de verdad.

    Un llamador viejo tiene que ROMPERSE, no pasar: devolver None ahi es dar
    verde sin mirar los montajes, que es justo el agujero.
    """
    with pytest.raises(sms.ArnesInseguro) as e:
        sms.verificar_entorno()
    assert "verificar(" in str(e.value)


def test_la_puerta_UNICA_corre_los_dos_frenos(monkeypatch):
    corridos = []
    monkeypatch.setattr(sms, "_verificar_entorno", lambda raices=(): corridos.append("entorno"))
    monkeypatch.setattr(sms, "verificar_montajes", lambda arena, raices=(): corridos.append("montajes"))
    sms.verificar("/trabajo")
    assert corridos == ["entorno", "montajes"]


# --- El mutante se registra de forma REPRODUCIBLE (medido 7-sep: 0 de 65) ---

def _sin_frenos(monkeypatch, tmp_path):
    """Aisla el registro de los frenos, que tienen sus propios brazos."""
    monkeypatch.setattr(sms, "_verificar_entorno", lambda raices=(): None)
    monkeypatch.setattr(sms, "verificar_montajes", lambda arena, raices=(): None)
    return str(tmp_path)


def test_el_registro_permite_reaplicar_el_MISMO_mutante(monkeypatch, tmp_path):
    arena = _sin_frenos(monkeypatch, tmp_path)
    fuente = "a = 1\nb = 2\n"
    mut, reg = sms.aplicar_y_registrar(fuente, "b = 2", "b = 99", arena=arena, sujeto="x.py")
    # el registro, y NADA mas, alcanza para reproducirlo
    rehecho = fuente.replace(reg["ancla"], reg["reemplazo"])
    assert rehecho == mut, "el registro no reproduce el mutante"


def test_niega_un_ancla_AMBIGUA(monkeypatch, tmp_path):
    """Con mas de una ocurrencia se muta donde no elegiste."""
    arena = _sin_frenos(monkeypatch, tmp_path)
    with pytest.raises(sms.ArnesInseguro, match="2 veces"):
        sms.aplicar_y_registrar("x = 1\nx = 1\n", "x = 1", "x = 2", arena=arena, sujeto="x.py")


def test_niega_un_ancla_AUSENTE(monkeypatch, tmp_path):
    """El caso peor: el mutante no se aplica y 'sobrevive' sin haber existido.

    Es el defecto que ya me costo una corrida el 4-sep: el mutante 'sobrevivio'
    con el numero IDENTICO al del control, que es la firma de que no se aplico.
    """
    arena = _sin_frenos(monkeypatch, tmp_path)
    with pytest.raises(sms.ArnesInseguro, match="0 veces"):
        sms.aplicar_y_registrar("x = 1\n", "no_existe", "y", arena=arena, sujeto="x.py")


def test_CONTROL_un_ancla_unica_no_levanta(monkeypatch, tmp_path):
    arena = _sin_frenos(monkeypatch, tmp_path)
    mut, reg = sms.aplicar_y_registrar("uno\ndos\n", "dos", "tres", arena=arena, sujeto="x.py")
    assert reg["ocurrencias_del_ancla"] == 1 and "tres" in mut


def test_el_registro_ata_el_ancla_y_el_sujeto_por_hash(monkeypatch, tmp_path):
    """Sin los hashes, 'aplique el mismo mutante' es una afirmacion sin prueba."""
    arena = _sin_frenos(monkeypatch, tmp_path)
    import hashlib
    fuente = "a = 1\n"
    _, reg = sms.aplicar_y_registrar(fuente, "a = 1", "a = 2", arena=arena, sujeto="x.py")
    assert reg["ancla_sha256"] == hashlib.sha256(b"a = 1").hexdigest()
    assert reg["sujeto_sha256_antes"] == hashlib.sha256(fuente.encode()).hexdigest()


def test_niega_un_mutante_que_NO_cambia_nada(monkeypatch, tmp_path):
    arena = _sin_frenos(monkeypatch, tmp_path)
    with pytest.raises(sms.ArnesInseguro, match="no cambio"):
        sms.aplicar_y_registrar("a = 1\n", "a = 1", "a = 1", arena=arena, sujeto="x.py")
