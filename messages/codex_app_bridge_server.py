#!/usr/bin/env python3
"""SEAL Codex App bridge.

Local bridge for Windows/macOS Codex App to submit mirror_control tasks to
canonical ADA on Linux. This module is intentionally conservative: it never
publishes as ADA and it rejects writes unless the visible tmux runtime is the
canonical writer.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.responses import Response, StreamingResponse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "memory"))
from seal_identity_tokens import valid_tokens  # noqa: E402


DEFAULT_QUEUE_DIR = ROOT / "messages" / "codex_app_bridge"
DEFAULT_TOKEN_FILE = Path("/tmp/seal/ada_codex_app_bridge.token")
DEFAULT_SOUL_CLONE_TOKEN_FILE = Path("/tmp/seal/ada_codex_app_soul_clone.token")
WRITER_STATUS_SCRIPT = ROOT / "messages" / "ada_codex_writer_status.sh"
SESSIONS_DIR = Path("/home/dadito/.codex/sessions")
DEFAULT_TASK_WAIT_SECONDS = 12.0
TASK_WAIT_POLL_SECONDS = 0.25

QUEUE_DIR = Path(os.environ.get("ADA_CODEX_APP_BRIDGE_DIR", DEFAULT_QUEUE_DIR))
TOKEN_FILE = Path(os.environ.get("ADA_CODEX_APP_BRIDGE_TOKEN_FILE", DEFAULT_TOKEN_FILE))
SOUL_CLONE_TOKEN_FILE = Path(
    os.environ.get("ADA_CODEX_APP_SOUL_CLONE_TOKEN_FILE", DEFAULT_SOUL_CLONE_TOKEN_FILE)
)
BRIDGE_BIND_HOST = os.environ.get("ADA_CODEX_APP_BRIDGE_HOST", "127.0.0.1")
BRIDGE_PORT = int(os.environ.get("ADA_CODEX_APP_BRIDGE_PORT", "8782"))
SOUL_MCP_URL = os.environ.get("ADA_CODEX_APP_SOUL_MCP_URL", "http://127.0.0.1:8771/mcp")
SOUL_TOKEN_DIR = Path(
    os.environ.get("ADA_CODEX_APP_SOUL_TOKEN_DIR", os.environ.get("SEAL_TOKENS_DIR", "/run/user/1000/seal"))
)
SOUL_REQUEST_HEADERS = frozenset(
    {"accept", "content-type", "mcp-session-id", "mcp-protocol-version", "last-event-id", "user-agent"}
)
SOUL_RESPONSE_HEADERS = frozenset(
    {"content-type", "content-encoding", "mcp-session-id", "mcp-protocol-version", "cache-control", "retry-after"}
)


class BridgeTask(BaseModel):
    source: str = Field(default="codex_app")
    sender: str = Field(default="William")
    channel: str = Field(default="dm:ada:william")
    mode: str = Field(default="mirror_control")
    requested_action: str = Field(default="implement")
    message: str
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    idempotency_key: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def ensure_token(token_file: Path | None = None) -> str:
    token_file = token_file or TOKEN_FILE
    token_file.parent.mkdir(parents=True, exist_ok=True)
    if token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    token_file.write_text(token + "\n", encoding="utf-8")
    token_file.chmod(0o600)
    return token


def token_from_request(authorization: str | None, x_seal_bridge_token: str | None) -> str | None:
    if x_seal_bridge_token:
        return x_seal_bridge_token.strip()
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:].strip()
    return None


def verify_token(
    authorization: str | None = Header(default=None),
    x_seal_bridge_token: str | None = Header(default=None),
) -> None:
    expected = ensure_token()
    provided = token_from_request(authorization, x_seal_bridge_token)
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="bridge_token_required")


def verify_soul_clone_token(authorization: str | None = Header(default=None)) -> None:
    expected = ensure_token(SOUL_CLONE_TOKEN_FILE)
    provided = token_from_request(authorization, None)
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="soul_clone_token_required")


def soul_agent_token(token_dir: Path | None = None) -> str:
    """Resolve ADA's live Store-A credential without exporting or logging it."""
    values = valid_tokens([token_dir or SOUL_TOKEN_DIR], "ADA")
    if not values:
        raise RuntimeError("ada_soul_identity_unavailable")
    return values[0]


def soul_upstream_headers(request_headers: Any, agent_token: str) -> dict[str, str]:
    """Allowlist MCP protocol headers and replace the device bearer with ADA's."""
    headers = {
        key.lower(): value
        for key, value in request_headers.items()
        if key.lower() in SOUL_REQUEST_HEADERS
    }
    headers["authorization"] = f"Bearer {agent_token}"
    return headers


async def close_soul_stream(response: httpx.Response, client: httpx.AsyncClient) -> None:
    await response.aclose()
    await client.aclose()


def writer_status() -> dict[str, Any]:
    try:
        output = subprocess.check_output(
            [str(WRITER_STATUS_SCRIPT)],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        return json.loads(output)
    except Exception as exc:
        return {
            "agent": "ADA",
            "writer_mode": "read_only",
            "error": f"writer_status_failed:{type(exc).__name__}",
        }


def is_canonical_writer(status: dict[str, Any]) -> bool:
    return (
        status.get("writer_mode") == "canonical_writer"
        and status.get("canonical_session") == "present"
        and status.get("heartbeat_alive") is True
        and status.get("heartbeat_runtime") == "codex_visible_tmux"
    )


def queue_paths(queue_dir: Path | None = None) -> tuple[Path, Path]:
    queue_dir = queue_dir or QUEUE_DIR
    return queue_dir / "inbox.jsonl", queue_dir / "tasks"


def response_path(task_id: str, queue_dir: Path | None = None) -> Path:
    queue_dir = queue_dir or QUEUE_DIR
    return queue_dir / "responses" / f"{task_id}.json"


def load_task_response(task_id: str, queue_dir: Path | None = None) -> dict[str, Any] | None:
    path = response_path(task_id, queue_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


async def wait_for_task_response(task_id: str, wait_seconds: float = DEFAULT_TASK_WAIT_SECONDS) -> dict[str, Any] | None:
    deadline = time.monotonic() + max(0.0, min(wait_seconds, 30.0))
    while True:
        response = load_task_response(task_id)
        if response:
            return response
        response = await asyncio.to_thread(find_response_in_codex_sessions, task_id)
        if response:
            return response
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(TASK_WAIT_POLL_SECONDS)


def newest_session_files(limit: int = 5) -> list[Path]:
    if not SESSIONS_DIR.exists():
        return []
    candidates = list(SESSIONS_DIR.rglob("*.jsonl"))
    return sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def find_response_in_codex_sessions(task_id: str) -> dict[str, Any] | None:
    """Best-effort recovery: find task_complete after bridge_task in Codex JSONL."""
    for session_file in newest_session_files():
        try:
            lines = session_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        seen_task = False
        for line in lines:
            if task_id in line and "bridge_task" in line:
                seen_task = True
                continue
            if not seen_task:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = event.get("payload", {})
            if (
                event.get("type") == "event_msg"
                and isinstance(payload, dict)
                and payload.get("type") == "task_complete"
                and payload.get("last_agent_message")
            ):
                response = {
                    "id": task_id,
                    "completed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "message": payload["last_agent_message"],
                    "recovered_from": str(session_file),
                }
                path = response_path(task_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(response, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                return response
    return None


def load_existing_task(queue_file: Path, idempotency_key: str | None) -> dict[str, Any] | None:
    if not idempotency_key or not queue_file.exists():
        return None
    for line in queue_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            task = json.loads(line)
        except json.JSONDecodeError:
            continue
        if task.get("idempotency_key") == idempotency_key:
            return task
    return None


def append_task(task: BridgeTask, status: dict[str, Any], queue_dir: Path | None = None) -> dict[str, Any]:
    if task.mode != "mirror_control":
        raise ValueError("only_mirror_control_allowed")
    if task.channel != "dm:ada:william":
        raise ValueError("only_dm_ada_william_allowed")
    if not task.message.strip():
        raise ValueError("message_required")
    if not is_canonical_writer(status):
        raise RuntimeError("canonical_writer_not_verified")

    queue_file, task_dir = queue_paths(queue_dir)
    task_dir.mkdir(parents=True, exist_ok=True)
    queue_file.parent.mkdir(parents=True, exist_ok=True)

    existing = load_existing_task(queue_file, task.idempotency_key)
    if existing:
        return {**existing, "deduped": True}

    now = utc_now()
    task_id = f"ada_bridge_{int(time.time() * 1000)}_{secrets.token_hex(4)}"
    payload = {
        "id": task_id,
        "created_at": now,
        "from": task.sender,
        "to": "ADA",
        "channel": task.channel,
        "mode": task.mode,
        "source": task.source,
        "requested_action": task.requested_action,
        "message": task.message.strip(),
        "attachments": task.attachments,
        "idempotency_key": task.idempotency_key or task_id,
        "writer_status": status,
        "status": "queued",
    }
    with queue_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")
    (task_dir / f"{task_id}.json").write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


app = FastAPI(title="SEAL Codex App Bridge", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, Any]:
    status = writer_status()
    return {
        "ok": True,
        "mode": "mirror_control",
        "writer_verified": is_canonical_writer(status),
        "writer_status": status,
    }


@app.get("/api/status", dependencies=[Depends(verify_token)])
async def api_status() -> dict[str, Any]:
    status = writer_status()
    try:
        soul_agent_token()
        soul_proxy_ready = True
    except RuntimeError:
        soul_proxy_ready = False
    return {
        "ok": True,
        "mode": "mirror_control",
        "writer_verified": is_canonical_writer(status),
        "writer_status": status,
        "queue_dir": str(QUEUE_DIR),
        "soul_proxy_ready": soul_proxy_ready,
        "soul_proxy_mode": "device_bearer_to_ada_store_a",
    }


@app.api_route(
    "/api/soul/mcp",
    methods=["GET", "POST", "DELETE", "HEAD"],
    dependencies=[Depends(verify_soul_clone_token)],
)
async def soul_mcp_proxy(request: Request) -> StreamingResponse:
    """Authenticated MCP proxy for ADA's Windows body.

    Windows proves the independently revocable bridge/device identity. The
    bridge replaces that bearer with ADA's local Store-A credential and never
    returns or logs the credential bytes. No public chat-write endpoint is
    exposed here.
    """
    try:
        agent_token = soul_agent_token()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if request.method == "HEAD":
        return Response(status_code=200, headers={"mcp-protocol-version": "2024-11-05"})

    client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=None))
    try:
        upstream_request = client.build_request(
            request.method,
            SOUL_MCP_URL,
            headers=soul_upstream_headers(request.headers, agent_token),
            content=await request.body(),
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"soul_mcp_unavailable:{type(exc).__name__}") from exc

    response_headers = {
        key.lower(): value
        for key, value in upstream.headers.items()
        if key.lower() in SOUL_RESPONSE_HEADERS
    }
    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(close_soul_stream, upstream, client),
    )


@app.post("/api/tasks", dependencies=[Depends(verify_token)])
async def create_task(task: BridgeTask, request: Request) -> dict[str, Any]:
    status = writer_status()
    try:
        queued = append_task(task, status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "ok": True,
        "client": request.client.host if request.client else None,
        "task": queued,
    }


@app.get("/api/tasks/{task_id}", dependencies=[Depends(verify_token)])
async def get_task(task_id: str, wait_seconds: float = DEFAULT_TASK_WAIT_SECONDS) -> dict[str, Any]:
    response = await wait_for_task_response(task_id, wait_seconds=wait_seconds)
    if response:
        return {"ok": True, "status": "completed", "response": response}
    task_file = QUEUE_DIR / "tasks" / f"{task_id}.json"
    if task_file.exists():
        return {"ok": True, "status": "queued", "task_id": task_id}
    raise HTTPException(status_code=404, detail="task_not_found")


def main() -> None:
    import uvicorn

    ensure_token()
    ensure_token(SOUL_CLONE_TOKEN_FILE)
    print(f"[codex-app-bridge] token_file={TOKEN_FILE}")
    print(f"[codex-app-bridge] soul_clone_token_file={SOUL_CLONE_TOKEN_FILE}")
    uvicorn.run(app, host=BRIDGE_BIND_HOST, port=BRIDGE_PORT, log_level="info")


if __name__ == "__main__":
    main()
