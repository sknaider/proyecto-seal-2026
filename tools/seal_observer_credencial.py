"""Lectura de la credencial del observador de Postgres — sin dependencias.

POR QUÉ EXISTE ESTE ARCHIVO (NEXUS, 8-sep-2026): la lógica vivía dentro de
`soul_postgres_mcp.py`, que importa FastMCP en el tope. `soul_metrics_exporter`
necesitaba exactamente la misma lectura, pero su unidad corre con
`/usr/bin/python3`, donde el paquete `mcp` NO está instalado: cargar aquel
módulo para reusar una función de 20 líneas fallaba con ModuleNotFoundError.
La alternativa era copiar el código, y las copias se borran (William, 7-sep).
Este módulo no importa nada fuera de la biblioteca estándar, a propósito.
"""
from __future__ import annotations

import os
import pathlib
import stat

RUTA_POR_DEFECTO = pathlib.Path.home() / ".config/seal/mcp_postgres_observer.env"
CLAVES_DSN = ("POSTGRES_MCP_DSN", "SEAL_PG_DSN", "DATABASE_URL", "PG_DSN")


def leer_dsn_del_archivo(ruta: pathlib.Path | None = None) -> str:
    """DSN del archivo del observer, o cadena vacía si no se puede leer.

    OJO CON EL DEFAULT (7-sep 19:26, costó una credencial): la primera versión
    escribía `ruta: pathlib.Path = RUTA_POR_DEFECTO`. Un default se evalúa UNA
    VEZ, al DEFINIR la función, así que apuntaba al archivo real para siempre y
    `monkeypatch` sobre el módulo no lo movía: un test que creía leer su señuelo
    leyó el archivo VIVO y pytest imprimió la contraseña del observer en el diff
    del assert. Se resuelve en cada llamada, que además es lo que permite rotar
    la credencial sin reiniciar el proceso.

    DEVUELVE CADENA VACÍA, NO LEVANTA, cuando el archivo no existe o no se puede
    leer. Quien la llame debe chequear el vacío explícitamente: un `except` no
    alcanza. (NEXUS, 8-sep: el exporter devolvía "" en silencio y el fallo
    reaparecía después disfrazado de error de conexión.)
    """
    if ruta is None:
        ruta = RUTA_POR_DEFECTO
    try:
        info = ruta.stat()
    except OSError:
        return ""
    # ADA, 7-sep 19:29: que hoy tenga 0600 es evidencia del DESPLIEGUE, no garantía
    # del CÓDIGO. El stability guard exige modo 600 sobre este archivo; el lector
    # debe exigir lo mismo, o el día que alguien lo afloje nadie se entera desde acá.
    if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
        raise RuntimeError(
            f"{ruta}: la credencial del observer debe ser modo 600 y del usuario actual "
            f"(modo={stat.S_IMODE(info.st_mode):o}, uid={info.st_uid})"
        )
    try:
        crudo = ruta.read_text(encoding="utf-8")
    except OSError:
        return ""
    for linea in crudo.splitlines():
        linea = linea.strip()
        for clave in CLAVES_DSN:
            if linea.startswith(clave + "="):
                return linea.split("=", 1)[1].strip().strip('"').strip("'")
    return ""
