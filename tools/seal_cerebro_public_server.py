#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seal_cerebro_public_server.py — GATE/ALLOWLIST del visor cerebro (NEXUS, #22).
================================================================================
Reemplaza `python -m http.server` (que sirve TODO el directorio) por un server que
expone SOLO una ALLOWLIST de paths sanitizados. Cierra de raíz el whack-a-mole de
redactar archivo-por-archivo: los vectores "otros archivos servidos" (brains/,
dashboard.html, viewer.html, Memories/*.md, *.md) simplemente NO son alcanzables.

Filtra en tiempo de REQUEST → robusto al cron de ALICE (puede regenerar el dir
completo; solo los paths de la allowlist responden 200, el resto 404).

Allowlist (exacta, sin traversal):
  /                 → 302 a /viewer_3d.html
  /viewer_3d.html   → el visor 3D (debe estar sanitizado: 0 private, 0 PII)
  /graph.json       → datos del grafo (sanitizados)
Cualquier otro path → 404.

NO sanitiza el CONTENIDO de esos 2 archivos — eso es el carril de ALICE (redacción
de previews + labels PII). Este server garantiza la SUPERFICIE; el gate de NEXUS
verifica el contenido. Defensa en capas.

Uso: seal_cerebro_public_server.py --root <export_dir> --port 8799 --bind 0.0.0.0
"""
from __future__ import annotations
import argparse
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Allowlist EXACTA de paths servibles. Nada fuera de esto se expone.
_ALLOW = {"/viewer_3d.html", "/graph.json"}
_INDEX = "/viewer_3d.html"
_MIME = {".html": "text/html; charset=utf-8", ".json": "application/json; charset=utf-8"}


class AllowlistHandler(BaseHTTPRequestHandler):
    server_version = "SEALCerebroGate/1.0"
    root = os.getcwd()  # seteado en main()

    def _deny(self, code: int = 404) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"404 Not Found\n" if code == 404 else b"403 Forbidden\n")

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        if path == "/":
            self.send_response(302)
            self.send_header("Location", _INDEX)
            self.end_headers()
            return
        # Allowlist exacta — sin normalización de '..' que pueda escapar; comparación literal.
        if path not in _ALLOW:
            self._deny(404)
            return
        # path está en allowlist → resolver dentro de root, confirmar que NO escapa (defensa extra)
        rel = path.lstrip("/")
        full = os.path.realpath(os.path.join(self.root, rel))
        if not full.startswith(os.path.realpath(self.root) + os.sep) or not os.path.isfile(full):
            self._deny(404)
            return
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            self._deny(404)
            return
        ext = os.path.splitext(full)[1].lower()
        self.send_response(200)
        self.send_header("Content-Type", _MIME.get(ext, "application/octet-stream"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:  # silencioso salvo errores
        return


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="Directorio del export (de donde se sirve la allowlist).")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--bind", default="0.0.0.0")
    a = ap.parse_args()
    AllowlistHandler.root = os.path.realpath(a.root)
    httpd = ThreadingHTTPServer((a.bind, a.port), AllowlistHandler)
    print(f"[cerebro-gate] allowlist {sorted(_ALLOW)} desde {AllowlistHandler.root} en {a.bind}:{a.port}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
