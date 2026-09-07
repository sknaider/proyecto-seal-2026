from __future__ import annotations

import json
import socket
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from scripts import verify_gtl_private_surfaces as canary


class _GTLHandler(BaseHTTPRequestHandler):
    leak_ada = False

    def do_GET(self):
        base = f"http://{self.headers['Host']}"
        if self.path == "/ada/":
            self.send_response(301)
            self.send_header("Location", f"{base}/sistema/ada/")
        elif self.path == "/sistema/ada/api":
            self.send_response(301)
            self.send_header("Location", f"{base}/sistema/ada/api/")
        elif self.path in {
            "/sistema/ada/",
            "/sistema/ada/index.html",
            "/sistema/ada//",
            "/sistema/ada%2F",
            "/sistema//ada/",
            "/sistema/ada/api/",
            "/sistema/ada/api/test",
        }:
            if self.leak_ada and self.path == "/sistema/ada/":
                self.send_response(200)
            else:
                self.send_response(302)
                self.send_header("Location", f"{base}/sistema/login?redirect=/sistema/ada/")
        elif self.path == "/sistema/openclaw/":
            self.send_response(302)
            self.send_header("Location", f"{base}/sistema/login?redirect=/sistema/openclaw/")
        elif self.path in {"/sistema/", "/sistema/login"}:
            self.send_response(200)
        else:
            self.send_response(404)
        self.end_headers()
        self.wfile.write(b"canary")

    def log_message(self, _format, *_args):
        return


@contextmanager
def _server(*, leak_ada: bool = False):
    handler = type("ConfiguredGTLHandler", (_GTLHandler,), {"leak_ada": leak_ada})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _unused_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _run(monkeypatch, capsys, server, gateway_port: int):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_gtl_private_surfaces.py",
            "--base-url",
            f"http://127.0.0.1:{server.server_port}",
            "--gateway-host",
            "127.0.0.1",
            "--gateway-port",
            str(gateway_port),
        ],
    )
    return canary.main(), json.loads(capsys.readouterr().out)


def test_location_path_normalizes_absolute_redirect():
    assert (
        canary._location_path("https://gtl.pe/sistema/login?redirect=/sistema/ada/")
        == "/sistema/login?redirect=/sistema/ada/"
    )


def test_canary_accepts_private_surfaces_and_healthy_controls(monkeypatch, capsys):
    with _server() as server:
        result, payload = _run(monkeypatch, capsys, server, _unused_port())
    assert result == 0
    assert payload["ok"] is True
    assert payload["observations"]["ada_fake_session"]["status"] == 302
    assert payload["observations"]["login"]["status"] == 200


def test_canary_rejects_public_ada_page(monkeypatch, capsys):
    with _server(leak_ada=True) as server:
        result, payload = _run(monkeypatch, capsys, server, _unused_port())
    assert result == 1
    assert "ada_page_not_login_redirect" in payload["failures"]


def test_canary_rejects_public_gateway_port(monkeypatch, capsys):
    with socket.socket() as gateway:
        gateway.bind(("127.0.0.1", 0))
        gateway.listen(1)
        with _server() as server:
            result, payload = _run(monkeypatch, capsys, server, gateway.getsockname()[1])
    assert result == 1
    assert "gateway_publicly_reachable" in payload["failures"]
