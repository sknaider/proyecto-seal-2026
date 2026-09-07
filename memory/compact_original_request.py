#!/usr/bin/env python3
"""Rescata el PEDIDO ORIGINAL de William del transcript, para que sobreviva la compactación.

POR QUÉ EXISTE (idea 5 del análisis de Bob, tarea 1699):
Nuestra compactación conserva lo RECIENTE. En una tarea larga eso alcanza para
seguir trabajando y no para recordar PARA QUÉ se empezó: a los tres o cuatro
ciclos el objetivo original ya no está en contexto y se deriva. Bob conserva la
petición original **y** lo reciente. Esto rescata la primera mitad.

POR QUÉ NO SE LEE DEL `sessionContext` DEL HOOK, que sería lo obvio:
`pre_compact_hook.py` lo intentaba y **nunca funcionó**. Medido el 4-sep-2026 en
el último estado guardado real (3-sep 21:47), los cuatro campos que salen de esa
extracción estaban vacíos:

    "decisions": []   "corrections": []   "tasks_in_progress": []   "technical_state": ""

mientras los campos que vienen de la DB sí tenían contenido. El hook pedía
`sessionContext`/`summary`/`compact_summary` y el harness no entrega ninguna de
las tres: entrega **`transcript_path`**. Por eso acá se lee el transcript.

EL FILTRO ES EL 90 % DEL TRABAJO, y no es un detalle cosmético: en el transcript,
los primeros renglones de tipo `user` casi nunca son el usuario. Son la salida de
un comando local, el eco de un `/model`, un recordatorio del sistema o el
resultado de una herramienta. Tomar el primero a ciegas guarda basura y **el
objetivo se pierde igual, con la diferencia de que ahora parece que se guardó**.

Owner: ADA — 4-sep-2026 — tarea 1699.
"""
from __future__ import annotations

import json
from pathlib import Path

# Prefijos que delatan un renglón `user` que NO escribió el humano.
_NOT_HUMAN_PREFIXES = (
    "<local-command-caveat>",
    "<local-command-stdout>",
    "<command-name>",
    "<command-message>",
    "<command-args>",
    "<system-reminder>",
    "<task-notification>",
    "[AUTO-BOOT",
    "[SOUL Active Recall",
    "Caveat: The messages below were generated",
)

MAX_LEN = 2_000


def _text_of(entry: dict) -> str:
    """Texto plano de una entrada del transcript, o '' si no lo tiene."""
    message = entry.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return " ".join(p for p in parts if p).strip()
    return ""


def is_human_turn(entry: dict) -> bool:
    """¿Este renglón lo escribió realmente el usuario?"""
    if not isinstance(entry, dict) or entry.get("type") != "user":
        return False
    if entry.get("isMeta") is True:
        return False
    message = entry.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        # Un tool_result viaja con role user pero no es una petición humana.
        if any(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for b in message["content"]
        ):
            return False
    text = _text_of(entry)
    if not text:
        return False
    return not text.lstrip().startswith(_NOT_HUMAN_PREFIXES)


def extract_original_request(transcript_path: str | Path | None) -> str | None:
    """Primer pedido humano del transcript, recortado. None si no hay o no se puede leer."""
    if not transcript_path:
        return None
    path = Path(transcript_path)
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return None
    with handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if is_human_turn(entry):
                text = " ".join(_text_of(entry).split())
                return text[:MAX_LEN] if text else None
    return None


def original_request_lines(state: dict | None) -> list[str]:
    """Renglones a reinyectar tras la compactación. Vacío si no hay pedido guardado.

    Es una función aparte y pura a propósito: la parte que de verdad se puede
    equivocar (qué se muestra y cuándo NO se muestra nada) queda testeable sin
    base de datos, y al hook sólo le queda la línea que la llama.
    """
    if not isinstance(state, dict):
        return []
    original = state.get("original_request")
    if not original or not str(original).strip():
        return []
    return [
        "PEDIDO ORIGINAL de esta sesión (no lo pierdas de vista):",
        f"  ▸ {str(original).strip()[:600]}",
        "",
    ]
