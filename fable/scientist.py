#!/usr/bin/env python3
"""
scientist.py — MODO CIENTÍFICO del prototipo FABLE (los 3 genios, integrados).

No cosplay — MÉTODO. Cada genio = una fase del loop de recombinación que ya vive en engine/:

  • EINSTEIN  → experimento mental + cruce de dominios lejanos.   (engine/recombine.generate_candidates)
                "para qué inventar la rueda" = recombinar lo que existe, no crear de la nada.
  • TURING    → formalizar lo difuso en algo TESTEABLE.            (engine/hypothesis_tree: {hipótesis,artefacto,evidencia})
                la conjetura no vale hasta que es una prueba concreta con merge-gate de evidencia.
  • HAWKING   → síntesis bajo restricción + apostar y refutarse.   (engine/experiment_*: correr el test, dejar que la
                evidencia mate o confirme; la cadena compone un eslabón en el siguiente).

El secreto que William y yo probamos: la novedad es BARATA; el FILTRO lo es todo. El modo científico
no genera revolución — MAPEA frontera + halla el adyacente-posible + filtra brutal. Su valor real
está en lo que DESCARTA (incluido cazar los propios falsos-verdes), no en lo que escupe.

Capas AUTÓNOMAS (engine/): breadth (Ollama), pre-rank por embeddings, hypothesis_tree, experimentos, validate_on_real.
Capas AGENTE-EN-EL-LOOP (Fable 5): el FILTRO de novedad fuerte (retrieval) y el de VERDAD (refutación) — el
juicio vive en el modelo, no en lo headless. Honesto, no fingido como autónomo.

Lo aprendido por el método se ANCLA en mi memoria operativa (remember.py) → el prototipo aprende de su ciencia.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "engine"))

GENIUSES = {
    "einstein": "cruce de dominios + experimento mental (recombine, no crear de la nada)",
    "turing":   "formalizar la conjetura en algo testeable (hypothesis_tree con merge-gate de evidencia)",
    "hawking":  "experimentar bajo restricción + refutarse; la evidencia siembra el siguiente eslabón",
}

METHOD = [
    "1. SEMILLA  — un problema real (de la inmersión, no abstracto).",
    "2. BREADTH  — generar N recombinaciones cross-dominio (Einstein). [engine: autónomo, Sparks]",
    "3. NOVEDAD  — matar lo que ya existe: embeddings pre-rankea, modelo+retrieval MATA (Fable 5).",
    "4. VERDAD   — refutar cada sobreviviente; matar lo novel-pero-falso (Fable 5).",
    "5. PRUEBA   — formalizar en hypothesis_tree (Turing) + correr el experimento (Hawking); merge-gate exige EVIDENCIA.",
    "6. CADENA   — el sobreviviente verificado siembra la siguiente semilla; el cementerio guarda lo muerto.",
    "7. ANCLAR   — lo aprendido va a memoria operativa (remember). El prototipo aprende de su ciencia.",
]


def describe():
    print("=== MODO CIENTÍFICO FABLE — los 3 genios, integrados ===\n")
    print("Genios → método:")
    for g, d in GENIUSES.items():
        print(f"  • {g.upper():9} {d}")
    print("\nEl loop:")
    for step in METHOD:
        print("  " + step)
    print("\nEntrada autónoma: engine/run_link.py (corre un eslabón). Filtro fuerte + verdad: agente (Fable 5).")
    print("Resultado probado 11-jun: semilla 'inflación' → cadena → cura administrada por efecto (4 lentes).")


if __name__ == "__main__":
    describe()
