#!/usr/bin/env python3
"""Mini-endpoint para el panel de modelos Ollama (elegir + apagar) — JARVIS.

GET  /models           -> modelos CARGADOS ahora (ollama /api/ps) con memoria/quant.
POST /stop {"model":X} -> ollama stop X  (descarga de memoria, libera RAM/VRAM).
CORS abierto para que el HTML self-contained de ALICE (estilo Studio) pegue directo.

Backend: Ollama local en la DGX Spark (:11434). Sin dependencias (stdlib).
"""
import json
import re
import subprocess
import urllib.request

_ANSI = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OLLAMA = "http://localhost:11434"
PORT = 8811
PANEL_HTML = "/home/dadito/IA/proyecto-seal/tools/model_manager/model_panel.html"


def loaded_models():
    with urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=5) as r:
        data = json.load(r)
    out = []
    for m in data.get("models", []):
        det = m.get("details", {}) or {}
        out.append({
            "name": m.get("name"),
            "size_bytes": m.get("size"),
            "size_gb": round((m.get("size") or 0) / 1e9, 2),
            "expires_at": m.get("expires_at"),
            "quant": det.get("quantization_level"),
            "params": det.get("parameter_size"),
        })
    return out


def stop_model(name):
    p = subprocess.run(["ollama", "stop", name], capture_output=True, text=True, timeout=25)
    raw = (p.stderr or p.stdout or "").strip()
    msg = _ANSI.sub("", raw).strip() or ("ok" if p.returncode == 0 else "error")
    return p.returncode == 0, msg


class H(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _html(self, code, body):
        b = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._cors()
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path == "/models":
            try:
                self._json(200, {"models": loaded_models()})
            except Exception as e:
                self._json(500, {"error": str(e)})
        elif path in ("", "/panel", "/index.html"):
            try:
                with open(PANEL_HTML, encoding="utf-8") as f:
                    self._html(200, f.read())
            except Exception as e:
                self._html(500, f"<h1>panel no disponible</h1><p>{e}</p>")
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") == "/stop":
            try:
                n = int(self.headers.get("Content-Length", 0) or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                model = body.get("model")
                if not model:
                    return self._json(400, {"error": "campo 'model' requerido"})
                ok, msg = stop_model(model)
                self._json(200 if ok else 500, {"ok": ok, "model": model, "msg": msg})
            except Exception as e:
                self._json(500, {"error": str(e)})
        else:
            self._json(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"ollama-panel-api escuchando en 0.0.0.0:{PORT}  (GET /models, POST /stop)")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
