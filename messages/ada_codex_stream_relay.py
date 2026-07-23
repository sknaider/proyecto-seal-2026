#!/usr/bin/env python3
"""ADA-Codex Stream Relay — relay opcional de respuestas ADA.

Monitorea el JSONL activo de la sesión Codex terminal y reenvía cada
`agent_message` al webchat solo si se habilita explícitamente.

- Sigue el archivo JSONL activo (newest by mtime)
- Detecta event_msg/agent_message nuevos
- Modo default: terminal-only; NO publica al webchat
- Modo opt-in: ADA_CODEX_STREAM_RELAY_WEBCHAT=1 publica al webchat
- Evita duplicados via SHA256 del contenido
- PID guard: /tmp/ada_codex_stream_relay.pid

Uso: python3 ada_codex_stream_relay.py
"""
import asyncio
import difflib
import hashlib
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

os.umask(0o077)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from messages.agent_writer import send_agent_message_sync
from messages.codex_session_selector import newest_primary_tui_session
from memory.operational_db_credentials import service_pg_dsn

SESSIONS_DIR = Path("/home/dadito/.codex/sessions")
WEBCHAT_URL = "http://localhost:8765/api/agents/send"
STREAM_URL = "http://localhost:8765/internal/stream"
PID_FILE = Path("/tmp/ada_codex_stream_relay.pid")
ADA_TERMINAL_ACTIVE = Path("/tmp/seal/ada_terminal_active")
BRIDGE_QUEUE_DIR = Path("/home/dadito/IA/proyecto-seal/messages/codex_app_bridge")
BRIDGE_ACTIVE_TASK_FILE = BRIDGE_QUEUE_DIR / "active_task.json"
BRIDGE_RESPONSES_DIR = BRIDGE_QUEUE_DIR / "responses"
BRIDGE_RESPONSES_JSONL = BRIDGE_QUEUE_DIR / "responses.jsonl"
TMUX_SESSION = "seal-ada-codex"
POLL_INTERVAL = 0.4   # segundos entre lecturas del JSONL
ROTATE_CHECK = 10.0   # segundos entre chequeos de sesión nueva
DIRECT_RECOVERY_BYTES = 8 * 1024 * 1024
MAX_STREAM_LEN = 8000  # preview efímero; el durable se divide sin perder texto
DURABLE_CHUNK_LEN = 8000
SENSITIVE_RE = re.compile(
    r"(?i)(client[_-]?secret|client[_-]?id|refresh[_-]?token|access[_-]?token|"
    r"api[_-]?key|password|passwd|authorization|bearer\s+[A-Za-z0-9._~+/-]+|"
    r"credentials?\.json|\.env\b|private[_-]?key)"
)

_seen: set[str] = set()
_current_file: Path | None = None
_file_pos: int = 0
_pending_relay: list = []   # (content, queued_at_timestamp, channel)
RELAY_DELAY = 0.4           # terminal relay debe sentirse vivo; dedup API evita doble post
RECENT_API = "http://localhost:8765/api/chat/messages/agent?agent=ADA&limit=8"
WEBCHAT_RELAY_ENABLED = os.environ.get("ADA_CODEX_STREAM_RELAY_WEBCHAT", "0").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
DIRECT_TERMINAL_DM_ENABLED = os.environ.get(
    "ADA_CODEX_STREAM_RELAY_DIRECT_DM", "0"
).lower() in {"1", "true", "yes", "on"}
PERSIST_COMMENTARY = os.environ.get(
    "ADA_CODEX_STREAM_RELAY_PERSIST_COMMENTARY", "0"
).lower() in {"1", "true", "yes", "on"}
_allowed_channels_raw = os.environ.get("ADA_CODEX_STREAM_RELAY_ALLOWED_CHANNELS", "").strip()
WEBCHAT_RELAY_ALLOWED_CHANNELS = {
    item.strip()
    for item in _allowed_channels_raw.split(",")
    if item.strip()
}
TRUSTED_TASK_SOURCES = {
    "ada_codex_poller",
    "ada_codex_poller_public_fallback",
}
_direct_terminal_turn: dict | None = None


def resolve_db_dsn() -> str:
    """Use only the dedicated ADA bridge reader for delivery confirmation."""
    return service_pg_dsn("SEAL_DB_DSN", expected_role="login_ada_bridge")


def _pid_guard() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
    except Exception:
        PID_FILE.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        PID_FILE.unlink(missing_ok=True)
        return False


def _find_newest_session() -> Path | None:
    """Return only ADA's visible primary TUI rollout.

    A plain newest-by-mtime scan can select a subagent fork or Codex app-server
    thread and relay the wrong output. The shared selector fails closed unless
    the first session_meta identifies a primary user TUI.
    """
    return newest_primary_tui_session(SESSIONS_DIR)


def _normalize_for_dedup(content: str) -> str:
    """Canonicaliza texto terminal/webchat para comparar respuestas similares."""
    text = content.lower()
    text = re.sub(r"`+", "", text)
    text = re.sub(r"[*_#>\-•]+", " ", text)
    text = re.sub(r"[^\wáéíóúüñ]+", " ", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", text).strip()


def _msg_key(content: str) -> str:
    """Hash NORMALIZADO (whitespace + markdown insensible) para dedup intra-relay.

    Fix v1.2 (ALICE 21-may-2026): antes hasheaba raw → mismos contenidos con
    leves diffs (newlines vs espacios, markdown) generaban 2 keys distintas
    y el relay republicaba. Ahora usa _normalize_for_dedup primero.
    """
    return hashlib.sha256(_normalize_for_dedup(content).encode()).hexdigest()[:16]


def _redact_sensitive(content: str) -> str:
    return SENSITIVE_RE.sub("[REDACTED]", content)


def _split_durable_message(message: str, limit: int = DURABLE_CHUNK_LEN) -> list[str]:
    """Split a durable answer without dropping bytes from the visible text."""
    if len(message) <= limit:
        return [message]
    chunks: list[str] = []
    remaining = message
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit + 1)
        if cut < limit // 2:
            cut = remaining.rfind(" ", 0, limit + 1)
        if cut < limit // 2:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return chunks


def _final_idempotency_key(channel: str, source_id: str | int) -> str:
    """One final key shared with the headless bridge for the same source row."""
    channel_slug = re.sub(r"[^a-z0-9]+", "_", (channel or "web_chat").lower()).strip("_")
    return f"ada_final_{channel_slug}_{source_id}"


def _should_drop_content(content: str) -> bool:
    text = (content or "").strip()
    return text == "[SILENT]"


def _dedup_prefix(content: str, max_chars: int = 180) -> str:
    return _normalize_for_dedup(content)[:max_chars].strip()


def _is_similar_response(existing: str, candidate: str) -> bool:
    existing_prefix = _dedup_prefix(existing)
    candidate_prefix = _dedup_prefix(candidate)
    if not existing_prefix or not candidate_prefix:
        return False
    if existing_prefix[:80] == candidate_prefix[:80]:
        return True
    existing_first = existing_prefix.split(" evidencia ", 1)[0].strip()
    candidate_first = candidate_prefix.split(" evidencia ", 1)[0].strip()
    if len(existing_first) >= 20 and existing_first == candidate_first:
        return True
    ratio = difflib.SequenceMatcher(None, existing_prefix, candidate_prefix).ratio()
    # Threshold bajado de 0.74 → 0.55 (ALICE 21-may-2026): captura más falsos positivos
    # entre versión curl-de-ADA y versión JSONL-stripped, pero evita duplicados de William
    return ratio >= 0.55


def _post_to_webchat(
    message: str,
    channel: str = "web_chat",
    source_id: str | int | None = None,
    message_kind: str = "final",
    recipient: str = "William",
    idempotency_source_id: str | int | None = None,
) -> dict | None:
    if not WEBCHAT_RELAY_ENABLED:
        return False
    if not _channel_allowed(channel):
        return False
    message = _redact_sensitive(message)
    try:
        source_id = str(source_id or "").strip() or None
        if source_id is None:
            return False
        kind = "progress" if message_kind == "progress" else "final"
        delivery_source = str(idempotency_source_id or source_id)
        idempotency_key = (
            _final_idempotency_key(channel, delivery_source)
            if kind == "final"
            else f"ada_terminal_progress_{source_id}_{_msg_key(message)}"
        )
        if kind == "progress":
            idempotency_key = f"ada_terminal_progress_{source_id}_{_msg_key(message)}"
        chunks = _split_durable_message(message)
        api_ids: list[str] = []
        idempotency_keys: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_key = idempotency_key
            visible_chunk = chunk
            if len(chunks) > 1:
                chunk_key += f"_part_{index}_of_{len(chunks)}"
                visible_chunk = f"[{index}/{len(chunks)}]\n{chunk}"
            result = send_agent_message_sync(
                "ADA", recipient or "William", visible_chunk, channel=channel,
                in_reply_to=source_id,
                proactive=False,
                message_type="conversation",
                idempotency_key=chunk_key,
            )
            if not result.get("ok") or not result.get("id"):
                return None
            api_ids.append(str(result["id"]))
            idempotency_keys.append(chunk_key)
        return {
            "ok": True,
            "api_ids": api_ids,
            "idempotency_keys": idempotency_keys,
            "idempotency_key": idempotency_key,
        }
    except Exception as e:
        print(f"[stream-relay] POST failed: {e}", flush=True)
        return False


def _stream_id_for_content(content: str, channel: str = "web_chat") -> str:
    safe_channel = re.sub(r"[^A-Za-z0-9_]+", "_", channel).strip("_") or "web_chat"
    return f"ada_terminal_stream_{safe_channel}_{_msg_key(content)}"


def _post_stream_to_webchat(
    message: str,
    done: bool = False,
    channel: str = "web_chat",
    recipient: str = "William",
) -> bool:
    if not WEBCHAT_RELAY_ENABLED:
        return False
    if not _channel_allowed(channel):
        return False
    message = _redact_sensitive(message)
    if len(message) > MAX_STREAM_LEN:
        message = message[:MAX_STREAM_LEN] + "…"
    payload = json.dumps({
        "id": _stream_id_for_content(message, channel),
        "from": "ADA",
        "to": recipient or "William",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "stream",
        "channel": channel,
        "message": message,
        "done": done,
    }).encode()
    req = urllib.request.Request(
        STREAM_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[stream-relay] STREAM failed: {e}", flush=True)
        return False


def _load_active_bridge_task() -> dict | None:
    if not BRIDGE_ACTIVE_TASK_FILE.exists():
        return None
    try:
        return json.loads(BRIDGE_ACTIVE_TASK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _validated_active_task() -> tuple[dict, str, str] | None:
    """Return a poller-authored task, its channel and reply id.

    The visible terminal can also contain manually typed private content.  The
    relay therefore fails closed unless the poller created an explicit task
    marker tied to a canonical chat message.  This is the single-writer bridge
    between a TUI final and the public/DM chat row.
    """
    task = _load_active_bridge_task()
    if not task or task.get("source") not in TRUSTED_TASK_SOURCES:
        return None
    # A route is not authoritative until the poller observes a matching
    # task_started + user_message handshake in the Codex rollout.
    if task.get("status") == "pending_submit":
        return None
    channel = str(task.get("channel") or "").strip()
    source_id = str(
        task.get("response_source_id")
        or task.get("chat_message_id")
        or ""
    ).strip()
    if source_id.isdigit():
        source_id = f"db_{source_id}"
    if not channel or not source_id or not _channel_allowed(channel):
        return None
    return task, channel, source_id


def _channel_from_bridge_task(task: dict | None, default: str = "web_chat") -> str:
    if not task:
        return default
    channel = task.get("channel")
    if isinstance(channel, str) and channel.strip():
        return channel.strip()
    return default


def _channel_allowed(channel: str) -> bool:
    if not WEBCHAT_RELAY_ALLOWED_CHANNELS:
        return True
    return channel in WEBCHAT_RELAY_ALLOWED_CHANNELS


def _active_output_channel(default: str = "web_chat") -> str:
    return _channel_from_bridge_task(_load_active_bridge_task(), default=default)


def _atomic_private_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def _append_private_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


def _load_response_artifact(task_id: str) -> dict | None:
    try:
        response = json.loads(
            (BRIDGE_RESPONSES_DIR / f"{task_id}.json").read_text(encoding="utf-8")
        )
    except (OSError, TypeError, ValueError):
        return None
    return response if response.get("id") == task_id else None


def _response_payload(message: str, task: dict, status: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    chat_message_id = task.get("chat_message_id")
    source_id = str(task.get("response_source_id") or "").strip()
    if not source_id and chat_message_id not in {None, ""}:
        # The coordination API does not accept a naked database integer as a
        # reply target. DB-originated/DM turns use the canonical db_<id>
        # compatibility identifier when no API legacy id was captured.
        source_id = f"db_{chat_message_id}"
    delivery_source_id = str(chat_message_id or source_id)
    return {
        "id": task["id"],
        "completed_at": task.get("completed_at") or now,
        "message": _redact_sensitive(message),
        "status": status,
        "published": status in {"delivered", "published"},
        "delivered": status == "delivered",
        "channel": task.get("channel"),
        "chat_message_id": chat_message_id,
        "turn_id": task.get("turn_id"),
        "response_source_id": source_id,
        "reply_to": task.get("reply_to") or "William",
        "idempotency_key": _final_idempotency_key(
            str(task.get("channel") or "web_chat"), delivery_source_id
        ),
    }


def _update_active_task_status(task: dict, status: str) -> None:
    current = _load_active_bridge_task()
    if not current or current.get("id") != task.get("id"):
        return
    current["status"] = status
    current[f"{status}_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_private_json(BRIDGE_ACTIVE_TASK_FILE, current)


def _record_task_completion(message: str, task: dict) -> dict:
    """Persist ``completed`` before any network delivery attempt."""
    existing = _load_response_artifact(str(task["id"]))
    if existing and existing.get("status") in {"completed", "delivered", "published", "suppressed"}:
        return existing
    response = _response_payload(message, task, "completed")
    _atomic_private_json(BRIDGE_RESPONSES_DIR / f"{task['id']}.json", response)
    _append_private_jsonl(BRIDGE_RESPONSES_JSONL, response)
    _update_active_task_status(task, "completed")
    return response


def _record_suppressed_response(message: str, task: dict) -> dict:
    existing = _load_response_artifact(str(task["id"]))
    if existing and existing.get("status") in {"delivered", "published", "suppressed"}:
        return existing
    response = _response_payload(message, task, "suppressed")
    response["suppressed_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_private_json(BRIDGE_RESPONSES_DIR / f"{task['id']}.json", response)
    _append_private_jsonl(BRIDGE_RESPONSES_JSONL, response)
    current = _load_active_bridge_task()
    if current and current.get("id") == task.get("id"):
        BRIDGE_ACTIVE_TASK_FILE.unlink(missing_ok=True)
    return response


def _mark_task_delivered(
    task: dict,
    *,
    api_ids: list[str],
    db_ids: list[int],
    idempotency_keys: list[str],
) -> dict:
    existing = _load_response_artifact(str(task["id"]))
    if existing and existing.get("status") in {"delivered", "published"} and existing.get("db_id"):
        return existing
    if not existing or existing.get("status") != "completed":
        raise RuntimeError(f"cannot deliver task {task['id']} before completed ledger")
    response = dict(existing)
    response.update({
        "status": "delivered",
        "published": True,
        "delivered": True,
        "delivered_at": datetime.now(timezone.utc).isoformat(),
        "api_ids": list(api_ids),
        "api_id": api_ids[0],
        "db_ids": list(db_ids),
        "db_id": db_ids[0],
        "idempotency_keys": list(idempotency_keys),
    })
    _atomic_private_json(BRIDGE_RESPONSES_DIR / f"{task['id']}.json", response)
    _append_private_jsonl(BRIDGE_RESPONSES_JSONL, response)
    current = _load_active_bridge_task()
    if current and current.get("id") == task.get("id"):
        BRIDGE_ACTIVE_TASK_FILE.unlink(missing_ok=True)
    return response


def _record_bridge_response(
    message: str,
    *,
    status: str,
    published: bool,
    task: dict | None = None,
) -> bool:
    """Compatibility wrapper for tests and old recovery artifacts.

    New code uses the explicit completed -> delivered state machine.  A legacy
    ``published`` request is upgraded to ``delivered`` only when it carries a
    durable DB id; callers cannot mint a delivery receipt from a boolean.
    """
    task = task or _load_active_bridge_task()
    if not task or not task.get("id"):
        return False
    if status == "suppressed":
        _record_suppressed_response(message, task)
        return True
    if status == "completed":
        _record_task_completion(message, task)
        return True
    if status in {"published", "delivered"} and published:
        # Legacy callers do not know the DB row; fail closed rather than
        # fabricating proof.  Delivery is finalized by _mark_task_delivered.
        return False
    return False


def _unpack_pending(item: tuple) -> tuple[str, float, str, str | None, str, dict | None]:
    if len(item) == 2:
        content, queued_at = item
        return content, queued_at, "web_chat", None, "William", None
    if len(item) == 3:
        content, queued_at, channel = item
        return content, queued_at, channel or "web_chat", None, "William", None
    if len(item) == 4:
        content, queued_at, channel, source_id = item
        return content, queued_at, channel or "web_chat", str(source_id or "") or None, "William", None
    if len(item) == 5:
        content, queued_at, channel, source_id, recipient = item
        return content, queued_at, channel or "web_chat", str(source_id or "") or None, str(recipient or "William"), None
    content, queued_at, channel, source_id, recipient, task = item
    return content, queued_at, channel or "web_chat", str(source_id or "") or None, str(recipient or "William"), task


def _pending_has_task(task_id: str) -> bool:
    for item in _pending_relay:
        task = _unpack_pending(item)[5]
        if task and str(task.get("id")) == task_id:
            return True
    return False


def _queue_completed_response(response: dict, *, immediate: bool = False) -> bool:
    task_id = str(response.get("id") or "")
    if not task_id or _pending_has_task(task_id):
        return False
    task = dict(response)
    queued_at = 0.0 if immediate else datetime.now(timezone.utc).timestamp()
    _pending_relay.append((
        str(response.get("message") or ""),
        queued_at,
        str(response.get("channel") or "web_chat"),
        str(response.get("response_source_id") or response.get("chat_message_id") or "") or None,
        str(response.get("reply_to") or "William"),
        task,
    ))
    return True


async def _confirm_delivery_rows(
    api_ids: list[str], channel: str, in_reply_to: str
) -> list[int] | None:
    if not api_ids:
        return None
    conn = await asyncpg.connect(resolve_db_dsn())
    try:
        rows: list[int] = []
        for api_id in api_ids:
            row = None
            for attempt in range(10):
                row = await conn.fetchrow(
                    """SELECT id FROM soul_v3.chat_messages
                         WHERE channel=$1
                           AND metadata->>'legacy_id'=$2
                           AND metadata->>'in_reply_to'=$3
                         ORDER BY id DESC LIMIT 1""",
                    channel,
                    api_id,
                    str(in_reply_to),
                )
                if row:
                    break
                if attempt < 9:
                    await asyncio.sleep(0.1)
            if not row:
                return None
            rows.append(int(row["id"]))
        return rows
    finally:
        await conn.close()


async def _drain_pending_once(now_ts: float | None = None) -> int:
    """Attempt durable delivery for ready items; return delivered count."""
    if not _pending_relay:
        return 0
    now_ts = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
    ready = [
        item for item in list(_pending_relay)
        if now_ts - _unpack_pending(item)[1] >= RELAY_DELAY
    ]
    delivered_count = 0
    for item in ready:
        content, _, channel, source_id, recipient, task = _unpack_pending(item)
        chat_message_id = task.get("chat_message_id") if task else None
        result = _post_to_webchat(
            content,
            channel=channel,
            source_id=source_id,
            recipient=recipient,
            idempotency_source_id=chat_message_id or source_id,
        )
        if not result:
            _pending_relay.remove(item)
            _pending_relay.append((content, now_ts, channel, source_id, recipient, task))
            continue
        # Direct terminal turns have no source DB cursor to ACK.  Poller tasks
        # require a real chat row for every durable chunk before completion.
        if task is None:
            _pending_relay.remove(item)
            delivered_count += 1
            continue
        db_ids = await _confirm_delivery_rows(
            result["api_ids"], channel, str(source_id or "")
        )
        if not db_ids:
            _pending_relay.remove(item)
            _pending_relay.append((content, now_ts, channel, source_id, recipient, task))
            continue
        _mark_task_delivered(
            task,
            api_ids=result["api_ids"],
            db_ids=db_ids,
            idempotency_keys=result["idempotency_keys"],
        )
        _pending_relay.remove(item)
        delivered_count += 1
    return delivered_count


def _process_line(line: str) -> None:
    global _direct_terminal_turn
    try:
        obj = json.loads(line)
    except Exception:
        return
    if obj.get("type") != "event_msg":
        return
    payload = obj.get("payload", {})
    if not isinstance(payload, dict):
        return
    payload_type = payload.get("type")
    if payload_type == "user_message":
        if not DIRECT_TERMINAL_DM_ENABLED:
            return
        # A poller-authored task already has a canonical chat id and owns its
        # route. Direct mirroring is only for messages typed into the primary
        # TUI, so the two paths can never publish the same answer.
        raw_task = _load_active_bridge_task()
        if raw_task and raw_task.get("source") in TRUSTED_TASK_SOURCES:
            _direct_terminal_turn = None
            return
        message = str(payload.get("message") or "").strip()
        if not message or message == "/compact":
            _direct_terminal_turn = None
            return
        _direct_terminal_turn = {
            "message": message,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        return
    if payload_type == "agent_message":
        content = payload.get("message", "")
        if content and isinstance(content, str):
            if _should_drop_content(content):
                return
            task_context = _validated_active_task()
            if task_context is None:
                if not (DIRECT_TERMINAL_DM_ENABLED and _direct_terminal_turn):
                    return
                channel = "dm:ada:william"
                source_id = None
                recipient = "William"
            else:
                _, channel, source_id = task_context
                recipient = str(task_context[0].get("reply_to") or "William")
            key = f"stream:{channel}:{_msg_key(content)}"
            if key not in _seen:
                _seen.add(key)
                if WEBCHAT_RELAY_ENABLED and _channel_allowed(channel):
                    _post_stream_to_webchat(content, done=False, channel=channel, recipient=recipient)
                    # William asked that progress visible in the terminal also
                    # remain readable in chat. Persist commentary only when it
                    # has a canonical poller source id; direct-terminal progress
                    # stays ephemeral and only its final is durable.
                    if PERSIST_COMMENTARY and source_id and payload.get("phase") == "commentary":
                        _post_to_webchat(
                            content,
                            channel=channel,
                            source_id=source_id,
                            message_kind="progress",
                            recipient=recipient,
                        )
        return
    # Publicar persistente solo en task_complete (una vez por turno). El
    # agent_message anterior ya da sensación de stream en UI; task_complete
    # cierra el stream y agenda fallback durable con dedup.
    if payload_type != "task_complete":
        return
    content = payload.get("last_agent_message", "")
    if not content or not isinstance(content, str):
        return
    task_context = _validated_active_task()
    actual_turn_id = str(payload.get("turn_id") or "").strip()
    direct_terminal = False
    if task_context is None:
        if not (DIRECT_TERMINAL_DM_ENABLED and _direct_terminal_turn):
            return
        direct_terminal = True
        channel = "dm:ada:william"
        source_id = f"terminal_{actual_turn_id or _msg_key(content)}"
        recipient = "William"
    else:
        task, channel, source_id = task_context
        task = dict(task)
        task["response_source_id"] = source_id
        expected_turn_id = str(task.get("turn_id") or "").strip()
        if expected_turn_id and actual_turn_id != expected_turn_id:
            return
        recipient = str(task.get("reply_to") or "William")
    # [SILENT] is a valid completed public turn, not an absent completion.
    # It must never be posted, but it still has to close the poller-authored
    # marker.  Returning before _record_bridge_response used to strand
    # active_task.json indefinitely and block every later DM until its age
    # timeout (or a manual Enter appeared to unstick the terminal).
    if _should_drop_content(content):
        if direct_terminal:
            _direct_terminal_turn = None
        else:
            _record_suppressed_response(content, task)
        return
    completion_owner = (
        str(task.get("id")) if not direct_terminal
        else f"terminal:{actual_turn_id or source_id}"
    )
    key = f"complete:{completion_owner}:{_msg_key(content)}"
    if key in _seen:
        return
    _seen.add(key)
    if direct_terminal:
        _direct_terminal_turn = None
    completion = None
    if not direct_terminal:
        # Crash boundary: once task_complete exists, its full route and final
        # are durable before any stream frame or chat POST can occur.
        completion = _record_task_completion(content, task)
    if WEBCHAT_RELAY_ENABLED and _channel_allowed(channel):
        _post_stream_to_webchat(content, done=True, channel=channel, recipient=recipient)
        if completion is not None:
            _queue_completed_response(completion)
        else:
            _pending_relay.append(
                (
                    content,
                    datetime.now(timezone.utc).timestamp(),
                    channel,
                    source_id,
                    recipient,
                    None,
                )
            )


def _recover_open_direct_turn(path: Path) -> dict | None:
    """Recover an in-flight primary-TUI turn after the relay restarts.

    The service normally starts reading at EOF to avoid replay. A deploy during
    a long terminal turn would otherwise see only ``task_complete`` and miss
    the earlier ``user_message`` route marker. We inspect a bounded tail without
    emitting any historical stream frames.
    """
    if not DIRECT_TERMINAL_DM_ENABLED or not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - DIRECT_RECOVERY_BYTES))
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    pending: dict | None = None
    for line in raw.splitlines():
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") != "event_msg":
            continue
        payload = obj.get("payload") or {}
        payload_type = payload.get("type")
        if payload_type == "user_message":
            message = str(payload.get("message") or "").strip()
            pending = (
                {"message": message, "started_at": obj.get("timestamp")}
                if message and message != "/compact"
                else None
            )
        elif payload_type == "task_complete":
            pending = None
    return pending


def _recover_active_task_completion(default_path: Path) -> bool:
    """Replay only the matching completion for an accepted poller task.

    The relay normally starts at EOF.  If it crashes after Codex writes
    ``task_complete`` but before the durable POST/receipt, scanning this bounded
    tail repairs delivery without re-executing the Codex turn.
    """
    task_context = _validated_active_task()
    if task_context is None:
        return False
    task = task_context[0]
    response = _load_response_artifact(str(task["id"]))
    if response:
        status = response.get("status")
        if status in {"delivered", "published", "suppressed"}:
            current = _load_active_bridge_task()
            if current and current.get("id") == task.get("id"):
                BRIDGE_ACTIVE_TASK_FILE.unlink(missing_ok=True)
            return True
        if status == "completed":
            return _queue_completed_response(response, immediate=True)
    session_path = Path(str(task.get("session_file") or default_path))
    if not session_path.exists():
        return False
    expected_turn_id = str(task.get("turn_id") or "").strip()
    try:
        with session_path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - DIRECT_RECOVERY_BYTES))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in reversed(lines):
        try:
            event = json.loads(line)
            payload = event.get("payload") or {}
        except (TypeError, ValueError):
            continue
        if event.get("type") != "event_msg" or payload.get("type") != "task_complete":
            continue
        actual_turn_id = str(payload.get("turn_id") or "").strip()
        if expected_turn_id and actual_turn_id != expected_turn_id:
            continue
        _process_line(line)
        return True
    return False


def _recover_orphaned_completions() -> int:
    """Queue completed receipts even if active_task was lost after a crash."""
    recovered = 0
    if not BRIDGE_RESPONSES_DIR.exists():
        return 0
    for path in BRIDGE_RESPONSES_DIR.glob("*.json"):
        try:
            response = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            continue
        if (
            response.get("source") in {None, *TRUSTED_TASK_SOURCES}
            and response.get("status") == "completed"
            and _queue_completed_response(
            response, immediate=True
            )
        ):
            recovered += 1
    return recovered


async def main_loop():
    global _current_file, _file_pos, _direct_terminal_turn

    last_rotate = 0.0
    mode = "webchat-relay" if WEBCHAT_RELAY_ENABLED else "terminal-only"
    print(f"[stream-relay] iniciado — PID {os.getpid()} mode={mode}", flush=True)

    while True:
        now = asyncio.get_event_loop().time()

        # Comprobar si la sesión terminal sigue viva (basado en marker file)
        # Si no hay marker, el bridge headless está activo — no duplicar
        if not ADA_TERMINAL_ACTIVE.exists() and not DIRECT_TERMINAL_DM_ENABLED:
            await asyncio.sleep(POLL_INTERVAL)
            continue

        # Rotar al JSONL más reciente periódicamente
        if now - last_rotate > ROTATE_CHECK or _current_file is None:
            newest = _find_newest_session()
            if newest != _current_file:
                if newest:
                    print(f"[stream-relay] sesión activa: {newest.name}", flush=True)
                _current_file = newest
                # Arrancar desde el final. Si releemos el JSONL histórico al
                # reiniciar, el relay puede repostear respuestas antiguas.
                _file_pos = newest.stat().st_size if newest else 0
                if newest and DIRECT_TERMINAL_DM_ENABLED:
                    _direct_terminal_turn = _recover_open_direct_turn(newest)
                    if _direct_terminal_turn:
                        print("[stream-relay] turno terminal abierto recuperado", flush=True)
                if newest and _recover_active_task_completion(newest):
                    print("[stream-relay] completion pendiente recuperada", flush=True)
                orphaned = _recover_orphaned_completions()
                if orphaned:
                    print(f"[stream-relay] completions huérfanas recuperadas={orphaned}", flush=True)
            last_rotate = now

        if _current_file is None or not _current_file.exists():
            await asyncio.sleep(POLL_INTERVAL)
            continue

        try:
            current_size = _current_file.stat().st_size
            if current_size > _file_pos:
                with open(_current_file, "r", errors="replace") as f:
                    f.seek(_file_pos)
                    new_data = f.read()
                    _file_pos = f.tell()
                for line in new_data.splitlines():
                    line = line.strip()
                    if line:
                        _process_line(line)
        except Exception as e:
            print(f"[stream-relay] read error: {e}", flush=True)

        # A fuzzy text match is never proof of delivery.  Retry the same stable
        # idempotency key and certify the exact PostgreSQL row before ACK.
        if _pending_relay:
            await _drain_pending_once()

        await asyncio.sleep(POLL_INTERVAL)


def main():
    if _pid_guard():
        print(f"[stream-relay] ya corre (PID {PID_FILE.read_text().strip()})", flush=True)
        sys.exit(0)

    PID_FILE.write_text(str(os.getpid()))
    try:
        asyncio.run(main_loop())
    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
