#!/usr/bin/env python3
"""lidar_web.py — Visor WEB liviano del LiDAR (ALICE, reto RC-4).

Sirve en el navegador la imagen que genera lidar_visualizer (/tmp/lidar_detection.png),
auto-refrescada. Es MUCHO más liviano que RViz2 (evita el lag): el navegador solo baja
un PNG cada ~1s. NO toca el robot ni v18 — solo lee el archivo que ya se genera.

Uso (en la Pi, con el robot + lidar_visualizer corriendo):
    python3 lidar_web.py
    # luego abrí en el navegador:  http://localhost:8088   (o  http://IP_DE_LA_PI:8088 )

Parámetros:
    python3 lidar_web.py --port 8088 --img /tmp/lidar_detection.png --refresh 1.0
"""
import argparse
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

IMG_PATH = "/tmp/lidar_detection.png"
REFRESH_S = 1.0

PAGE = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Detección LiDAR — CapyTown</title>
<style>
 body{{margin:0;background:#0b0f1a;color:#e6edf3;font-family:system-ui,sans-serif;
      display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh}}
 h1{{font-weight:600;font-size:1.1rem;letter-spacing:.02em;margin:.6rem 0}}
 img{{max-width:92vw;max-height:82vh;border:1px solid #223;border-radius:10px;background:#fff}}
 .st{{opacity:.6;font-size:.85rem;margin-top:.5rem}}
</style></head><body>
 <h1>🛰️ Detección del LiDAR — en vivo</h1>
 <img id="v" src="/img" alt="LiDAR">
 <div class="st">refresco cada {refresh}s · liviano (sin RViz)</div>
 <script>
   const img=document.getElementById('v');
   setInterval(()=>{{ img.src='/img?t='+Date.now(); }}, {refresh_ms});
 </script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    img_path = IMG_PATH
    refresh = REFRESH_S

    def log_message(self, *a):  # silencio
        pass

    def do_GET(self):
        if self.path.startswith("/img"):
            try:
                with open(self.img_path, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except FileNotFoundError:
                self.send_error(404, "aun no hay imagen (¿corre lidar_visualizer?)")
            return
        html = PAGE.format(refresh=self.refresh, refresh_ms=int(self.refresh * 1000)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8088)
    ap.add_argument("--img", default=IMG_PATH)
    ap.add_argument("--refresh", type=float, default=REFRESH_S)
    args = ap.parse_args()
    Handler.img_path = args.img
    Handler.refresh = args.refresh
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"[lidar_web] sirviendo {args.img} en http://0.0.0.0:{args.port}  (Ctrl+C para salir)")
    print(f"[lidar_web] abrí en el navegador: http://localhost:{args.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
