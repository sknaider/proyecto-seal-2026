"""El corte de bucles avisa a las 3 y corta a la 6, y NUNCA deja mudo a nadie.

POR QUE EXISTE: el 2-may-2026 el bucle de reflejos de NEXUS publico 199 mensajes en
60 segundos y lo corto JARVIS a mano matando el proceso. Idea de IBM Bob reescrita
desde cero (licencia 5900-BVU: se estudia el diseno, no se copia el codigo).

LO QUE ESTA SUITE PROTEGE NO ES EL CORTE: es que el corte NO SILENCIE A NADIE.
El 1-sep nuestro candado destructivo dejo mudos a ALICE, a JARVIS y dos veces a
NEXUS por limpiar temporales. Un guard en el UNICO escritor del equipo puede hacer
exactamente el mismo dano, y por eso la mayoria de los casos de abajo son de
contencion, no del camino feliz:

  * un mensaje DISTINTO pasa siempre, aunque otro este cortado
  * la salida de emergencia lo desactiva por completo
  * falla ABIERTO: estado corrupto, ilegible o no escribible -> se manda igual
  * fuera de la ventana el contador se reinicia

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

DIR = Path(os.environ.get("SEAL_LOOP_GUARD_DIR", str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(DIR))

from seal_loop_guard import (  # noqa: E402
    AVISO_EN, CORTA_DESDE, VENTANA_SEG, evaluar, huella,
)


class CorteDeBucles(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.estado = Path(self._t.name) / "g.json"
        os.environ["SEAL_LOOP_GUARD_STATE"] = str(self.estado)
        os.environ.pop("SEAL_SEND_NO_LOOP_GUARD", None)

    def tearDown(self):
        os.environ.pop("SEAL_LOOP_GUARD_STATE", None)
        self._t.cleanup()

    def _n(self, veces, cuerpo="idem", dest="William", canal="web_chat", t0=1000.0):
        out = []
        for i in range(veces):
            out.append(evaluar(dest, canal, cuerpo, "ADA", ahora=t0 + i))
        return out

    # --- los dos escalones -------------------------------------------------
    def test_los_dos_escalones_en_orden(self):
        acc = [a for a, _, _ in self._n(7)]
        self.assertEqual(acc[:AVISO_EN - 1], ["enviar"] * (AVISO_EN - 1))
        self.assertEqual(acc[AVISO_EN - 1], "avisar")
        self.assertEqual(acc[CORTA_DESDE - 1], "suprimir")
        self.assertEqual(acc[-1], "suprimir")

    def test_la_secuencia_COMPLETA_esta_fijada_escalon_por_escalon(self):
        """El borde de la 5a copia no estaba sujetado, y ahi el modulo decide entre
        AVISAR y SILENCIAR.

        Lo encontro NEXUS revisando: `CORTA_DESDE = 6 -> 5` SOBREVIVIA. Mi otro brazo
        comprobaba "antes/despues" y no el escalon exacto, asi que un refactor podia
        correr el borde una posicion y callar la quinta copia sin que nada fallara.
        Mi propia especificacion dice que la 5a «avisa fuerte y SALE».
        """
        self.assertEqual([a for a, _, _ in self._n(7)],
                         ["enviar", "enviar", "avisar", "avisar", "avisar",
                          "suprimir", "suprimir"])

    def test_el_aviso_dice_que_hacer_no_solo_que_pasa(self):
        _, _, aviso = self._n(AVISO_EN)[-1]
        self.assertIn("cambia de enfoque", aviso.lower())
        _, _, corte = self._n(CORTA_DESDE)[-1]
        self.assertIn("SEAL_SEND_NO_LOOP_GUARD=1", corte)

    # --- contencion: lo que NO puede pasar ---------------------------------
    def test_un_mensaje_DISTINTO_pasa_aunque_otro_este_cortado(self):
        """El riesgo real del guard: silenciar contenido nuevo."""
        self._n(CORTA_DESDE + 2)
        for campo, args in (("cuerpo", ("William", "web_chat", "OTRO texto")),
                            ("destinatario", ("JARVIS", "web_chat", "idem")),
                            ("canal", ("William", "dm:ada:william", "idem"))):
            with self.subTest(cambia=campo):
                self.assertEqual(evaluar(*args, "ADA", ahora=1010.0)[0], "enviar")

    def test_la_salida_de_emergencia_lo_desactiva(self):
        """OJO: el `ahora` explicito NO es cosmetico.

        Mi primera version llamaba con ahora=None, o sea tiempo real, y la ventana
        de 120 s ya habia expirado respecto del t0=1000 de la preparacion: el
        contador valia 1 y devolvia "enviar" con flag o sin flag. Pasaba por la
        razon equivocada y un mutante que ignoraba el flag SOBREVIVIA. Lo cazo la
        mutacion, no yo.
        """
        self._n(CORTA_DESDE + 3)
        sin_flag = evaluar("William", "web_chat", "idem", "ADA", ahora=1010.0)
        self.assertEqual(sin_flag[0], "suprimir", "la preparacion no dejo el contador en zona de corte")
        os.environ["SEAL_SEND_NO_LOOP_GUARD"] = "1"
        try:
            self.assertEqual(evaluar("William", "web_chat", "idem", "ADA", ahora=1011.0)[0], "enviar")
        finally:
            os.environ.pop("SEAL_SEND_NO_LOOP_GUARD", None)

    def test_falla_ABIERTO_con_estado_roto_o_no_escribible(self):
        self.estado.write_text("esto no es json", encoding="utf-8")
        self.assertEqual(evaluar("William", "web_chat", "x", "ADA")[0], "enviar")
        os.environ["SEAL_LOOP_GUARD_STATE"] = "/proc/no/se/puede/escribir.json"
        for _ in range(CORTA_DESDE + 2):
            self.assertEqual(evaluar("William", "web_chat", "y", "ADA")[0], "enviar")

    def test_falla_ABIERTO_tambien_ante_una_excepcion_INESPERADA(self):
        """El `except` EXTERNO, que es el candado 1, no lo tocaba ningun caso.

        El json corrupto lo absorbe `_leer` y la ruta no escribible la absorbe el
        write interno, asi que el manejador de ultimo recurso quedaba sin probar y
        un mutante que lo cambiaba a "suprimir" SOBREVIVIA. Un NUL en la ruta hace
        estallar a Path() y llega hasta el.
        """
        import seal_loop_guard as glg
        original = glg.huella

        def revienta(*_a, **_k):
            raise RuntimeError("fallo inesperado dentro del guard")

        glg.huella = revienta
        try:
            for _ in range(CORTA_DESDE + 2):
                self.assertEqual(glg.evaluar("William", "web_chat", "z", "ADA")[0], "enviar",
                                 "el guard fallo CERRADO ante una excepcion inesperada")
        finally:
            glg.huella = original
        # y despues de restaurar, sigue funcionando: el parche no dejo residuo
        self.assertEqual(glg.evaluar("William", "web_chat", "w", "ADA")[0], "enviar")

    def test_fuera_de_la_ventana_el_contador_se_reinicia(self):
        self._n(CORTA_DESDE + 1)
        a, n, _ = evaluar("William", "web_chat", "idem", "ADA", ahora=1000.0 + VENTANA_SEG + 10)
        self.assertEqual((a, n), ("enviar", 1))

    def test_agentes_distintos_no_se_suman(self):
        self._n(CORTA_DESDE + 1)
        self.assertEqual(evaluar("William", "web_chat", "idem", "JARVIS", ahora=1005.0)[0], "enviar")

    # --- control de no-vacuidad -------------------------------------------
    def test_control_la_huella_distingue_los_tres_campos(self):
        """Si huella() ignorara un campo, los casos de contencion pasarian por la
        razon equivocada: cualquier cambio los dejaria pasar igual."""
        base = huella("William", "web_chat", "hola")
        self.assertEqual(base, huella("William", "web_chat", "hola"))
        for otro in (huella("JARVIS", "web_chat", "hola"),
                     huella("William", "dm:ada:william", "hola"),
                     huella("William", "web_chat", "chau")):
            self.assertNotEqual(base, otro)

    # --- cableado ----------------------------------------------------------
    def test_el_escritor_REAL_respeta_la_supresion(self):
        """Probar el modulo no prueba que seal_send.py lo llame.

        Lleva el contador a la zona de corte y despues invoca el escritor de
        verdad: si el cableado existe, sale con `suppressed` y NO publica nada.
        """
        # La identidad NO se hardcodea: un revisor que corra esto como ADA estaria
        # revisando con la identidad del OWNER. Lo marco NEXUS, y es el mismo punto
        # que yo le marque a el en su delivery dos horas antes.
        ag = os.environ.get("SEAL_TEST_AGENT", os.environ.get("SEAL_AGENT", "ADA"))
        cuerpo = f"canario de cableado del corte de bucles ({ag})"
        for i in range(CORTA_DESDE):
            evaluar(ag, "web_chat", cuerpo, ag)
        proc = subprocess.run(
            [sys.executable, str(DIR / "seal_send.py"), ag, ag, cuerpo,
             "--channel", "web_chat", "--type", "conversation",
             "--idempotency-key", "test-cableado-corte-bucles"],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "SEAL_AGENT": ag, "SEAL_LOOP_GUARD_STATE": str(self.estado)},
        )
        self.assertEqual(proc.returncode, 0, "suprimir NO puede romper al llamador")
        self.assertIn("suppressed", proc.stdout, "el escritor no aplico el corte: cableado ausente")
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertTrue(payload.get("suppressed"))
        self.assertEqual(payload.get("reason"), "loop_guard")
        self.assertNotIn('"id"', proc.stdout, "publico igual: la supresion no evito el POST")


if __name__ == "__main__":
    unittest.main(verbosity=2)
