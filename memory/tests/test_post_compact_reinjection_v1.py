#!/usr/bin/env python3
"""El hook post-compactación reinyecta contexto REAL, no un aviso de que no pudo.

POR QUÉ EXISTE, medido el 4-sep-2026:
`post_compact_session_start_hook.py` abría PostgreSQL con el rol histórico `seal`,
cuyo password ya no autentica. Todo lo que hace el hook está dentro de ese `try`,
así que en CADA compactación, de CADA agente, el bloque reinyectado era esto y
nada más:

    ⚡ POST-COMPACTACIÓN — ADA — contexto crítico restaurado desde SOUL
    (SOUL DB no disponible: password authentication failed for user "seal")

Ni correcciones de William, ni reglas activas, ni estado del equipo. El hook
existía, corría, devolvía JSON válido y **no reinyectaba nada**. Tercera aparición
del mismo patrón en un día: una herramienta con credencial muerta que informa mal
el sistema que mide (las otras dos: el self-test de arranque y el job de embeddings).

QUÉ PROTEGE: que el aviso de degradación NO vuelva a ser la salida normal, y que
el pedido original aparezca cuando está guardado. La aserción que importa es la
NEGATIVA — que el texto de degradación no esté —, porque es exactamente la forma
que tuvo el defecto durante semanas.

NOTA PARA QUIEN LO CORRA EN OTRA MÁQUINA: es un test de integración y necesita la
SOUL DB local. Eso es deliberado: el defecto que cubre sólo se ve contra la base
real; con un doble de prueba habría pasado en verde todo este tiempo.

Owner: ADA — 4-sep-2026 — tarea 1699.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

MEMORY_DIR = Path(__file__).resolve().parents[1]
# El sujeto se puede redirigir para mutar una COPIA y no el árbol vivo.
HOOK = Path(os.environ.get("SEAL_POST_COMPACT_HOOK_PATH",
                           str(MEMORY_DIR / "post_compact_session_start_hook.py")))
DEGRADADO = "SOUL DB no disponible"

sys.path.insert(0, str(MEMORY_DIR))
from compact_original_request import original_request_lines  # noqa: E402


def run_hook(agent: str = "ADA") -> dict:
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input="{}", capture_output=True, text=True, timeout=60,
        env={**os.environ, "SEAL_AGENT": agent},
    )
    assert proc.returncode == 0, f"exit {proc.returncode}: {proc.stderr[:300]}"
    return json.loads(proc.stdout)


class ReinjeccionPostCompactacion(unittest.TestCase):
    def test_no_reinyecta_el_aviso_de_base_caida(self):
        """LA aserción: durante semanas ésta fue la única línea que salía."""
        out = run_hook()
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn(DEGRADADO, ctx,
                         "el hook volvió a quedarse sin credencial y no reinyecta nada")

    def test_reinyecta_contenido_de_soul_no_solo_el_encabezado(self):
        out = run_hook()
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Estado del equipo:", ctx)
        self.assertGreater(len(ctx), 400, f"contexto sospechosamente corto: {len(ctx)} chars")

    def test_la_salida_es_un_hook_valido(self):
        out = run_hook()
        hso = out["hookSpecificOutput"]
        self.assertEqual(hso["hookEventName"], "SessionStart")
        self.assertIsInstance(hso["additionalContext"], str)
        self.assertIn("boot_context", hso["initialUserMessage"])

    def test_el_hook_REALMENTE_reinyecta_el_pedido_guardado(self):
        """Lo cazó un mutante: probar el formateador NO prueba que el hook lo llame.

        Mi primera versión sólo ejercitaba `original_request_lines` por separado, así
        que un mutante que borraba la llamada dentro del hook SOBREVIVIÓ. Es el mismo
        agujero por el que casi despacho el hook sin el import: la función andaba y el
        cableado no. Acá se compara contra lo que hay guardado de verdad, y las dos
        ramas asertan — no hay salida por 'no habÍa nada que comprobar'.
        """
        import asyncio
        import asyncpg
        from config import settings

        async def leer():
            conn = await asyncpg.connect(settings.pg_dsn)
            try:
                row = await conn.fetchrow(
                    "SELECT state FROM soul_v3.working_state WHERE agent = 'ADA'")
            finally:
                await conn.close()
            if not row or not row["state"]:
                return None
            raw = row["state"]
            state = json.loads(raw) if isinstance(raw, str) else raw
            return (state or {}).get("original_request")

        guardado = asyncio.run(leer())
        ctx = run_hook()["hookSpecificOutput"]["additionalContext"]

        if guardado and str(guardado).strip():
            self.assertIn("PEDIDO ORIGINAL", ctx,
                          "hay pedido guardado y el hook no lo reinyecta: cableado roto")
            self.assertIn(str(guardado).strip()[:60], ctx,
                          "reinyecta el encabezado pero no el pedido")
        else:
            self.assertNotIn("PEDIDO ORIGINAL", ctx,
                             "no hay pedido guardado y aun así inventa la sección")

    def test_el_pedido_original_se_formatea_cuando_existe(self):
        lineas = original_request_lines({"original_request": "arregla el candado"})
        self.assertIn("PEDIDO ORIGINAL", lineas[0])
        self.assertIn("arregla el candado", lineas[1])

    def test_control_sin_pedido_guardado_no_inventa_una_seccion(self):
        """CONTROL de no-vacuidad: si siempre devolviera renglones, el caso de
        arriba pasaría por la razón equivocada. Sin pedido guardado, silencio."""
        for estado in ({}, {"original_request": None}, {"original_request": "   "}, None, "no soy un dict"):
            with self.subTest(estado=str(estado)[:24]):
                self.assertEqual(original_request_lines(estado), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
