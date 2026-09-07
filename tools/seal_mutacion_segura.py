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

import os
import pathlib
import tempfile

MARCA_GUARDA = "# GUARDA-DESTRUCTIVA"
_RAICES_PROHIBIDAS = ("/home", "/etc", "/var", "/usr", "/boot")


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


def verificar_entorno(raices: tuple[str, ...] = _RAICES_PROHIBIDAS) -> None:
    """Freno 1: si este proceso puede escribir donde vive el trabajo, no corre."""
    escribibles = [r for r in raices if pathlib.Path(r).is_dir() and _puede_escribir(r)]
    if escribibles:
        raise ArnesInseguro(
            "el arnes de mutacion puede ESCRIBIR en "
            + ", ".join(escribibles)
            + f" (uid={os.geteuid()}). Un mutante sobre una rama destructiva podria "
            "borrarlas. Corrreme como un usuario sin esos derechos o en un contenedor."
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


def mutar(fuente: str, ancla: str, reemplazo: str) -> str:
    """Unica via autorizada: aplica los dos frenos antes de devolver el mutante."""
    verificar_entorno()
    verificar_mutacion(fuente, ancla)
    return fuente.replace(ancla, reemplazo)
