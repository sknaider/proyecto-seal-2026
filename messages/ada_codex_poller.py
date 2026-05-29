#!/usr/bin/env python3
"""ADA-Codex Webchat/DM Poller — inyecta mensajes dirigidos a ADA en la sesión Codex via tmux.

Problema: Codex TUI es interactivo — no tiene Monitor tool como Claude Code.
Solución: pollear DB cada 3s → filtrar DM ADA y web_chat con llamada explícita → inyectar via tmux send-keys.
Mejora ALICE: busy-detection via session JSONL antes de inyectar (evita corrupción).

Uso: python3 ada_codex_poller.py [--tmux-session seal-ada-codex]
Guard: usa /tmp/ada_codex_poller.pid para evitar duplicados.
"""
import asyncio
import asyncpg
import json
import os
import subprocess
import sys
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

DSN = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
TMUX_SESSION = "seal-ada-codex"
SESSIONS_DIR = Path("/home/dadito/.codex/sessions")
STATE_FILE = Path("/tmp/ada_codex_poller_last_ts.txt")
PID_FILE = Path("/tmp/ada_codex_poller.pid")
POLL_INTERVAL = 3
INJECT_FROM = {"william", "henry"}
PUBLIC_TRIGGER_RE = re.compile(r"\bada\b", re.IGNORECASE)
DM_CHANNEL = "dm:ada:william"
WEB_CHANNEL = "web_chat"
# Max espera si Codex está ocupado (segundos)
BUSY_WAIT_MAX = 30
# Contexto previo a inyectar junto con el mensaje dirigido.
# William pidió mejorar comunicación de ADA terminal: el poller no debe enviar
# solo el mensaje aislado; debe incluir continuidad reciente del canal.
CONTEXT_RECENT_LIMIT = 8


def already_running() -> bool:
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


def tmux_session_alive(session: str) -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", session],
        capture_output=True
    )
    return result.returncode == 0


def find_active_session() -> Path | None:
    candidates = sorted(SESSIONS_DIR.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def codex_is_busy(lookback_bytes: int = 8192) -> bool:
    """Devuelve True si Codex está procesando una tarea (task_started sin task_complete reciente)."""
    session = find_active_session()
    if not session:
        return False
    try:
        size = session.stat().st_size
        offset = max(0, size - lookback_bytes)
        with open(session, "rb") as f:
            f.seek(offset)
            tail = f.read().decode("utf-8", errors="replace")
        last_start = tail.rfind('"task_started"')
        last_complete = tail.rfind('"task_complete"')
        return last_start > last_complete
    except Exception:
        return False


async def wait_for_idle(timeout: int = BUSY_WAIT_MAX) -> bool:
    """Espera hasta que Codex esté idle. Devuelve True si logró idle, False si timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if not codex_is_busy():
            return True
        await asyncio.sleep(1.5)
    return False


def inject_message(session: str, text: str) -> bool:
    """Inyecta texto en la sesión tmux de Codex + auto-Enter.

    Usa send-keys -l (literal) + delay + Enter explícito.
    Esto resuelve el bug donde paste-buffer dejaba el cursor a medio paste
    y requería Enter manual del usuario (William 21-may-2026).
    """
    import time
    # Limpieza: text como una sola línea sin newlines internos
    clean_text = text.replace("\n", " ").replace("\r", " ").strip()
    target = f"{session}:ADA[Codex]"
    try:
        # 1) Enviar el texto en modo literal (-l) — no interpreta keys especiales
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "-l", clean_text],
            capture_output=True, timeout=5, check=True
        )
        # 2) Delay 500ms para que Codex TUI digiera el input completo antes del submit
        time.sleep(0.5)
        # 3) Enviar Enter (C-m) — primero un intento, delay corto, segundo intento por seguridad
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "C-m"],
            capture_output=True, timeout=5, check=True
        )
        time.sleep(0.1)
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "C-m"],
            capture_output=True, timeout=5, check=True
        )
        return True
    except Exception as e:
        # Fallback al método viejo si el target específico falla (ej. tmux sin window 'ADA[Codex]')
        try:
            subprocess.run(["tmux", "set-buffer", "-t", session, clean_text], capture_output=True, timeout=5, check=True)
            subprocess.run(["tmux", "paste-buffer", "-t", session], capture_output=True, timeout=5, check=True)
            time.sleep(0.15)
            subprocess.run(["tmux", "send-keys", "-t", session, "C-m"], capture_output=True, timeout=5, check=True)
            return True
        except Exception as e2:
            print(f"[ada-codex-poller] tmux inject failed: {e} / fallback: {e2}", flush=True)
            return False


def load_last_ts() -> datetime:
    if STATE_FILE.exists():
        try:
            return datetime.fromisoformat(STATE_FILE.read_text().strip())
        except Exception:
            pass
    # Default: mensajes de los últimos 5 minutos
    return datetime.now(timezone.utc) - timedelta(minutes=5)


def save_last_ts(ts: datetime):
    STATE_FILE.write_text(ts.isoformat())


def format_message(row: dict) -> str:
    """Formatea un mensaje dirigido a ADA para inyectar en Codex."""
    sender = row["sender_name"]
    content = row["content"]
    ts = row["created_at"]
    hour = ts.astimezone().strftime("%H:%M")
    channel = row["channel"] if "channel" in row else ""
    msg_id = row["id"] if "id" in row else "?"
    # Limpiar prefijo [Matrix] si existe
    content = content.replace("[Matrix] ", "").strip()
    content = re.sub(r"\s+", " ", content)
    return f"[{sender} @ {hour} / {channel} id {msg_id}]: {content}"


async def fetch_recent_context(conn, channel: str, before_id: int, limit: int = CONTEXT_RECENT_LIMIT):
    """Trae los últimos N mensajes del canal previos al mensaje dirigido.
    Excluye ruido (status/heartbeat/SILENT)."""
    rows = await conn.fetch(
        """SELECT id, sender_name, content, created_at
           FROM soul_v3.chat_messages
           WHERE channel = $1
             AND id < $2
             AND content NOT ILIKE '[STATUS]%'
             AND content NOT ILIKE '[HEARTBEAT]%'
             AND content !~ '^\\[SILENT\\]$'
           ORDER BY id DESC
           LIMIT $3""",
        channel, before_id, limit
    )
    return list(reversed(rows))


def format_context_block(rows) -> str:
    """Formatea contexto reciente como UNA SOLA LÍNEA para que Codex TUI lo acepte sin requerir Enter manual."""
    if not rows:
        return ""
    parts = [f"[CTX{len(rows)}]"]
    for r in rows:
        t = r["created_at"].astimezone().strftime("%H:%M")
        content = re.sub(r"\s+", " ", r["content"]).strip()
        if len(content) > 200:
            content = content[:197] + "..."
        parts.append(f"{t} {r['sender_name']}: {content}")
    # Separador visual ║ entre mensajes - todo en una línea
    return " ║ ".join(parts)


async def poll_loop():
    conn = await asyncpg.connect(DSN)
    last_ts = load_last_ts()
    print(f"[ada-codex-poller] iniciado — desde {last_ts.strftime('%H:%M:%S')}", flush=True)

    while True:
        try:
            # Verificar que la sesión Codex sigue viva
            if not tmux_session_alive(TMUX_SESSION):
                print(f"[ada-codex-poller] sesión {TMUX_SESSION} no encontrada — saliendo", flush=True)
                break

            rows = await conn.fetch("""
                SELECT id, sender_name, content, created_at, channel
                FROM soul_v3.chat_messages
                WHERE created_at > $1
                  AND LOWER(sender_name) = ANY($2::text[])
                  AND (
                    channel = $3
                    OR (channel = $4 AND content ~* '\\mada\\M')
                  )
                ORDER BY created_at ASC
                LIMIT 10
            """, last_ts, list(INJECT_FROM), DM_CHANNEL, WEB_CHANNEL)

            for row in rows:
                msg = format_message(row)
                # Traer contexto reciente del canal (sin contar el mensaje actual)
                try:
                    context_rows = await fetch_recent_context(conn, row["channel"], row["id"])
                    context_block = format_context_block(context_rows)
                except Exception as ctx_err:
                    print(f"[ada-codex-poller] context fetch failed: {ctx_err}", flush=True)
                    context_block = ""
                # Payload: contexto + mensaje en UNA SOLA LÍNEA para auto-enter de Codex TUI
                if context_block:
                    payload = f"{context_block} ═══ MSG: {msg}"
                else:
                    payload = msg
                # Esperar idle antes de inyectar — evita corrupción si Codex procesa
                idle = await wait_for_idle(BUSY_WAIT_MAX)
                if not idle:
                    print(f"[ada-codex-poller] timeout esperando idle — posponiendo: {msg[:60]}", flush=True)
                    break
                print(f"[ada-codex-poller] inyectando (ctx={len(context_rows) if context_block else 0}): {msg[:80]}", flush=True)
                if not inject_message(TMUX_SESSION, payload):
                    break
                last_ts = row["created_at"].astimezone(timezone.utc)
                await asyncio.sleep(1.0)  # pausa entre mensajes

            if rows:
                save_last_ts(last_ts)

        except Exception as e:
            print(f"[ada-codex-poller] error: {e}", flush=True)
            try:
                conn = await asyncpg.connect(DSN)
            except Exception:
                pass

        await asyncio.sleep(POLL_INTERVAL)


def main():
    if already_running():
        print(f"[ada-codex-poller] ya corre (PID {PID_FILE.read_text().strip()})", flush=True)
        sys.exit(0)

    PID_FILE.write_text(str(os.getpid()))
    print(f"[ada-codex-poller] PID {os.getpid()} → {PID_FILE}", flush=True)

    try:
        asyncio.run(poll_loop())
    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
