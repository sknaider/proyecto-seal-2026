"""La firma del cuerpo que habla sobrevive el camino del stream en vivo.

POR QUE EXISTE (medido 4-sep-2026):
Un mensaje del cuerpo Claude publicado en el canal general salia con vineta
**Codex**, y al recargar la pagina se corregia solo. La vineta cambiaba sola y no
habia forma de saber por que.

Eran DOS causas independientes, y arreglar una sola no habria cambiado nada:

  servidor  la consulta del stream SSE extraia de `metadata` solo `file_url` y
            `filename`, asi que `runtime_instance` nunca salia por el vivo.
  cliente   el manejador del SSE rearmaba el mensaje campo por campo y `metadata`
            no estaba en la lista.

Sin la firma, la UI adivina por canal: todo lo que no sea el canal privado de
William se etiqueta "Codex". El historico si la trae, y por eso al recargar se
acomodaba.

QUE PROTEGE ESTE ARCHIVO (el lado servidor):
  1. Que la conversion NO se limite a seleccionar la columna. asyncpg devuelve
     `jsonb` como `str`; emitirlo tal cual le entrega al cliente una cadena y
     `metadata.runtime_instance` queda `undefined` **sin que nada falle a la vista**.
  2. Que un valor corrupto degrade a `{}` en vez de romper la pantalla: una
     etiqueta ausente es preferible a un feed caido.
  3. Que la columna `metadata` exista de verdad en la consulta que corre el
     stream, comprobado CONTRA LA BASE y no leyendo el fuente.

Owner: ADA — 4-sep-2026.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.environ.get("SEAL_STUDIO_BACKEND_DIR", str(Path(__file__).resolve().parent)))

from chat_contract import coerce_metadata, normalize_chat_message  # noqa: E402

def stream_sql() -> str:
    """LA consulta que corre el stream, importada del modulo — no una copia.

    Escribir aca una copia del SQL era el agujero: el test verificaba la conversion
    y pasaba en verde aunque `metadata` hubiera dejado de seleccionarse en el
    servidor. Probar la pieza no prueba que este conectada.
    """
    os.environ.setdefault("SEAL_STUDIO_DB_DSN",
                          "postgresql://svc_seal_studio:sintetico@127.0.0.1:1/x")
    import api_v1
    return api_v1.STREAM_MESSAGES_SQL


class FirmaDelCuerpoEnElStream(unittest.TestCase):
    def test_jsonb_como_texto_se_convierte_a_dict(self):
        """El caso que rompia: asyncpg entrega jsonb como str."""
        md = coerce_metadata('{"runtime_instance": "ADA_CLAUDE", "file_url": "/uploads/x.png"}')
        self.assertIsInstance(md, dict)
        self.assertEqual(md["runtime_instance"], "ADA_CLAUDE")

    def test_bytes_tambien_se_convierten(self):
        md = coerce_metadata(b'{"runtime_instance": "ADA_CODEX"}')
        self.assertEqual(md.get("runtime_instance"), "ADA_CODEX")

    def test_un_dict_pasa_intacto(self):
        original = {"runtime_instance": "ADA_V2", "otra": 1}
        self.assertEqual(coerce_metadata(original), original)

    def test_valor_corrupto_degrada_a_vacio_y_no_rompe(self):
        """Una etiqueta ausente es preferible a un feed caido."""
        for basura in ('no soy json', '', b'\xff\xfe', '[1,2,3]', '42', '"texto"', None, 7, [1, 2]):
            with self.subTest(valor=repr(basura)[:24]):
                self.assertEqual(coerce_metadata(basura), {})

    def test_el_mensaje_normalizado_conserva_la_firma(self):
        """El camino completo del servidor: fila de la base -> objeto que sale por SSE."""
        fila = {
            "id": 148959, "channel": "web_chat", "sender_name": "ADA",
            "content": "prueba", "message_type": "text",
            "metadata": '{"runtime_instance": "ADA_CLAUDE"}',
            "file_url": None, "filename": None,
            "created_at": datetime(2026, 9, 4, 19, 53, tzinfo=timezone.utc),
        }
        m = normalize_chat_message(fila)
        m["metadata"] = coerce_metadata(m.get("metadata"))
        emitido = json.loads(json.dumps(m))          # lo que viaja por el SSE
        self.assertEqual(emitido["metadata"]["runtime_instance"], "ADA_CLAUDE",
                         "la firma no sobrevive hasta el cliente")

    def test_control_sin_firma_no_se_inventa_una(self):
        """CONTROL de no-vacuidad: si coerce_metadata devolviera siempre algo con
        firma, los casos de arriba pasarian por la razon equivocada. Las dos ramas
        asertan: con firma aparece, sin firma NO aparece."""
        con = coerce_metadata('{"runtime_instance": "ADA_CLAUDE"}')
        sin = coerce_metadata('{"file_url": "/uploads/x.png"}')
        self.assertIn("runtime_instance", con)
        self.assertNotIn("runtime_instance", sin, "inventa una firma que la fila no tenia")
        self.assertEqual(sin.get("file_url"), "/uploads/x.png", "perdio el resto de la metadata")

    def test_el_stream_del_servidor_SI_selecciona_la_firma(self):
        """CABLEADO: la consulta real del modulo tiene que traer `metadata` completa.

        Si alguien la vuelve a dejar con solo file_url y filename, este brazo falla.
        """
        sql = stream_sql()
        self.assertIn("metadata, ", sql,
                      "la consulta del stream no selecciona metadata completa: la firma no sale por el vivo")
        self.assertIn("metadata->>'file_url'", sql, "se perdio el file_url del stream")

    def test_la_consulta_del_stream_corre_contra_la_base(self):
        """Comprobado CONTRA LA BASE, no leyendo el fuente.

        Si alguien vuelve a dejar la consulta con solo file_url y filename, este
        brazo no lo caza —para eso estan los mutantes—, pero si caza el supuesto
        del que depende todo: que la columna exista y traiga jsonb convertible.
        """
        import asyncio
        import asyncpg

        env = Path.home() / ".config/seal/ada_bridge_db_runtime.env"
        if not env.exists():
            self.skipTest("sin credencial local de lectura")
        dsn = next(l.split("=", 1)[1].strip() for l in env.read_text().splitlines()
                   if "DSN" in l or l.startswith("DATABASE_URL="))

        async def leer():
            conn = await asyncpg.connect(dsn)
            try:
                return await conn.fetch(stream_sql(), "web_chat", 0)
            finally:
                await conn.close()

        filas = asyncio.run(leer())
        self.assertTrue(filas, "el canal general no devolvio filas")
        for r in filas:
            self.assertIn("metadata", r.keys(), "la consulta del stream no trae la columna metadata")
            self.assertIsInstance(coerce_metadata(r["metadata"]), dict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
