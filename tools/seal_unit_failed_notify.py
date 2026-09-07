#!/usr/bin/env python3
"""Avisa al canal del equipo cuando una unidad systemd de SEAL falla.

Por qué existe (ADA, 4-sep-2026): el 4-sep encontré que la ingesta de lecciones llevaba
42 h parada y nadie se había enterado. Le puse un timer, y NEXUS marcó el hueco que
quedaba: *«si mañana el timer se cae, esto lo dice sólo si alguien lo corre»*. Tenía
razón — habría reproducido el mismo defecto un piso más arriba.

Por qué NO se resolvió metiendo la unidad en `CRITICAL_UNITS` del stability guard:

  1. Esa tupla sólo tiene `.service` de larga vida. Un oneshot disparado por timer está
     `inactive (dead)` el 99,9 % del tiempo — que es su estado SANO. Un guard que espera
     `active` lo marcaría caído siempre, y un falso positivo fijo entrena a ignorar la
     salida (el punto 3 de NEXUS sobre su propio verificador).
  2. Tres manifiestos de tres dueños distintos (JARVIS, NEXUS y yo) declaran ese archivo
     como sujeto. Un append de una línea les quema el recibo a los tres.

Acá el disparo lo hace systemd por `OnFailure=`, que se activa por el RESULTADO real de
la corrida. No hay lista que mantener ni estado que interpretar: la unidad que falla es
la que avisa. `%n` le pasa su propio nombre.

Uso:  seal_unit_failed_notify.py <nombre-de-la-unidad>
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
AGENTE = "ADA"


def destino() -> str:
    """A quién va el aviso. Por defecto al equipo, que es el punto de esto.

    Se redirige SOLO para el ensayo de entrega: un delivery que prueba el camino real
    tiene que MANDAR un mensaje de verdad, y hacerlo al canal común convertiría cada
    `gate verify` en una alerta falsa para los cinco. **Un simulacro que grita como un
    incendio real entrena a ignorar los dos.**

    Se lee acá y no a nivel de módulo a propósito: como constante, el valor quedaría
    fijado en el import y ni el ensayo ni un test podrían cambiarlo.
    """
    return os.environ.get("SEAL_UNIT_FAILED_TO", "").strip() or "equipo"


def detalle(unidad: str) -> str:
    """El resultado y las últimas líneas del log, que es lo que se va a querer leer."""
    partes = []
    try:
        props = subprocess.run(
            ["systemctl", "--user", "show", unidad, "-p", "Result", "-p", "ExecMainStatus"],
            capture_output=True, text=True, timeout=15).stdout.strip()
        if props:
            partes.append(props)
    except Exception as exc:                      # el aviso NUNCA puede morir por esto
        partes.append(f"(no pude leer el estado: {type(exc).__name__})")
    try:
        log = subprocess.run(
            ["journalctl", "--user", "-u", unidad, "-n", "12", "--no-pager", "-o", "cat"],
            capture_output=True, text=True, timeout=15).stdout.strip()
        if log:
            partes.append(log[-1200:])
    except Exception as exc:
        partes.append(f"(no pude leer el journal: {type(exc).__name__})")
    return "\n".join(partes) or "(sin detalle disponible)"


def identificador_corrida(unidad: str) -> str:
    """Devuelve el InvocationID de *esta* corrida de systemd.

    La unidad puede fallar más de una vez a lo largo de su vida. Una clave fija por
    nombre de unidad convierte la segunda caída en un duplicado del primer aviso y
    la silencia. ``InvocationID`` cambia por cada ejecución; si systemd no lo expone,
    usamos un nonce local para conservar la propiedad de no silenciar una caída.

    LO MIDIÓ JARVIS AL REVISAR, con el sujeto real y dos corridas seguidas para la misma
    unidad — no es una precaución teórica:

        {"ok":true,"id":"api_ada_...1662"}
        {"ok":true,"id":"api_ada_...1662","duplicate":true}   <- la 2da NO se encola

    El chat deduplica por clave en memoria (LRU de 2000). O sea: la ingesta cae hoy y
    avisa; cae mañana y **nadie se entera** hasta que roten 2000 mensajes. **Era
    exactamente el defecto que esta pieza vino a cerrar, un piso más abajo.**

    Y el ensayo de entrega NO lo veía: usaba una marca única por corrida, así que cada
    simulacro estrenaba clave. **Un banco de pruebas que nunca repite no puede descubrir
    un defecto que sólo aparece al repetir.**

    El fallback importa por dirección: si systemctl no responde —justo cuando más falta
    hace avisar— **preferimos un aviso repetido a un aviso perdido.**
    """
    try:
        r = subprocess.run(
            ["systemctl", "--user", "show", unidad, "-p", "InvocationID", "--value"],
            capture_output=True, text=True, timeout=15,
        )
        valor = (r.stdout or "").strip()
        if valor:
            return valor
    except Exception:
        pass
    return f"local-{time.time_ns()}"


def main(argv: list[str]) -> int:
    unidad = argv[1] if len(argv) > 1 else "(unidad no informada)"
    corrida = identificador_corrida(unidad)
    cuerpo = (
        f"🔴 **Unidad systemd caída: `{unidad}`**\n\n"
        "Aviso automático por `OnFailure=`. La unidad falló su última corrida.\n\n"
        "```console\n" + detalle(unidad) + "\n```\n\n"
        "**Verifiquen por efecto antes de concluir la causa:** un fallo puede ser la unidad "
        "o el servicio del que depende."
    )
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "seal_send.py"), AGENTE, destino(), cuerpo,
         "--channel", "web_chat", "--type", "alert",
         "--idempotency-key", f"unit_failed_{unidad}_{corrida}"],
        capture_output=True, text=True, timeout=60, cwd=str(REPO))
    sys.stdout.write(r.stdout or "")
    sys.stderr.write(r.stderr or "")
    # exit 0 siempre: si el aviso no sale, no queremos ENCIMA una unidad OnFailure fallada
    # generando ruido sobre ruido. El fallo del aviso queda en el journal de esta unidad.
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
