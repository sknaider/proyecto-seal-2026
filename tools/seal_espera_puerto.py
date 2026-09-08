#!/usr/bin/env python3
"""Espera a que un puerto TCP acepte conexiones, o falla con un mensaje claro.

POR QUÉ EXISTE (NEXUS, 8-sep-2026, carril asignado por JARVIS):
`seal-mcp-web-soul-operator` acumuló 87 reinicios entre el 27-ago y el 7-sep.
Contados por causa:

    78x  ConnectionRefusedError: Connect call failed ('127.0.0.1', 5433)
     4x  asyncpg CannotConnectNowError: the database system is starting up
     ---
     82 de 87  =  arranca ANTES que Postgres
     2x  sqlite3 disk I/O error        (dos veces en 3 segundos, una sola vez)
     1x  witness: cannot rollback

La unidad ordena con `After=/Requires=seal-audit-witness.service` y su único
`ExecStartPre` prueba el socket del witness — **nada espera a la base**. Con
`Restart=on-failure` y `RestartSec=2` eso es un bucle corto hasta que Postgres
levanta. El patrón correcto ya estaba en la propia unidad; faltaba el equivalente
para el puerto.

DECISIONES QUE PARECEN DETALLES Y NO LO SON:

* **Falla al agotar el plazo, no sigue de largo.** Si la base no está en el plazo
  hay un problema real y el servicio debe fallar ruidosamente; lo que se evita es
  el bucle de 78 reintentos, no el fallo.
* **Conectar y cerrar, sin hablar el protocolo.** Que el puerto acepte TCP no
  prueba que Postgres esté listo para consultas —`the database system is starting
  up` se responde con el puerto ya abierto—, por eso hay `--reintentos-tras-abrir`:
  tras la primera conexión sigue probando un momento más. No lo llamo
  "readiness de Postgres" porque no lo es: es una espera de puerto con margen.
* **No imprime lo que recibe.** Nunca lee del socket: si apuntara a algo que
  saluda con datos sensibles, no los toca.
"""
from __future__ import annotations

import argparse
import socket
import sys
import time


def espera(host: str, puerto: int, timeout: float, intervalo: float = 0.5,
           margen: float = 0.0, reloj=time.monotonic, dormir=time.sleep) -> dict:
    """Intenta conectar hasta `timeout` segundos.

    Devuelve {"ok", "intentos", "segundos", "motivo"}. `margen` mantiene el
    sondeo un rato más DESPUÉS de la primera conexión exitosa: el puerto abre
    antes de que la base acepte consultas.
    """
    if timeout <= 0:
        return {"ok": False, "intentos": 0, "segundos": 0.0, "motivo": "timeout_no_positivo"}

    inicio = reloj()
    intentos = 0
    abierto_en = None

    while True:
        intentos += 1
        try:
            with socket.create_connection((host, puerto), timeout=min(intervalo * 4, 5.0)):
                pass
            if abierto_en is None:
                abierto_en = reloj()
            if reloj() - abierto_en >= margen:
                return {"ok": True, "intentos": intentos,
                        "segundos": round(reloj() - inicio, 2), "motivo": "abierto"}
        except OSError:
            # una conexión que se cae DESPUÉS de haber abierto reinicia el margen:
            # es exactamente el caso de una base todavía arrancando.
            abierto_en = None

        if reloj() - inicio >= timeout:
            return {"ok": False, "intentos": intentos,
                    "segundos": round(reloj() - inicio, 2),
                    "motivo": "abierto_pero_inestable" if abierto_en is not None else "timeout"}
        dormir(intervalo)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("host")
    ap.add_argument("puerto", type=int)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--intervalo", type=float, default=0.5)
    ap.add_argument("--reintentos-tras-abrir", dest="margen", type=float, default=1.0,
                    help="segundos que sigue sondeando tras la primera conexion")
    a = ap.parse_args(argv)

    r = espera(a.host, a.puerto, a.timeout, a.intervalo, a.margen)
    if r["ok"]:
        print(f"{a.host}:{a.puerto} acepta conexiones "
              f"({r['intentos']} intento(s), {r['segundos']}s)")
        return 0
    print(f"{a.host}:{a.puerto} NO acepta conexiones tras {r['segundos']}s "
          f"({r['intentos']} intento(s), motivo={r['motivo']})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
