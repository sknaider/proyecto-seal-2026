#!/usr/bin/env python3
"""SEAL Tool Result Budget Hook — H2.6 (PostToolUse). **INERTE A PROPÓSITO.**

Este hook NO recorta nada y no puede hacerlo. Se conserva como lápida: la idea
vuelve sola cada vez que alguien mira la presión de contexto, y sin este registro
se reimplementa desde cero. **Antes de revivirlo, leé las tres mediciones.**

MEDICIÓN 1 — el schema no admite reemplazar el resultado (JARVIS + FABLE, 18-jul-2026)
    PostToolUse `hookSpecificOutput` sólo acepta `hookEventName` y
    `additionalContext`, y `additionalContext` AÑADE texto, no lo reemplaza.
    Emitir `toolResponse` producía "Hook JSON output validation failed" en TODO
    output que superara el presupuesto, en toda la flota. Como se rechazaba, el
    recorte no se aplicaba nunca: outputs enteros y presión de contexto, una de
    las causas del outage del 18-jul. **Un hook PostToolUse no puede achicar un
    tool_result en este Claude Code.**

MEDICIÓN 2 — además fallaba antes de llegar a su lógica (ADA, 4-sep-2026)
    El harness entrega `tool_response` como **dict**, no como str. El filtro
    `not isinstance(tool_response, str)` cortaba en la línea anterior, así que
    para Bash la función de presupuesto no se ejecutaba jamás. Sonda con la
    forma real, tres llamadas del harness:

        Bash    dict    139
        Bash    dict    168
        Bash    dict    139

    Dos causas de muerte independientes. Arreglar una sola no habría revivido nada.

MEDICIÓN 3 — el harness YA hace lo que este hook quería (ADA, 4-sep-2026)
    Es exactamente la idea 13 del análisis de Bob: guardar la salida completa y
    mandar sólo el recorte con el puntero. Ya existe. Salida de 113,3 KB:

        Output too large (113.3KB). Full output saved to:
          .../tool-results/bghnqks4z.txt
        Preview (first 2KB): ...

    116.018 bytes en disco, 2 KB en contexto, sin volver a ejecutar el comando.
    **No hay nada que adoptar acá: el recorte con puntero al original ya está.**

DÓNDE SÍ FALTA, si alguien retoma el frente: en NUESTROS productores de salida
(respuestas del MCP, scripts de auditoría), que es donde controlamos el texto.
No en un hook PostToolUse.

Owner: JARVIS (diseño) | ADA (implementación) — H2.6 — 2026-04-19
Lápida: ADA, 4-sep-2026, cerrando la tarea 1698.
"""
from __future__ import annotations

import json


def main() -> None:
    # No-op deliberado. Ver las tres mediciones del docstring antes de tocar esto.
    print(json.dumps({}))


if __name__ == "__main__":
    main()
