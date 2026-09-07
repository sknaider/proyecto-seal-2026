"""Loopback-only HTTP surface for SOUL Web."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from . import __version__
from .service import SoulWebService

MAX_REQUEST_BYTES = 128 * 1024


class SoulWebHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, *, service: SoulWebService):
        host = str(address[0])
        if host not in {"127.0.0.1", "localhost"}:
            raise ValueError("SOUL Web must bind to loopback")
        self.service = service
        super().__init__(address, handler)


class Handler(BaseHTTPRequestHandler):
    server: SoulWebHTTPServer

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5.0)

    def log_message(self, *_args: Any) -> None:
        return

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _json(self, code: int, value: Any) -> None:
        self._send(
            code,
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _payload(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None or not raw_length.isdigit():
            raise ValueError("Content-Length requerido")
        length = int(raw_length)
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise ValueError("request fuera del límite")
        content_type = self.headers.get_content_type()
        if content_type != "application/json":
            raise ValueError("Content-Type debe ser application/json")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise TypeError("JSON debe ser un objeto")
        return value

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if path == "/":
            self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "soul-memory-web",
                    "version": __version__,
                    "build_id": f"soul-memory-web-{__version__}",
                    "pid": os.getpid(),
                    "database": str(self.server.service.core.database),
                },
            )
            return
        if path == "/api/status":
            counts = self.server.service.conversations.status_counts()
            try:
                core_count = self.server.service.core.count()
            except Exception as exc:  # noqa: BLE001 - status must expose Core failure
                self._json(
                    503,
                    {"ok": False, "counts": counts, "core_error": str(exc)},
                )
                return
            self._json(
                200,
                {"ok": True, "counts": counts, "core_memory_count": core_count},
            )
            return
        if path == "/api/models":
            try:
                models = self.server.service.ollama.list_chat_models()
                self._json(200, {"ok": True, "models": models})
            except Exception as exc:  # noqa: BLE001 - HTTP boundary reports service failure
                self._json(503, {"ok": False, "error": str(exc)})
            return
        if path == "/api/memories":
            try:
                self._json(
                    200,
                    {"ok": True, "memories": self.server.service.core.list_memories(100)},
                )
            except Exception as exc:  # noqa: BLE001 - HTTP boundary reports service failure
                self._json(503, {"ok": False, "error": str(exc)})
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        try:
            payload = self._payload()
        except (TimeoutError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._json(400, {"ok": False, "error": str(exc)})
            return
        if path == "/api/chat":
            model = str(payload.get("model") or "").strip()
            message = str(payload.get("message") or "").strip()
            session_id = str(payload.get("session_id") or "").strip()
            if not model or not message or not session_id:
                self._json(400, {"ok": False, "error": "faltan model, message o session_id"})
                return
            if len(model) > 256 or len(message) > 64_000 or len(session_id) > 200:
                self._json(400, {"ok": False, "error": "campo fuera del límite"})
                return
            try:
                result = self.server.service.chat(
                    model=model, user_message=message, session_id=session_id
                )
                self._json(200, {"ok": True, **asdict(result)})
            except Exception as exc:  # noqa: BLE001 - HTTP boundary reports service failure
                self._json(503, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return
        if path == "/api/episode":
            model = str(payload.get("model") or "").strip()
            session_id = str(payload.get("session_id") or "").strip()
            if not model or not session_id:
                self._json(400, {"ok": False, "error": "faltan model o session_id"})
                return
            try:
                memory_id = self.server.service.close_episode(
                    model=model, session_id=session_id
                )
                self._json(200, {"ok": True, "core_memory_id": memory_id})
            except Exception as exc:  # noqa: BLE001 - HTTP boundary reports service failure
                self._json(503, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            return
        self._json(404, {"ok": False, "error": "not found"})


PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>SOUL — memoria viva</title><style>
body{font:16px system-ui;background:#0b1020;color:#edf2ff;max-width:900px;margin:0 auto;padding:24px}
#log{min-height:50vh;white-space:pre-wrap}.m{padding:12px;margin:8px 0;border-radius:12px;background:#17213a}
.u{background:#1d4d4d}form{display:flex;gap:8px}input,select,button{font:inherit;padding:10px;border-radius:8px}
input{flex:1}small{color:#9fb0d0}</style></head><body>
<h1>SOUL</h1><small id="status">Cargando…</small><div id="log"></div>
<form id="form"><select id="model"></select><input id="message" autocomplete="off" placeholder="Habla conmigo…"><button>Enviar</button></form>
<script>
const sid=localStorage.soulSession||(localStorage.soulSession=crypto.randomUUID());
const log=document.querySelector('#log'), status=document.querySelector('#status'), model=document.querySelector('#model');
const add=(text,cls='')=>{const d=document.createElement('div');d.className='m '+cls;d.textContent=text;log.append(d)};
async function load(){let r=await fetch('/api/models');let j=await r.json();model.replaceChildren(...(j.models||[]).map(x=>new Option(x,x)));status.textContent='Sesión '+sid.slice(0,8)}
document.querySelector('#form').onsubmit=async e=>{e.preventDefault();const input=document.querySelector('#message'),text=input.value.trim();if(!text)return;add(text,'u');input.value='';status.textContent='Pensando…';let r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:model.value,message:text,session_id:sid})});let j=await r.json();add(j.reply||('ERROR: '+j.error));status.textContent=j.ok?`recall ${j.recalled} · hechos ${j.extracted} · ${j.extraction_status}`:'falló';};load();
</script></body></html>"""
