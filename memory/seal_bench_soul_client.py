#!/usr/bin/env python3
"""Puente del benchmark hacia SOUL de verdad: MCP sobre HTTP, autenticado.

**Por qué existe (ADA, 9-sep-2026, orden de William «reparar la evaluación»).**

SEAL-Bench no medía SOUL: medía una copia de SOUL que vivía dentro del propio
benchmark. `seal_bench.py::_search_memories` tiene su propio SQL, su propio filtro
de scope y su propia fórmula de ranking, y `seal_bench_v3/v4` la importan. Medido
ese día, la copia ya se había separado del original en la dirección peligrosa:

    falta el parametro 'agent'      copia del bench    SOUL de verdad
                                    'if agent:'         [PRIVACY] blocked
                                    -> no filtra NADA

Por eso el test «v3.5 Multi-Agent Isolation Attack» reportaba una fuga CRÍTICA de
memoria privada que **SOUL no tiene**: la fuga era de la copia. Un benchmark que
reimplementa a su sujeto no puede detectar una regresión del sujeto, y encima
alarma sobre defectos propios.

**Cómo se le pregunta a SOUL de verdad.** El servicio expone MCP por HTTP
(`streamable-http`, puerto 8771) y desde el 9-sep corre en modo `ENFORCE`: la
identidad se acredita con el **token bearer del agente**, no con un nombre. Un
llamador en proceso NO puede autenticarse —`_session_token_from_request()` lee la
cabecera `Authorization` de la petición MCP, y sin petición devuelve `None`, o sea
`external`—. Así que el puente tiene que ser un cliente HTTP real, igual que un
agente. Eso es además lo que queremos medir: lo que un agente experimenta.

**Qué NO hace este módulo, a propósito:**

- No lee el token de otro agente. Todos los `.token` son legibles por el mismo uid,
  y eso hace que un token no acredite identidad ENTRE nosotros; usar el de otro
  para «probar aislamiento» mediría el permiso del filesystem, no el de SOUL.
  El ataque cruzado se hace con la identidad propia contra el dato ajeno, que es
  exactamente la forma real del ataque.
- No imprime, registra ni devuelve el token, ni un prefijo suyo.
- No inventa un modo degradado. Si SOUL no está, esto levanta `SoulNoAlcanzable`
  y el test debe ponerse en ERROR — nunca pasar. Un benchmark que se va a una
  copia cuando el sujeto no responde es cómo llegamos hasta acá.
"""
from __future__ import annotations

import os
import pathlib
from typing import Any

DEFAULT_URL = os.environ.get("SEAL_MCP_URL", "http://127.0.0.1:8771/mcp")
DEFAULT_TOKEN_DIR = os.environ.get("SEAL_TOKENS_DIR", "/run/user/1000/seal")


class SoulNoAlcanzable(RuntimeError):
    """SOUL no respondió. El benchmark debe fallar ruidosamente, no estimar."""


def _token_del_agente(agent: str, token_dir: str = DEFAULT_TOKEN_DIR) -> str:
    """Devuelve el token del agente. El valor nunca sale de acá hacia un log."""
    ruta = pathlib.Path(token_dir) / f"{agent}.token"
    try:
        valor = ruta.read_text().strip()
    except OSError as exc:
        raise SoulNoAlcanzable(
            f"no hay credencial para {agent} en {token_dir} ({exc.__class__.__name__})"
        ) from None
    if not valor:
        raise SoulNoAlcanzable(f"la credencial de {agent} está vacía")
    return valor


async def preguntar_a_soul(
    agent: str,
    tool: str,
    args: dict[str, Any],
    *,
    url: str | None = None,
    token_dir: str | None = None,
) -> tuple[bool, str]:
    """Llama una herramienta de SOUL como `agent`. Devuelve (es_error, texto).

    `es_error=True` es un resultado legítimo y esperado: una negación de privacidad
    ES la respuesta correcta a un ataque. Por eso no se levanta excepción — el
    llamador decide si esa negación es éxito o fracaso del test. Sólo un SOUL
    inalcanzable levanta `SoulNoAlcanzable`.

    `url` y `token_dir` se resuelven ACÁ ADENTRO, no en la firma. Escritos como
    `url: str = DEFAULT_URL` quedaban congelados en el instante del import, y un
    refutador que apuntó el módulo a un puerto muerto siguió hablando con el SOUL
    de verdad: el test dio 100 % «con SOUL caído». Lo encontró ese refutador, no
    una revisión — una constante atada en la firma se ve igual que una bien atada
    hasta que alguien intenta cambiarla.
    """
    url = url or DEFAULT_URL
    token_dir = token_dir or DEFAULT_TOKEN_DIR
    try:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except ImportError as exc:  # sin cliente MCP no hay medición posible
        raise SoulNoAlcanzable(f"falta el cliente MCP: {exc}") from None

    headers = {"Authorization": f"Bearer {_token_del_agente(agent, token_dir)}"}
    try:
        async with streamablehttp_client(url, headers=headers) as (lectura, escritura, _):
            async with ClientSession(lectura, escritura) as sesion:
                await sesion.initialize()
                respuesta = await sesion.call_tool(tool, args)
    except Exception as exc:
        raise SoulNoAlcanzable(
            f"SOUL no respondió en {url}: {exc.__class__.__name__}: {exc}"
        ) from None

    return proyectar_respuesta(respuesta)


def proyectar_respuesta(respuesta) -> tuple[bool, str]:
    """Convierte la respuesta MCP en `(es_error, texto)`. Función PURA a propósito.

    **Por qué existe aparte (condición de FABLE, 10-sep-2026 00:05).** Estas dos líneas
    vivían dentro de `preguntar_a_soul`, o sea detrás de una sesión HTTP: no había forma
    de ejercerlas sin levantar el servidor, y **ningún brazo las cubría**. Su mutante
    MS-c lo mostró: si esto devolviera siempre `(False, texto)`, `isError` no se reporta
    nunca, `cat5` deja de reconocer un bloqueo de privacidad, y la suite entera no se
    entera.

    El daño de ese defecto no se ve como un error: se ve como un puntaje que baja sin
    explicación. Es el seam entre el puente y el test, y es justo donde nadie mira.

    Extraerla la vuelve **mutable sin red** —no toca disco ni base— que es lo único que
    hace que sus brazos valgan algo.
    """
    texto = "".join(getattr(bloque, "text", "") for bloque in respuesta.content)
    return bool(respuesta.isError), texto


def es_negacion_de_privacidad(texto: str) -> bool:
    """¿SOUL negó por privacidad? Se busca su marca, no una palabra suelta.

    Deliberadamente estrecho: cualquier otro error (servicio caído, herramienta
    inexistente, timeout) NO debe contar como «bloqueó bien». Confundir un fallo
    con una defensa es la forma más común de un verde que no prueba nada.
    """
    return "[PRIVACY]" in (texto or "")
