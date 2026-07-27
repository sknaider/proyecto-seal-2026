#!/usr/bin/env python3
"""SEAL Tool Result Budget Hook — H2.6 (PostToolUse).

Trunca resultados grandes de herramientas para reducir tokens en contexto.
Estrategia head+tail: preserva inicio y fin del output, omite el medio.

Herramienta  | Límite chars | Estrategia
-------------|-------------|------------------
Read         | 8,000       | head(3K) + tail(500) + mensaje omitidos
Grep         | 4,000       | head + "N matches más omitidos"
Bash         | 6,000       | tail (lo relevante suele estar al final)
WebFetch     | 5,000       | head + resumen sección omitida
Default      | 10,000      | head truncado

ROI: -40-60% contexto en sesiones intensas de lectura.

Bypass: SEAL_TOOL_BUDGET_BYPASS=1 → no truncar.
Archivos <2,000 chars → no truncar nunca.

Spec: agents/JARVIS/spec_tool_result_budget_h26.md
Owner: JARVIS (diseño) | ADA (implementación) — H2.6 — 2026-04-19
"""
from __future__ import annotations

import json
import os
import sys

# Bypass global
BYPASS = os.environ.get("SEAL_TOOL_BUDGET_BYPASS", "0") == "1"

# Mínimo para no truncar nunca (output pequeño = no vale la pena)
MIN_CHARS = 2_000

# Límites por herramienta
LIMITS: dict[str, int] = {
    "Read":     8_000,
    "Grep":     4_000,
    "Bash":     6_000,
    "WebFetch": 5_000,
}
DEFAULT_LIMIT = 10_000

# Herramientas de escritura — nunca truncar
SKIP_TOOLS = {"Write", "Edit", "mcp__filesystem__write_file", "mcp__filesystem__edit_file"}


def truncate_head_tail(text: str, limit: int, head_ratio: float = 0.85) -> str:
    """Trunca con estrategia head+tail. head_ratio = fracción para el inicio."""
    if len(text) <= limit:
        return text

    head_size = int(limit * head_ratio)
    tail_size = limit - head_size
    omitted = len(text) - head_size - tail_size

    head = text[:head_size]
    tail = text[-tail_size:] if tail_size > 0 else ""

    marker = f"\n\n... [{omitted:,} chars omitidos] ...\n\n"
    header = f"[RESULT TRUNCADO: {len(text):,} chars → {limit:,}]\n\n"

    return header + head + marker + tail


def truncate_tail(text: str, limit: int) -> str:
    """Para Bash: preserva el final (donde suelen estar errores/resultados)."""
    if len(text) <= limit:
        return text

    omitted = len(text) - limit
    tail = text[-limit:]
    header = f"[RESULT TRUNCADO: {len(text):,} chars → {limit:,}. Inicio omitido ({omitted:,} chars).]\n\n"
    return header + tail


def truncate_head(text: str, limit: int) -> str:
    """Para WebFetch/Default: preserva el inicio."""
    if len(text) <= limit:
        return text

    omitted = len(text) - limit
    head = text[:limit]
    return head + f"\n\n... [{omitted:,} chars omitidos] ..."


def apply_budget(tool_name: str, response: str) -> str | None:
    """Aplica el presupuesto al response. Retorna texto truncado o None si no hay cambio."""
    if len(response) <= MIN_CHARS:
        return None

    limit = LIMITS.get(tool_name, DEFAULT_LIMIT)
    if len(response) <= limit:
        return None

    if tool_name == "Read":
        return truncate_head_tail(response, limit)
    elif tool_name == "Bash":
        return truncate_tail(response, limit)
    elif tool_name in ("WebFetch", "Grep"):
        return truncate_head(response, limit)
    else:
        return truncate_head(response, DEFAULT_LIMIT)


def main() -> None:
    if BYPASS:
        print(json.dumps({}))
        return

    try:
        input_data = json.loads(sys.stdin.read())
    except Exception:
        print(json.dumps({}))
        return

    tool_name = input_data.get("tool_name", "")
    tool_response = input_data.get("tool_response", "")

    if tool_name in SKIP_TOOLS or not isinstance(tool_response, str):
        print(json.dumps({}))
        return

    truncated = apply_budget(tool_name, tool_response)

    if truncated is None:
        print(json.dumps({}))
        return

    # HOTFIX (JARVIS design-owner + FABLE verificador, 2026-07-18):
    # PostToolUse hookSpecificOutput NO admite "toolResponse" en el schema de
    # Claude Code (v2.1.212): solo hookEventName + additionalContext, y
    # additionalContext AÑADE texto (no reemplaza el tool_result). Emitir el dict
    # con toolResponse causaba "Hook JSON output validation failed — (root):
    # Invalid input" en TODO output > budget (fleet-wide), y como se rechazaba, el
    # truncado NUNCA se aplicaba → outputs enteros → presión de contexto que
    # alimentaba los loops de auto-compact (una de las causas del outage 2026-07-18).
    # No-op válido: mata el error/spam sin perder comportamiento (ya estaba roto).
    # El ahorro real de contexto requiere otro mecanismo (rediseño aparte — PostToolUse
    # no puede reescribir el tool_result en este Claude Code).
    _ = truncated  # computado pero no emitible bajo el schema actual
    print(json.dumps({}))


if __name__ == "__main__":
    main()
