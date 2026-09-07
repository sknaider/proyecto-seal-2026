#!/usr/bin/env python3
"""Paquetes que Python importa con exito y estan VACIOS.

POR QUE EXISTE (7-sep-2026, ALICE): el rescate de .so desde /proc creo
directorios como site-packages/pandas/ con 42 binarios y CERO fuentes .py.
Sin __init__.py, Python los trata como NAMESPACE PACKAGES:

    import pandas      ->  NO falla
    pandas.__file__    ->  None
    pandas.NA          ->  AttributeError   <- tumbo el MCP

Ese dia el equipo tenia DOS chequeos y ninguno lo veia:
    metrica de fantasmas   contaba .so AUSENTES en disco
    chequeo de imports     preguntaba si el import falla, y no falla
Este mide lo INVERSO: .so presentes SIN su paquete.

NO importa nada (un import de diagnostico ejecuta codigo del paquete roto);
decide por lo que hay en el disco.
"""
from __future__ import annotations
import pathlib, sys

# Un directorio sin __init__.py es legitimo si es un namespace de verdad
# (p.ej. plugins) o si no pretende ser un paquete: datos, cache, metadatos.
_NO_SON_PAQUETES = {
    "__pycache__", "bin", "include", "lib", "share", "etc", "tests",
    "licenses", "site-packages", "dist-packages",
}


def duenos(sp: pathlib.Path) -> set[str]:
    """Directorios de primer nivel que alguna distribucion instalada RECLAMA.

    Esta es la prueba correcta, y no la que use primero. Mi version 1 marcaba
    "sin __init__.py" y dio 3 falsos positivos: numpy.libs y scipy.libs (los
    binarios que auditwheel empaqueta al lado) y nvidia/ (namespace real de
    los wheels nvidia-*). Los tres son legitimos y los tres carecen de
    __init__.py. Lo que separa un paquete MUTILADO de un directorio legitimo
    no es su forma: es si algun RECORD lo declara suyo.
    """
    reclamados: set[str] = set()
    for info in sp.glob("*.dist-info"):
        rec = info / "RECORD"
        if not rec.exists():
            continue
        for linea in rec.read_text(errors="replace").splitlines():
            ruta = linea.split(",", 1)[0]
            if "/" in ruta:
                reclamados.add(ruta.split("/", 1)[0])
    return reclamados


def sospechosos(sp: pathlib.Path) -> list[tuple[str, int, int, int]]:
    reclamados = duenos(sp)
    out = []
    for d in sorted(p for p in sp.iterdir() if p.is_dir()):
        n = d.name
        if n in _NO_SON_PAQUETES or n.startswith(".") or n.endswith((".dist-info", ".egg-info", ".data")):
            continue
        if (d / "__init__.py").exists():
            continue
        if n in reclamados:
            continue          # alguna distribucion instalada lo declara suyo
        pys = len(list(d.rglob("*.py")))
        sos = len(list(d.rglob("*.so")))
        if sos > 0 and pys == 0:
            out.append((n, pys, sos, 0))
    return out


def main() -> int:
    rutas = [pathlib.Path(a) for a in sys.argv[1:]]
    if not rutas:
        print("uso: seal_detector_paquetes_vacios.py <site-packages> [...]")
        return 2
    total = 0
    for sp in rutas:
        if not sp.is_dir():
            print(f"  {sp}: no existe"); continue
        hall = sospechosos(sp)
        print(f"{sp}: {len(list(sp.iterdir()))} entradas revisadas")
        for n, pys, sos, meta in hall:
            total += 1
            print(f"  {n}\n      .py={pys}  .so={sos}  dist-info={meta}")
            print("      import tendria EXITO y el modulo estaria VACIO")
    if not total:
        print("sin paquetes mutilados: ningun directorio con binarios y sin fuentes")
        return 0
    print(f"\nPAQUETES MUTILADOS: {total}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
