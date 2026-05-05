#!/usr/bin/env python3
"""SEAL Web Search — módulo compartido para todos los agentes locales.

Interface pública:
    web_search(query: str) -> str          # async, retorna texto formateado
    needs_search(text: str) -> bool        # detecta intent de búsqueda

Uso:
    from web_search import web_search, needs_search

    if needs_search(user_message):
        results = await web_search(user_message[:200])

API key: SERPER_API_KEY en env o en proyecto-seal/.env
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import httpx

SERPER_URL = "https://google.serper.dev/search"
TIMEOUT = 8.0

_SEARCH_INTENT_RE = re.compile(
    r'\b(busca|buscar|qu[eé] es|cu[aá]l es|c[oó]mo funciona|qui[eé]n es|'
    r'd[oó]nde est[aá]|cu[aá]ndo|noticias|[uú]ltim[oa]s?|reciente|'
    r'search|find|investiga|explica|qu[eé] pas[oó])\b',
    re.IGNORECASE,
)


def _load_key() -> str:
    key = os.environ.get("SERPER_API_KEY", "")
    if key:
        return key
    # Fallback: buscar en proyecto-seal/.env
    for candidate in [
        Path(__file__).resolve().parent.parent / ".env",
        Path(__file__).resolve().parent / ".env",
    ]:
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                if line.startswith("SERPER_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    os.environ["SERPER_API_KEY"] = key
                    return key
        except Exception:
            continue
    return ""


def needs_search(text: str) -> bool:
    return bool(_SEARCH_INTENT_RE.search(text))


async def web_search(query: str, num: int = 4, gl: str = "pe", hl: str = "es") -> str:
    key = _load_key()
    if not key:
        return "[web_search: SERPER_API_KEY no configurada]"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            resp = await c.post(
                SERPER_URL,
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                json={"q": query, "num": num, "gl": gl, "hl": hl},
            )
            resp.raise_for_status()
            data = resp.json()

        results: list[str] = []
        box = data.get("answerBox", {})
        answer = box.get("answer") or box.get("snippet")
        if answer:
            results.append(f"Respuesta directa: {answer}")
        for item in data.get("organic", [])[:num]:
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            link = item.get("link", "")
            if title or snippet:
                results.append(f"• {title}: {snippet} [{link}]")
        return "\n".join(results) if results else "[web_search: sin resultados]"
    except Exception as e:
        return f"[web_search error: {e}]"
