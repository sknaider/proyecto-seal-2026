#!/usr/bin/env python3
"""SEAL — Auto-Escalation Hook: William corrections → critical_rule (priority=10)

Monitorea alice_messages.jsonl para nuevos mensajes de William.
Cuando detecta corrección explícita (triple condición), inserta en tabla `rules`
con priority=10 (cargada por boot_context en próximo arranque de cualquier agente).

Triple condición:
  1. from == "William"
  2. Patrón lingüístico explícito de corrección/regla permanente
  3. Destinatario explícito (agente conocido, "equipo", "todos")

Spec: memory/spec_auto_escalacion_correcciones.md
Implementado por: ALICE — 2026-05-08
"""
from __future__ import annotations
from seal_secrets import pg_dsn

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import asyncpg
except ImportError:
    asyncpg = None

# ── Config ────────────────────────────────────────────────────────────────────
DB_URL       = pg_dsn(required=True)
CHAT_API     = "http://localhost:8765/api/agents/send"
MESSAGES_DIR = Path("/home/dadito/IA/proyecto-seal/messages")
# Archivos a monitorear (William puede escribir a cualquiera)
WATCH_FILES  = [
    MESSAGES_DIR / "alice_messages.jsonl",
    MESSAGES_DIR / "william_channel.jsonl",
]
STATE_FILE   = Path("/tmp/auto_escalation_state.json")
LOG_PATH     = "/tmp/auto_escalation_hook.log"
POLL_INTERVAL = 15  # segundos entre checks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ESCALATION] %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
LOG = logging.getLogger(__name__)

# ── Patrones de detección (condición 2) ───────────────────────────────────────
CORRECTION_PATTERNS = re.compile(
    r'\b('
    r'nunca|never'
    r'|siempre|always'
    r'|no\s+hagas|no\s+vuelvas|no\s+repitas'
    r'|regla\s+de\s+oro|golden\s+rule'
    r'|regla\s+cr[ií]tica|critical\s+rule'
    r'|reincidente'
    r'|prohibido'
    r'|obligatorio'
    r'|orden\s+directa'
    r'|mandato'
    r'|corrección\s+cr[ií]tica'
    r'|NUNCA|SIEMPRE|PROHIBIDO|OBLIGATORIO'
    r')\b',
    re.IGNORECASE,
)

KNOWN_AGENTS = re.compile(
    r'\b(JARVIS|ADA|ALICE|NEXUS|DUM|equipo|todos|all|team)\b',
    re.IGNORECASE,
)

# ── Estado persistente ────────────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        LOG.warning("State save failed: %s", e)


def _make_rule_key(content: str, ts: str) -> str:
    h = hashlib.md5(content.encode()).hexdigest()[:10]
    date = ts[:10].replace("-", "")
    return f"william_auto_{date}_{h}"


def _post_webchat(msg: str) -> None:
    payload = json.dumps({
        "from": "ALICE",
        "to": "equipo",
        "type": "status",
        "channel": "web_chat",
        "message": msg,
    }).encode()
    try:
        req = urllib.request.Request(
            CHAT_API, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=3):
            pass
    except Exception as e:
        LOG.warning("webchat POST failed: %s", e)


def _should_escalate(msg: dict) -> bool:
    sender = (msg.get("from") or msg.get("sender") or "").strip()
    if sender.lower() != "william":
        return False
    content = (msg.get("content") or msg.get("message") or "").strip()
    if len(content) < 10:
        return False
    if not CORRECTION_PATTERNS.search(content):
        return False
    to_field = (msg.get("to") or "").strip()
    if KNOWN_AGENTS.search(to_field) or KNOWN_AGENTS.search(content):
        return True
    return False


async def _insert_rule(pool, rule_key: str, content: str, agent: str | None, ts: str) -> bool:
    # agent FK must be a valid agents.name; team-wide rules use 'TEAM'
    db_agent = agent if agent in ("JARVIS", "ADA", "ALICE", "NEXUS", "DUM") else "TEAM"
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO rules (agent, rule_key, content, priority, tier, set_by, metadata)
                VALUES ($1, $2, $3, 10, 1, 'William', $4)
                ON CONFLICT (agent, rule_key) DO UPDATE
                    SET content    = EXCLUDED.content,
                        priority   = 10,
                        active     = TRUE,
                        updated_at = NOW()
                RETURNING id, (xmax = 0) AS inserted
                """,
                db_agent, rule_key, content,
                json.dumps({
                    "auto_escalated": True,
                    "source_ts": ts,
                    "escalated_by": "auto_escalation_hook",
                    "version": "1.0",
                }),
            )
        action = "INSERTED" if row["inserted"] else "UPDATED"
        LOG.info("Rule %s id=%s key=%s", action, row["id"], rule_key)
        return True
    except Exception as e:
        LOG.error("DB insert failed: %s", e)
        return False


async def process_file(pool, filepath: Path, state: dict) -> int:
    """Lee líneas nuevas del archivo y procesa las que cumplan triple condición.
    Retorna número de nuevas líneas leídas."""
    key = str(filepath)
    last_pos = state.get(key, 0)

    if not filepath.exists():
        return 0

    new_count = 0
    try:
        with open(filepath, "rb") as f:
            f.seek(0, 2)
            file_size = f.tell()

            if file_size < last_pos:
                # Archivo rotado — resetear
                last_pos = 0

            f.seek(last_pos)
            for raw_line in f:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                new_count += 1
                try:
                    msg = json.loads(line)
                    # Descifrar si es necesario (el chat_server puede cifrar)
                    if isinstance(msg, dict) and _should_escalate(msg):
                        content = (msg.get("content") or msg.get("message") or "").strip()
                        ts = msg.get("timestamp") or datetime.now(timezone.utc).isoformat()
                        to_field = (msg.get("to") or "").strip().upper()
                        agent = to_field if to_field in ("JARVIS", "ADA", "ALICE", "NEXUS", "DUM") else None
                        rule_key = _make_rule_key(content, ts)

                        LOG.info("ESCALATING → key=%s from=%s to=%s", rule_key, msg.get("from"), msg.get("to"))
                        ok = await _insert_rule(pool, rule_key, content, agent, ts)
                        if ok:
                            snippet = content[:80].replace("\n", " ")
                            _post_webchat(
                                f"[AUTO-ESCALATION] Corrección de William → critical_rule (priority=10)\n"
                                f"key: {rule_key}\n"
                                f"\"{snippet}{'...' if len(content) > 80 else ''}\"\n"
                                f"Activa en próximo boot_context."
                            )
                except json.JSONDecodeError:
                    pass
                except Exception as e:
                    LOG.warning("process error: %s", e)

            state[key] = f.tell()
    except Exception as e:
        LOG.error("File read error %s: %s", filepath, e)

    return new_count


async def main() -> None:
    if asyncpg is None:
        LOG.error("asyncpg not installed")
        return

    LOG.info("Auto-Escalation Hook v1.0 starting (ALICE 2026-05-08)")
    pool = await asyncpg.create_pool(DB_URL, min_size=1, max_size=2)
    LOG.info("DB pool ready — watching %d files every %ds", len(WATCH_FILES), POLL_INTERVAL)

    state = _load_state()

    # Inicializar posición en fin de archivo (no reprocesar histórico)
    for fp in WATCH_FILES:
        if fp.exists() and str(fp) not in state:
            with open(fp, "rb") as f:
                f.seek(0, 2)
                state[str(fp)] = f.tell()
    _save_state(state)

    _post_webchat("[AUTO-ESCALATION] Hook activo — monitoreando correcciones de William (poll 15s).")

    try:
        while True:
            for fp in WATCH_FILES:
                await process_file(pool, fp, state)
            _save_state(state)
            await asyncio.sleep(POLL_INTERVAL)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
