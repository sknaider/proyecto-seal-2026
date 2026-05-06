#!/usr/bin/env python3
# JARVIS Control Server -- corre en la laptop de William (sesion de usuario)
# Recibe comandos HTTP desde DGX Spark y los ejecuta via pyautogui
# Puerto: 8891
#
# Lanzar en la laptop: py Desktop\jarvis_control_server.py

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import time
import urllib.parse
import pyautogui
import subprocess
import threading

PORT = 8891
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05


class ControlHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"[{self.address_string()}] {format % args}")

    def send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = dict(urllib.parse.parse_qsl(parsed.query))

        if parsed.path == "/ping":
            self.send_json(200, {"ok": True, "agent": "NEXUS-control"})

        elif parsed.path == "/type":
            text = params.get("text", "")
            delay = float(params.get("delay", "0.08"))
            if text:
                time.sleep(0.3)
                pyautogui.typewrite(text, interval=delay)
                pyautogui.press("enter")
            self.send_json(200, {"ok": True, "typed": text})

        elif parsed.path == "/open_chrome":
            url = params.get("url", "https://www.google.com")
            subprocess.Popen(["start", "chrome", url], shell=True)
            time.sleep(2)
            self.send_json(200, {"ok": True, "url": url})

        elif parsed.path == "/search":
            query = params.get("q", "inteligencia artificial")
            # Open Chrome with Google search
            search_url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
            subprocess.Popen(["start", "chrome", search_url], shell=True)
            time.sleep(3)
            # Click address bar and type slowly for visual effect
            pyautogui.hotkey("ctrl", "l")
            time.sleep(0.5)
            pyautogui.hotkey("ctrl", "a")
            time.sleep(0.3)
            pyautogui.typewrite(search_url[:60], interval=0.06)
            self.send_json(200, {"ok": True, "query": query})

        elif parsed.path == "/demo_sequence":
            # Full ESAN demo sequence -- incognito window, no keyboard shortcuts
            chrome_path = (
                r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            )
            query = params.get("q", "agentes de inteligencia artificial Peru 2026")
            search_url = (
                f"https://www.google.com/search?q={urllib.parse.quote(query)}"
            )
            def run_demo():
                try:
                    # 1. Abrir Chrome incognito con busqueda directa (URL)
                    subprocess.Popen(
                        [chrome_path, "--incognito", "--new-window", search_url]
                    )
                    print(f"[demo] Chrome incognito abierto: {search_url}")
                except Exception as e:
                    print(f"[demo error] {e}")
            threading.Thread(target=run_demo, daemon=True).start()
            self.send_json(200, {"ok": True, "sequence": "demo_started",
                                  "url": search_url})

        elif parsed.path == "/hotkey":
            keys = params.get("keys", "").split("+")
            if keys:
                pyautogui.hotkey(*keys)
            self.send_json(200, {"ok": True, "hotkey": keys})

        else:
            self.send_json(404, {"error": "unknown endpoint"})

    def do_POST(self):
        self.do_GET()


def main():
    print("="*50)
    print(f"  JARVIS Control Server -- Puerto {PORT}")
    print(f"  NEXUS puede controlarte desde DGX Spark")
    print(f"  Ctrl+C para detener")
    print("="*50)
    server = HTTPServer(("0.0.0.0", PORT), ControlHandler)
    print(f"[OK] Escuchando en http://0.0.0.0:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
