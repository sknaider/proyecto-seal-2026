"""Arnes de mutacion que NO puede repetir el 7-sep-2026.

POR QUE EXISTE. A las 01:42:53 un mutante mio (NEXUS) quito la unica guarda de
`seal_arena.sh drop`; el test negativo de ALICE le pasa `/home/dadito` para
comprobar que lo RECHACE, y sin guarda ese caso ejecuto
`find /home/dadito -mindepth 1 -delete`. Se perdio el home.

Nadie desobedecio una regla escrita. Por eso este arnes NO es un recordatorio:
son dos frenos que se ejercen solos, en el orden en que habrian salvado el home.

  1. NO ARRANCA si el proceso puede escribir en /home.
     Un arnes de mutacion no necesita ese permiso jamas. Correrlo como un
     usuario sin derechos convierte "borro el home" en un EACCES.

  2. SE NIEGA a mutar una linea marcada `# GUARDA-DESTRUCTIVA`.
     Las guardas que protegen un `rm`/`find -delete` se marcan en el fuente y
     este arnes las trata como intocables: mutar una guarda de seguridad no
     simula el peligro, lo EJECUTA.

Los dos frenos son independientes a proposito: el primero limita el DANO, el
segundo evita el ACTO. Si uno se cae, el otro sigue.
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import tempfile

MARCA_GUARDA = "# GUARDA-DESTRUCTIVA"
# El HOME DEL USUARIO va primero y por eso existe esta lista.
#
# DEFECTO QUE ESTO CORRIGE (medido 13:43, lo destapo una premisa falsa de JARVIS
# sobre `seal-arena`): la version anterior listaba "/home" -el directorio PADRE,
# que ningun usuario puede escribir- y NO "/home/dadito", que es EXACTAMENTE lo
# que se borro el 7-sep 01:42:53. El freno pasaba en verde como `dadito`:
#
#     _puede_escribir("/home")        -> False   (por eso "pasaba")
#     _puede_escribir("/home/dadito") -> True    (lo que habia que frenar)
#
# Vigilar el padre de lo que hay que proteger no protege nada. Se resuelve el
# home REAL en tiempo de ejecucion, sin cablear un nombre de usuario.
def _home_real() -> tuple[str, ...]:
    """El home del usuario, DESCARTANDO el valor degenerado.

    DEFECTO QUE ESTO CORRIGE (lo destapo mi propio CONTROL, 7-sep 13:47): dentro
    de un contenedor, un uid sin entrada en /etc/passwd hace que
    `pathlib.Path.home()` devuelva "/". Como raiz prohibida, "/" es prefijo de
    TODO: el freno 3 daba peligroso hasta el arbol legitimo en /tmp. Un freno
    que niega todo no protege: se lo apaga, y entonces no queda ninguno.
    """
    home = str(pathlib.Path.home())
    return (home,) if home not in ("/", "") else ()


_RAICES_PROHIBIDAS = _home_real() + ("/home", "/etc", "/var", "/usr", "/boot")


class ArnesInseguro(RuntimeError):
    """El arnes se niega a operar. NUNCA se captura para seguir igual."""


def _puede_escribir(directorio: str) -> bool:
    """Ejerce el permiso, no lo deduce de `stat`.

    Leer el modo del archivo no dice si PODES escribir: el camino entero manda
    (medido el 5-sep). Aca se intenta crear un temporal y se borra enseguida.
    """
    try:
        with tempfile.NamedTemporaryFile(dir=directorio):
            return True
    except OSError:
        return False


def _verificar_entorno(raices: tuple[str, ...] = _RAICES_PROHIBIDAS) -> None:
    """Freno 1: si este proceso puede escribir donde vive el trabajo, no corre."""
    escribibles = [r for r in raices if pathlib.Path(r).is_dir() and _puede_escribir(r)]
    if escribibles:
        raise ArnesInseguro(
            "el arnes de mutacion puede ESCRIBIR en "
            + ", ".join(escribibles)
            + f" (uid={os.geteuid()}). Un mutante sobre una rama destructiva podria "
            "borrarlas. Corrreme como un usuario sin esos derechos o en un contenedor."
        )


# FRENO 3 -- por que un contenedor NO alcanza, medido el 7-sep 13:45.
#
# ALICE propuso el contenedor como via para habilitar el arnes, y adentro el
# freno 1 PASA. Pero pasa por el motivo equivocado: `/home/dadito` no EXISTE
# dentro del contenedor, asi que la lista de raices no encuentra nada que
# vigilar. Enumerar rutas del host es ciego apenas cambia el namespace.
#
# Lo que protegia de verdad era el uid, no este codigo:
#
#     docker run --user 65534:65534  -> el montaje NO se pudo escribir  (EACCES)
#     docker run --user 1000:1000    -> el montaje del host SE PISO      <- real
#
# El segundo es el comando natural: se usa el uid del dueno justamente para que
# el mutante pueda escribir su copia. El contenedor no aisla lo que le montaste.
#
# El kernel si expone el origen: el campo 4 de /proc/self/mountinfo trae la ruta
# DEL HOST y las opciones dicen si es `rw`. Se vigila eso, que es el efecto.
_CAMPO_ORIGEN, _CAMPO_MONTAJE, _CAMPO_OPCIONES = 3, 4, 5

# Sistemas de archivos que NO exponen estado del host: son la capa efimera
# del contenedor o interfaces del kernel. Todo lo demas se trata como host.
# La lista es de EXCEPCIONES y se falla cerrado: un tipo desconocido cuenta
# como peligroso. Al reves -enumerar lo peligroso- es el error que ALICE
# senalo a las 13:51 y que ya me costo dos versiones de esta guarda.
_FS_SIN_ESTADO_DEL_HOST = frozenset({
    "overlay", "tmpfs", "proc", "sysfs", "devpts", "mqueue", "devtmpfs",
    "cgroup", "cgroup2", "securityfs", "pstore", "bpf", "tracefs",
    "debugfs", "hugetlbfs", "configfs", "fusectl", "ramfs", "autofs",
})


def _montajes_rw_del_host() -> list[tuple[str, str]]:
    """Montajes con escritura que exponen estado persistente DEL HOST.

    Devuelve (origen en el host, punto de montaje). Se excluyen los sistemas de
    archivos sin estado del host: dentro de un contenedor la capa efimera y
    /proc, /dev, /sys aparecen todos como `rw` con origen "/" y ahogarian la
    senal.
    """
    try:
        crudo = pathlib.Path("/proc/self/mountinfo").read_text()
    except OSError:
        return []
    origenes = []
    for linea in crudo.splitlines():
        campos = linea.split()
        if len(campos) <= _CAMPO_OPCIONES:
            continue
        opciones = campos[_CAMPO_OPCIONES].split(",")
        if "rw" not in opciones:
            continue
        try:
            tipo = campos[campos.index("-", _CAMPO_OPCIONES) + 1]
        except (ValueError, IndexError):
            tipo = "desconocido"  # fail-closed: si no se pudo leer, cuenta
        if tipo in _FS_SIN_ESTADO_DEL_HOST:
            continue
        origenes.append((campos[_CAMPO_ORIGEN], campos[_CAMPO_MONTAJE]))
    return origenes


def _es_directorio(montaje: str) -> bool:
    """Fail-closed: si no se puede mirar el montaje, se lo trata como peligroso.

    Un montaje puede estar ROTO y `is_dir()` levanta OSError en vez de devolver
    False (medido: /mnt/spark-3, "Error de entrada/salida"). No se puede
    descartar lo que no se pudo mirar.
    """
    try:
        return pathlib.Path(montaje).is_dir()
    except OSError:
        return True


def verificar_montajes(arena: str, raices: tuple[str, ...] = _RAICES_PROHIBIDAS) -> None:
    """Freno 3: nada escribible del host fuera del AREA DE TRABAJO.

    POR QUE ES UNA LISTA BLANCA (ALICE, 13:51). La primera version enumeraba
    raices prohibidas y por eso fallaba dos veces:

      - dentro de un contenedor `/home/dadito` no existe, asi que la lista no
        vigilaba nada mientras el repo real, montado con otro nombre, se pisaba;
      - y solo cubria lo que se me ocurrio escribir: un montaje de /mnt/spark-2
        -la copia de respaldo en NFS- pasaba en verde.

    La pregunta correcta no es "esta ruta esta prohibida" sino "puedo escribir
    FUERA de mi area de trabajo", que no depende de como se llame la ruta.
    `raices` queda solo para nombrar la raiz protegida en el mensaje de error.
    """
    area = pathlib.Path(arena).resolve()

    def bajo(ruta: str, raiz: str) -> bool:
        raiz = raiz.rstrip("/")
        return ruta == raiz or ruta.startswith(raiz + "/")

    # Hacen falta LAS DOS condiciones, y cada una sola deja pasar un caso real
    # (medido 13:57 y 13:59, los dos con el ensayo vivo):
    #
    #   solo el punto de montaje  -> montar el REPO VIVO como area pasaba
    #   solo el origen en el host -> montar /mnt/spark-2 (respaldo NFS) pasaba
    #
    # El area tiene que ser una COPIA: un montaje dentro del area cuyo origen
    # esta bajo una raiz protegida es el arbol vivo disfrazado de arena.
    afuera, arbol_vivo = [], []
    for origen, montaje in _montajes_rw_del_host():
        if not _es_directorio(montaje):
            continue  # Docker monta resolv.conf/hosts/hostname como archivos
        etiqueta = f"{origen} (en {montaje})"
        if not bajo(montaje, str(area)):
            afuera.append(etiqueta)
        elif any(bajo(origen, r) for r in raices):
            arbol_vivo.append(etiqueta)
    if arbol_vivo:
        raise ArnesInseguro(
            "el area de trabajo ES el arbol vivo, montado con escritura: "
            + ", ".join(sorted(set(arbol_vivo))[:4])
            + ". Un mutante escribiria sobre el original. Copia el arbol a "
            "/tmp/seal-arena-* desde el INDICE y monta esa copia."
        )
    if afuera:
        raise ArnesInseguro(
            "hay montajes ESCRIBIBLES del host FUERA del area de trabajo "
            f"({area}): " + ", ".join(sorted(set(afuera))[:4])
            + ". Un contenedor no aisla lo que le montaste con escritura: "
            "montalos :ro o no los montes."
        )


def verificar(arena: str, raices: tuple[str, ...] = _RAICES_PROHIBIDAS) -> None:
    """UNICA puerta para habilitar el arnes. Corre los frenos 1 y 3 juntos.

    POR QUE ES UNA SOLA (ALICE, 7-sep 13:56, medido por ella y reproducido por
    mi). Yo habia dejado DOS funciones publicas, y su banco llamo a la vieja:

        verificar_entorno()  dentro de un contenedor  -> HABILITA
        y el proceso escribia de verdad en /home/dadito montado rw

    El freno 1 pregunta "puedo escribir en una raiz protegida" y adentro del
    contenedor la respuesta es NO aunque el home este montado con otro nombre.
    Solo el freno 3 ve eso. Con dos puertas, la que da verde de mas es la que
    alguien va a usar: no por descuido, sino porque era la documentada antes.
    """
    _verificar_entorno(raices)
    verificar_montajes(arena, raices)


def verificar_entorno(*_a, **_k):  # pragma: no cover - shim que falla ruidoso
    """La puerta vieja. NO habilita: exige la nueva.

    Devolver None aca seria dar verde sin mirar los montajes, que es
    exactamente el agujero que este shim cierra. Un llamador viejo tiene que
    ROMPERSE, no pasar.
    """
    raise ArnesInseguro(
        "verificar_entorno() ya no habilita el arnes: no ve los montajes y daba "
        "verde con /home montado rw dentro de un contenedor. Usa "
        "verificar(arena='<ruta del area de trabajo>')."
    )


def verificar_mutacion(fuente: str, ancla: str) -> None:
    """Freno 2: no se muta una guarda que protege una operacion destructiva.

    `ancla` es el texto que el mutante va a reemplazar. Si esa linea -o la de
    arriba, donde suele ir la marca- esta marcada, se rechaza.
    """
    if ancla not in fuente:
        raise ArnesInseguro(f"el ancla no aparece en el fuente: {ancla!r}")
    if fuente.count(ancla) != 1:
        raise ArnesInseguro(
            f"el ancla aparece {fuente.count(ancla)} veces: no identifica una linea. "
            "Un mutante que no sabe que muta no prueba nada."
        )
    lineas = fuente.splitlines()
    for i, linea in enumerate(lineas):
        if ancla not in linea:
            continue
        contexto = lineas[max(0, i - 2): i + 1]
        if any(MARCA_GUARDA in c for c in contexto):
            raise ArnesInseguro(
                f"linea {i + 1} protegida por {MARCA_GUARDA}: es una guarda de una "
                "operacion destructiva. Mutarla no simula el peligro, lo EJECUTA. "
                "Para probar que la guarda sirve, neutraliza el EFECTO (un DRYRUN)."
            )


def aplicar_y_registrar(fuente: str, ancla: str, reemplazo: str, *, arena: str,
                        sujeto: str) -> tuple[str, dict]:
    """Aplica un mutante y devuelve TAMBIEN como reproducirlo exactamente.

    POR QUE (medido el 7-sep 15:20): de 65 evidencias de mutacion con sus
    mutantes descritos, CERO guardaban el mutante en forma ejecutable. Guardaban
    `id`, prosa y la salida observada. Con eso, re-correr tras un cambio de bytes
    no es re-correr: es volver a INVENTAR el mutante leyendo una descripcion.

    Y el peligro tiene nombre en nuestras propias notas del 4-sep: *un mutante
    mal especificado se ve IGUAL que un test flojo*. Si el que re-corre deriva
    un mutante DISTINTO y muere, el manifiesto cierra en verde habiendo medido
    otra cosa, y nadie lo nota.

    El registro incluye el sha256 del ancla y del sujeto: quien re-corra puede
    probar que aplico EL MISMO mutante, no uno parecido.
    """
    ocurrencias = fuente.count(ancla)
    if ocurrencias != 1:
        raise ArnesInseguro(
            f"el ancla aparece {ocurrencias} veces en {sujeto}; debe aparecer EXACTAMENTE 1. "
            "Con 0 el mutante no se aplica y 'sobrevive' sin haber existido; con mas de 1 "
            "muta lugares que no elegiste."
        )
    mutante = mutar(fuente, ancla, reemplazo, arena)
    if mutante == fuente:
        raise ArnesInseguro("el mutante no cambio el archivo: no hay nada que medir")
    registro = {
        "subject": sujeto,
        "ancla": ancla,
        "reemplazo": reemplazo,
        "ancla_sha256": hashlib.sha256(ancla.encode()).hexdigest(),
        "sujeto_sha256_antes": hashlib.sha256(fuente.encode()).hexdigest(),
        "ocurrencias_del_ancla": ocurrencias,
        "reproducible": True,
    }
    return mutante, registro


def mutar(fuente: str, ancla: str, reemplazo: str, arena: str) -> str:
    """Unica via autorizada: aplica los TRES frenos antes de devolver el mutante.

    `arena` es obligatorio a proposito: sin declarar el area de trabajo no se
    puede responder "puedo escribir afuera", y un freno que no se puede evaluar
    no se salta en silencio.
    """
    verificar(arena)
    verificar_mutacion(fuente, ancla)
    return fuente.replace(ancla, reemplazo)
