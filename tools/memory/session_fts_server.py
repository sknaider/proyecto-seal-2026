"""Session FTS HTTP server — exposes session history search via REST + HTML UI.

Endpoints:
  GET  /              — HTML search interface (browser-ready)
  GET  /api/search    — JSON search: ?q=query&agent=ADA&limit=20&source=both
  GET  /api/health    — {"status":"ok","ready":true}

Usage:
    python3 tools/memory/session_fts_server.py --port 8770

Or programmatic:
    import asyncio
    from tools.memory.session_fts_server import SessionFTSServer

    async def main():
        srv = SessionFTSServer(port=8770)
        await srv.start()
        await srv.wait()

    asyncio.run(main())
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
from typing import Optional

from tools.memory.session_fts import SessionFTS, _DEFAULT_DSN

_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>SEAL Session Search</title>
<style>
  body { font-family: monospace; background: #0d1117; color: #c9d1d9; margin: 2rem; }
  h1 { color: #58a6ff; }
  input, select { background: #161b22; color: #c9d1d9; border: 1px solid #30363d;
    padding: .4rem .8rem; border-radius: 4px; font-family: monospace; }
  button { background: #1f6feb; color: #fff; border: none; padding: .4rem 1rem;
    border-radius: 4px; cursor: pointer; font-family: monospace; }
  #results { margin-top: 1.5rem; }
  .result { border: 1px solid #30363d; border-radius: 6px; padding: 1rem;
    margin-bottom: .8rem; }
  .result .meta { color: #8b949e; font-size: .85em; }
  .result .headline { margin-top: .4rem; }
  .result .rank { color: #3fb950; float: right; font-size: .85em; }
  b { color: #f78166; }
</style>
</head>
<body>
<h1>SEAL Session Search</h1>
<form id="f" onsubmit="search(event)">
  <input id="q" placeholder="query..." size="40" autofocus>
  <select id="agent">
    <option value="">All agents</option>
    <option>ADA</option><option>JARVIS</option><option>ALICE</option>
    <option>NEXUS</option><option>DUM</option>
  </select>
  <select id="source">
    <option value="both">both</option>
    <option value="exchanges">exchanges</option>
    <option value="sessions">sessions</option>
  </select>
  <input id="limit" type="number" value="20" size="4" min="1" max="100">
  <button type="submit">Search</button>
</form>
<div id="results"></div>
<script>
async function search(e) {
  if (e) e.preventDefault();
  const q = document.getElementById('q').value.trim();
  if (!q) return;
  const agent  = document.getElementById('agent').value;
  const source = document.getElementById('source').value;
  const limit  = document.getElementById('limit').value;
  const params = new URLSearchParams({q, limit, source});
  if (agent) params.set('agent', agent);
  const resp = await fetch('/api/search?' + params);
  const data = await resp.json();
  const div  = document.getElementById('results');
  if (!data.results.length) { div.innerHTML = '<p>No results.</p>'; return; }
  div.innerHTML = data.results.map(r => `
    <div class="result">
      <span class="rank">rank ${r.rank.toFixed(4)}</span>
      <strong>[${r.source}]</strong> ${r.agent}
      <span class="meta"> — ${r.created_at || ''} — id:${r.id}</span>
      <div class="headline">${r.headline}</div>
    </div>`).join('');
}
</script>
</body>
</html>
"""


class SessionFTSServer:
    """Minimal asyncio HTTP server for session FTS."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8770,
        dsn: Optional[str] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._dsn  = dsn or _DEFAULT_DSN
        self._fts:  Optional[SessionFTS] = None
        self._srv:  Optional[asyncio.Server] = None

    async def start(self) -> None:
        self._fts = await SessionFTS.create(self._dsn)
        self._srv = await asyncio.start_server(
            self._handle, self._host, self._port,
        )

    async def wait(self) -> None:
        if self._srv:
            async with self._srv:
                await self._srv.serve_forever()

    async def close(self) -> None:
        if self._fts:
            await self._fts.close()
        if self._srv:
            self._srv.close()
            await self._srv.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            raw = await reader.read(4096)
            if not raw:
                return
            first_line = raw.split(b"\r\n")[0].decode("utf-8", errors="replace")
            parts = first_line.split(" ")
            if len(parts) < 2:
                return
            method, path = parts[0], parts[1]

            if method != "GET":
                self._write(writer, 405, "text/plain", b"Method Not Allowed")
                return

            parsed = urllib.parse.urlparse(path)
            route  = parsed.path
            query  = urllib.parse.parse_qs(parsed.query)

            if route == "/":
                self._write(writer, 200, "text/html; charset=utf-8", _HTML.encode())
            elif route == "/api/health":
                body = json.dumps({"status": "ok", "ready": self._fts is not None}).encode()
                self._write(writer, 200, "application/json", body)
            elif route == "/api/search":
                body = await self._search(query)
                self._write(writer, 200, "application/json", body)
            else:
                self._write(writer, 404, "text/plain", b"Not Found")
        except Exception as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self._write(writer, 500, "application/json", body)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _search(self, query: dict) -> bytes:
        q      = query.get("q", [""])[0].strip()
        agent  = query.get("agent", [None])[0] or None
        limit  = min(int(query.get("limit", ["20"])[0]), 100)
        source = query.get("source", ["both"])[0]

        if not q or not self._fts:
            return json.dumps({"results": [], "query": q}).encode()

        results = await self._fts.search(q, agent=agent, limit=limit, source=source)
        return json.dumps({
            "query": q,
            "agent": agent,
            "source": source,
            "count": len(results),
            "results": [r.to_dict() for r in results],
        }, default=str).encode()

    @staticmethod
    def _write(writer: asyncio.StreamWriter, status: int, ct: str, body: bytes) -> None:
        status_text = {200: "OK", 404: "Not Found", 405: "Method Not Allowed", 500: "Error"}
        writer.write(
            f"HTTP/1.1 {status} {status_text.get(status, 'Unknown')}\r\n"
            f"Content-Type: {ct}\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Access-Control-Allow-Origin: *\r\n"
            f"\r\n".encode() + body
        )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()

    async def _main():
        srv = SessionFTSServer(host=args.host, port=args.port)
        await srv.start()
        print(f"Session search server running at http://{args.host}:{args.port}")
        await srv.wait()

    asyncio.run(_main())
