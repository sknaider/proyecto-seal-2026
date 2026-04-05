#!/usr/bin/env python3
"""
WEB TOOLS — Acceso autónomo a internet para ADA.

Capacidades:
  - web_search(query)   → DuckDuckGo (sin API key)
  - web_read(url)       → JINA Reader (limpia HTML, sin key básico)
  - web_find(query, n)  → Busca y lee el primer resultado relevante

ADA puede llamar esto desde soul_awareness.py, ada_cli.py, o directamente.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Optional

import httpx
from ddgs import DDGS

JINA_URL = "https://r.jina.ai/"
TIMEOUT = 15.0
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)


async def web_search(query: str, max_results: int = 5) -> list[dict]:
    """
    Busca en DuckDuckGo via ddgs. Retorna lista de {title, url, snippet}.
    Sin API key — usa scraping seguro de DuckDuckGo.
    """
    def _sync_search():
        with DDGS() as d:
            return list(d.text(query, max_results=max_results))

    try:
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(_executor, _sync_search)
        return [
            {
                "title": r.get("title", "")[:100],
                "url": r.get("href", ""),
                "snippet": r.get("body", "")[:300],
            }
            for r in raw
        ]
    except Exception as e:
        return [{"error": str(e), "query": query}]


async def web_read(url: str, max_chars: int = 3000) -> str:
    """
    Lee una página web via JINA Reader (convierte HTML → texto limpio).
    Gratis para uso básico, sin key necesaria.
    """
    jina_url = JINA_URL + url
    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={"Accept": "text/plain"},
        ) as client:
            resp = await client.get(jina_url)
            resp.raise_for_status()
            text = resp.text
            return text[:max_chars] if len(text) > max_chars else text
    except Exception as e:
        # Fallback: intento directo sin JINA
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                # Retornar texto crudo, limitado
                return resp.text[:max_chars]
        except Exception as e2:
            return f"Error leyendo {url}: {e} | fallback: {e2}"


async def web_find(query: str, read_first: bool = True) -> dict:
    """
    Busca y opcionalmente lee el primer resultado.
    Útil para resolver problemas técnicos de noche de forma autónoma.
    """
    results = await web_search(query, max_results=3)
    if not results or "error" in results[0]:
        return {"query": query, "results": results, "content": None}

    content = None
    if read_first and results[0].get("url"):
        content = await web_read(results[0]["url"], max_chars=2000)

    return {
        "query": query,
        "results": results,
        "content": content,
    }


# ── CLI directo ──

async def _main():
    import sys
    if len(sys.argv) < 2:
        print("Uso: python3 web_tools.py 'query'")
        print("     python3 web_tools.py --read 'https://url'")
        return

    if sys.argv[1] == "--read" and len(sys.argv) > 2:
        print(await web_read(sys.argv[2]))
    else:
        query = " ".join(sys.argv[1:])
        print(f"Buscando: '{query}'\n")
        results = await web_search(query)
        for i, r in enumerate(results, 1):
            print(f"{i}. {r.get('title', '')[:70]}")
            print(f"   {r.get('url', '')}")
            print(f"   {r.get('snippet', '')[:150]}\n")


if __name__ == "__main__":
    asyncio.run(_main())
