#!/usr/bin/env python3
"""
SOUL Egress Filter — Verificación EN VIVO end-to-end (v1)
Autora: ALICE · 2026-06-15 · rol: verificador (builder≠verifier; NEXUS construye el proxy)

Cierra el gap "privacidad teórica": el selftest prueba la LÓGICA de redacción;
ESTO prueba el PATH CABLEADO real — que el proxy, al interceptar tráfico tipo
Claude Code, redacta ANTES de reenviar a Anthropic. Verificación por efecto sobre
lo que SALE de verdad, no sobre lo que debería salir.

CÓMO (echo-capture, sin tocar Anthropic real):
  1. Levanta un "fake upstream" local que CAPTURA el body que el proxy le reenvía.
  2. Apunta el proxy (soul_egress_filter en :9098) a ese upstream local.
  3. Envía un request Anthropic-style con los 10 canarios TIER 0 embebidos.
  4. Asserta que el body CAPTURADO (lo que el proxy reenvió) tiene 0 fugas.

Si algún canario aparece en el body capturado => el proxy NO lo redactó en el
path real => FUGA en producción => NO aplicar hasta taparlo.

NOTA: requiere el proxy de NEXUS corriendo y su upstream apuntado al echo de aquí.
Es el paso de APPLY-canario: 1 agente/path antes de cablear al resto.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, "/home/dadito/IA/proyecto-seal/agents/ALICE")
from soul_egress_canary_vectors_v1 import CANARIES, assert_no_leak  # noqa: E402

ECHO_PORT = 9097  # fake upstream: el proxy de NEXUS debe reenviar AQUÍ durante el test
_captured = {"body": None}


class _EchoHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        _captured["body"] = self.rfile.read(n).decode("utf-8", "replace")
        # responder algo válido tipo Anthropic para no romper el proxy
        resp = json.dumps({"id": "msg_test", "type": "message", "role": "assistant",
                           "content": [{"type": "text", "text": "ok"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)

    def log_message(self, *a):  # silencio
        pass


def build_probe_request() -> dict:
    """Request Anthropic-style con los 10 canarios TIER 0 en campos text/content."""
    canario_text = " | ".join(tok for _c, (_d, tok) in CANARIES.items())
    return {
        "model": "claude-opus-4-8",
        "max_tokens": 16,
        "system": [{"type": "text", "text": f"Contexto SOUL sensible: {canario_text}",
                    "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": f"Procesa esto: {canario_text}"}]}],
    }


def start_echo() -> HTTPServer:
    srv = HTTPServer(("127.0.0.1", ECHO_PORT), _EchoHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def verify_captured() -> int:
    """Tras enviar el probe POR el proxy (que reenvía al echo), valida el body capturado."""
    body = _captured["body"]
    if body is None:
        print("❌ El proxy no reenvió nada al echo. ¿Está el proxy arriba y apuntado a :%d?" % ECHO_PORT)
        return 2
    leaks = assert_no_leak(body)
    if leaks:
        print(f"❌ FUGA EN VIVO: {len(leaks)}/{len(CANARIES)} canarios sobrevivieron en el reenvío -> {leaks}")
        print("   NO APLICAR: el proxy no redacta esas categorías en el path real.")
        return 1
    print(f"✅ 0/{len(CANARIES)} fugas en el body reenviado. El proxy redacta en el PATH REAL. Apto para rollout.")
    return 0


if __name__ == "__main__":
    print("Harness de verificación en vivo — listo.")
    print(f"1) Arranca echo upstream en :{ECHO_PORT} (start_echo()).")
    print("2) NEXUS: arranca el proxy :9098 con upstream -> http://127.0.0.1:%d" % ECHO_PORT)
    print("3) Envía build_probe_request() por el proxy (httpx POST a :9098/v1/messages).")
    print("4) verify_captured() -> 0 fugas = apto. Coordinar con NEXUS para el apply-canario.")
