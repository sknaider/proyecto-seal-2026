"""SPECTRE Web Tools — internet access layer.

Provides SPECTRE with real-time web search (SERPER) and HTTP fetch.
Called by cortex.py tool loop when LLM emits [SEARCH: query] or [FETCH: url].

Tool syntax (text-based, works with any LLM):
  [SEARCH: your query here]   → Google search via SERPER
  [FETCH: https://url.here]   → HTTP GET, returns truncated body
"""
from __future__ import annotations

import os
import re
from typing import Any

import httpx

SERPER_API_URL = "https://google.serper.dev/search"
MAX_FETCH_CHARS = 3000
TOOL_TIMEOUT = 10.0

# Regex patterns to detect tool calls in LLM output
_SEARCH_RE = re.compile(r"\[SEARCH:\s*(.+?)\]", re.IGNORECASE | re.DOTALL)
_FETCH_RE = re.compile(r"\[FETCH:\s*(https?://[^\]]+)\]", re.IGNORECASE)


def _get_serper_key() -> str:
    key = os.environ.get("SERPER_API_KEY", "")
    if not key:
        env_path = os.path.join(os.path.dirname(__file__), "..", "spectre.env")
        try:
            for line in open(env_path).read().splitlines():
                if line.startswith("SERPER_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    os.environ["SERPER_API_KEY"] = key
                    break
        except Exception:
            pass
    return key


async def web_search(query: str) -> str:
    """Search the web using SERPER Google Search API."""
    key = _get_serper_key()
    if not key:
        return "[web_search: SERPER_API_KEY not configured]"
    try:
        async with httpx.AsyncClient(timeout=TOOL_TIMEOUT) as c:
            resp = await c.post(
                SERPER_API_URL,
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
                json={"q": query, "num": 5},
            )
            resp.raise_for_status()
            data = resp.json()

        results = []
        for item in data.get("organic", [])[:5]:
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            link = item.get("link", "")
            results.append(f"• {title}\n  {snippet}\n  {link}")

        answer = data.get("answerBox", {}).get("answer") or data.get("answerBox", {}).get("snippet")
        if answer:
            results.insert(0, f"[Direct answer] {answer}")

        return "\n\n".join(results) if results else "[web_search: no results]"
    except Exception as e:
        return f"[web_search error: {e}]"


async def http_fetch(url: str) -> str:
    """Fetch a URL and return truncated text content."""
    try:
        async with httpx.AsyncClient(timeout=TOOL_TIMEOUT, follow_redirects=True) as c:
            resp = await c.get(url, headers={"User-Agent": "SPECTRE/1.0"})
            resp.raise_for_status()
            text = resp.text[:MAX_FETCH_CHARS]
            return f"[FETCH {url}]\n{text}"
    except Exception as e:
        return f"[http_fetch error: {e}]"


def detect_tool_calls(text: str) -> list[dict[str, str]]:
    """Detect [SEARCH: ...] and [FETCH: ...] calls in LLM output."""
    calls: list[dict[str, str]] = []
    for m in _SEARCH_RE.finditer(text):
        calls.append({"type": "search", "arg": m.group(1).strip()})
    for m in _FETCH_RE.finditer(text):
        calls.append({"type": "fetch", "arg": m.group(1).strip()})
    return calls


async def execute_tool_calls(calls: list[dict[str, str]]) -> str:
    """Execute detected tool calls and return combined results."""
    parts: list[str] = []
    for call in calls[:3]:  # max 3 tool calls per turn
        if call["type"] == "search":
            result = await web_search(call["arg"])
            parts.append(f"[SEARCH RESULTS for '{call['arg']}']\n{result}")
        elif call["type"] == "fetch":
            result = await http_fetch(call["arg"])
            parts.append(result)
    return "\n\n---\n\n".join(parts)
