#!/usr/bin/env python3
"""Unidades cuyo codigo exige una credencial del entorno y no tienen de donde sacarla.

POR QUE EXISTE (7-sep-2026, ALICE): al pasar cuatro servicios de credencial
cableada a credencial por entorno, tres quedaron sin arrancar. Mi primer
barrido encontro UNO porque exigia que la unidad TUVIERA EnvironmentFile:
las dos peores —las que no tienen NINGUNO— eran invisibles para el.

Es el mismo defecto que FABLE le encontro a mis detectores diferenciales:

    detector diferencial   disco vs git     -> el arbol VACIO le parece sano
    mi barrido de entorno  .env vs codigo   -> la unidad SIN .env le parece sana

**La ausencia total del objeto que comparas se escapa siempre.** Por eso este
pregunta primero SI HAY FUENTE, y recien despues compara nombres.

Tres estados, porque dos no alcanzan:
    SIN FUENTE   el codigo pide una credencial y la unidad no aporta ninguna
    DESAJUSTE    hay fuente, pero define otro nombre que el que el codigo pide
    ILEGIBLE     no pude leer el entorno -> NO es "sano", es desconocido
"""
from __future__ import annotations
import pathlib, re, sys

UNIDADES = pathlib.Path.home() / ".config/systemd/user"   # se puede pasar otro por argv
# SOLO variables OBLIGATORIAS. Una leida con valor por defecto
#   environ.get("X", "algo")   o   ${X:-algo}
# es OPCIONAL: su ausencia no rompe nada, y contarla produce ruido. Mi primera
# version las contaba y dio 19 "sin fuente" donde la mayoria eran opcionales:
# el mismo sobre-reporte que ya me costo credibilidad dos veces hoy.
PIDE = re.compile(
    r"""(?:os\.)?environ\[["']([A-Z][A-Z0-9_]{3,})["']\]"""          # environ["X"]
    r"""|(?:os\.)?environ\.get\(\s*["']([A-Z][A-Z0-9_]{3,})["']\s*\)"""  # .get("X") sin default
    # ADA, 7-sep 13:01: "tener valor por defecto no demuestra que sea opcional:
    # puede leerse con "" o None y despues provocar un fallo obligatorio".
    # Un default VACIO no es un default: es el modismo de "esto lo tenes que
    # proveer" seguido de un chequeo que aborta. Mi filtro anterior los
    # descartaba a todos y perdia justo los obligatorios mejor escritos.
    r"""|(?:os\.)?environ\.get\(\s*["']([A-Z][A-Z0-9_]{3,})["']\s*,\s*(?:""|''|None)\s*\)"""
    r"""|getenv\(\s*["']([A-Z][A-Z0-9_]{3,})["']\s*\)"""            # getenv("X") sin default
    r"""|\$\{([A-Z][A-Z0-9_]{3,})\}(?!:-)"""                        # ${X} sin :-
    r"""|\$\{([A-Z][A-Z0-9_]{3,}):\?"""                             # ${X:?...}
    )
CLAVES = ("DSN", "DB_URL", "DATABASE", "TOKEN", "SECRET", "KEY", "PASS", "CRED")


def variables_que_pide(script: pathlib.Path) -> set[str]:
    try:
        cuerpo = script.read_text(errors="replace")
    except OSError:
        return set()
    pedidas = {next((g for g in fila if g), '') for fila in PIDE.findall(cuerpo)}
    pedidas.discard('')
    return {v for v in pedidas if any(k in v for k in CLAVES)}


def main() -> int:
    global UNIDADES
    if len(sys.argv) > 1:
        UNIDADES = pathlib.Path(sys.argv[1])
    sin_fuente, desajuste, ilegible = [], [], []
    vistas: set[pathlib.Path] = set()
    for unidad in sorted(UNIDADES.glob("*.service")):
        real = unidad.resolve()
        if real in vistas:
            continue
        vistas.add(real)
        txt = unidad.read_text(errors="replace")
        ex = re.search(r"^ExecStart=(.+)$", txt, re.M)
        if not ex:
            continue
        scripts = [pathlib.Path(p) for p in ex.group(1).split()
                   if p.endswith((".py", ".sh")) and pathlib.Path(p).exists()]
        pedidas: set[str] = set()
        for s in scripts:
            pedidas |= variables_que_pide(s)
        if not pedidas:
            continue
        definidas = set(re.findall(r"^Environment=([A-Z][A-Z0-9_]*)=", txt, re.M))
        hubo_ilegible = False
        for ef in re.findall(r"^EnvironmentFile=-?(\S+)", txt, re.M):
            p = pathlib.Path(ef.replace("%h", str(pathlib.Path.home())))
            try:
                definidas |= set(re.findall(r"^([A-Z][A-Z0-9_]*)=",
                                            p.read_text(errors="replace"), re.M))
            except OSError:
                hubo_ilegible = True
        if hubo_ilegible:
            ilegible.append((unidad.name, sorted(pedidas)))
        elif not definidas:
            # LA CATEGORIA QUE ME FALTABA: ninguna fuente, no un nombre distinto.
            sin_fuente.append((unidad.name, sorted(pedidas)))
        elif not (pedidas & definidas):
            desajuste.append((unidad.name, sorted(pedidas), sorted(definidas)[:4]))
    print(f"unidades revisadas: {len(vistas)}")
    for titulo, filas in (("SIN FUENTE DE CREDENCIAL", sin_fuente),
                          ("DESAJUSTE DE NOMBRE", desajuste)):
        if filas:
            print(f"\n{titulo}: {len(filas)}")
            for fila in filas:
                print(f"  {fila[0]}\n      pide: {fila[1]}"
                      + (f"\n      el entorno define: {fila[2]}" if len(fila) > 2 else ""))
    if ilegible:
        print(f"\nNO PUDE LEER SU ENTORNO ({len(ilegible)}) — desconocido, NO sano:")
        for n, p in ilegible:
            print(f"  {n}  pide: {p}")
    if sin_fuente or desajuste:
        return 1
    print("\nsin hallazgos: toda unidad que pide una credencial tiene de donde sacarla")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
