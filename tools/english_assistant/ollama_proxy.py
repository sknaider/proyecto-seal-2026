#!/usr/bin/env python3
"""Proxy: expone Ollama (127.0.0.1:11434) a la red en 0.0.0.0:11435"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.request, urllib.error

OLLAMA = "http://127.0.0.1:11434"

class Proxy(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = urllib.request.Request(
                OLLAMA + self.path,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(str(e).encode())

    def log_message(self, *a): pass

if __name__ == "__main__":
    srv = HTTPServer(("0.0.0.0", 11435), Proxy)
    print("Ollama proxy en 0.0.0.0:11435 → 127.0.0.1:11434")
    srv.serve_forever()
