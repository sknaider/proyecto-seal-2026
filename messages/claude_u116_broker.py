#!/usr/bin/env python3
"""Provider-credential-zero Claude transport for isolated JARVIS-u116.

The clone speaks the narrow Ollama ``/api/chat`` contract over a Unix socket.
The clone receives only a narrow request capability, never the Anthropic key.
The broker adds no SOUL memory or identity: u116 supplies its isolated prompt
and encrypted history.  Absolute custody still depends on host-root custody;
an actor with sudo/docker-root equivalence is outside this process boundary.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import hmac
import http.server
import json
import os
import socket
import socketserver
import stat
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error, request

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


INSTANCE = "JARVIS-u116"
PROVIDER = "Anthropic"
MODEL = "claude-sonnet-5"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
MAX_OUTPUT_TOKENS = 1_500
DEFAULT_REQUESTS_PER_HOUR = 30
DEFAULT_DAILY_BUDGET_USD = 1.00
INPUT_USD_PER_MILLION = 3.00
OUTPUT_USD_PER_MILLION = 15.00


class BrokerDenied(RuntimeError):
    """A fail-closed policy denial safe to report without secret material."""


class _DenyRedirect(request.HTTPRedirectHandler):
    """Never forward the dedicated key to a redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise BrokerDenied("Anthropic redirect denied")


@dataclass(frozen=True)
class BrokerConfig:
    key_file: Path
    client_capability_file: Path
    consent_file: Path
    consent_signature_file: Path
    consent_public_key_file: Path
    socket_path: Path
    state_dir: Path
    requests_per_hour: int = DEFAULT_REQUESTS_PER_HOUR
    daily_budget_usd: float = DEFAULT_DAILY_BUDGET_USD
    max_output_tokens: int = MAX_OUTPUT_TOKENS


def _read_secure_regular(
    path: Path, *, max_bytes: int, allowed_owners: set[int] | None = None,
    allowed_modes: set[int] | None = None,
) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise BrokerDenied(f"required private artifact unavailable: {path.name}") from exc
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode):
            raise BrokerDenied(f"private artifact is not a regular file: {path.name}")
        owners = allowed_owners or {os.geteuid()}
        modes = allowed_modes or {0o400, 0o600}
        if meta.st_uid not in owners:
            raise BrokerDenied(f"private artifact owner mismatch: {path.name}")
        if stat.S_IMODE(meta.st_mode) not in modes:
            raise BrokerDenied(f"private artifact permissions are invalid: {path.name}")
        if meta.st_size <= 0 or meta.st_size > max_bytes:
            raise BrokerDenied(f"private artifact is empty or oversized: {path.name}")
        data = os.read(fd, max_bytes + 1)
        if len(data) > max_bytes:
            raise BrokerDenied(f"private artifact is oversized: {path.name}")
        return data
    finally:
        os.close(fd)


def load_api_key(path: Path) -> str:
    try:
        key = _read_secure_regular(
            path, max_bytes=512, allowed_owners={0, os.geteuid()},
            allowed_modes={0o400, 0o440, 0o600},
        ).decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise BrokerDenied("dedicated API key is not ASCII") from exc
    if not key.startswith("sk-ant-") or not 24 <= len(key) <= 256 or any(c.isspace() for c in key):
        raise BrokerDenied("dedicated API key has an invalid shape")
    return key


def load_client_capability(path: Path) -> str:
    try:
        value = _read_secure_regular(
            path, max_bytes=256, allowed_owners={0, os.geteuid()},
            allowed_modes={0o400, 0o440, 0o600},
        ).decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise BrokerDenied("client capability is not ASCII") from exc
    if not 43 <= len(value) <= 128 or not all(c.isalnum() or c in "-_" for c in value):
        raise BrokerDenied("client capability has an invalid shape")
    return value


def load_signed_consent(
    path: Path, signature_path: Path, public_key_path: Path,
) -> dict[str, Any]:
    owners = {0, os.geteuid()}
    raw = _read_secure_regular(
        path, max_bytes=4096, allowed_owners=owners,
        allowed_modes={0o400, 0o440, 0o600},
    )
    signature_raw = _read_secure_regular(
        signature_path, max_bytes=256, allowed_owners=owners,
        allowed_modes={0o400, 0o440, 0o600},
    )
    public_raw = _read_secure_regular(
        public_key_path, max_bytes=1024, allowed_owners=owners,
        allowed_modes={0o400, 0o440, 0o444, 0o600},
    )
    try:
        signature = base64.b64decode(signature_raw.strip(), validate=True)
        public_key = serialization.load_pem_public_key(public_raw)
        if not isinstance(public_key, Ed25519PublicKey):
            raise ValueError("not Ed25519")
        public_key.verify(signature, raw)
    except Exception as exc:
        raise BrokerDenied("consent signature verification failed") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrokerDenied("signed consent is not valid UTF-8 JSON") from exc
    required = {
        "schema", "instance", "provider", "model", "subject_id", "consented",
        "scope", "data_classes", "accepted_at", "expires_at", "recorded_by",
        "evidence_ref",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise BrokerDenied("consent artifact fields do not match the contract")
    if (
        payload["schema"] != "seal.external-model-consent.v2"
        or payload["instance"] != INSTANCE
        or payload["provider"] != PROVIDER
        or payload["model"] != MODEL
        or payload["consented"] is not True
        or payload["scope"] != "u116-complete-prompt-to-anthropic"
    ):
        raise BrokerDenied("consent does not authorize this exact instance/provider/model")
    expected_classes = [
        "curated_technical_context",
        "professor_chat_history",
        "public_voice_few_shot",
        "system_prompt",
    ]
    if payload["data_classes"] != expected_classes:
        raise BrokerDenied("consent does not cover every outbound prompt data class")
    import re
    if not re.fullmatch(r"professor:[a-z0-9][a-z0-9._-]{1,63}", str(payload["subject_id"])):
        raise BrokerDenied("consent subject identity is invalid")
    if payload["recorded_by"] != "William":
        raise BrokerDenied("consent recorder is not authorized")
    if not (
        re.fullmatch(r"seal-chat:db_[1-9][0-9]*", str(payload["evidence_ref"]))
        or re.fullmatch(r"document:sha256:[0-9a-f]{64}", str(payload["evidence_ref"]))
    ):
        raise BrokerDenied("consent evidence reference is invalid")
    try:
        accepted = datetime.fromisoformat(str(payload["accepted_at"]).replace("Z", "+00:00"))
        expires = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise BrokerDenied("consent timestamp is invalid") from exc
    now = datetime.now(timezone.utc)
    if (
        accepted.tzinfo is None or expires.tzinfo is None or accepted > now
        or expires <= now or expires <= accepted
        or (expires - accepted).days > 365
    ):
        raise BrokerDenied("consent validity window is invalid")
    return payload


def _normalize_messages(payload: dict[str, Any], max_output_tokens: int) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - {"model", "stream", "messages", "options"}:
        raise BrokerDenied("request contains unsupported fields")
    if payload.get("stream") not in (False, None):
        raise BrokerDenied("streaming is not enabled for the isolated clone broker")
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages or len(raw_messages) > 80:
        raise BrokerDenied("messages must be a non-empty bounded list")
    system_parts: list[str] = []
    messages: list[dict[str, str]] = []
    total_chars = 0
    for index, item in enumerate(raw_messages):
        if not isinstance(item, dict) or set(item) != {"role", "content"}:
            raise BrokerDenied(f"message {index} has an unsupported shape")
        role = item.get("role")
        content = item.get("content")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str):
            raise BrokerDenied(f"message {index} role/content is invalid")
        content = content.strip()
        if not content or len(content) > 64_000:
            raise BrokerDenied(f"message {index} is empty or oversized")
        total_chars += len(content)
        if total_chars > 200_000:
            raise BrokerDenied("conversation exceeds the broker input budget")
        if role == "system":
            if messages:
                raise BrokerDenied("system messages must precede conversational messages")
            system_parts.append(content)
        else:
            messages.append({"role": role, "content": content})
    if not messages or messages[-1]["role"] != "user":
        raise BrokerDenied("conversation must end in a user message")
    # Anthropic accepts alternating turns. Merge adjacent same-role messages.
    merged: list[dict[str, str]] = []
    for item in messages:
        if merged and merged[-1]["role"] == item["role"]:
            merged[-1]["content"] += "\n\n" + item["content"]
        else:
            merged.append(dict(item))
    result: dict[str, Any] = {
        "model": MODEL,
        "max_tokens": int(max_output_tokens),
        "messages": merged,
        "thinking": {"type": "disabled"},
    }
    if system_parts:
        result["system"] = "\n\n".join(system_parts)
    return result


def _estimated_input_tokens(payload: dict[str, Any]) -> int:
    # One token per UTF-8 byte is intentionally conservative for the budget
    # reservation.  The real API usage later refunds the unused reservation.
    text = str(payload.get("system", ""))
    text += "".join(item["content"] for item in payload["messages"])
    return max(1, len(text.encode("utf-8")))


def _cost_usd(input_tokens: int, output_tokens: int) -> float:
    return (
        max(0, input_tokens) * INPUT_USD_PER_MILLION
        + max(0, output_tokens) * OUTPUT_USD_PER_MILLION
    ) / 1_000_000


class UsageLedger:
    def __init__(self, path: Path, *, requests_per_hour: int, daily_budget_usd: float):
        self.path = path
        self.requests_per_hour = requests_per_hour
        self.daily_budget_usd = daily_budget_usd

    @staticmethod
    def _row(*, now: float, request_id: str, status: str, input_tokens: int,
             output_tokens: int, cost_usd: float, counts_request: bool) -> dict[str, Any]:
        return {
            "at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "at_epoch": now,
            "day": datetime.fromtimestamp(now, timezone.utc).date().isoformat(),
            "request_id_sha256": hashlib.sha256(request_id.encode("utf-8")).hexdigest(),
            "status": status,
            "counts_request": bool(counts_request),
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "cost_usd": round(float(cost_usd), 8),
        }

    def reserve(self, *, now: float, request_id: str, reserve_usd: float) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.path.parent, 0o700)
        with self.path.open("a+", encoding="utf-8") as handle:
            os.chmod(self.path, 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.seek(0)
            try:
                rows = [json.loads(line) for line in handle if line.strip()]
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                raise BrokerDenied("usage ledger integrity check failed") from exc
            hour_count = sum(
                1 for row in rows
                if row.get("counts_request") is True and now - float(row["at_epoch"]) < 3600
            )
            day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
            day_cost = sum(float(row.get("cost_usd", 0.0)) for row in rows if row.get("day") == day)
            if hour_count >= self.requests_per_hour:
                raise BrokerDenied("hourly request limit reached")
            if day_cost + reserve_usd > self.daily_budget_usd:
                raise BrokerDenied("daily cost budget would be exceeded")
            row = self._row(
                now=now, request_id=request_id, status="reserved", input_tokens=0,
                output_tokens=0, cost_usd=reserve_usd, counts_request=True,
            )
            handle.seek(0, os.SEEK_END)
            handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def adjust(self, *, now: float, request_id: str, status: str, input_tokens: int,
               output_tokens: int, cost_delta_usd: float) -> None:
        row = self._row(
            now=now, request_id=request_id, status=status, input_tokens=input_tokens,
            output_tokens=output_tokens, cost_usd=cost_delta_usd, counts_request=False,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.path.open("a", encoding="utf-8") as handle:
            os.chmod(self.path, 0o600)
            fcntl.flock(handle, fcntl.LOCK_EX)
            handle.write(json.dumps(row, sort_keys=True) + "\n")


class ClaudeBroker:
    def __init__(self, config: BrokerConfig, *, opener: Callable[..., Any] | None = None):
        self.config = config
        load_api_key(config.key_file)
        load_client_capability(config.client_capability_file)
        load_signed_consent(
            config.consent_file, config.consent_signature_file,
            config.consent_public_key_file,
        )
        if config.requests_per_hour < 1 or config.requests_per_hour > 120:
            raise BrokerDenied("requests-per-hour is outside the safe range")
        if not 0.05 <= config.daily_budget_usd <= 25.0:
            raise BrokerDenied("daily budget is outside the safe range")
        if not 64 <= config.max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise BrokerDenied("max-output-tokens is outside the safe range")
        # Ignore host/user proxy environment: outbound traffic is pinned to the
        # TLS-verified Anthropic endpoint, not redirectable through HTTP_PROXY.
        self.opener = opener or request.build_opener(
            request.ProxyHandler({}), _DenyRedirect()
        ).open
        self.ledger = UsageLedger(
            config.state_dir / "usage.jsonl",
            requests_per_hour=config.requests_per_hour,
            daily_budget_usd=config.daily_budget_usd,
        )

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Key and consent are live policy, not startup-only configuration.
        # Removing/rotating either artifact takes effect on the next request.
        api_key = load_api_key(self.config.key_file)
        load_signed_consent(
            self.config.consent_file, self.config.consent_signature_file,
            self.config.consent_public_key_file,
        )
        outbound = _normalize_messages(payload, self.config.max_output_tokens)
        estimated_input = _estimated_input_tokens(outbound)
        reserve = _cost_usd(estimated_input, self.config.max_output_tokens)
        now = time.time()
        local_request_id = os.urandom(16).hex()
        self.ledger.reserve(now=now, request_id=local_request_id, reserve_usd=reserve)
        body = json.dumps(outbound, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            ANTHROPIC_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "User-Agent": "SEAL-JARVIS-u116-broker/1.0",
            },
            method="POST",
        )
        request_id = "unavailable"
        try:
            with self.opener(req, timeout=180) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise BrokerDenied("Anthropic response exceeded the broker limit")
                request_id = str(response.headers.get("request-id") or "unavailable")
            result = json.loads(raw.decode("utf-8"))
            content = result.get("content")
            answer = "".join(
                str(block.get("text") or "")
                for block in content or []
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
            usage = result.get("usage") or {}
            input_tokens = int(usage.get("input_tokens") or estimated_input)
            output_tokens = int(usage.get("output_tokens") or 0)
            if not answer:
                raise BrokerDenied("Anthropic returned no text content")
            cost = _cost_usd(input_tokens, output_tokens)
            self.ledger.adjust(
                now=now, request_id=request_id, status="ok", input_tokens=input_tokens,
                output_tokens=output_tokens, cost_delta_usd=cost - reserve,
            )
            return {
                "model": MODEL,
                "message": {"role": "assistant", "content": answer},
                "done": True,
                "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
            }
        except (error.HTTPError, error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            self.ledger.adjust(
                now=now, request_id=request_id, status="upstream_error",
                input_tokens=0, output_tokens=0, cost_delta_usd=0.0,
            )
            raise BrokerDenied("Anthropic request failed") from exc

    def health(self) -> dict[str, Any]:
        load_api_key(self.config.key_file)
        load_signed_consent(
            self.config.consent_file, self.config.consent_signature_file,
            self.config.consent_public_key_file,
        )
        return {"ok": True, "instance": INSTANCE, "model": MODEL}

    def authorized(self, authorization: str, instance: str) -> bool:
        capability = load_client_capability(self.config.client_capability_file)
        expected = "Bearer " + capability
        supplied = str(authorization or "")
        supplied_instance = str(instance or "")
        return hmac.compare_digest(supplied, expected) and hmac.compare_digest(
            supplied_instance, INSTANCE
        )


class _UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, *args: Any, max_workers: int = 32, **kwargs: Any):
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


def make_handler(broker: ClaudeBroker) -> type[http.server.BaseHTTPRequestHandler]:
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "SEALClaudeBroker/1.0"

        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(2.0)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                self.close_connection = True

        def _authenticated(self) -> bool:
            ok = broker.authorized(
                self.headers.get("Authorization", ""),
                self.headers.get("X-SEAL-Instance", ""),
            )
            if not ok:
                self.close_connection = True
                self._json(401, {"ok": False, "error": "client_auth_required"})
            return ok

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                if not self._authenticated():
                    return
                try:
                    self._json(200, broker.health())
                except BrokerDenied:
                    self._json(503, {"ok": False, "error": "not_ready"})
            else:
                self._json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/api/chat":
                self._json(404, {"ok": False, "error": "not_found"})
                return
            # Authenticate from headers before reading even one body byte.
            if not self._authenticated():
                return
            try:
                size = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                size = 0
            if size <= 0 or size > MAX_REQUEST_BYTES:
                self._json(413, {"ok": False, "error": "request_size_denied"})
                return
            try:
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise BrokerDenied("incomplete request body")
                payload = json.loads(raw.decode("utf-8"))
                self._json(200, broker.chat(payload))
            except socket.timeout:
                self._json(408, {"ok": False, "error": "request_timeout"})
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._json(400, {"ok": False, "error": "invalid_json"})
            except BrokerDenied as exc:
                self._json(429 if "limit" in str(exc) or "budget" in str(exc) else 502,
                           {"ok": False, "error": str(exc)})

    return Handler


def serve(config: BrokerConfig) -> None:
    broker = ClaudeBroker(config)
    config.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    os.chmod(config.socket_path.parent, 0o750)
    if config.socket_path.exists():
        if not stat.S_ISSOCK(config.socket_path.lstat().st_mode):
            raise BrokerDenied("socket path exists and is not a socket")
        config.socket_path.unlink()
    with _UnixHTTPServer(str(config.socket_path), make_handler(broker)) as server:
        os.chmod(config.socket_path, 0o660)
        server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--client-capability-file", type=Path, required=True)
    parser.add_argument("--consent-file", type=Path, required=True)
    parser.add_argument("--consent-signature-file", type=Path, required=True)
    parser.add_argument("--consent-public-key-file", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--requests-per-hour", type=int, default=DEFAULT_REQUESTS_PER_HOUR)
    parser.add_argument("--daily-budget-usd", type=float, default=DEFAULT_DAILY_BUDGET_USD)
    parser.add_argument("--max-output-tokens", type=int, default=MAX_OUTPUT_TOKENS)
    args = parser.parse_args()
    serve(BrokerConfig(
        key_file=args.key_file,
        client_capability_file=args.client_capability_file,
        consent_file=args.consent_file,
        consent_signature_file=args.consent_signature_file,
        consent_public_key_file=args.consent_public_key_file,
        socket_path=args.socket,
        state_dir=args.state_dir,
        requests_per_hour=args.requests_per_hour,
        daily_budget_usd=args.daily_budget_usd,
        max_output_tokens=args.max_output_tokens,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
