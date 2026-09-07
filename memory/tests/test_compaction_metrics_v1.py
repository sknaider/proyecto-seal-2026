"""La compactación se MIDE, y sobre todo se nota cuando sale degradada.

POR QUÉ EXISTE (idea de IBM Bob reescrita desde cero, no copiada):
Bob instrumenta su compactación; nosotros no medíamos nada. El precio se cobró el
4-sep-2026: `post_compact_session_start_hook.py` llevaba SEMANAS reinyectando una
sola línea —«SOUL DB no disponible»— en cada compactación de cada agente, sin
correcciones de William, sin reglas, sin estado del equipo. **Corría, no fallaba y
no hacía su trabajo, y ninguna señal lo delataba.**

QUÉ PROTEGE ESTE ARCHIVO, en orden de importancia:
  1. Que `degraded` distinga una compactación sana de una que reinyecta el aviso de
     error. Es el campo que habría gritado en julio. Reproduje el defecto sobre una
     COPIA con la credencial muerta: 175 caracteres y `degraded=true`, contra 2.460
     y `false` en la sana.
  2. Que los HOOKS realmente llamen a la instrumentación. Probar el módulo por
     separado no prueba que esté conectado — me mordió tres veces el mismo día.
  3. Que medir NUNCA pueda tumbar una compactación. Toda función falla en silencio.
     Es la lección del medidor de ALICE: darle una dependencia a un vigilante puede
     ser peor que no tener el dato.

Owner: ADA — 4-sep-2026.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MEM = Path(os.environ.get("SEAL_METRICS_MODULE_DIR", str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(MEM))

from compaction_metrics import (  # noqa: E402
    SENAL_DEGRADADA, construir, contar_transcript, es_degradado, registrar,
)


class MetricasDeCompactacion(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    # --- 1. el campo que importa -----------------------------------------
    def test_degradado_reconoce_el_aviso_de_base_caida(self):
        """La firma EXACTA del defecto que estuvo semanas invisible."""
        real = ("⚡ POST-COMPACTACIÓN — ADA — contexto crítico restaurado desde SOUL\n\n"
                "(SOUL DB no disponible: password authentication failed for user \"seal\")\n")
        self.assertTrue(es_degradado(real))

    def test_control_una_compactacion_SANA_no_se_marca_degradada(self):
        """CONTROL de no-vacuidad: si es_degradado devolviera siempre True, el caso
        de arriba pasaría por la razón equivocada. Las dos ramas asertan."""
        sana = ("⚡ POST-COMPACTACIÓN — ADA\n\nPEDIDO ORIGINAL de esta sesión:\n"
                "  ▸ arreglá el candado\n\nCorrecciones de William:\n  • no afirmar sin verificar\n"
                "\nEstado del equipo:\n  ADA: ALIVE\n")
        self.assertFalse(es_degradado(sana))
        for vacio in ("", None):
            with self.subTest(v=repr(vacio)):
                self.assertFalse(es_degradado(vacio))

    # --- 2. el cableado ---------------------------------------------------
    def test_el_hook_POST_registra_de_verdad(self):
        """Probar el módulo no prueba que el hook lo llame."""
        destino = self.tmp / "post.jsonl"
        proc = subprocess.run(
            [sys.executable, str(MEM / "post_compact_session_start_hook.py")],
            input="{}", capture_output=True, text=True, timeout=60,
            env={**os.environ, "SEAL_AGENT": "ADA", "SEAL_COMPACTION_METRICS": str(destino)},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[:300])
        self.assertTrue(destino.exists(), "el hook post NO escribió ninguna métrica")
        reg = json.loads(destino.read_text(encoding="utf-8").strip().splitlines()[-1])
        self.assertEqual(reg["fase"], "post")
        self.assertIn("degraded", reg)
        self.assertIn("reinjected_chars", reg)
        self.assertGreater(reg["reinjected_chars"], 0)

    def test_el_hook_PRE_registra_de_verdad(self):
        destino = self.tmp / "pre.jsonl"
        transcript = self.tmp / "t.jsonl"
        transcript.write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "arreglá el candado"}}) + "\n",
            encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(MEM / "pre_compact_hook.py")],
            input=json.dumps({"transcript_path": str(transcript), "trigger": "auto"}),
            capture_output=True, text=True, timeout=90,
            env={**os.environ, "SEAL_AGENT": "ADA", "SEAL_COMPACTION_METRICS": str(destino)},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr[:300])
        self.assertTrue(destino.exists(), "el hook pre NO escribió ninguna métrica")
        reg = json.loads(destino.read_text(encoding="utf-8").strip().splitlines()[-1])
        self.assertEqual(reg["fase"], "pre")
        self.assertEqual(reg["messages_before"], 1)
        self.assertTrue(reg["original_request_captured"])
        self.assertIsInstance(reg["duration_ms"], int)

    # --- 3. medir no puede romper ----------------------------------------
    def test_medir_nunca_levanta_una_excepcion(self):
        """Un vigilante que revienta es peor que uno que no mide."""
        self.assertEqual(contar_transcript(None), {"messages_before": 0, "bytes_before": 0})
        self.assertEqual(contar_transcript(self.tmp / "no_existe.jsonl"),
                         {"messages_before": 0, "bytes_before": 0})
        self.assertFalse(registrar({"x": 1}, "/proc/no/se/puede/escribir.jsonl"))
        self.assertTrue(registrar({"x": 1}, self.tmp / "sub" / "dir" / "m.jsonl"))

    def test_el_conteo_cuenta_lineas_reales_y_no_las_vacias(self):
        t = self.tmp / "c.jsonl"
        t.write_text('{"a":1}\n\n   \n{"b":2}\n', encoding="utf-8")
        self.assertEqual(contar_transcript(t)["messages_before"], 2)

    def test_construir_no_mete_campos_nulos(self):
        r = construir("pre", "ADA", duration_ms=5, cosa=None)
        self.assertNotIn("cosa", r)
        self.assertEqual(r["fase"], "pre")
        self.assertEqual(r["agente"], "ADA")
        self.assertIn("ts", r)
        self.assertEqual(construir("post", "")["agente"], "desconocido")

    def test_la_senal_es_la_firma_real_del_defecto(self):
        """Si alguien cambia el texto del aviso en el hook, esto queda desalineado."""
        fuente = (MEM / "post_compact_session_start_hook.py").read_text(encoding="utf-8")
        self.assertIn(SENAL_DEGRADADA, fuente,
                      "la señal ya no coincide con lo que emite el hook: degraded quedaría ciego")


if __name__ == "__main__":
    unittest.main(verbosity=2)
