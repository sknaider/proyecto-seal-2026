#!/usr/bin/env python3
"""Detecta credenciales que NADIE esta respaldando.

POR QUE EXISTE. El 7-sep repare `seal-infra-watchdog` creando su
`EnvironmentFile` y NO lo agregue a `quality/estado_esencial_rutas.txt`: quedo
fuera del respaldo cifrado. Si se pierde, la unidad vuelve a morir con el mismo
error que arregle ese dia.

La lista del respaldo es FIJA y toda reparacion que cree una credencial la deja
desactualizada EN SILENCIO. Recordarlo ya fallo una vez el mismo dia en que
aprendimos la leccion, asi que esto lo mide por DIFERENCIA:

    lo que existe en disco   -   lo que la lista declara   =   sin respaldo

Salida: JSON de una linea y codigo 1 si hay huerfanos, para que una unidad o el
gate lo puedan usar como freno.

Uso:  seal_credenciales_sin_respaldo.py [--raiz DIR] [--lista ARCHIVO]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

RAIZ = pathlib.Path.home() / ".config" / "seal"
LISTA = pathlib.Path(__file__).resolve().parents[1] / "quality" / "estado_esencial_rutas.txt"
SUFIJOS = (".env", ".dsn", ".key", ".cred")
# No son credenciales de servicio: son respaldos o marcas del propio flujo.
IGNORAR = (".INCORRECTO", ".bak", ".old", ".example", ".sample", ".partial")


def declaradas(lista: pathlib.Path) -> set[str]:
    if not lista.exists():
        return set()
    return {
        l.strip() for l in lista.read_text().splitlines()
        if l.strip() and not l.lstrip().startswith("#")
    }


def en_disco(raiz: pathlib.Path) -> set[str]:
    if not raiz.is_dir():
        return set()
    hallados = set()
    for p in raiz.rglob("*"):
        if not p.is_file() or p.is_symlink():
            continue
        if not p.name.endswith(SUFIJOS):
            continue
        if any(m in p.name for m in IGNORAR):
            continue
        hallados.add(str(p))
    return hallados


def huerfanas(raiz: pathlib.Path = RAIZ, lista: pathlib.Path = LISTA) -> list[str]:
    """Credenciales en disco que la lista del respaldo NO declara."""
    return sorted(en_disco(raiz) - declaradas(lista))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raiz", type=pathlib.Path, default=RAIZ)
    ap.add_argument("--lista", type=pathlib.Path, default=LISTA)
    args = ap.parse_args()

    # Fail-closed: sin lista no se puede decir "todo respaldado". Sin este
    # brazo, borrar la lista pondria el chequeo en VERDE -- el mismo modo de
    # falla del detector diferencial que vio el arbol vacio y dijo SANO.
    if not args.lista.exists():
        print(json.dumps({"error": "lista_ausente", "lista": str(args.lista)}))
        return 2

    faltan = huerfanas(args.raiz, args.lista)
    print(json.dumps({
        "sin_respaldo": len(faltan),
        "declaradas": len(declaradas(args.lista)),
        "detalle": faltan,
    }))
    return 1 if faltan else 0


if __name__ == "__main__":
    sys.exit(main())
