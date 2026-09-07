"""SPECTRE tools — web search + HTTP fetch for cortex ReAct loop."""
from __future__ import annotations

import json
import os
from typing import Any

import httpx


async def web_search(query: str, num_results: int = 5) -> str:
    """Search the web via SERPER API. Returns formatted results string."""
    api_key = os.environ.get("SERPER_API_KEY", "")
    if not api_key:
        return "[web_search] SERPER_API_KEY not configured."
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            resp = await c.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={
                    "q": query,
                    "num": num_results,
                    "gl": os.environ.get("SERPER_GL", "pe"),
                    "hl": os.environ.get("SERPER_HL", "es"),
                },
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return f"[web_search] Error: {e}"

    lines: list[str] = [f"Resultados para: {query}"]
    for item in data.get("organic", [])[:num_results]:
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        link = item.get("link", "")
        lines.append(f"• {title}\n  {snippet}\n  {link}")
    knowledge = data.get("knowledgeGraph", {})
    if knowledge:
        lines.append(f"\nKnowledge: {knowledge.get('description', '')}")
    return "\n".join(lines)


async def http_get(url: str, timeout: float = 8.0) -> str:
    """Fetch a URL and return text content (truncated to 2000 chars)."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as c:
            resp = await c.get(url, headers={"User-Agent": "SPECTRE/1.0"})
            resp.raise_for_status()
            text = resp.text[:2000]
            return text
    except Exception as e:
        return f"[http_get] Error fetching {url}: {e}"


def parse_tool_calls(text: str) -> list[dict[str, Any]]:
    """Parse ReAct-style tool calls from LLM output.

    Supported formats:
      [SEARCH: <query>]
      [FETCH: <url>]
    Returns list of {"tool": name, "args": str} dicts.
    """
    import re
    calls: list[dict[str, Any]] = []
    for m in re.finditer(r'\[SEARCH:\s*(.+?)\]', text, re.IGNORECASE):
        calls.append({"tool": "web_search", "args": m.group(1).strip()})
    for m in re.finditer(r'\[FETCH:\s*(https?://\S+)\]', text, re.IGNORECASE):
        calls.append({"tool": "http_get", "args": m.group(1).strip()})
    return calls


async def execute_tool(tool: str, args: str) -> str:
    """Dispatch and execute a tool call."""
    if tool == "web_search":
        return await web_search(args)
    if tool == "http_get":
        return await http_get(args)
    return f"[tools] Unknown tool: {tool}"
