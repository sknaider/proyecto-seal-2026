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
import ast, pathlib, subprocess, sys, warnings


def versionados(repo: pathlib.Path) -> set[str]:
    r = subprocess.run(["git", "ls-files"], cwd=repo, capture_output=True, text=True)
    return set(r.stdout.split())


def imports_relativos(archivo: pathlib.Path) -> list[str]:
    # ast.parse emite SyntaxWarning por escapes invalidos del archivo LEIDO
    # (p.ej. "\\*" en un docstring ajeno). Es ruido del sujeto, no un hallazgo.
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
    warnings.simplefilter("ignore", SyntaxWarning)
    repo = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
    en_git = versionados(repo)
    bombas = []
    ignorados = []
    for py in repo.rglob("*.py"):
        if ".git" in py.parts or "node_modules" in py.parts:
            continue
        d = py.parent
        for mod in set(imports_relativos(py)):
            cand = d / f"{mod}.py"
            if not cand.exists():
                continue
            rel = str(cand.relative_to(repo))
            if rel in en_git:
                continue
            # ADA, 7-sep 11:08: ".gitignore no demuestra que un archivo sea
            # prescindible o reproducible: el estado de nerves perdido estaba
            # ignorado precisamente". Tiene razon y mi version anterior
            # SALTABA estos en silencio, que es peor que marcarlos mal:
            # un hallazgo mal clasificado se discute, uno invisible no.
            # .gitignore prueba que ALGUIEN lo excluyo a proposito; NO prueba
            # que sepa regenerarlo. Por eso ahora son una categoria propia
            # que pide una respuesta humana, no un descarte automatico.
            if subprocess.run(["git", "check-ignore", "-q", rel],
                              cwd=repo).returncode == 0:
                ignorados.append((str(py.relative_to(repo)), rel))
                continue
            bombas.append((str(py.relative_to(repo)), rel))
    ign = sorted(set(ignorados))
    if ign:
        print(f"IGNORADOS A PROPOSITO: {len(ign)} — se importan, existen en disco,")
        print("  no estan en git y .gitignore los excluye. NO son automaticamente")
        print("  prescindibles: hay que decir de cada uno si es artefacto")
        print("  REGENERABLE (que comando lo regenera) o ESTADO ESENCIAL (que")
        print("  necesita respaldo fuera de git, sin versionar secretos).\n")
        grupos: dict[str, list[str]] = {}
        for _quien, que in ign:
            grupos.setdefault(que.split("/")[0], []).append(que)
        for raiz, files in sorted(grupos.items(), key=lambda g: -len(g[1])):
            print(f"  {raiz}/  — {len(files)} archivos. Ej: {files[0]}")
        print()

    if not bombas:
        print("sin bombas: todo lo que se importa, existe y no esta ignorado,")
        print("esta versionado. Alcance: imports de Python del mismo directorio.")
        return 1 if ign else 0
    print(f"BOMBAS DE TIEMPO: {len(bombas)} archivos existen en disco y NO en git\n")
    for quien, que in sorted(set(bombas))[:40]:
        print(f"  {que}\n      lo necesita: {quien}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
