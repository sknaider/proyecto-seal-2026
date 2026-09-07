"""Detecta procesos que corren desde codigo que YA NO EXISTE en disco.

POR QUE EXISTE. El 7-sep-2026 se borro el home. Durante 9 horas la maquina
"funciono": 126 servicios activos, 0 en failed. Pero 17 procesos corrian desde
ejecutables borrados —incluidos `claude` y `codex`, nuestros propios cuerpos— y
el primer reinicio los habria matado sin vuelta. **Ningun vigilante lo veia:**
DUM mira GPU y latidos, el Stability Guard mira unidades, systemd informa
`active` porque el proceso VIVE. Nadie preguntaba de donde salio ese proceso.

QUE MIDE, y es una pregunta que no se hacia:

    un proceso puede estar SANO y ser IRRECUPERABLE al mismo tiempo

    /proc/<pid>/exe        -> el ejecutable, borrado o no
    /proc/<pid>/maps       -> sus bibliotecas .so
    /proc/<pid>/cwd        -> su directorio de trabajo

METRICA (la que hay que vigilar): `fantasmas`, procesos cuyo ejecutable o
biblioteca fue borrado y NO repuesto. En un sistema sano vale CERO. Cualquier
valor mayor significa que ese proceso no sobrevive a su proximo reinicio.

Salida JSON para que la consuma un timer y avise. Solo LEE: no rescata, no borra.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys


def _borrado(destino: str | None) -> str | None:
    """Devuelve la ruta real si el destino esta marcado como borrado."""
    if not destino or not destino.endswith(" (deleted)"):
        return None
    return destino[: -len(" (deleted)")]


def revisar(pid: str) -> dict | None:
    """Un proceso es FANTASMA si su codigo ya no esta en disco.

    Ojo: `(deleted)` NO alcanza como criterio —un proceso puede conservar el
    inodo viejo aunque la ruta ya este restaurada (lo corrigio ADA el 7-sep)—.
    Por eso se comprueba ADEMAS que la ruta no exista HOY.
    """
    base = pathlib.Path("/proc") / pid
    try:
        exe = _borrado(os.readlink(base / "exe"))
    except OSError:
        return None

    faltan: list[str] = []
    if exe and not pathlib.Path(exe).exists():
        faltan.append(exe)

    try:
        for linea in (base / "maps").read_text().splitlines():
            if "(deleted)" not in linea:
                continue
            ruta = linea.split(maxsplit=5)[-1].removesuffix(" (deleted)")
            if ".so" in ruta and not pathlib.Path(ruta).exists() and ruta not in faltan:
                faltan.append(ruta)
    except (OSError, IndexError, ValueError):
        pass

    if not faltan:
        return None
    try:
        cmd = (base / "cmdline").read_bytes().replace(b"\0", b" ").decode().strip()
    except OSError:
        cmd = ""
    return {"pid": pid, "cmdline": cmd[:120], "faltan": faltan}


def barrer() -> dict:
    fantasmas = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        r = revisar(pid)
        if r:
            fantasmas.append(r)
    return {"fantasmas": len(fantasmas), "detalle": fantasmas}


if __name__ == "__main__":
    informe = barrer()
    print(json.dumps(informe, indent=2, ensure_ascii=False))
    # Codigo de salida = la metrica. 0 fantasmas = sano.
    sys.exit(1 if informe["fantasmas"] else 0)
