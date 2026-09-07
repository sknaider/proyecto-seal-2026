#!/usr/bin/env python3
"""Proxy de egress con resolución pinneada para el navegador SOUL.

La compuerta CDP decide política y este proxy la hace cumplir en el instante de
conexión. El hostname se resuelve una sola vez, se rechaza cualquier respuesta
no global y el socket se conecta a ESA IP concreta. Chrome conserva el hostname
original para Host/SNI, pero no puede re-resolverlo hacia loopback/LAN después
del chequeo (DNS rebinding/TOCTOU).
"""

from __future__ import annotations

import base64
import hmac
import ipaddress
import queue
import select
import re
import secrets
import socket
import socketserver
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Iterable


MAX_HEADER_BYTES = 64 * 1024
MAX_BODY_BYTES = 16 * 1024 * 1024
IO_TIMEOUT = 15.0
REQUEST_DEADLINE = 5.0
MAX_CLIENTS = 32
DNS_TIMEOUT = 2.0
DNS_WORKERS = 4
DNS_QUEUE = 8
HEADER_NAME_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}")
HOP_BY_HOP = {
    "connection", "proxy-connection", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade",
}


class EgressDenied(RuntimeError):
    """La conexión solicitada viola la política de egress."""


@dataclass(frozen=True)
class PinnedTarget:
    host: str
    port: int
    family: int
    sockaddr: tuple
    ip: str


class _ResolverJob:
    def __init__(self, resolver, host: str, port: int) -> None:
        self.resolver = resolver
        self.host = host
        self.port = port
        self.done = threading.Event()
        self.answers = None
        self.error: Exception | None = None


class _BoundedResolver:
    """DNS acotado: un NSS bloqueado nunca consume los 32 handlers HTTP.

    ``getaddrinfo`` no ofrece cancelacion portable. Los workers son daemon y el
    pool/queue tienen cotas duras; al vencer el deadline el request falla
    cerrado, aunque una llamada NSS defectuosa quede retenida en uno de los
    cuatro workers.
    """

    def __init__(self) -> None:
        self.jobs: queue.Queue[_ResolverJob] = queue.Queue(maxsize=DNS_QUEUE)
        for index in range(DNS_WORKERS):
            threading.Thread(
                target=self._worker,
                name=f"mcp-web-soul-dns-{index}",
                daemon=True,
            ).start()

    def _worker(self) -> None:
        while True:
            job = self.jobs.get()
            try:
                job.answers = job.resolver(
                    job.host,
                    job.port,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                )
            except Exception as exc:  # NSS/resolver boundary
                job.error = exc
            finally:
                job.done.set()
                self.jobs.task_done()

    def resolve(self, host: str, port: int, *, timeout: float):
        if timeout <= 0:
            raise EgressDenied("destination DNS resolution timed out")
        # Capturar el callable hace que una prueba/override sea consistente aun
        # si el caller restaura socket.getaddrinfo mientras el worker termina.
        job = _ResolverJob(socket.getaddrinfo, host, port)
        try:
            self.jobs.put_nowait(job)
        except queue.Full as exc:
            raise EgressDenied("destination DNS resolver busy") from exc
        if not job.done.wait(timeout):
            raise EgressDenied("destination DNS resolution timed out")
        if job.error is not None:
            raise EgressDenied("destination DNS resolution failed") from job.error
        return job.answers


_RESOLVER = _BoundedResolver()


class EgressPolicy:
    """Valida origen/método y devuelve direcciones globales ya pinneadas."""

    def __init__(
        self,
        *,
        allowed_origins: Iterable[str] | None = None,
        allowed_methods: Iterable[str] | None = None,
        deny_private_networks: bool = True,
    ) -> None:
        self.allowed_origins = (
            frozenset(str(value) for value in allowed_origins)
            if allowed_origins is not None else None
        )
        self.allowed_methods = (
            frozenset(str(value).upper() for value in allowed_methods)
            if allowed_methods is not None else None
        )
        self.deny_private_networks = bool(deny_private_networks)

    @staticmethod
    def canonical_origin(scheme: str, host: str, port: int) -> str:
        normalized = host.rstrip(".").encode("idna").decode("ascii").lower()
        return f"{scheme}://{normalized}:{int(port)}"

    def resolve(
        self, *, scheme: str, host: str, port: int, method: str,
        deadline: float | None = None,
    ) -> tuple[PinnedTarget, ...]:
        normalized = str(host or "").rstrip(".").encode("idna").decode("ascii").lower()
        if not normalized:
            raise EgressDenied("private or local destination denied")
        if self.deny_private_networks and (
            normalized == "localhost" or normalized.endswith(".local")
        ):
            raise EgressDenied("private or local destination denied")
        method = str(method or "GET").upper()
        if self.allowed_methods is not None and method == "CONNECT":
            raise EgressDenied("CONNECT denied while HTTP methods are restricted")
        if self.allowed_methods is not None and method not in self.allowed_methods:
            raise EgressDenied(f"method denied: {method}")
        origin = self.canonical_origin(scheme, normalized, port)
        if self.allowed_origins is not None and origin not in self.allowed_origins:
            raise EgressDenied(f"origin denied: {origin}")
        dns_timeout = DNS_TIMEOUT
        if deadline is not None:
            dns_timeout = min(dns_timeout, max(0.0, deadline - time.monotonic()))
        answers = _RESOLVER.resolve(normalized, int(port), timeout=dns_timeout)
        targets: list[PinnedTarget] = []
        seen: set[tuple[int, str]] = set()
        for family, socktype, proto, _canonname, sockaddr in answers:
            if socktype != socket.SOCK_STREAM or proto not in (0, socket.IPPROTO_TCP):
                continue
            ip = str(sockaddr[0])
            try:
                address = ipaddress.ip_address(ip)
            except ValueError as exc:
                raise EgressDenied("invalid DNS address") from exc
            if self.deny_private_networks and not address.is_global:
                raise EgressDenied("private or non-routable DNS answer denied")
            marker = (family, ip)
            if marker not in seen:
                seen.add(marker)
                targets.append(PinnedTarget(normalized, int(port), family, sockaddr, ip))
        if not targets:
            raise EgressDenied("destination has no global TCP address")
        return tuple(targets)

    def connect(
        self, *, scheme: str, host: str, port: int, method: str,
        deadline: float | None = None,
    ) -> socket.socket:
        last_error: OSError | None = None
        for target in self.resolve(
            scheme=scheme, host=host, port=port, method=method, deadline=deadline,
        ):
            upstream = socket.socket(target.family, socket.SOCK_STREAM)
            connect_timeout = IO_TIMEOUT
            if deadline is not None:
                connect_timeout = min(connect_timeout, max(0.0, deadline - time.monotonic()))
            if connect_timeout <= 0:
                upstream.close()
                raise EgressDenied("upstream connection deadline exceeded")
            upstream.settimeout(connect_timeout)
            try:
                # Conecta al sockaddr ya resuelto; no vuelve a consultar DNS.
                upstream.connect(target.sockaddr)
                return upstream
            except OSError as exc:
                last_error = exc
                upstream.close()
        raise EgressDenied("no global destination address accepted connection") from last_error


def _authority(value: str, default_port: int) -> tuple[str, int]:
    try:
        parsed = urllib.parse.urlsplit("//" + str(value), allow_fragments=False)
        host = parsed.hostname
        port = parsed.port or default_port
    except ValueError as exc:
        raise EgressDenied("invalid target authority") from exc
    if not host or parsed.username or parsed.password or not (1 <= int(port) <= 65535):
        raise EgressDenied("invalid target authority")
    return host, int(port)


def _relay(left: socket.socket, right: socket.socket) -> None:
    peers = {left: right, right: left}
    live = set(peers)
    left.settimeout(IO_TIMEOUT)
    right.settimeout(IO_TIMEOUT)
    while live:
        try:
            ready, _, _ = select.select(list(live), [], [], IO_TIMEOUT)
        except (OSError, ValueError):
            return
        if not ready:
            return
        for source in ready:
            destination = peers[source]
            try:
                chunk = source.recv(65536)
            except (ConnectionResetError, OSError):
                chunk = b""
            if not chunk:
                live.discard(source)
                try:
                    destination.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                continue
            try:
                destination.sendall(chunk)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return


class _ProxyHandler(socketserver.StreamRequestHandler):
    def setup(self) -> None:
        super().setup()
        self._request_deadline = time.monotonic() + REQUEST_DEADLINE
        self.server.register_connection(self.connection)  # type: ignore[attr-defined]

    def finish(self) -> None:
        self.server.unregister_connection(self.connection)  # type: ignore[attr-defined]
        try:
            super().finish()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    @property
    def policy(self) -> EgressPolicy:
        return self.server.policy  # type: ignore[attr-defined]

    def _line(self) -> bytes:
        line = bytearray()
        while len(line) <= MAX_HEADER_BYTES:
            remaining = self._request_deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("absolute request deadline exceeded")
            self.connection.settimeout(remaining)
            byte = self.rfile.read(1)
            if not byte:
                return bytes(line)
            line.extend(byte)
            if byte == b"\n":
                return bytes(line)
        raise EgressDenied("request header too large")

    def _body(self, size: int) -> bytes:
        body = bytearray()
        while len(body) < size:
            remaining = self._request_deadline - time.monotonic()
            if remaining <= 0:
                raise socket.timeout("absolute request deadline exceeded")
            self.connection.settimeout(remaining)
            part = self.rfile.read(min(65536, size - len(body)))
            if not part:
                break
            body.extend(part)
        return bytes(body)

    def _headers(self) -> list[tuple[str, str]]:
        headers: list[tuple[str, str]] = []
        consumed = 0
        while True:
            raw = self._line()
            consumed += len(raw)
            if consumed > MAX_HEADER_BYTES:
                raise EgressDenied("request headers too large")
            if raw in (b"\r\n", b"\n", b""):
                break
            if raw[:1] in (b" ", b"\t"):
                raise EgressDenied("obsolete folded headers denied")
            try:
                name, value = raw.decode("iso-8859-1").split(":", 1)
            except ValueError as exc:
                raise EgressDenied("malformed proxy header") from exc
            name = name.strip()
            if not HEADER_NAME_RE.fullmatch(name):
                raise EgressDenied("invalid proxy header name")
            headers.append((name, value.strip()))
        return headers

    def _authenticate(self, headers: list[tuple[str, str]]) -> None:
        supplied = [value for name, value in headers if name.lower() == "proxy-authorization"]
        if len(supplied) != 1:
            raise EgressDenied("proxy authentication required")
        expected = self.server.proxy_authorization  # type: ignore[attr-defined]
        if not hmac.compare_digest(supplied[0].encode(), expected.encode()):
            raise EgressDenied("proxy authentication required")

    def _error(self, code: int, reason: str) -> None:
        safe = reason.replace("\r", " ").replace("\n", " ")[:200]
        body = (safe + "\n").encode("utf-8")
        status = {403: "Forbidden", 407: "Proxy Authentication Required", 408: "Request Timeout", 502: "Bad Gateway"}.get(
            code, "Proxy Error",
        )
        challenge = 'Proxy-Authenticate: Basic realm="SEAL pinned egress"\r\n' if code == 407 else ""
        try:
            self.wfile.write(
                f"HTTP/1.1 {code} {status}\r\n{challenge}Connection: close\r\n"
                f"Content-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\n\r\n".encode()
                + body
            )
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def handle(self) -> None:
        try:
            request_line = self._line()
            if not request_line:
                return
            try:
                method, target, version = request_line.decode("iso-8859-1").strip().split(" ", 2)
            except ValueError as exc:
                raise EgressDenied("malformed proxy request line") from exc
            if version not in {"HTTP/1.0", "HTTP/1.1"}:
                raise EgressDenied("unsupported HTTP version")
            headers = self._headers()
            try:
                self._authenticate(headers)
            except EgressDenied as exc:
                self._error(407, str(exc))
                return
            if method.upper() == "CONNECT":
                self._connect_tunnel(target)
            else:
                self._forward_http(method.upper(), target, version, headers)
        except EgressDenied as exc:
            self._error(403, str(exc))
        except (TimeoutError, socket.timeout):
            self._error(408, "proxy request timeout")
        except (BrokenPipeError, ConnectionResetError):
            return
        except OSError:
            self._error(502, "upstream connection failed")

    def _connect_tunnel(self, authority: str) -> None:
        host, port = _authority(authority, 443)
        upstream = self.policy.connect(
            scheme="https", host=host, port=port, method="CONNECT",
            deadline=self._request_deadline,
        )
        try:
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.wfile.flush()
            _relay(self.connection, upstream)
        finally:
            upstream.close()

    def _forward_http(
        self, method: str, target: str, version: str, headers: list[tuple[str, str]],
    ) -> None:
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme != "http" or not parsed.hostname or parsed.username or parsed.password:
            raise EgressDenied("HTTP proxy requires an absolute public URL")
        try:
            port = parsed.port or 80
        except ValueError as exc:
            raise EgressDenied("invalid HTTP target port") from exc
        content_length = 0
        content_lengths: list[str] = []
        hosts: list[str] = []
        forwarded: list[tuple[str, str]] = []
        for name, value in headers:
            lowered = name.lower()
            if lowered == "transfer-encoding":
                raise EgressDenied("chunked proxy requests are not supported")
            if lowered == "content-length":
                content_lengths.append(value)
                try:
                    content_length = int(value)
                except ValueError as exc:
                    raise EgressDenied("invalid content length") from exc
                if content_length < 0 or content_length > MAX_BODY_BYTES:
                    raise EgressDenied("request body too large")
            if lowered == "host":
                hosts.append(value)
            if lowered not in HOP_BY_HOP and lowered not in {"content-length", "host"}:
                forwarded.append((name, value))
        if len(content_lengths) > 1:
            raise EgressDenied("duplicate content length denied")
        if len(hosts) > 1:
            raise EgressDenied("duplicate host denied")
        body = self._body(content_length) if content_length else b""
        if len(body) != content_length:
            raise EgressDenied("short request body")
        upstream = self.policy.connect(
            scheme="http", host=parsed.hostname, port=port, method=method,
            deadline=self._request_deadline,
        )
        try:
            path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            upstream.sendall(f"{method} {path} {version}\r\n".encode("iso-8859-1"))
            authority = parsed.hostname
            if port != 80:
                authority = f"{authority}:{port}"
            upstream.sendall(f"Host: {authority}\r\n".encode("idna"))
            for name, value in forwarded:
                upstream.sendall(f"{name}: {value}\r\n".encode("iso-8859-1"))
            if content_length:
                upstream.sendall(f"Content-Length: {content_length}\r\n".encode())
            upstream.sendall(b"Connection: close\r\n\r\n" + body)
            while True:
                chunk = upstream.recv(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
            self.wfile.flush()
        finally:
            upstream.close()


class _PinnedProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(
        self, server_address: tuple[str, int], policy: EgressPolicy,
        proxy_authorization: str,
    ) -> None:
        self.policy = policy
        self.proxy_authorization = proxy_authorization
        self._slots = threading.BoundedSemaphore(MAX_CLIENTS)
        self._connections: set[socket.socket] = set()
        self._connections_lock = threading.Lock()
        super().__init__(server_address, _ProxyHandler)

    def verify_request(self, request, client_address) -> bool:
        return self._slots.acquire(blocking=False)

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def register_connection(self, connection: socket.socket) -> None:
        with self._connections_lock:
            self._connections.add(connection)

    def unregister_connection(self, connection: socket.socket) -> None:
        with self._connections_lock:
            self._connections.discard(connection)

    def close_active_connections(self) -> None:
        with self._connections_lock:
            connections = tuple(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                connection.close()
            except OSError:
                pass


class PinnedEgressProxy:
    """Lifecycle del proxy loopback usado por una única instancia CDP."""

    def __init__(
        self, policy: EgressPolicy, *, username: str | None = None, password: str | None = None,
    ) -> None:
        self.policy = policy
        self.username = username or secrets.token_urlsafe(12)
        self.password = password or secrets.token_urlsafe(24)
        encoded = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        self.proxy_authorization = f"Basic {encoded}"
        self.server: _PinnedProxyServer | None = None
        self.thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self.server is None:
            raise RuntimeError("egress proxy no iniciado")
        return int(self.server.server_address[1])

    def start(self) -> "PinnedEgressProxy":
        if self.server is not None:
            return self
        self.server = _PinnedProxyServer(
            ("127.0.0.1", 0), self.policy, self.proxy_authorization,
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name=f"mcp-web-soul-egress-{self.port}",
            daemon=True,
        )
        self.thread.start()
        return self

    def close(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.close_active_connections()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=3)
        self.server = None
        self.thread = None

    def __enter__(self) -> "PinnedEgressProxy":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False
