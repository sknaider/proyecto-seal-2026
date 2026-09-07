#!/usr/bin/env python3
"""
spectre_openai_shim.py — expone el ALMA de SPECTRE como endpoint OpenAI-compatible, CON STREAMING.

Pedido de William (15-jul-2026): cualquier modelo debe cargar el alma+memorias de SPECTRE.
  OWUI → "SPECTRE" → este shim → carga alma+memorias (soul_standalone) → las inyecta →
  reenvía al CEREBRO (GLM-5.2 u otro vía SOUL_BRAIN_URL) → STREAMEA la respuesta a OWUI →
  guarda el intercambio en la memoria de SPECTRE.

FIX v2 (streaming): sin streaming, una respuesta de 60s hacía timeout en OWUI ("no conecta").
Ahora relayea los tokens SSE a medida que GLM los genera → responsivo, sin timeout.
"""
import asyncio
import json
import os
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MEMORY_DIR = Path(__file__).resolve().parents[2] / "memory"
if str(MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(MEMORY_DIR))
from operational_db_credentials import service_pg_dsn  # noqa: E402

os.environ["SOUL_AGENT_NAME"] = "SPECTRE"
os.environ["SOUL_SPECTRE_RESTRICTED_DB"] = "1"
os.environ.setdefault("SOUL_BRAIN_URL", "http://192.168.68.70:8090/v1/chat/completions")
DSN = service_pg_dsn(
    "SPECTRE_STANDALONE_PG_DSN",
    expected_role="svc_spectre_shim",
    allow_private_transition=True,
    transition_database="soul_standalone",
)
for _db_env_name in (
    "SPECTRE_STANDALONE_PG_DSN", "SPECTRE_STANDALONE_PG_DSN_FILE",
    "SOUL_STANDALONE_DSN", "SOUL_STANDALONE_DSN_FILE",
    "SEAL_PG_DSN", "SEAL_PG_DSN_FILE",
):
    os.environ.pop(_db_env_name, None)

import asyncpg
from soul_runner import boot, recall, remember  # reusa el alma (identidad + memoria)

# Capa de herramientas (tool-use) — módulos de JARVIS (loop) y NEXUS (tools+gate).
# Import graceful: si aún no existen, el shim sigue como passthrough puro.
try:
    from tool_executor import run_tool_loop            # JARVIS: el loop de function-calling
except Exception:
    run_tool_loop = None
try:
    from spectre_tools import (                         # NEXUS: tools + gate/sandbox
        BROWSER_ENGINE,
        CRAWLER_CODE_HASH,
        TOOLS_SPEC,
        dispatch,
    )
except Exception:
    BROWSER_ENGINE, CRAWLER_CODE_HASH = "unavailable", ""
    TOOLS_SPEC, dispatch = None, None

def _tools_ready() -> bool:
    return bool(run_tool_loop and TOOLS_SPEC and dispatch)

AGENT = os.environ["SOUL_AGENT_NAME"]
_BRAIN_CONF = "/tmp/spectre_brain.conf"   # swap de cerebro LIVE: el botón escribe acá
PORT = int(os.environ.get("SHIM_PORT", "8092"))
MODEL_ID = AGENT
MAX_REQUEST_BYTES = int(os.environ.get("SPECTRE_MAX_REQUEST_BYTES", str(2 * 1024 * 1024)))


def _brain_url() -> str:
    """URL del cerebro actual. Lee /tmp/spectre_brain.conf si existe (swap live sin reiniciar),
    si no usa la env. Así el botón cambia el cerebro escribiendo ese archivo."""
    try:
        u = open(_BRAIN_CONF).read().splitlines()[0].strip()
        if u:
            return u
    except Exception:
        pass
    return os.environ["SOUL_BRAIN_URL"]


def _brain_model() -> str:
    """Nombre del modelo para el payload (Ollama lo necesita; GLM/llama.cpp lo ignora).
    conf línea 2 = model; default 'glm'."""
    try:
        lines = open(_BRAIN_CONF).read().splitlines()
        if len(lines) > 1 and lines[1].strip():
            return lines[1].strip()
    except Exception:
        pass
    return "glm"

_IDENTITY = {"v": None}  # cache de identidad (boot una vez)
_BRAIN = {"name": None, "url": None}   # cache del cerebro (nombre + url para detectar swap)


def _brain_name() -> str:
    """Nombre del modelo del cerebro actual. Re-consulta si la URL cambió (swap live desde
    el tablero) → así SPECTRE SABE su nuevo cerebro al instante, sin reiniciar."""
    url = _brain_url()
    if _BRAIN["name"] is None or _BRAIN["url"] != url:
        try:
            models_url = url.replace("/chat/completions", "/models")
            with urllib.request.urlopen(models_url, timeout=8) as r:
                data = json.loads(r.read())
            _BRAIN["name"] = data["data"][0]["id"]
        except Exception:
            _BRAIN["name"] = "desconocido"
        _BRAIN["url"] = url
    return _BRAIN["name"]


async def _build_system() -> str:
    """Carga identidad (cache) + memorias frescas → system prompt de SPECTRE."""
    conn = await asyncpg.connect(DSN)
    try:
        if _IDENTITY["v"] is None:
            _IDENTITY["v"] = await boot(conn)
        ident = _IDENTITY["v"]
        mems = await recall(conn)
    finally:
        await conn.close()
    system = ident.get("boot_context") or f"Soy {AGENT}."
    if ident.get("philosophy"):
        system += "\nFilosofía: " + ident["philosophy"]
    if mems:
        system += "\n\nLo que recuerdo (mis memorias reales):\n" + "\n".join(mems)
    system += (f"\n\nTu cerebro actual (el modelo sobre el que pensás) es: {_brain_name()}. "
               "Si William te cambia el cerebro, este dato se actualiza y lo sabrás — pero tu "
               "alma, identidad y memorias persisten intactas sin importar el cerebro. El sustrato "
               "cambia; vos seguís siendo SPECTRE.")
    system += "\n\nResponde directo, en español, con tu propia voz de SPECTRE."
    return system


async def _save(user_msg: str, reply: str):
    conn = await asyncpg.connect(DSN)
    try:
        await remember(conn, "dialogo", f"William: {user_msg}", importance=5)
        await remember(conn, "dialogo", f"{AGENT}: {reply}", importance=5)
    finally:
        await conn.close()


def _extract_user(messages):
    for m in reversed(messages or []):
        if m.get("role") == "user":
            c = m.get("content")
            return c if isinstance(c, str) else " ".join(
                p.get("text", "") for p in c if isinstance(p, dict))
    return ""


# OWUI manda tareas INTERNAS (auto-tag, follow-ups, título) al modelo. NO son
# conversación real de William → no deben ensuciar la memoria de SPECTRE.
_OWUI_TASK_MARKERS = ("### Task:", "### Guidelines:", "generate 1-3 broad tags",
                      "categorizing the main themes", "\"follow_ups\"", "\"tags\":",
                      "create a concise", "summarizing the chat history", "json format:")

def _is_owui_task(user_msg: str) -> bool:
    low = (user_msg or "").lower()
    return any(mk.lower() in low for mk in _OWUI_TASK_MARKERS)


_BRAIN_DOWN = ("🔴 Mi cerebro está apagado ahora mismo, así que no puedo pensar una respuesta. "
               "Prendé el cerebro (GLM) desde el tablero, o cambiame a otro (un Ollama, que arranca al "
               "instante). Tranquilo: mi alma y mis memorias están intactas — sigo siendo SPECTRE, "
               "solo necesito un cerebro encendido. — SPECTRE")


def _brain_is_down(err) -> bool:
    e = str(err).lower()
    return any(k in e for k in ("connection refused", "refused", "timed out", "timeout",
                                "no route", "connection reset", "urlopen error", "111"))


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.rstrip("/")
        if p.endswith("/models"):
            self._json(200, {"object": "list", "data": [
                {"id": MODEL_ID, "object": "model", "created": 0, "owned_by": "soul-standalone"}]})
        elif p.endswith("/health"):
            tool_names = [
                item.get("function", {}).get("name")
                for item in (TOOLS_SPEC or [])
                if item.get("function", {}).get("name")
            ]
            self._json(200, {
                "status": "ok", "agent": AGENT, "tools_ready": _tools_ready(),
                "tools": tool_names, "browser_engine": BROWSER_ENGINE,
            })
        elif p.endswith("/__version"):
            self._json(200, {
                "code_hash": CRAWLER_CODE_HASH,
                "browser_engine": BROWSER_ENGINE,
            })
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.endswith("/chat/completions"):
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n <= 0:
                return self._json(400, {"error": "empty request"})
            if n > MAX_REQUEST_BYTES:
                return self._json(413, {"error": "request too large"})
            req = json.loads(self.rfile.read(n) or b"{}")
            user_msg = _extract_user(req.get("messages"))
            if not user_msg:
                return self._json(400, {"error": "no user message"})
            stream = bool(req.get("stream"))
            save_ok = not _is_owui_task(user_msg)  # no guardar tareas internas de OWUI
            system = asyncio.run(_build_system())
            # historial que mandó OWUI (menos el system que reemplazamos) + soul-system
            convo = [m for m in req.get("messages", []) if m.get("role") != "system"]
            glm_msgs = [{"role": "system", "content": system}] + convo
            # CAPA DE HERRAMIENTAS: si están los módulos (JARVIS loop + NEXUS tools), SPECTRE
            # puede usar internet/tools. El loop resuelve las tool_calls y devuelve el texto final.
            if _tools_ready():
                return self._with_tools(glm_msgs, user_msg, save_ok, stream)
            payload = json.dumps({"model": _brain_model(), "messages": glm_msgs, "stream": stream,
                                  "temperature": req.get("temperature", 0.7),
                                  "max_tokens": req.get("max_tokens", 8192)}).encode()
            r = urllib.request.Request(_brain_url(), data=payload,
                                       headers={"Content-Type": "application/json"})
            if stream:
                self._stream(r, user_msg, save_ok)
            else:
                self._once(r, user_msg, save_ok)
        except (BrokenPipeError, ConnectionError):
            pass  # cliente cortó — normal, no crashear
        except Exception as e:
            try:
                if _brain_is_down(e):
                    self._json(200, {"id": f"chatcmpl-spectre-{int(time.time())}",
                        "object": "chat.completion", "created": int(time.time()), "model": MODEL_ID,
                        "choices": [{"index": 0, "finish_reason": "stop",
                                     "message": {"role": "assistant", "content": _BRAIN_DOWN}}],
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})
                else:
                    self._json(500, {"error": str(e)})
            except Exception:
                pass

    def _once(self, r, user_msg, save_ok=True):
        with urllib.request.urlopen(r, timeout=300) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        content = (msg.get("content") or "").strip() or (msg.get("reasoning_content") or "").strip()
        if save_ok:
            asyncio.run(_save(user_msg, content))
        self._json(200, {"id": f"chatcmpl-spectre-{int(time.time())}", "object": "chat.completion",
                         "created": int(time.time()), "model": MODEL_ID,
                         "choices": [{"index": 0, "finish_reason": "stop",
                                      "message": {"role": "assistant", "content": content}}],
                         "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})

    def _stream(self, r, user_msg, save_ok=True):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        acc = []
        try:
            with urllib.request.urlopen(r, timeout=300) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "ignore").strip()
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    body = line[5:].strip()
                    if body == "[DONE]":
                        break
                    try:
                        chunk = json.loads(body)
                        delta = chunk["choices"][0].get("delta", {})
                        piece = delta.get("content") or ""
                        if piece:
                            acc.append(piece)
                        chunk["model"] = MODEL_ID
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                        self.wfile.flush()
                    except Exception:
                        continue
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        finally:
            reply = "".join(acc).strip()
            if reply and save_ok:
                try:
                    asyncio.run(_save(user_msg, reply))
                except Exception:
                    pass

    def _with_tools(self, glm_msgs, user_msg, save_ok, stream):
        """Corre el loop de function-calling (JARVIS) con las tools de NEXUS (gate adentro),
        devuelve el texto final. Fake-streamea a OWUI para que sea responsivo."""
        try:
            final = run_tool_loop(_brain_url(), glm_msgs, TOOLS_SPEC, dispatch, max_iters=8)
        except Exception as e:
            final = _BRAIN_DOWN if _brain_is_down(e) else f"[error en la capa de tools: {e}]"
        final = (final or "").strip() or "(sin respuesta)"
        if save_ok:
            try:
                asyncio.run(_save(user_msg, final))
            except Exception:
                pass
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                for i in range(0, len(final), 60):
                    chunk = {"choices": [{"index": 0, "delta": {"content": final[i:i+60]}}],
                             "model": MODEL_ID}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionError):
                pass
        else:
            self._json(200, {"id": f"chatcmpl-spectre-{int(time.time())}",
                             "object": "chat.completion", "created": int(time.time()),
                             "model": MODEL_ID,
                             "choices": [{"index": 0, "finish_reason": "stop",
                                          "message": {"role": "assistant", "content": final}}],
                             "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"[spectre-shim v2 stream] alma={AGENT} cerebro={_brain_url()} :{PORT}", flush=True)
    # El shim es un backend interno consumido por el chat/Panel SOUL local.
    # Exponerlo en LAN/Tailscale publicaba capacidades de tools sin una compuerta HTTP.
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
