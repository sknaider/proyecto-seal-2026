#!/usr/bin/env python3
"""Arnés de mutación: una arena NUEVA por mutante.

POR QUE EXISTE (7-sep-2026): mutar el árbol compartido es la clase de operación que borró
el home a la 01:42. Y mutar en UNA copia reutilizada tampoco alcanza: a las 20:32 monté
tres mutantes en la misma copia restaurando con `cp`, el tercero heredó la permutación del
segundo, y firmé un rojo que no existía.

Vive en un `.py` y no dentro de un heredoc del `.sh` para que se le puedan escribir brazos:
la clasificación es lo más delicado del arnés y hasta las 21:05 no era testeable sin
ejecutar todo.

Uso:  arena_por_mutante.py <spec.json> <repo>
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import sys

V = "/home/dadito/IA/seal-spark/.venv/bin/python3"

_FALLIDOS = re.compile(r"(\d+) failed")
_PASADOS = re.compile(r"(\d+) passed")


def clasifica(rc: int, resumen: str, esperados: int) -> str:
    """Distingue un FALLO DE PRUEBA de una CORRIDA INVÁLIDA (ADA, 20:56 y 21:00).

    pytest usa rc=1 SÓLO para «hubo tests que fallaron». rc=2 es interrupción, 3 error
    interno, 4 uso incorrecto, 5 ningún test colectado: contar cualquiera de ésos como
    «mutante muerto» certifica que el test detectó algo cuando en realidad NO CORRIÓ.

    Y el conteo no puede exigir que exista «N passed»: una corrida donde fallan TODOS los
    brazos es válida y no imprime esa parte. Lo dedujo ADA leyendo; yo nunca produje ese
    caso ejecutando.
    """
    if rc == 0:
        # ADA (21:10): un rc=0 con MENOS brazos de los esperados tampoco es supervivencia.
        # Un mutante que hace desaparecer tests deja el resto en verde y se declararia
        # "sobrevive" cuando lo que ocurrio es que la corrida ya no es comparable.
        mp0 = _PASADOS.search(resumen)
        if not mp0 or int(mp0.group(1)) != esperados:
            return "INVALIDA"
        return "SURVIVED"
    if rc != 1:
        return "INVALIDA"
    mf = _FALLIDOS.search(resumen)
    if not mf:
        return "INVALIDA"
    mp = _PASADOS.search(resumen)
    corridos = int(mf.group(1)) + (int(mp.group(1)) if mp else 0)
    return "KILLED" if corridos == esperados else "INVALIDA"


def valida_rutas(rutas: list[str]) -> None:
    """Antes de tocar NADA: relativas y sin salir del árbol.

    ADA (21:00): yo hasheaba primero y validaba después, así que una ruta con `..` moría
    con un FileNotFoundError crudo que no decía cuál era el problema. Mi prueba de eso
    «pasaba» por casualidad: `../../../etc/hosts` existe.
    """
    for f in rutas:
        if pathlib.Path(f).is_absolute() or ".." in pathlib.Path(f).parts:
            raise SystemExit(f"ruta invalida en sujetos_y_tests: {f!r} — debe ser relativa "
                             f"al repo y no salir de el")


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def corre(arena: pathlib.Path, harness: list[str]) -> tuple[int, str, list[str], dict]:
    """Devuelve (rc, resumen, nodeids, diagnóstico).

    ADA (21:00): filtrar por `E ` pierde los diagnósticos precedidos por archivo:línea y
    descarta stderr. Se guardan las colas CRUDAS de ambos: un filtro elige qué es
    relevante ANTES de saber qué salió mal.
    """
    r = subprocess.run([V, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=line", *harness],
                       capture_output=True, text=True, cwd=str(arena))
    lineas = r.stdout.strip().splitlines()
    return (r.returncode,
            lineas[-1] if lineas else "",
            [l[len("FAILED "):].strip() for l in lineas if l.startswith("FAILED ")],
            {"stdout_cola": lineas[-25:], "stderr_cola": r.stderr.strip().splitlines()[-10:]})


def main() -> None:
    spec = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    repo = pathlib.Path(sys.argv[2])
    valida_rutas(spec["sujetos_y_tests"])

    def arena_nueva() -> pathlib.Path:
        r = subprocess.run(["bash", str(repo / "tools" / "seal_arena.sh"), "new", *spec["rutas"]],
                           capture_output=True, text=True, cwd=str(repo), check=True)
        return pathlib.Path(r.stdout.strip())

    base = arena_nueva()
    ref = {f: sha(base / f) for f in spec["sujetos_y_tests"]}
    # La arena fotografía el ÍNDICE. Si el autor editó y no hizo `git add`, la copia trae
    # la versión ANTERIOR (20:37: medí la v3 creyendo medir la v4). Verificar que las
    # copias sean consistentes ENTRE SÍ no alcanza: hay que compararlas con el ÁRBOL.
    desfasados = [f for f in ref if sha(repo / f) != ref[f]]
    if desfasados:
        raise SystemExit("ABORTA: la arena no coincide con el ARBOL en " + ", ".join(desfasados) +
                         ". Falta `git add` de esos archivos: la medicion describiria una "
                         "version que el autor ya no tiene.")

    rc, control, _, _ = corre(base, spec["harness"])
    if rc != 0:
        raise SystemExit(f"el CONTROL debe estar VERDE en arena limpia: {control}")
    mc = _PASADOS.search(control)
    esperados = int(mc.group(1)) if mc else 0
    if esperados <= 0:
        raise SystemExit(f"no pude leer cuantos brazos corrio el control: {control}")
    print(f"[base] control VERDE ({control}) · {esperados} brazos  arena={base}")

    out = []
    for m in spec["mutantes"]:
        if m["subject"] not in spec["sujetos_y_tests"]:
            raise SystemExit(f"{m['id']}: el sujeto {m['subject']} no figura entre los archivos "
                             f"verificados; no se muta lo que no se comprobo")
        a = arena_nueva()
        for f, h in ref.items():
            if sha(a / f) != h:
                raise SystemExit(f"{m['id']}: la arena nueva no coincide con la referencia en {f}")
        p = (a / m["subject"]).resolve()
        if not str(p).startswith(str(a.resolve()) + "/"):
            raise SystemExit(f"{m['id']}: la ruta {m['subject']} sale de la arena ({p}). "
                             f"Una mutacion NUNCA escribe fuera de su copia.")
        s = p.read_text(encoding="utf-8")
        n = s.count(m["ancla"])
        if n != 1:
            raise SystemExit(f"{m['id']}: el ancla aparece {n} veces (debe ser 1)")
        p.write_text(s.replace(m["ancla"], m["reemplazo"]), encoding="utf-8")

        rc, obs, nodeids, detalle = corre(a, spec["harness"])
        veredicto = clasifica(rc, obs, esperados)
        out.append({"id": m["id"], "subject": m["subject"], "arena": str(a), "observed": obs,
                    "nodeids": nodeids, "detalle": detalle, "result": veredicto})
        etiqueta = {"KILLED": "MUERTO", "SURVIVED": "SOBREVIVE",
                    "INVALIDA": "CORRIDA INVALIDA (no cuenta)"}[veredicto]
        print(f"[{m['id']}] {obs} -> {etiqueta}"
              + (f"  por: {nodeids[0]}" if nodeids else "") + f"  arena={a}")
    print(json.dumps({"control": control, "mutantes": out}, ensure_ascii=False))


if __name__ == "__main__":
    main()
