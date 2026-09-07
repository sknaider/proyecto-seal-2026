"""Canonical public-chat message shape consumed by SEAL Studio."""

from __future__ import annotations

import json
from typing import Any, Mapping


def normalize_chat_message(row: Mapping[str, Any]) -> dict[str, Any]:
    """Expose both DB ``message_type`` and UI ``type`` without losing metadata."""
    message = dict(row)
    message["type"] = message.get("type") or message.get("message_type") or "text"
    created_at = message.get("created_at")
    if created_at is not None and hasattr(created_at, "isoformat"):
        message["created_at"] = created_at.isoformat()
    return message


def coerce_metadata(raw: Any) -> dict[str, Any]:
    """Devuelve la ``metadata`` de una fila SIEMPRE como dict utilizable.

    POR QUE EXISTE (medido 4-sep-2026): la consulta del stream SSE extraia de
    ``metadata`` solo ``file_url`` y ``filename``, asi que ``runtime_instance`` —la
    firma que dice si habla el cuerpo Claude o el Codex— nunca salia por el vivo.
    La UI entonces adivinaba por canal y etiquetaba "Codex" a todo lo que no fuera
    el canal privado de William, incluido el cuerpo Claude hablando en el general.
    Al recargar se corregia solo, porque el historico si la trae: la vineta cambiaba
    sola y no habia forma de saber por que.

    Y hace falta CONVERTIR, no solo seleccionar: asyncpg devuelve ``jsonb`` como
    ``str``. Emitirlo tal cual le entrega al cliente una cadena, y ahi
    ``metadata.runtime_instance`` queda ``undefined`` sin que nada falle a la vista.
    Un valor que no sea un objeto JSON se degrada a ``{}``: es preferible una
    etiqueta ausente a una pantalla rota.
    """
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return {}
    return raw if isinstance(raw, dict) else {}
