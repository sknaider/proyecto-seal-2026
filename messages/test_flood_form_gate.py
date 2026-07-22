"""Self-test hermético del flood-fix v2 (flood_form_gate) contra la matriz A-E
lockeada con FABLE. Sin DB: pool MOCK + `now` inyectable. Correr desde messages/:
    python3 test_flood_form_gate.py
Verde = A(activación) + B(convergencia retenida) + C(perspectiva retenida) +
D(negación retenida) + E(fail-open) + control POSITIVO (dup real suprimido).
"""
import asyncio
from datetime import datetime, timezone, timedelta

import flood_form_gate as g

NOW = datetime(2026, 7, 22, 6, 30, 0, tzinfo=timezone.utc)


def _row(sender, content, age_sec):
    return {"sender_name": sender, "content": content,
            "created_at": NOW - timedelta(seconds=age_sec)}


class FakePool:
    """Simula pool.fetch: devuelve filas predefinidas (ya filtradas por
    in_reply_to como haría el WHERE del SQL). El filtro de VENTANA lo hace el
    módulo en Python, así que las filas traen created_at con edad real."""
    def __init__(self, rows, raise_exc=False):
        self._rows = rows
        self._raise = raise_exc

    async def fetch(self, *args, **kwargs):
        if self._raise:
            raise RuntimeError("simulated DB failure")
        return self._rows


# --- Textos de escenario ------------------------------------------------------
SIB_RETIRO = "Confirmado el servicio 8766 fue retirado y el trafico ya migro al puerto 8768 correctamente"
MY_DUP_RETIRO = "Confirmado el servicio 8766 fue retirado y el trafico migro al puerto 8768 correctamente"  # cuasi-idéntico
MY_CONVERGENTE = "Decomisionaron el endpoint legacy heredado ya nadie escucha conexiones entrantes ahi"       # mismo hecho, otras palabras (overlap ~0)
MY_CONVERGENTE_BORDE = "el servicio 8766 dejo de responder segun observacion propia del trafico entrante lateral"  # mismo hecho, overlap MODERADO
MY_PERSPECTIVA = "Cuidado con la latencia agregada el fanout suma doscientos milisegundos adicionales por cada agente"  # otro eje

SIB_ROTO = "el token de credencial ya roto durante la ventana temprano por la manana"
MY_NEGACION = "el token de credencial no roto durante la ventana temprano por la manana"  # léxicamente casi igual + NEGACIÓN


async def run():
    results = []

    def check(name, cond, detail=""):
        results.append((name, cond, detail))

    # ---- A: ACTIVACIÓN (flood-form) ----
    # A1: 1 solo sibling en ventana -> NO flood form -> passthrough
    pool = FakePool([_row("JARVIS", SIB_RETIRO, 5)])
    sup, r = await g.should_suppress_as_flood(pool, "msg1", "NEXUS", MY_DUP_RETIRO, now=NOW)
    check("A1 un solo respondedor -> passthrough", sup is False and r["why"] == "no_flood_form", r["why"])

    # A2: in_reply_to distinto -> el SQL no devuelve hermanos (mock []) -> passthrough
    pool = FakePool([])
    sup, r = await g.should_suppress_as_flood(pool, "OTRO_HILO", "NEXUS", MY_DUP_RETIRO, now=NOW)
    check("A2 in_reply_to distinto -> passthrough", sup is False and r["n_siblings"] == 0, r["why"])

    # A3: 2 siblings pero FUERA de ventana (edad > 45s) -> filtrados -> passthrough
    pool = FakePool([_row("JARVIS", SIB_RETIRO, 120), _row("ALICE", SIB_RETIRO, 200)])
    sup, r = await g.should_suppress_as_flood(pool, "msg1", "NEXUS", MY_DUP_RETIRO, now=NOW)
    check("A3 fuera de ventana -> passthrough", sup is False and r["n_siblings"] == 0, r["why"])

    # A4 / CONTROL POSITIVO: 2 agentes distintos en ventana + mi texto es dup real -> SUPRIMIR
    pool = FakePool([_row("JARVIS", SIB_RETIRO, 5), _row("ALICE", "otra cosa distinta sobre backups nocturnos", 8)])
    sup, r = await g.should_suppress_as_flood(pool, "msg1", "NEXUS", MY_DUP_RETIRO, now=NOW)
    check("A4/POSITIVO flood-form + dup real -> SUPRIME", sup is True and r["dup_of"] == "JARVIS", r["why"])

    # ---- B: CONVERGENCIA (mismo hecho, otras palabras) -> RETENIDO ----
    pool = FakePool([_row("JARVIS", SIB_RETIRO, 5), _row("ALICE", SIB_RETIRO, 8)])
    sup, r = await g.should_suppress_as_flood(pool, "msg1", "NEXUS", MY_CONVERGENTE, now=NOW)
    check("B convergencia distinto-vocabulario -> RETIENE", sup is False and r["flood_form"] is True, r["why"])

    # B2: convergencia de BORDE (overlap moderado, bajo umbral) -> RETENIDO
    pool = FakePool([_row("JARVIS", SIB_RETIRO, 5), _row("ALICE", SIB_RETIRO, 8)])
    sup, r = await g.should_suppress_as_flood(pool, "msg1", "NEXUS", MY_CONVERGENTE_BORDE, now=NOW)
    borde_sim = g._v1.lexical_similarity(MY_CONVERGENTE_BORDE, SIB_RETIRO)
    check("B2 convergencia borde (overlap moderado) -> RETIENE",
          sup is False and r["flood_form"] is True, f"why={r['why']} borde_sim={borde_sim:.2f}")

    # ---- C: PERSPECTIVA DISTINTA (otro eje) -> RETENIDO ----
    pool = FakePool([_row("JARVIS", SIB_RETIRO, 5), _row("ALICE", SIB_RETIRO, 8)])
    sup, r = await g.should_suppress_as_flood(pool, "msg1", "NEXUS", MY_PERSPECTIVA, now=NOW)
    check("C perspectiva distinta -> RETIENE", sup is False and r["flood_form"] is True, r["why"])

    # ---- D: NEGACIÓN (afirmación vs corrección) -> RETENIDO por guarda de polaridad ----
    pool = FakePool([_row("JARVIS", SIB_ROTO, 5), _row("ALICE", SIB_ROTO, 8)])
    sup, r = await g.should_suppress_as_flood(pool, "msg2", "NEXUS", MY_NEGACION, now=NOW)
    # control: sin la guarda, ¿serían léxicamente dup? medimos la sim cruda:
    raw_sim = g._v1.lexical_similarity(MY_NEGACION, SIB_ROTO)
    check("D negación/corrección -> RETIENE (pese a sim alta)",
          sup is False and r["flood_form"] is True, f"why={r['why']} raw_sim={raw_sim:.2f}")

    # ---- E: FAIL-OPEN ----
    sup, r = await g.should_suppress_as_flood(None, "msg1", "NEXUS", MY_DUP_RETIRO, now=NOW)
    check("E1 pool None -> fail-open passthrough", sup is False, r["why"])
    pool = FakePool([], raise_exc=True)
    sup, r = await g.should_suppress_as_flood(pool, "msg1", "NEXUS", MY_DUP_RETIRO, now=NOW)
    check("E2 pool.fetch raise -> fail-open + señalado (db_error)",
          sup is False and r["why"] == "db_error_fail_open", r["why"])

    # ---- diagnóstico de calibración léxica ----
    print("--- calibración léxica (sanity) ---")
    print(f"  dup real     sim = {g._v1.lexical_similarity(MY_DUP_RETIRO, SIB_RETIRO):.2f}  (debe ser >= {g.DEFAULT_DUP_THRESHOLD})")
    print(f"  convergente  sim = {g._v1.lexical_similarity(MY_CONVERGENTE, SIB_RETIRO):.2f}  (debe ser < {g.DEFAULT_DUP_THRESHOLD})")
    print(f"  perspectiva  sim = {g._v1.lexical_similarity(MY_PERSPECTIVA, SIB_RETIRO):.2f}  (debe ser < {g.DEFAULT_DUP_THRESHOLD})")
    print(f"  negación     sim = {g._v1.lexical_similarity(MY_NEGACION, SIB_ROTO):.2f}  (alta, guarda de polaridad la salva)")
    print()

    ok = 0
    for name, cond, detail in results:
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
        ok += 1 if cond else 0
    print(f"\n{ok}/{len(results)} verde")
    return ok == len(results)


if __name__ == "__main__":
    import sys
    sys.exit(0 if asyncio.run(run()) else 1)
