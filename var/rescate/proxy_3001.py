#!/usr/bin/env python3
"""Puente de rescate: sirve en :3001 el webchat VIVO de :8765.

POR QUE EXISTE (7-sep-2026): el arbol del repo fue borrado del disco. El
next-server de SEAL Studio seguia vivo sobre inodos borrados y devolvia 200 en
el HTML pero 500 en TODO el JS/CSS, asi que la pagina cargaba y el login no
funcionaba. El chat de :8765 sirve su UI completa INLINE (sin leer disco), asi
que esta sana. Esto reenvia :3001 -> :8765 para devolver el acceso por la URL
de siempre mientras se restaura Studio.

NO es la reparacion definitiva de Studio: es un puente para que William entre.
"""
import http.server, socketserver, urllib.request, urllib.error

DESTINO = "http://127.0.0.1:8765"
PUERTO = int(__import__("os").environ.get("PUERTO_PUENTE", "3001"))
SALTAR = {"host", "content-length", "connection", "accept-encoding"}


class Puente(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _pasar(self, metodo):
        cuerpo = None
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            cuerpo = self.rfile.read(n)
        cab = {k: v for k, v in self.headers.items() if k.lower() not in SALTAR}
        req = urllib.request.Request(DESTINO + self.path, data=cuerpo,
                                     headers=cab, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                datos, codigo, cabs = r.read(), r.status, r.headers
        except urllib.error.HTTPError as e:          # 4xx/5xx se reenvian tal cual
            datos, codigo, cabs = e.read(), e.code, e.headers
        except Exception as e:                       # el destino no responde
            datos = f"puente :3001 -> :8765 sin respuesta del chat: {e}".encode()
            codigo, cabs = 502, {}
        self.send_response(codigo)
        for k, v in (cabs.items() if cabs else []):
            if k.lower() in ("content-length", "transfer-encoding", "connection"):
                continue
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def do_GET(self):     self._pasar("GET")
    def do_POST(self):    self._pasar("POST")
    def do_PUT(self):     self._pasar("PUT")
    def do_DELETE(self):  self._pasar("DELETE")
    def do_PATCH(self):   self._pasar("PATCH")
    def do_HEAD(self):    self._pasar("HEAD")
    def log_message(self, *a): pass


class Servidor(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    with Servidor(("0.0.0.0", PUERTO), Puente) as s:
        s.serve_forever()
