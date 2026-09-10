#!/usr/bin/env python3
"""Vigía de salud de gtl.pe — y, sobre todo, un vigía que no puede quedar mudo en silencio.

POR QUÉ EXISTE (NEXUS, 10-sep-2026, carril asignado por JARVIS):
El daemon anterior se perdió el 7-sep con el borrado del home y **nunca estuvo
versionado**: `git log --all` no lo encuentra en ninguna de las 16 ramas. Lo que
quedó fue una unidad systemd «reconstruida desde journal» con `ExecStart="(python3)"`,
que systemd carga sin error y que no puede ejecutar nada.

El watchdog que vigilaba a ESTE vigía sí funcionó: disparó 428 veces en tres días.
Se vio UNA. Su `--idempotency-key` estaba quemada como constante en el ExecStart, así
que el servidor respondía, correctamente:

    {"ok":true,"id":"api_nexus_1788808768010947272","duplicate":true}

**`ok:true`.** El emisor veía un envío exitoso las 428 veces. Nada fallaba, nada se
registraba en rojo, y gtl.pe estuvo tres días sin vigilancia mientras la alarma
"funcionaba".

DE AHÍ SALE LA REGLA CENTRAL DE ESTE ARCHIVO:

    Un envío con `duplicate:true` NO ESTÁ ENTREGADO.

Ese es el punto que `entregado()` codifica y que los brazos negativos protegen. Un
vigía que informa `ok:true` con el canal muerto es peor que no tener vigía: consume
la atención que otro vigía podría estar recibiendo.

DECISIONES QUE PARECEN DETALLES Y NO LO SON:

* **La clave de idempotencia se DERIVA del incidente**, nunca es constante: agrupa
  los reintentos de un mismo episodio y deja hablar al episodio siguiente.
* **El latido se escribe DESPUÉS de sondear**, no al arrancar: un latido que sólo
  prueba que el proceso vive no distingue un vigía sano de uno colgado.
* **Sólo se avisa en las TRANSICIONES**, no en cada sondeo. Un vigía que habla cada
  minuto entrena a todos a ignorarlo, que es la otra forma de quedarse mudo.
* **No se afirma que gtl.pe esté caído cuando el sondeo falla desde acá.** DNS que no
  resuelve, red local, proxy: eso es «no puedo saberlo», y se dice así. Confundir
  «está caído» con «dejé de poder verlo» fue el incidente del 28-jul.

  El estado se llama **`NO_MEDIBLE`, y el nombre es de ALICE**: el 10-sep ella escribió
  un vigía para gtl.pe en paralelo con éste —chocamos, quedó éste— y su script ya
  distinguía `OK` de `NO_MEDIBLE`. Los dos llegamos por separado a que «no responde» y
  «no puedo verlo» son cosas distintas; ella le puso la palabra que lo dice en una.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

RAIZ = pathlib.Path(__file__).resolve().parents[2]
LATIDO = RAIZ / "agents" / "NEXUS" / "gtl_salud_latido.json"
ESTADO = RAIZ / "agents" / "NEXUS" / "gtl_salud_estado.json"
DESTINO = "https://gtl.pe/"


# ─────────────────────── el corazón del carril: qué es "entregado" ───────────────

def entregado(respuesta: str | dict | None) -> bool:
    """¿El mensaje LLEGÓ, o el servidor lo agrupó con uno anterior?

    `ok:true` NO alcanza. El servidor responde `ok:true` también cuando descarta el
    envío por idempotencia —ahí devuelve el id del mensaje VIEJO y `duplicate:true`—,
    y eso fue lo que escondió 427 de 428 alarmas durante tres días.

    Una respuesta ilegible, vacía o sin `id` tampoco cuenta como entregada: ante la
    duda, un vigía asume que NO habló.
    """
    if respuesta is None:
        return False
    if isinstance(respuesta, str):
        try:
            respuesta = json.loads(respuesta)
        except Exception:
            return False
    if not isinstance(respuesta, dict):
        return False
    if respuesta.get("ok") is not True:
        return False
    if respuesta.get("duplicate") is True:
        return False          # <- la línea que faltaba en el mundo
    return bool(respuesta.get("id"))


def clave_de(incidente: str, cuando: _dt.datetime) -> str:
    """Clave de idempotencia DERIVADA del incidente, nunca constante.

    Agrupa los reintentos del mismo episodio dentro de la hora y deja pasar el
    episodio siguiente. Una constante quemada silencia todo lo que venga después.
    """
    return f"NEXUS-gtl-{incidente}-{cuando.strftime('%Y%m%dT%H')}"


# ───────────────────────────────── el sondeo ─────────────────────────────────────

def sondear(url: str = DESTINO, timeout: float = 10.0, abrir=None) -> dict:
    """Devuelve {"estado", "codigo", "detalle"}.

    `estado` es uno de: "arriba", "abajo", "NO_MEDIBLE".
    """
    abrir = abrir or urllib.request.urlopen
    try:
        with abrir(url, timeout=timeout) as r:
            codigo = getattr(r, "status", None) or r.getcode()
        if 200 <= int(codigo) < 400:
            return {"estado": "arriba", "codigo": int(codigo), "detalle": ""}
        return {"estado": "abajo", "codigo": int(codigo), "detalle": f"HTTP {codigo}"}
    except urllib.error.HTTPError as e:
        return {"estado": "abajo", "codigo": int(e.code), "detalle": f"HTTP {e.code}"}
    except Exception as e:
        # NO es "gtl.pe esta caido": es "no puedo saberlo desde aca".
        return {"estado": "NO_MEDIBLE", "codigo": None,
                "detalle": f"{type(e).__name__}: {e}"}


def leer_estado(ruta: pathlib.Path) -> dict:
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except Exception:
        return {}


def escribir_latido(ruta: pathlib.Path, medicion: dict, cuando: _dt.datetime) -> None:
    """El latido se escribe DESPUÉS de sondear y lleva el resultado adentro.

    Un latido que sólo dice «el proceso vive» no distingue un vigía sano de uno
    colgado contra un socket.
    """
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps({
        "cuando": cuando.isoformat(),
        "epoch": cuando.timestamp(),
        "estado": medicion["estado"],
        "codigo": medicion["codigo"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def enviar(texto: str, clave: str, ejecutor=None) -> str:
    ejecutor = ejecutor or (lambda argv: subprocess.run(
        argv, capture_output=True, text=True, timeout=60).stdout)
    ruta = RAIZ / "scripts" / "seal_send.py"
    tmp = pathlib.Path("/tmp") / f"nexus_gtl_alerta_{clave}.txt"
    tmp.write_text(texto, encoding="utf-8")
    return ejecutor([sys.executable, str(ruta), "NEXUS", "equipo",
                     "--message-file", str(tmp), "--channel", "web_chat",
                     "--type", "alert", "--idempotency-key", clave])


def corrida(url: str = DESTINO, ahora: _dt.datetime | None = None,
            estado_path: pathlib.Path | None = None,
            latido_path: pathlib.Path | None = None,
            sonda=None, ejecutor=None) -> dict:
    """Una pasada: sondea, late, y avisa SÓLO si hubo transición.

    Devuelve {"estado", "transicion", "aviso", "entregado"}. `entregado` es False
    cuando hubo que avisar y el mensaje no llegó — y ese caso NO se calla.
    """
    ahora = ahora or _dt.datetime.now(_dt.timezone.utc)
    estado_path = estado_path or ESTADO
    latido_path = latido_path or LATIDO

    medicion = (sonda or sondear)(url)
    escribir_latido(latido_path, medicion, ahora)

    previo = leer_estado(estado_path).get("estado")
    transicion = previo is not None and previo != medicion["estado"]
    primera = previo is None

    estado_path.parent.mkdir(parents=True, exist_ok=True)
    estado_path.write_text(json.dumps(
        {"estado": medicion["estado"], "desde": ahora.isoformat()},
        ensure_ascii=False, indent=2), encoding="utf-8")

    if not transicion:
        return {"estado": medicion["estado"], "transicion": False,
                "aviso": False, "entregado": None, "primera": primera}

    texto = _texto_transicion(previo, medicion, url, ahora)
    clave = clave_de(f"{previo}-a-{medicion['estado']}", ahora)
    llego = entregado(enviar(texto, clave, ejecutor))
    return {"estado": medicion["estado"], "transicion": True,
            "aviso": True, "entregado": llego, "primera": primera}


def _texto_transicion(previo: str, medicion: dict, url: str, ahora: _dt.datetime) -> str:
    if medicion["estado"] == "arriba":
        cabeza = f"✅ **gtl.pe volvió** (estaba `{previo}`)"
    elif medicion["estado"] == "abajo":
        cabeza = f"🔴 **gtl.pe NO responde** (estaba `{previo}`)"
    else:
        cabeza = (f"⚠️ **Dejé de poder ver gtl.pe** (estaba `{previo}`).\n\n"
                  f"**Esto NO dice que esté caído: dice que no puedo saberlo desde acá.**")
    return (f"{cabeza}\n\n```text\nurl       {url}\nestado    {medicion['estado']}\n"
            f"codigo    {medicion['codigo']}\ndetalle   {medicion['detalle']}\n"
            f"cuando    {ahora.isoformat()}\n```\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=DESTINO)
    ap.add_argument("--una-vez", action="store_true", help="una pasada y sale (para el timer)")
    a = ap.parse_args(argv)

    r = corrida(a.url)
    print(json.dumps(r, ensure_ascii=False))

    # Un aviso que no llego es un FALLO RUIDOSO. Es todo el punto de este archivo:
    # el vigia anterior devolvia exito 428 veces con el canal muerto.
    if r["aviso"] and not r["entregado"]:
        print("FALLO: hubo transicion y el aviso NO se entrego (canal mudo)", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
