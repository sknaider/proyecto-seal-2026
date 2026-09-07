#!/usr/bin/env python3
"""Rutas criticas que TIENEN que existir. Grita cuando FALTAN.

POR QUE EXISTE (7-sep-2026, ALICE; condicion del veredicto de FABLE):
mis tres detectores anteriores son DIFERENCIALES —preguntan "de lo que se
importa, falta algo en git?"— y FABLE midio la consecuencia con un senuelo:

    senuelo con un archivo no versionado   -> BOMBAS: 1   exit 1   (avisa)
    el MISMO senuelo VACIADO por completo  -> sin bombas  exit 0   (NO avisa)

**Un arbol borrado les parece sano**, porque si no queda nada que importe,
no falta nada. Es exactamente el caso del 7-sep 01:42:53, cuando se borro
/home/dadito entero y ningun chequeo del equipo dijo una palabra.

Este mide lo contrario y por eso es el complemento, no el reemplazo:
una lista FIJA de rutas que deben estar, con su hash. No deduce nada del
arbol: si el arbol desaparece, la lista sigue diciendo que faltan.

Uso:
    seal_detector_existencia.py                 verifica
    seal_detector_existencia.py --actualizar    regraba los hashes (explicito)
"""
from __future__ import annotations
import hashlib, json, pathlib, sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
LISTA = RAIZ / "quality/rutas-criticas.json"


def sha(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for bloque in iter(lambda: fh.read(65536), b""):
            h.update(bloque)
    return h.hexdigest()


def verificar(datos: dict) -> int:
    faltan, cambiaron, ok = [], [], 0
    for ruta, esperado in sorted(datos["rutas"].items()):
        p = RAIZ / ruta
        if not p.exists():
            faltan.append(ruta); continue
        if esperado and p.is_file():
            actual = sha(p)
            if actual != esperado:
                cambiaron.append(ruta); continue
        ok += 1
    print(f"rutas criticas declaradas: {len(datos['rutas'])}   presentes y con su hash: {ok}")
    if faltan:
        print(f"\nFALTAN {len(faltan)} RUTAS CRITICAS:")
        for r in faltan:
            print(f"  {r}")
    if cambiaron:
        # Cambiar NO es faltar: se informa aparte y no es una alarma roja.
        print(f"\ncambiaron de contenido ({len(cambiaron)}) — normal si se editaron a proposito:")
        for r in cambiaron:
            print(f"  {r}")
    if faltan:
        return 1
    if not datos["rutas"]:
        # Una lista vacia haria que este detector "pase" siempre: es el modo
        # de fallo que lo volveria inutil, asi que se trata como error.
        print("\nERROR: la lista de rutas criticas esta VACIA: este detector no mide nada.")
        return 2
    print("\nsin faltantes: todas las rutas criticas declaradas existen")
    return 0


def main() -> int:
    if not LISTA.exists():
        print(f"ERROR: no existe {LISTA.relative_to(RAIZ)} — sin lista no hay medicion")
        return 2
    datos = json.loads(LISTA.read_text())
    if "--actualizar" in sys.argv:
        cambios = 0
        for ruta in list(datos["rutas"]):
            p = RAIZ / ruta
            if p.is_file():
                nuevo = sha(p)
                if datos["rutas"][ruta] != nuevo:
                    datos["rutas"][ruta] = nuevo; cambios += 1
        LISTA.write_text(json.dumps(datos, ensure_ascii=False, indent=2) + "\n")
        print(f"hashes actualizados: {cambios}")
        return 0
    return verificar(datos)


if __name__ == "__main__":
    raise SystemExit(main())
