"""Tests for WebhookServer — no live network sockets required."""

import asyncio
import hashlib
import hmac
import json
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from tools.gateway.webhooks import (
    WebhookEvent,
    WebhookServer,
    WebhookError,
    verify_signature,
    verify_github_signature,
    _compute_hmac,
    from_env,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_body(payload: dict) -> bytes:
    return json.dumps(payload).encode()


def _sig(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _dispatch(server: WebhookServer, method: str, path: str,
                    body: bytes = b"", headers: dict | None = None) -> tuple[int, bytes]:
    """Drive the server's request handler through a pair of in-memory streams."""
    extra_headers = headers or {}
    header_lines = "\r\n".join(f"{k}: {v}" for k, v in extra_headers.items())
    raw = (
        f"{method} {path} HTTP/1.1\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"{header_lines}\r\n"
        "\r\n"
    ).encode() + body

    reader = asyncio.StreamReader()
    reader.feed_data(raw)
    reader.feed_eof()

    output = bytearray()

    class FakeWriter:
        def write(self, data):
            output.extend(data)
        async def drain(self):
            pass
        def close(self):
            pass
        async def wait_closed(self):
            pass

    await server._handle_connection(reader, FakeWriter())
    response = bytes(output)
    first_line = response.split(b"\r\n", 1)[0].decode()
    status = int(first_line.split()[1])
    body_part = response.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in response else b""
    return status, body_part


# ── tests ─────────────────────────────────────────────────────────────────────

async def test_verify_signature_valid():
    body = b"hello world"
    secret = "mysecret"
    sig = _sig(body, secret)
    assert verify_signature(body, secret, sig)


async def test_verify_signature_invalid():
    assert not verify_signature(b"hello", "secret", "sha256=bad")


async def test_verify_github_signature_valid():
    body = b'{"action":"push"}'
    secret = "ghsecret"
    sig = _sig(body, secret)
    assert verify_github_signature(body, secret, sig)


async def test_verify_github_signature_missing_prefix():
    assert not verify_github_signature(b"body", "secret", "badhex")


async def test_route_not_found_returns_404():
    server = WebhookServer()
    status, _ = await _dispatch(server, "POST", "/unknown", b"{}")
    assert status == 404


async def test_method_not_allowed_returns_405():
    server = WebhookServer()
    async def handler(e): return "ok"
    server.register("test", handler)
    status, _ = await _dispatch(server, "GET", "/test", b"{}")
    assert status == 405


async def test_valid_request_dispatched():
    received: list[WebhookEvent] = []

    async def handler(event: WebhookEvent):
        received.append(event)
        return "processed"

    server = WebhookServer()
    server.register("github", handler)

    body = _make_body({"action": "push"})
    status, resp = await _dispatch(
        server, "POST", "/github", body,
        headers={"x-github-event": "push"}
    )
    assert status == 200
    assert resp == b"processed"
    assert len(received) == 1
    assert received[0].source == "github"
    assert received[0].event_type == "push"
    assert received[0].payload == {"action": "push"}


async def test_signature_verification_pass():
    received: list[WebhookEvent] = []
    secret = "s3cr3t"

    async def handler(event):
        received.append(event)
        return "ok"

    server = WebhookServer()
    server.register("secure", handler, secret=secret)

    body = _make_body({"msg": "hello"})
    sig = _sig(body, secret)
    status, _ = await _dispatch(
        server, "POST", "/secure", body,
        headers={"x-hub-signature-256": sig}
    )
    assert status == 200
    assert len(received) == 1


async def test_signature_verification_fail():
    async def handler(e): return "ok"
    server = WebhookServer()
    server.register("secure", handler, secret="correctsecret")

    body = _make_body({"msg": "hi"})
    status, _ = await _dispatch(
        server, "POST", "/secure", body,
        headers={"x-hub-signature-256": "sha256=wrongsig"}
    )
    assert status == 401


async def test_no_secret_skips_verification():
    received: list[WebhookEvent] = []

    async def handler(event):
        received.append(event)
        return "ok"

    server = WebhookServer()
    server.register("open", handler, secret=None)

    body = _make_body({"data": 1})
    status, _ = await _dispatch(server, "POST", "/open", body)
    assert status == 200
    assert len(received) == 1


async def test_empty_body_parsed_as_empty_dict():
    received: list[WebhookEvent] = []

    async def handler(event):
        received.append(event)
        return "ok"

    server = WebhookServer()
    server.register("ping", handler)
    status, _ = await _dispatch(server, "POST", "/ping", b"")
    assert status == 200
    assert received[0].payload == {}


async def test_handler_exception_returns_500():
    async def bad_handler(event):
        raise RuntimeError("crash")

    server = WebhookServer()
    server.register("crash", bad_handler)
    body = _make_body({})
    status, _ = await _dispatch(server, "POST", "/crash", body)
    assert status == 500


async def test_custom_path_routing():
    received: list[WebhookEvent] = []

    async def handler(event):
        received.append(event)
        return "ok"

    server = WebhookServer()
    server.register("alerts", handler, path="/custom/alerts")
    body = _make_body({"alert": "cpu high"})
    status, _ = await _dispatch(server, "POST", "/custom/alerts", body)
    assert status == 200
    assert received[0].source == "alerts"


async def test_from_env_defaults():
    import os
    with __import__("unittest.mock").mock.patch.dict(os.environ, {}, clear=False):
        server = from_env()
    assert server.host == "0.0.0.0"
    assert server.port == 9100


async def test_multiple_routes():
    results: list[str] = []

    async def gh_handler(e):
        results.append("github")
        return "gh"

    async def grafana_handler(e):
        results.append("grafana")
        return "graf"

    server = WebhookServer()
    server.register("github", gh_handler)
    server.register("grafana", grafana_handler)

    await _dispatch(server, "POST", "/github", _make_body({"ev": "push"}))
    await _dispatch(server, "POST", "/grafana", _make_body({"ev": "alert"}))

    assert results == ["github", "grafana"]


async def main() -> int:
    tests = [
        test_verify_signature_valid,
        test_verify_signature_invalid,
        test_verify_github_signature_valid,
        test_verify_github_signature_missing_prefix,
        test_route_not_found_returns_404,
        test_method_not_allowed_returns_405,
        test_valid_request_dispatched,
        test_signature_verification_pass,
        test_signature_verification_fail,
        test_no_secret_skips_verification,
        test_empty_body_parsed_as_empty_dict,
        test_handler_exception_returns_500,
        test_custom_path_routing,
        test_from_env_defaults,
        test_multiple_routes,
    ]
    passed = 0
    for t in tests:
        try:
            await t()
            print(f"[OK] {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {t.__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    return 0 if passed == len(tests) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
