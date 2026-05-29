#!/usr/bin/env python3
"""ADA-Codex Stream Relay — publica respuestas de ADA al webchat en tiempo real.

Monitorea el JSONL activo de la sesión Codex terminal y reenvía cada
`agent_message` al webchat automáticamente, sin esperar que ADA corra curl.

- Sigue el archivo JSONL activo (newest by mtime)
- Detecta event_msg/agent_message nuevos
- POST a /api/agents/send → ADA→William web_chat
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

SESSIONS_DIR = Path("/home/dadito/.codex/sessions")
WEBCHAT_URL = "http://localhost:8765/api/agents/send"
STREAM_URL = "http://localhost:8765/internal/stream"
PID_FILE = Path("/tmp/ada_codex_stream_relay.pid")
ADA_TERMINAL_ACTIVE = Path("/tmp/seal/ada_terminal_active")
TMUX_SESSION = "seal-ada-codex"
POLL_INTERVAL = 1.5   # segundos entre lecturas del JSONL
ROTATE_CHECK = 10.0   # segundos entre chequeos de sesión nueva
MAX_MSG_LEN = 2000    # truncar mensajes muy largos

_seen: set[str] = set()
_current_file: Path | None = None
_file_pos: int = 0
_pending_relay: list = []   # (content, queued_at_timestamp)
RELAY_DELAY = 1.5           # terminal relay debe sentirse vivo; dedup API evita doble post
RECENT_API = "http://localhost:8765/api/chat/messages/agent?agent=ADA&limit=8"


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
    candidates = list(SESSIONS_DIR.rglob("*.jsonl"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


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


def _post_to_webchat(message: str) -> bool:
    if len(message) > MAX_MSG_LEN:
        message = message[:MAX_MSG_LEN] + "…"
    payload = json.dumps({
        "from": "ADA",
        "to": "William",
        "type": "conversation",
        "channel": "web_chat",
        "message": message,
    }).encode()
    req = urllib.request.Request(
        WEBCHAT_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception as e:
        print(f"[stream-relay] POST failed: {e}", flush=True)
        return False


def _stream_id_for_content(content: str) -> str:
    return f"ada_terminal_stream_{_msg_key(content)}"


def _post_stream_to_webchat(message: str, done: bool = False) -> bool:
    if len(message) > MAX_MSG_LEN:
        message = message[:MAX_MSG_LEN] + "…"
    payload = json.dumps({
        "id": _stream_id_for_content(message),
        "from": "ADA",
        "to": "William",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "stream",
        "channel": "web_chat",
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


def _process_line(line: str) -> None:
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
    if payload_type == "agent_message":
        content = payload.get("message", "")
        if content and isinstance(content, str):
            key = f"stream:{_msg_key(content)}"
            if key not in _seen:
                _seen.add(key)
                _post_stream_to_webchat(content, done=False)
        return
    # Publicar persistente solo en task_complete (una vez por turno). El
    # agent_message anterior ya da sensación de stream en UI; task_complete
    # cierra el stream y agenda fallback durable con dedup.
    if payload_type != "task_complete":
        return
    content = payload.get("last_agent_message", "")
    if not content or not isinstance(content, str):
        return
    key = _msg_key(content)
    if key in _seen:
        return
    _seen.add(key)
    _post_stream_to_webchat(content, done=True)
    # Marcar como pendiente — se publica en main_loop con delay para dar tiempo a ADA de postear ella
    _pending_relay.append((content, datetime.now(timezone.utc).timestamp()))


async def main_loop():
    global _current_file, _file_pos

    last_rotate = 0.0
    print(f"[stream-relay] iniciado — PID {os.getpid()}", flush=True)

    while True:
        now = asyncio.get_event_loop().time()

        # Comprobar si la sesión terminal sigue viva (basado en marker file)
        # Si no hay marker, el bridge headless está activo — no duplicar
        if not ADA_TERMINAL_ACTIVE.exists():
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

        # Drenar pending: publicar solo si ADA no lo publicó sola en RELAY_DELAY segundos
        if _pending_relay:
            now_ts = datetime.now(timezone.utc).timestamp()
            ready = [(c, t) for c, t in _pending_relay if now_ts - t >= RELAY_DELAY]
            for content, _ in ready:
                _pending_relay.remove((content, _))
                # Verificar si ADA ya publicó contenido similar via API
                already_posted = False
                try:
                    with urllib.request.urlopen(RECENT_API, timeout=3) as r:
                        recent = json.loads(r.read())
                        msgs = recent if isinstance(recent, list) else recent.get("messages", [])
                        for m in msgs:
                            body = m.get("content", m.get("message", ""))
                            if _is_similar_response(body, content):
                                already_posted = True
                                break
                except Exception:
                    pass
                if already_posted:
                    print(f"[stream-relay] skip (ADA ya publicó): {content[:60]}", flush=True)
                else:
                    ts = datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")
                    print(f"[stream-relay] fallback-post {ts} → {content[:60]}", flush=True)
                    _post_to_webchat(content)

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
