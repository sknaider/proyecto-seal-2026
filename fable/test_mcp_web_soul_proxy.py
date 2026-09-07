from __future__ import annotations

import http.server
import socket
import threading
import time

import pytest

import mcp_web_soul_proxy as proxy_module
from mcp_web_soul_proxy import EgressDenied, EgressPolicy, PinnedEgressProxy


class _HTTPFixture(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"pinned-ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def _request(port: int, payload: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
        client.sendall(payload)
        chunks = []
        while True:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    return b"".join(chunks)


def _authorized(proxy: PinnedEgressProxy, request: bytes) -> bytes:
    head, marker, body = request.partition(b"\r\n\r\n")
    assert marker
    payload = head + b"\r\nProxy-Authorization: " + proxy.proxy_authorization.encode() + marker + body
    return _request(proxy.port, payload)


def test_policy_rejects_private_literal_and_mixed_dns(monkeypatch):
    policy = EgressPolicy(deny_private_networks=True)
    with pytest.raises(EgressDenied):
        policy.resolve(scheme="https", host="127.0.0.1", port=443, method="CONNECT")

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(EgressDenied):
        policy.resolve(scheme="https", host="mixed.example", port=443, method="CONNECT")


def test_connect_uses_the_already_resolved_sockaddr_once(monkeypatch):
    calls = {"dns": 0, "connect": []}

    def fake_getaddrinfo(*_args, **_kwargs):
        calls["dns"] += 1
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", 443)),
        ]

    class FakeSocket:
        def __init__(self, *_args):
            self.closed = False

        def settimeout(self, _timeout):
            pass

        def connect(self, sockaddr):
            calls["connect"].append(sockaddr)

        def close(self):
            self.closed = True

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(proxy_module.socket, "socket", FakeSocket)
    upstream = EgressPolicy().connect(
        scheme="https", host="pin.example", port=443, method="CONNECT",
    )
    assert calls == {"dns": 1, "connect": [("1.1.1.1", 443)]}
    upstream.close()


def test_live_proxy_denies_loopback_even_when_service_exists():
    origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HTTPFixture)
    thread = threading.Thread(target=origin.serve_forever, daemon=True)
    thread.start()
    try:
        with PinnedEgressProxy(EgressPolicy(deny_private_networks=True)) as proxy:
            port = origin.server_address[1]
            response = _authorized(
                proxy,
                f"GET http://127.0.0.1:{port}/ HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode(),
            )
            assert response.startswith(b"HTTP/1.1 403 Forbidden")
            assert b"pinned-ok" not in response
    finally:
        origin.shutdown()
        origin.server_close()
        thread.join(timeout=2)


def test_live_proxy_positive_control_and_slow_client_does_not_block_others():
    origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HTTPFixture)
    thread = threading.Thread(target=origin.serve_forever, daemon=True)
    thread.start()
    port = origin.server_address[1]
    policy = EgressPolicy(
        allowed_origins={f"http://127.0.0.1:{port}"},
        deny_private_networks=False,
    )
    try:
        with PinnedEgressProxy(policy) as proxy:
            slow = socket.create_connection(("127.0.0.1", proxy.port), timeout=2)
            slow.sendall(b"GET http://127.0.0.1")
            started = time.monotonic()
            response = _authorized(
                proxy,
                f"GET http://127.0.0.1:{port}/ HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode(),
            )
            elapsed = time.monotonic() - started
            slow.close()
            assert b"200 OK" in response
            assert response.endswith(b"pinned-ok")
            assert elapsed < 2.0
    finally:
        origin.shutdown()
        origin.server_close()
        thread.join(timeout=2)


def test_absolute_deadline_stops_drip_slowloris(monkeypatch):
    monkeypatch.setattr(proxy_module, "REQUEST_DEADLINE", 0.25)
    with PinnedEgressProxy(EgressPolicy()) as proxy:
        slow = socket.create_connection(("127.0.0.1", proxy.port), timeout=2)
        try:
            for byte in b"GET http://example.com/ HTTP/1.1\r\n":
                time.sleep(0.08)
                try:
                    slow.sendall(bytes([byte]))
                except (BrokenPipeError, ConnectionResetError):
                    break
            slow.settimeout(1)
            response = slow.recv(4096)
            assert response == b"" or b"408 Request Timeout" in response
        finally:
            slow.close()


def test_close_terminates_existing_client_connection():
    proxy = PinnedEgressProxy(EgressPolicy()).start()
    client = socket.create_connection(("127.0.0.1", proxy.port), timeout=2)
    client.sendall(b"GET http://example.com")
    proxy.close()
    client.settimeout(1)
    try:
        response = client.recv(4096)
    except (ConnectionResetError, OSError):
        response = b""
    finally:
        client.close()
    assert response == b""


def test_proxy_requires_unpredictable_per_instance_credential():
    with PinnedEgressProxy(EgressPolicy()) as proxy:
        response = _request(
            proxy.port,
            b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n",
        )
        assert response.startswith(b"HTTP/1.1 407 Proxy Authentication Required")


def test_duplicate_content_length_smuggling_is_denied():
    port = 8123
    policy = EgressPolicy(
        allowed_origins={f"http://127.0.0.1:{port}"}, deny_private_networks=False,
    )
    with PinnedEgressProxy(policy) as proxy:
        response = _authorized(
            proxy,
            (
                f"GET http://127.0.0.1:{port}/ HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                "Content-Length: 0\r\nContent-Length: 32\r\n\r\n"
                "POST /smuggled HTTP/1.1\r\n\r\n"
            ).encode(),
        )
        assert response.startswith(b"HTTP/1.1 403 Forbidden")
        assert b"duplicate content length denied" in response


def test_connect_is_denied_when_http_methods_are_restricted():
    policy = EgressPolicy(allowed_methods={"GET"})
    with pytest.raises(EgressDenied, match="CONNECT denied"):
        policy.resolve(scheme="https", host="example.com", port=443, method="CONNECT")


def test_dns_resolution_obeys_request_deadline(monkeypatch):
    def blocked_dns(*_args, **_kwargs):
        time.sleep(0.8)
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("1.1.1.1", 80)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", blocked_dns)
    started = time.monotonic()
    with pytest.raises(EgressDenied, match="timed out"):
        EgressPolicy().resolve(
            scheme="http", host="slow-dns.example", port=80, method="GET",
            deadline=time.monotonic() + 0.1,
        )
    assert time.monotonic() - started < 0.35
