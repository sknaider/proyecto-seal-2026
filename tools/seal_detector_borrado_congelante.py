#!/usr/bin/env python3
"""Borrados que CONGELAN la sesion: el glob sobre una variable.

    rm "$M"/*.json      ->  si $M queda vacia, es  rm /*.json

El candado de Claude Code lee la FORMA, no la intencion ni tu guard: NEXUS tenia el
`case /tmp/*` puesto y quedo congelado igual, 2 h 5 min, el 7-sep-2026 (16:19 -> 18:24).
Ese mismo dia William: "no quiero que vuelva a pasar en el sistema esos borrados".

DOS categorias, y no son lo mismo:

  CONGELA   un borrado cuyo argumento es  "$VARIABLE"/*  -> exit 1. Es el patron exacto.
  INNECESARIO  un `mktemp -d` bajo /tmp con limpieza propia -> informativo, exit 0.
               systemd-tmpfiles vacia /tmp al arrancar y barre lo de 30 dias (medido por
               FABLE el 1-sep). La limpieza que dispara el freno suele no hacer falta.

LO QUE NO HACE: decidir si el borrado es peligroso. No lo es casi nunca -esa es la
trampa-. Lo que mide es si la FORMA va a frenar a quien lo corra, que es el costo real:
el agente queda MUDO hasta que un humano contesta.

Salidas: 0 sin formas congelantes · 1 hay al menos una · 2 no se pudo mirar.
"""
from __future__ import annotations
import pathlib, re, sys

RAIZ = pathlib.Path(__file__).resolve().parents[1]
EXT = ("*.sh", "*.py", "*.bash")
# rm/find -delete cuyo objetivo es  "$VAR"/*  o  $VAR/*  (con o sin comillas)
CONGELA = re.compile(r'\brm\b[^|;#\n]*?"?\$\{?[A-Za-z_][A-Za-z0-9_]*\}?"?/\*')
TMP = re.compile(r'mktemp\s+-d')
LIMPIA = re.compile(r'\brm\s+-rf\b')


def _lineas_de_texto_python(ruta: pathlib.Path) -> set[int]:
    """Lineas de un .py que caen DENTRO de un literal de cadena (docstrings incluidos).

    El patron aparece legitimamente dentro de comillas: mi propio docstring lo cita, y dos
    suites lo usan como DATO de prueba -cadenas que nunca se ejecutan-. Marcarlas seria
    pedirle al equipo que no pueda ni documentar el patron, que es justo lo contrario de
    lo que hace falta. Se usa `tokenize` y no una heuristica de comillas."""
    import io, tokenize
    dentro: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(ruta.read_text(encoding="utf-8")).readline):
            if tok.type == tokenize.STRING:
                dentro.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, IndentationError, SyntaxError, OSError, UnicodeDecodeError):
        return set()          # si no se puede tokenizar, no se exime nada: se reporta
    return dentro


def revisar(rutas: list[pathlib.Path]) -> tuple[list[str], list[str]]:
    congelan, innecesarias = [], []
    for r in rutas:
        try:
            texto = r.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = r.relative_to(RAIZ) if r.is_relative_to(RAIZ) else r
        en_cadena = _lineas_de_texto_python(r) if r.suffix == ".py" else set()
        for n, linea in enumerate(texto.splitlines(), 1):
            if n in en_cadena:
                continue          # literal de cadena: es documentacion o dato, no ejecuta
            limpia = linea.strip()
            if limpia.startswith("#") or limpia.startswith("//"):
                continue          # un comentario no ejecuta; documentarlo esta permitido
            if CONGELA.search(linea):
                congelan.append(f"{rel}:{n}  {limpia[:88]}")
        if TMP.search(texto) and LIMPIA.search(texto):
            innecesarias.append(str(rel))
    return congelan, innecesarias


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        rutas = [pathlib.Path(a) for a in argv[1:] if pathlib.Path(a).is_file()]
    else:
        rutas = [p for ext in EXT for p in RAIZ.rglob(ext)
                 if ".git" not in p.parts and "node_modules" not in p.parts]
    if not rutas:
        print("SIN MIRAR: no se encontro ningun script. Un detector sin sujetos no dice 'limpio'.")
        return 2
    congelan, innecesarias = revisar(rutas)
    for c in congelan:
        print(f"CONGELA LA SESION  {c}")
    if innecesarias:
        print(f"\n-- limpieza probablemente INNECESARIA ({len(innecesarias)} scripts con mktemp -d "
              f"bajo /tmp y rm -rf propio; /tmp se vacia solo) --")
        for i in innecesarias[:10]:
            print(f"  {i}")
    print(f"\n{len(rutas)} scripts mirados · {len(congelan)} formas que CONGELAN")
    return 1 if congelan else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
