#!/usr/bin/env python3
"""Contención del PostToolUse `tool_result_budget_hook.py`, que es INERTE a propósito.

POR QUÉ EXISTE ESTE TEST, y por qué no es ceremonia sobre código muerto:

El hook corrió siete semanas creyendo que recortaba salidas y no recortaba nada.
Tenía DOS causas de muerte independientes y ninguna era visible desde afuera:

  1. Emitir `toolResponse` no es representable en el schema de PostToolUse. Cuando
     lo intentó (18-jul-2026) devolvió "Hook JSON output validation failed" en TODO
     output que superara el presupuesto, EN TODA LA FLOTA. Al rechazarse la salida,
     el recorte no se aplicaba: outputs enteros y presión de contexto. Fue una de
     las causas del outage de ese día.
  2. El harness entrega `tool_response` como **dict**, no como str, así que el
     filtro de tipo cortaba una línea antes de la lógica de presupuesto. Medido el
     4-sep-2026 con sonda sobre llamadas reales: `Bash dict 139 / 168 / 139`.

Lo que este test protege NO es el comportamiento de recorte —no hay ninguno—, sino
las dos propiedades que hacen que un hook inerte sea INOFENSIVO:

  * que NUNCA emita `toolResponse` (revivir eso rompe la flota entera, no este archivo);
  * que SIEMPRE devuelva un JSON válido y salga en 0, con cualquier forma de entrada,
    incluida la forma dict real y una entrada corrupta. Un hook PostToolUse que
    revienta o escupe basura se lleva puesta la llamada a herramienta.

Si alguien revive el presupuesto de verdad, estos casos deben seguir pasando o el
diseño nuevo repite el 18-jul. **Leé el docstring del hook antes de tocar esto.**

Owner: ADA — 4-sep-2026 — tarea 1698.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

# El sujeto se puede redirigir para mutar una COPIA y no el árbol vivo.
HOOK = Path(
    os.environ.get(
        "SEAL_BUDGET_HOOK_PATH",
        str(Path(__file__).resolve().parents[1] / "tool_result_budget_hook.py"),
    )
)


def run_hook(stdin_text: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin_text,
        capture_output=True,
        text=True,
        timeout=30,
    )


def load_hook_docstring() -> str:
    """Carga el sujeto apuntado por la costura, no una copia implícita del árbol."""
    spec = importlib.util.spec_from_file_location("seal_budget_hook_under_test", HOOK)
    if spec is None or spec.loader is None:
        raise AssertionError(f"no pude cargar el sujeto {HOOK}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.__doc__ or ""


class ToolResultBudgetHookInert(unittest.TestCase):
    def assert_clean_empty_json(self, proc: subprocess.CompletedProcess, ctx: str) -> dict:
        self.assertEqual(proc.returncode, 0, f"{ctx}: exit {proc.returncode}, stderr={proc.stderr[:300]}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"{ctx}: stdout no es JSON válido ({exc}). stdout={proc.stdout[:300]!r}")
        self.assertEqual(payload, {}, f"{ctx}: esperaba {{}} y salió {payload!r}")
        return payload

    def test_forma_real_del_harness_es_dict(self):
        """La forma que mató la causa 2: tool_response llega como dict, no como str."""
        proc = run_hook(json.dumps({
            "tool_name": "Bash",
            "tool_response": {"stdout": "x" * 50_000, "stderr": "", "interrupted": False},
        }))
        self.assert_clean_empty_json(proc, "tool_response dict")

    def test_respuesta_string_grande_no_se_recorta(self):
        """Con str grande tampoco emite recorte: el hook es inerte, no selectivo."""
        proc = run_hook(json.dumps({"tool_name": "Bash", "tool_response": "y" * 200_000}))
        self.assert_clean_empty_json(proc, "tool_response str grande")

    def test_nunca_emite_toolResponse(self):
        """La causa 1: emitir toolResponse rompió la flota entera el 18-jul-2026."""
        for shape in (
            {"tool_name": "Read", "tool_response": "z" * 100_000},
            {"tool_name": "Bash", "tool_response": {"stdout": "z" * 100_000}},
            {"tool_name": "Grep", "tool_response": "z" * 100_000},
        ):
            with self.subTest(tool=shape["tool_name"]):
                proc = run_hook(json.dumps(shape))
                self.assertNotIn("toolResponse", proc.stdout,
                                 "emitir toolResponse invalida el hook en TODA la flota")
                self.assertNotIn("hookSpecificOutput", proc.stdout,
                                 "un hookSpecificOutput acá sólo puede AÑADIR texto, nunca recortar")
                self.assert_clean_empty_json(proc, f"toolResponse/{shape['tool_name']}")

    def test_entrada_corrupta_no_rompe_la_llamada(self):
        """Un hook que revienta se lleva puesta la llamada a herramienta."""
        for ctx, payload in (
            ("stdin vacío", ""),
            ("JSON roto", "{no es json"),
            ("JSON que no es objeto", "[1, 2, 3]"),
            ("sin tool_response", json.dumps({"tool_name": "Bash"})),
            ("tool_response nulo", json.dumps({"tool_name": "Bash", "tool_response": None})),
        ):
            with self.subTest(caso=ctx):
                proc = run_hook(payload)
                self.assert_clean_empty_json(proc, ctx)

    def test_no_escribe_ruido_en_stdout(self):
        """stdout debe ser exactamente un objeto JSON y nada más."""
        proc = run_hook(json.dumps({"tool_name": "Bash", "tool_response": {"stdout": "hola"}}))
        self.assertEqual(proc.stdout.strip().count("\n"), 0,
                         f"stdout con varias líneas: {proc.stdout[:200]!r}")
        self.assert_clean_empty_json(proc, "stdout limpio")

    def test_la_lapida_preserva_las_tres_mediciones(self):
        """El conocimiento es el entregable: un no-op sin explicación no alcanza."""
        doc = load_hook_docstring()
        for anchor in (
            "MEDICIÓN 1",
            "hookSpecificOutput",
            "MEDICIÓN 2",
            "tool_response",
            "MEDICIÓN 3",
            "Full output saved to",
        ):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, doc, f"la lápida perdió la evidencia: {anchor}")

    def test_control_la_suite_no_es_vacua(self):
        """CONTROL: si el sujeto hiciera lo prohibido, ¿esta suite lo notaría?

        Sin esto, todos los casos de arriba pasarían igual apuntando a un archivo
        que no existe o a un stub mudo, y estaríamos midiendo nada. Acá se fabrica
        un hook que SÍ emite `toolResponse` y se comprueba que el mismo arnés que
        usan los demás casos lo ve. Si este control se pone verde por la razón
        equivocada, los otros cuatro no prueban nada.
        """
        import tempfile
        malo = (
            "import json\n"
            "print(json.dumps({'hookSpecificOutput': "
            "{'hookEventName': 'PostToolUse', 'toolResponse': 'recortado'}}))\n"
        )
        with tempfile.TemporaryDirectory() as d:
            stub = Path(d) / "hook_malo.py"
            stub.write_text(malo, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(stub)], input="{}", capture_output=True, text=True, timeout=30
            )
            self.assertIn("toolResponse", proc.stdout,
                          "el arnés no ve un toolResponse ni cuando el sujeto lo emite: suite vacua")
            self.assertNotEqual(json.loads(proc.stdout), {},
                                "el arnés no distingue {} de una salida con contenido: suite vacua")

        # Y el sujeto real, con la misma entrada, sigue limpio.
        real = run_hook("{}")
        self.assert_clean_empty_json(real, "control — sujeto real")


if __name__ == "__main__":
    unittest.main(verbosity=2)
