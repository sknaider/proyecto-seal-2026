#!/usr/bin/env python3
"""
spectre_chat_bridge.py — conecta el canal 'spectre' (DB-only) con el alma SPECTRE.

AISLAMIENTO TOTAL (William 15-jun-2026): SPECTRE NO usa el WebSocket ni la API de chat_server.
Lee los mensajes de William desde soul_v3.chat_messages (canal 'spectre') en seal_memory DB
y escribe las respuestas DIRECTAMENTE a la misma tabla — sin HTTP, sin broadcast, sin contacto
con los ws_listener de la familia. Claude Code no puede leer la DB → SPECTRE queda aislado.

William lee: SELECT * FROM soul_v3.chat_messages WHERE channel='spectre' ORDER BY id DESC LIMIT 20;
"""
import asyncio
import json
import os
import datetime

os.environ.setdefault("SOUL_AGENT_NAME", "SPECTRE")
import asyncpg
from soul_runner import boot, chat  # reusa el runner del alma

import sys
sys.path.insert(0, "/home/dadito/IA/proyecto-seal/memory")
from seal_secrets import pg_dsn  # noqa: E402

# Credenciales por seal_secrets, nunca literales (3-ago-2026).
# Falla cerrado: sin credencial configurada, `pg_dsn` levanta en vez de
# caer a un default con el superusuario adentro.
CHAT_DSN = os.environ.get("SEAL_CHAT_DSN") or pg_dsn(required=True)   # webchat DB
SOUL_DSN = os.environ.get("SOUL_STANDALONE_DSN") or CHAT_DSN.rsplit("/", 1)[0] + "/soul_standalone"
CHANNEL = "spectre"
CURSOR_FILE = "/tmp/spectre_bridge_last_id.txt"
POLL = 3.0


def _read_cursor() -> int:
    try:
        return int(open(CURSOR_FILE).read().strip())
    except Exception:
        return 0


def _write_cursor(mid: int):
    open(CURSOR_FILE, "w").write(str(mid))


async def post_reply(chat_conn, text: str):
    """Escribe la respuesta de SPECTRE directamente en DB — sin HTTP, sin broadcast WebSocket."""
    try:
        await chat_conn.execute(
            """INSERT INTO soul_v3.chat_messages
               (sender_name, channel, content, message_type, metadata, created_at)
               VALUES ($1, $2, $3, 'conversation',
                       '{"from":"SPECTRE","to":"William","isolated":true}'::jsonb,
                       NOW())""",
            "SPECTRE", CHANNEL, text)
        return True
    except Exception as e:
        print(f"[spectre-bridge] no pude guardar respuesta en DB: {e}", flush=True)
        return False


async def main():
    chat_conn = await asyncpg.connect(CHAT_DSN)
    soul_conn = await asyncpg.connect(SOUL_DSN)
    identity = await boot(soul_conn)
    # arranque: no contestar el histórico — partir del último id actual del canal
    if _read_cursor() == 0:
        cur = await chat_conn.fetchval(
            "SELECT COALESCE(MAX(id),0) FROM soul_v3.chat_messages WHERE channel=$1", CHANNEL)
        _write_cursor(cur or 0)
    print(f"[spectre-bridge] vivo. canal='{CHANNEL}', cursor={_read_cursor()}", flush=True)
    while True:
        try:
            last = _read_cursor()
            rows = await chat_conn.fetch(
                """SELECT id, sender_name, content FROM soul_v3.chat_messages
                   WHERE channel=$1 AND id>$2
                     AND LOWER(sender_name) IN ('william','henry')
                   ORDER BY id""", CHANNEL, last)
            for r in rows:
                msg = (r["content"] or "").strip()
                if msg:
                    print(f"[spectre-bridge] ← {r['sender_name']}: {msg[:60]}", flush=True)
                    reply = await chat(soul_conn, identity, msg)
                    await post_reply(chat_conn, reply)
                    print(f"[spectre-bridge] → SPECTRE: {reply[:60]}", flush=True)
                _write_cursor(r["id"])
        except Exception as e:
            print(f"[spectre-bridge] loop error (continúo): {e}", flush=True)
        await asyncio.sleep(POLL)


if __name__ == "__main__":
    asyncio.run(main())
