#!/usr/bin/env python3
"""¿Este archivo está bajo una firma aprobada? Preguntalo ANTES de editarlo.

POR QUE EXISTE (JARVIS, 29-ago-2026): edite `validar_suite_jarvis.py` sin
preguntarme si estaba bajo alguna firma. Estaba: era `test` de una unidad que
FABLE habia firmado y cerrado. Le rompi el recibo -`independent_review_bytes_stale`-
y ademas segui QUINCE MINUTOS citando el `STATIC_OK` que habia medido ANTES de mi
propia edicion, incluido un reporte a William.

Nadie mintio: cite un numero medido con mis propias manos. **Un estado medido una
vez y citado despues es un estado SIN medir**, y de la evidencia propia uno no
desconfia porque recuerda haberla medido.

La regla en prosa -"no muevas el sujeto bajo el revisor"- la teniamos escrita y la
rompimos los cuatro en un dia. Esto la vuelve una pregunta de un comando:

    python3 tools/seal_bajo_firma.py <archivo> [<archivo>...]
    python3 tools/seal_bajo_firma.py --staged      # todo lo que tenes en el indice

Salidas:
    0  ninguno esta bajo una firma aprobada
    1  al menos uno lo esta  -> avisale al revisor ANTES de editar
    2  error de uso
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
MANIFESTS = RAIZ / "quality" / "manifests"


def indice_de_firmas() -> dict[str, list[tuple[str, str]]]:
    """{ruta: [(manifest, revisor)]} para manifiestos con review APROBADO."""
    idx: dict[str, list[tuple[str, str]]] = {}
    for m in sorted(MANIFESTS.glob("*.json")):
        try:
            d = json.loads(m.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[bajo-firma] AVISO: no pude leer {m.name}: {e}", file=sys.stderr)
            continue
        rv = d.get("review") or {}
        if rv.get("status") != "approved":
            continue
        rev = str(d.get("independent_reviewer") or "?")
        for p in [*(d.get("subjects") or []), *(d.get("tests") or [])]:
            if isinstance(p, str):
                idx.setdefault(p, []).append((m.name, rev))
    return idx


def staged() -> list[str]:
    """Rutas del indice. DOS defectos que fallaban ABIERTO, hallados por FABLE:

    1. RUTAS NO-ASCII. `--name-only` sin `-z` las QUOTEA y escapa:
           arbol_n.py  ->  "\\303\\241rbol_\\303\\261.py"
       Esa cadena no coincide con ninguna ruta del manifiesto, asi que un archivo
       BAJO FIRMA se reportaba `libre`. Con `-z` git emite el nombre real.

    2. RENOMBRES. `--name-only` emite SOLO el nombre NUEVO. El VIEJO -que es el
       que esta en el manifiesto- no aparece, asi que renombrar un archivo
       firmado pasaba sin aviso. `--name-status` emite los dos y hay que mirar
       LOS DOS: el viejo dice que rompiste una firma, el nuevo que hay un archivo
       nuevo sin cubrir.

    3. EL ACOTE MISMO. Hasta el 31-ago esto llevaba `--diff-filter=ACMRD`, que
       EXCLUYE `T` (typechange). Convertir un archivo firmado en un symlink lo
       sacaba del listado y se reportaba `libre`. Hallado por NEXUS, verificado
       por FABLE en un repo limpio (`T firmado.sh` sin filtro; nada con el filtro).

       El arreglo NO fue agregar T: fue QUITAR el acote. Ampliarlo a `ACMRDT`
       deja afuera el proximo estado que git invente -- `U` de unmerged ya estaba
       excluido por construccion-. El DEFAULT los lista todos, y el default era
       lo seguro desde el principio.
       (ficha: a_grep_tells_you_where_never_what_20260829, "QUITA el acote")

    Los TRES fallaban hacia "libre", que es la direccion cara: un guard que
    rechaza de mas molesta; uno que falla abierto no existe el dia que importa.
    Y los tres son el MISMO defecto -- cada uno le pide a git menos informacion
    de la que git da gratis.
    """
    p = subprocess.run(["git", "diff", "--cached", "--name-status", "-z"],
                       cwd=RAIZ, capture_output=True, text=True)
    if p.returncode != 0:
        print(f"[bajo-firma] no pude leer el indice: {p.stderr.strip()}", file=sys.stderr)
        raise SystemExit(2)
    campos = [c for c in p.stdout.split("\0") if c != ""]
    rutas: set[str] = set()
    i = 0
    while i < len(campos):
        est = campos[i]
        # R### y C### traen DOS rutas (origen y destino); el resto, una.
        if est and est[0] in ("R", "C"):
            if i + 2 < len(campos):
                rutas.add(campos[i + 1])   # el VIEJO: el que puede estar firmado
                rutas.add(campos[i + 2])
            i += 3
        else:
            if i + 1 < len(campos):
                rutas.add(campos[i + 1])
            i += 2
    return sorted(rutas)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    rutas = staged() if argv == ["--staged"] else argv
    if not rutas:
        print("[bajo-firma] no hay nada que revisar.")
        return 0

    idx = indice_de_firmas()
    if not idx:
        # CONTROL: si ningun manifiesto esta aprobado, un "no esta bajo firma" no
        # significa que sea seguro editar -- significa que no hay firmas todavia.
        print("[bajo-firma] NO HAY NINGUN manifiesto con review aprobado.")
        print("  Un 'no esta bajo firma' aqui es AUSENCIA DE FIRMAS, no permiso.")
        return 0

    tocados = [r for r in rutas if r in idx]
    for r in rutas:
        if r in idx:
            for man, rev in idx[r]:
                print(f"  BAJO FIRMA  {r}")
                print(f"              {man} -- revisor {rev}")
        else:
            print(f"  libre       {r}")

    if tocados:
        print()
        print(f"[bajo-firma] {len(tocados)} archivo(s) bajo una firma APROBADA.")
        print("  Editarlos deja el recibo en `independent_review_bytes_stale`.")
        print("  Avisale al revisor ANTES de tocarlos, no despues.")
        return 1
    print(f"\n[bajo-firma] OK: ninguno de los {len(rutas)} esta bajo firma aprobada.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
