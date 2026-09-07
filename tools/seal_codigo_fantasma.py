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



def _paquete_vivo(ruta_so: str) -> bool:
    """Una `.so` borrada NO es una perdida si su PAQUETE sigue instalado.

    MEDIDO el 7-sep: `pip install` reemplaza los binarios de un paquete, asi que
    un proceso vivo conserva mapeada la `.so` de la version ANTERIOR y su ruta
    exacta ya no existe. Eso se ve identico a una perdida y NO lo es.

        av, pandas, pyarrow    el paquete NO importa   -> perdida REAL
        watchfiles, rpds       el paquete SI importa   -> version reemplazada

    Sin esta distincion la metrica mezcla dos cosas y se vuelve ruido, que es
    como muere un vigilante.
    """
    if "site-packages/" not in ruta_so:
        return False
    resto = ruta_so.split("site-packages/", 1)[1]
    raiz = pathlib.Path(ruta_so.split("site-packages/", 1)[0]) / "site-packages"
    paquete = resto.split("/", 1)[0].split(".")[0]
    # El paquete esta vivo si su directorio (o su modulo suelto) sigue en el venv.
    return (raiz / paquete).exists() or bool(list(raiz.glob(f"{paquete}.*")))


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
            if ".so" not in ruta or pathlib.Path(ruta).exists() or ruta in faltan:
                continue
            if _paquete_vivo(ruta):
                continue
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
    """Barre /proc y ademas comprueba que HUBO ALGO QUE BARRER.

    HALLAZGO DE FABLE (7-sep 12:22, juzgando el detector de ALICE): un arbol
    VACIADO le parecia sano a su chequeo -sin hallazgos, exit 0-. Aplique su
    caso a esta metrica y tenia el MISMO defecto: con cero procesos devolvia
    "fantasmas: 0" y salida 0, o sea "sistema sano".

        cero fantasmas porque todo esta bien       != cero fantasmas porque
        cero fantasmas porque no mire nada            no habia nada que mirar

    Un vigilante que no distingue "no encontre nada" de "no busque" reporta
    salud justo cuando el sistema desaparecio. Por eso `revisados` viaja en el
    informe y `barrido_vacio` lo marca explicito.
    """
    fantasmas = []
    revisados = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        revisados += 1
        r = revisar(pid)
        if r:
            fantasmas.append(r)
    return {
        "fantasmas": len(fantasmas),
        "revisados": revisados,
        "barrido_vacio": revisados == 0,
        "detalle": fantasmas,
    }


if __name__ == "__main__":
    informe = barrer()
    print(json.dumps(informe, indent=2, ensure_ascii=False))
    # Codigo de salida = la metrica. 0 fantasmas = sano.
    # Un barrido vacio NO es salud: es un oraculo ciego. Sale != 0 igual.
    sys.exit(2 if informe["barrido_vacio"] else (1 if informe["fantasmas"] else 0))
