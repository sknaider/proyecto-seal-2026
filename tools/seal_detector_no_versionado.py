#!/usr/bin/env python3
"""Detecta archivos que un servicio NECESITA y que NO estan versionados.

POR QUE EXISTE (7-sep-2026): el borrado destapo tres archivos que no se
perdieron ese dia — nunca habian estado versionados, y funcionaban porque
vivian sueltos en el disco:

    seal-studio/frontend/                    vacio en TODAS las fuentes
    seal-desktop/ui/package.json             ausente en todas
    companion_core/ui/.../InboxView.tsx  ausente en todas

Los tres se descubren SOLO cuando algo intenta arrancar de cero. Por eso
nadie los vio en un ano.

QUE HACE: para cada entrypoint de codigo, lee sus imports/require relativos
y comprueba dos cosas distintas que se confunden facil:

    existe en DISCO   -> el sistema anda hoy
    esta en GIT       -> el sistema sobrevive a perder el disco

Un archivo con  existe=SI  git=NO  es una BOMBA DE TIEMPO: funciona hasta
que alguien clone el repo o el disco se pierda.

NO EJECUTA NADA: solo lee y parsea. La objecion de ADA (7-sep 10:54) es que
importar ejecuta codigo; este detector no importa nada.
"""
from __future__ import annotations
import ast, pathlib, subprocess, sys


def versionados(repo: pathlib.Path) -> set[str]:
    r = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True)
    return set(r.stdout.split())


def imports_relativos(archivo: pathlib.Path) -> list[str]:
    """Modulos que el archivo importa desde SU MISMO directorio."""
    try:
        arbol = ast.parse(archivo.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    mods = []
    for n in ast.walk(arbol):
        if isinstance(n, ast.ImportFrom) and n.level and n.module:
            mods.append(n.module.split(".")[0])
        elif isinstance(n, ast.Import):
            mods += [a.name.split(".")[0] for a in n.names]
    return mods


def main() -> int:
    repo = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    en_git = versionados(repo)
    bombas = []
    for py in repo.rglob("*.py"):
        if ".git" in py.parts or "node_modules" in py.parts:
            continue
        # Un archivo IGNORADO a proposito no es una bomba: es una decision.
        # Sin esto el detector marcaba 48 "bombas" en soul-v2-lab que eran
        # todas de build/ — artefacto compilado, ignorado adrede. Un detector
        # que no distingue "falta" de "no corresponde" produce ruido, y el
        # ruido hace que nadie lo lea (mi propio baseline del Pilar III:
        # 77 % de mis alertas no eran ni auditables).
        if subprocess.run(["git", "check-ignore", "-q", str(py.relative_to(repo))],
                          cwd=repo).returncode == 0:
            continue
        d = py.parent
        for mod in set(imports_relativos(py)):
            cand = d / f"{mod}.py"
            if not cand.exists():
                continue
            rel = str(cand.relative_to(repo))
            if subprocess.run(["git", "check-ignore", "-q", rel],
                              cwd=repo).returncode == 0:
                continue
            if rel not in en_git:
                bombas.append((str(py.relative_to(repo)), rel))
    if not bombas:
        print("sin hallazgos: todo lo que se importa y existe, esta versionado")
        return 0
    print(f"BOMBAS DE TIEMPO: {len(bombas)} archivos existen en disco y NO en git\n")
    for quien, que in sorted(set(bombas))[:40]:
        print(f"  {que}\n      lo necesita: {quien}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
