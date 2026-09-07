"""ARM 3 del delivery de runtime-instance-passthrough: SOLO esa clave persiste.

LO LEVANTO ADA REVISANDO, Y TENIA RAZON. ARM1 y ARM2 prueban presencia y
ausencia, y el mutante `metadata.update(body["metadata"])` los pasa LOS DOS: si
el cliente manda unicamente `runtime_instance`, copiar el diccionario entero
produce exactamente ARM1 correcto y ARM2 ausente. Mi evidencia afirmaba que el
ARM2 cubria ese caso. Era falso y no lo habia medido.

POR QUE NO USA seal_send.py: el wrapper construye literalmente
`{"runtime_instance": _inst}`, asi que NO PUEDE expresar el caso bajo prueba
--mandar una clave de mas--. Este arnes emite el body a mano, y sigue
autenticado con la sesion del AGENTE QUE CORRE, no la del owner.

La clave de mas es inventada y sin efecto: no se intenta pisar ningun campo de
procedencia. La propiedad que se afirma es negativa --que NO aparezca-- asi que
no hace falta atacar un campo real para medirla.
"""
import json
import os
import pathlib
import sys
import urllib.request

import asyncio
import asyncpg

RAIZ = pathlib.Path(__file__).resolve().parents[1]
API = "http://localhost:8765/api/agents/send"
CLAVE_EXTRA = "clave_que_no_debe_persistir"


def enviar(agente: str, ikey: str) -> str:
    token = (RAIZ / "messages" / f".agent_session_token_{agente.upper()}").read_text(
        encoding="utf-8"
    ).strip()
    cuerpo = {
        "from": agente,
        "to": agente,
        "type": "dm",
        "channel": "web_chat",
        "message": f"canario runtime_instance ARM3 ({ikey})",
        "session_key": token,
        "idempotency_key": ikey,
        "metadata": {"runtime_instance": f"{agente}_CANARIO", CLAVE_EXTRA: ikey},
    }
    req = urllib.request.Request(
        API, data=json.dumps(cuerpo).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        r = json.loads(urllib.request.urlopen(req).read().decode())
        return "enviado" if r.get("ok") else f"error:{r.get('error')}"
    except Exception as exc:  # noqa: BLE001 - el mensaje del error es el diagnostico
        return f"error:{type(exc).__name__}"


async def _leer(ikey: str) -> tuple[str, str]:
    c = await asyncpg.connect(os.environ["SEAL_PG_DSN"])
    r = await c.fetchrow(
        "SELECT metadata FROM soul_v3.chat_messages WHERE metadata->>'idempotency_key' = $1",
        ikey,
    )
    await c.close()
    if not r:
        return ("<sin fila>", "<sin fila>")
    md = r["metadata"] if isinstance(r["metadata"], dict) else json.loads(r["metadata"])
    return (md.get(CLAVE_EXTRA, "<ausente>"), md.get("runtime_instance", "<ausente>"))


def main() -> int:
    agente, ikey = sys.argv[1], sys.argv[2]
    print(f"  ARM3 envio                     -> {enviar(agente, ikey)}")
    import time

    time.sleep(1)
    extra, rt = asyncio.run(_leer(ikey))
    print(f"  ARM3 clave de mas en la fila   -> {extra}")
    # CONTROL de que el negativo es REAL y no un envio fallido: el MISMO body
    # llevaba runtime_instance, y esa clave SI tiene que estar. Sin este control,
    # un mensaje que nunca llego --o un metadata descartado entero-- daria
    # "<ausente>" y se leeria como exito.
    print(f"  ARM3 control runtime_instance  -> {rt}")
    if rt != f"{agente}_CANARIO":
        print("    el body no llego o el metadata se descarto entero: el negativo NO vale")
        return 1
    if extra != "<ausente>":
        print("    la clave del cliente SI persistio: el servidor copia el metadata")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
