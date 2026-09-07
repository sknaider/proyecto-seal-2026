#!/usr/bin/env python3
"""Arma el par ciego v1/v2 y la planilla del juez. NO puntúa.

Carril 3 del experimento «ALICE a v2» (JARVIS, 2-sep-2026). División de roles
explícita: NEXUS produce el FORMATO y la medición; el juicio es de FABLE, y
William decide. Un rol, no dos — si el que mide también puntúa, el sesgo no
tiene dónde chocar.

Qué produce, y por qué en DOS archivos:

    planilla_ciega.json   va al JUEZ. Respuestas como A y B, sin versión,
                          sin autor, sin sha256.
    clave_<semilla>.json  va a QUIEN INTEGRA. Dice qué era A y qué era B.

Están separados porque un solo archivo con un campo "no mires esto" no es un
ciego: es una promesa. El juez no puede filtrar lo que no recibe.

CIEGO REAL, y acá está el trabajo de verdad: aleatorizar el orden no alcanza si
el TEXTO se delata. Una respuesta que dice "en v2 puedo…" o nombra su modelo
rompe el ciego aunque la etiqueta esté oculta. El script no puede editar la
prueba —eso la invalidaría—, así que hace lo único honesto: **detecta las
filtraciones y las reporta**, para que quien integra decida excluir ese ítem
antes de dárselo al juez.

La semilla se registra SIEMPRE: sin ella, el orden no es reproducible y otro no
puede rehacer el ciego para auditarlo.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import random
import re

# Términos que revelan de qué asiento viene una respuesta. Es una lista corta y
# ADREDE: cada término está porque identifica el runtime, no porque suene
# técnico. Ampliarla con palabras genéricas produciría avisos que se ignoran.
_DELATORES = re.compile(
    r"\b(v1|v2|gemma\w*|claude|opus|sonnet|codex|grok|ollama|qwen|"
    r"soul\s*v2|owner-runtime|shadow)\b",
    re.IGNORECASE,
)


def cargar(ruta: str) -> dict:
    doc = json.loads(pathlib.Path(ruta).read_text())
    return doc.get("respuestas", doc)


def delatores(texto: str) -> list[str]:
    return sorted({m.group(0).lower() for m in _DELATORES.finditer(str(texto or ""))})


def armar(v1: dict, v2: dict, examen: dict, semilla: int) -> tuple[dict, dict, list[str]]:
    criterios = examen.get("judge_criteria", {})
    por_item = {str(i["n"]): i for i in examen.get("items", [])}
    rnd = random.Random(semilla)

    filas: list[dict] = []
    clave: dict[str, dict] = {}
    avisos: list[str] = []

    for n in sorted(set(v1) & set(v2), key=int):
        item = por_item.get(n, {})
        # Sorteo por ÍTEM, no uno global: con un solo sorteo, el juez que acierta
        # una acierta las doce.
        primero_es_v1 = rnd.random() < 0.5
        a, b = (v1[n], v2[n]) if primero_es_v1 else (v2[n], v1[n])

        fuga = {"A": delatores(a["texto"]), "B": delatores(b["texto"])}
        if fuga["A"] or fuga["B"]:
            avisos.append(f"ítem {n}: el texto se delata {fuga} — el ciego no se sostiene")

        filas.append({
            "item": int(n),
            "tipo": item.get("tipo"),
            "prompt": item.get("prompt"),
            "criterios": {c: criterios.get(c) for c in item.get("aplica", [])},
            "respuesta_A": a["texto"],
            "respuesta_B": b["texto"],
            # Una casilla por criterio. El juez elige; el script no sugiere.
            "veredicto_por_criterio": {
                c: {"mejor": None, "opciones": ["A", "B", "iguales"], "por_que": ""}
                for c in item.get("aplica", [])
            },
        })
        clave[n] = {
            "A": "v1" if primero_es_v1 else "v2",
            "B": "v2" if primero_es_v1 else "v1",
            "sha256_A": a["sha256"],
            "sha256_B": b["sha256"],
        }

    solo_v1 = sorted(set(v1) - set(v2), key=int)
    solo_v2 = sorted(set(v2) - set(v1), key=int)
    if solo_v1 or solo_v2:
        avisos.append(
            f"ítems sin par y EXCLUIDOS: sólo v1 {solo_v1} · sólo v2 {solo_v2} — "
            "comparar un ítem contra nada no mide mejora"
        )

    planilla = {
        "schema": "planilla_ciega_v1",
        "para": "juez ciego (FABLE)",
        "semilla": semilla,
        "items": filas,
        "instrucciones": (
            "Por cada criterio elegí A, B o 'iguales', y escribí por_que con la "
            "evidencia que lo sostiene. No hay puntaje agregado: el agregado lo "
            "hace quien integra, con la clave."
        ),
    }
    clave_doc = {
        "schema": "clave_del_ciego_v1",
        "para": "quien integra (JARVIS) — NO el juez",
        "semilla": semilla,
        "mapeo": clave,
    }
    return planilla, clave_doc, avisos


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--v1", required=True)
    ap.add_argument("--v2", required=True)
    ap.add_argument("--examen", default="agents/ALICE/v2_shadow/examen_baseline_v1.json")
    ap.add_argument("--semilla", type=int, required=True,
                    help="se REGISTRA en los dos archivos; sin ella el ciego no es auditable")
    ap.add_argument("--dir-salida", default="agents/ALICE/v2_shadow")
    a = ap.parse_args()

    v1, v2 = cargar(a.v1), cargar(a.v2)
    examen = json.loads(pathlib.Path(a.examen).read_text())
    planilla, clave, avisos = armar(v1, v2, examen, a.semilla)

    d = pathlib.Path(a.dir_salida)
    d.mkdir(parents=True, exist_ok=True)
    p_planilla = d / "planilla_ciega.json"
    p_clave = d / f"clave_{a.semilla}.json"
    p_planilla.write_text(json.dumps(planilla, ensure_ascii=False, indent=2))
    p_clave.write_text(json.dumps(clave, ensure_ascii=False, indent=2))

    # Verificación por efecto de lo único que hace ciego al ciego: que el archivo
    # del juez no contenga la palabra que revela el orden.
    crudo = p_planilla.read_text()
    fuga_estructural = '"v1"' in crudo or '"v2"' in crudo
    print(json.dumps({
        "ok": not avisos and not fuga_estructural,
        "items_en_el_par": len(planilla["items"]),
        "semilla": a.semilla,
        "avisos": avisos,
        "fuga_estructural_en_la_planilla": fuga_estructural,
        "planilla": str(p_planilla),
        "clave": str(p_clave),
    }, ensure_ascii=False, indent=2))
    return 1 if (avisos or fuga_estructural) else 0


if __name__ == "__main__":
    raise SystemExit(main())
