#!/usr/bin/env python3
"""Narrow host-side chat relay for network-none JARVIS-u116."""

from __future__ import annotations

import argparse
import hmac
import http.server
import json
import os
import socket
import socketserver
import stat
from pathlib import Path
from typing import Any
from urllib import error, request

INSTANCE = "JARVIS-u116"
AGENT = "JARVIS"
UPSTREAM = "http://127.0.0.1:8765"
MAX_BODY = 128 * 1024
MAX_RESPONSE = 512 * 1024


class RelayDenied(RuntimeError):
    pass


class _DenyRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise RelayDenied("upstream redirect denied")


def _read_token(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid not in {0, os.geteuid()}:
            raise RelayDenied("relay credential ownership invalid")
        if stat.S_IMODE(meta.st_mode) not in {0o400, 0o440, 0o600}:
            raise RelayDenied("relay credential permissions invalid")
        raw = os.read(fd, 513)
    finally:
        os.close(fd)
    token = raw.decode("ascii").strip()
    if not 32 <= len(token) <= 512 or any(c.isspace() for c in token):
        raise RelayDenied("relay credential shape invalid")
    return token


class ChatRelay:
    def __init__(self, token_file: Path, opener=None):
        self.token_file = token_file
        _read_token(token_file)
        self.opener = opener or request.build_opener(
            request.ProxyHandler({}), _DenyRedirect()
        ).open

    def authorized(self, supplied: str) -> bool:
        return hmac.compare_digest(str(supplied or ""), "Bearer " + _read_token(self.token_file))

    def forward(self, method: str, path: str, body: bytes = b"") -> tuple[int, bytes, str]:
        token = _read_token(self.token_file)
        if method == "GET" and path.startswith("/api/user-clones/inbox?"):
            from urllib.parse import parse_qs, urlencode, urlsplit
            query = parse_qs(urlsplit(path).query, keep_blank_values=True)
            if set(query) - {"cursor", "limit", "initialize"} or any(len(v) != 1 for v in query.values()):
                raise RelayDenied("inbox query denied")
            normalized = urlencode({key: values[0] for key, values in query.items()})
            upstream_path = "/api/user-clones/inbox?" + normalized
            data = None
        elif method == "POST" and path == "/api/agents/send":
            try:
                payload = json.loads(body.decode("utf-8"))
            except Exception as exc:
                raise RelayDenied("invalid send payload") from exc
            allowed = {
                "from", "to", "type", "channel", "message", "session_key",
                "in_reply_to", "instance_id", "idempotency_key",
            }
            if not isinstance(payload, dict) or set(payload) - allowed:
                raise RelayDenied("send payload fields denied")
            if (
                str(payload.get("from", "")).upper() != AGENT
                or payload.get("instance_id") != INSTANCE
                or payload.get("type") != "conversation"
                or not str(payload.get("channel", "")).startswith("dm:jarvis:")
                or not hmac.compare_digest(str(payload.get("session_key", "")), token)
            ):
                raise RelayDenied("send identity denied")
            payload["from"] = AGENT
            payload["instance_id"] = INSTANCE
            payload["session_key"] = token
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            upstream_path = "/api/agents/send"
        else:
            raise RelayDenied("route denied")
        req = request.Request(
            UPSTREAM + upstream_path,
            data=data,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with self.opener(req, timeout=10) as response:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise RelayDenied("upstream response oversized")
                return int(response.status), raw, str(response.headers.get("Content-Type") or "application/json")
        except error.HTTPError as exc:
            raw = exc.read(MAX_RESPONSE + 1)
            return int(exc.code), raw[:MAX_RESPONSE], "application/json"
        except (error.URLError, TimeoutError) as exc:
            raise RelayDenied("chat upstream unavailable") from exc


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, *args: Any, max_workers: int = 16, **kwargs: Any):
        self._slots = __import__("threading").BoundedSemaphore(max_workers)
        super().__init__(*args, **kwargs)

    def process_request(self, sock: socket.socket, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            sock.close()
            return
        try:
            super().process_request(sock, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request_sock: socket.socket, client_address: Any) -> None:
        try:
            super().process_request_thread(request_sock, client_address)
        finally:
            self._slots.release()


def make_handler(relay: ChatRelay):
    class Handler(http.server.BaseHTTPRequestHandler):
        def setup(self):
            super().setup(); self.connection.settimeout(2.0)
        def log_message(self, *_args): return
        def _send(self, status: int, data: bytes, content_type="application/json"):
            try:
                self.send_response(status); self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data))); self.send_header("Connection", "close")
                self.end_headers(); self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                self.close_connection = True
        def _auth(self):
            ok = relay.authorized(self.headers.get("Authorization", ""))
            if not ok: self._send(401, b'{"ok":false,"error":"relay_auth_required"}')
            return ok
        def do_GET(self):  # noqa: N802
            if not self._auth(): return
            if self.path == "/health":
                self._send(200, b'{"ok":true,"instance":"JARVIS-u116","relay":"chat-only"}')
                return
            try: self._send(*relay.forward("GET", self.path))
            except RelayDenied as exc: self._send(403, json.dumps({"ok":False,"error":str(exc)}).encode())
        def do_POST(self):  # noqa: N802
            if not self._auth(): return
            if self.path != "/api/agents/send": self._send(404, b'{"ok":false}'); return
            try: size = int(self.headers.get("Content-Length") or "0")
            except ValueError: size = 0
            if not 0 < size <= MAX_BODY: self._send(413, b'{"ok":false}'); return
            try:
                body = self.rfile.read(size)
                if len(body) != size: raise RelayDenied("incomplete body")
                self._send(*relay.forward("POST", self.path, body))
            except socket.timeout: self._send(408, b'{"ok":false,"error":"timeout"}')
            except RelayDenied as exc: self._send(403, json.dumps({"ok":False,"error":str(exc)}).encode())
    return Handler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    args = parser.parse_args()
    relay = ChatRelay(args.token_file)
    args.socket.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    if args.socket.exists():
        if not stat.S_ISSOCK(args.socket.lstat().st_mode): raise SystemExit("unsafe socket path")
        args.socket.unlink()
    with _Server(str(args.socket), make_handler(relay)) as server:
        os.chmod(args.socket, 0o660); server.serve_forever()
    return 0


if __name__ == "__main__": raise SystemExit(main())
