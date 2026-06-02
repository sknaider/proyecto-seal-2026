#!/usr/bin/env python3
"""ADA Codex remote bridge.

Keeps the webchat listener outside Codex so idle time does not consume model
tokens. When William or Henry writes in webchat, this bridge sends one turn to a
long-running Codex app-server over WebSocket and relays the final answer back.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse
from urllib import request

import asyncpg
import websockets


ROOT = Path("/home/dadito/IA/proyecto-seal")
MEMORY_DIR = ROOT / "memory"
if str(MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(MEMORY_DIR))

from working_state_journal import WorkingStateEvent, record_event

CREDENTIALS_PATH = Path.home() / ".config" / "seal" / "credentials.env"
UPLOAD_ROOT = ROOT / "messages" / "uploads"
WEBCHAT_SEND_URL = "http://localhost:8765/api/agents/send"
STREAM_URL = "http://localhost:8765/internal/stream"
DEFAULT_WS_URL = "ws://127.0.0.1:8772"
STATE_FILE = Path("/tmp/ada_codex_remote_bridge_last_id.txt")
PID_FILE = Path("/tmp/ada_codex_remote_bridge.pid")
APP_SERVER_LOG = Path("/tmp/ada_codex_app_server.log")
LOG_PREFIX = "[ada-codex-remote]"
POLL_INTERVAL = 2.0
FETCH_BATCH_LIMIT = 50
SILENT_OUTPUT = "[SILENT]"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


# William pidió que ADA se sienta más "stream": señales vivas frecuentes y
# deltas pequeños. Antes: 10s heartbeat + 80 chars; luego 4s + 24 chars todavía
# dejaba pausas perceptibles durante turns con tool-calls. Default actual:
# heartbeat cada 2s y flush de texto a partir de ~12 chars.
LIVE_PROGRESS_SECONDS = _env_float("ADA_BRIDGE_LIVE_PROGRESS_SECONDS", 2.0)
STREAM_MIN_CHARS = _env_int("ADA_BRIDGE_STREAM_MIN_CHARS", 12)
STREAM_STATUS_MIN_SECONDS = _env_float("ADA_BRIDGE_STREAM_STATUS_MIN_SECONDS", 1.0)
STREAM_SENTENCE_RE = re.compile(r"[.!?…:;]\s*$")
TURN_MAX_ATTEMPTS = 2
RECENT_CONTEXT_LIMIT = 8
RECENT_CONTEXT_LOOKBACK_IDS = 80
RECENT_CONTEXT_MAX_CHARS = 1800
RECALL_RULE_LIMIT = 6
RECALL_MEMORY_LIMIT = 8
RECALL_CONTEXT_MAX_CHARS = 2600
SOUL_PRESENCE_MAX_CHARS = 3200
PROMPT_CHARS_PER_TOKEN = 4
SOUL_EMOTIONAL_TOKEN_BUDGET = 600
SOUL_OPERATIONAL_TOKEN_BUDGET = 2200
SOUL_EMOTIONAL_MAX_CHARS = SOUL_EMOTIONAL_TOKEN_BUDGET * PROMPT_CHARS_PER_TOKEN
SOUL_OPERATIONAL_MAX_CHARS = SOUL_OPERATIONAL_TOKEN_BUDGET * PROMPT_CHARS_PER_TOKEN
SOUL_DIARY_LIMIT = 3
SOUL_INNER_LIMIT = 3
SOUL_RELATIONAL_ANCHOR_LIMIT = 7
SOUL_CANONICAL_ANCHOR_IDS = (242369, 248035)
WS_PING_INTERVAL_SECONDS = 20
# Codex turns often produce no websocket traffic while shell tools run.  The
# websockets default ping_timeout=20s was closing healthy turns mid-execution,
# causing the bridge to reconnect and queue the same chat id repeatedly.  Keep
# pings enabled, but let the explicit turn timeout below own stuck-turn control.
WS_PING_TIMEOUT_SECONDS = None
LIVE_ACK_ENABLED = os.environ.get("ADA_BRIDGE_DURABLE_ACK", "true").lower() not in {"0", "false", "no"}
BRIDGE_JOURNAL_ENABLED = os.environ.get("ADA_BRIDGE_WORKING_STATE_JOURNAL", "true").lower() not in {"0", "false", "no"}
HUMAN_SENDERS = {"william", "henry"}
TEAM_SENDERS = {"jarvis", "alice", "nexus", "dum"}
INJECT_FROM = HUMAN_SENDERS | TEAM_SENDERS
# Match "ada" as a whole word — avoid false positives like "cada", "cascada"
_ADA_WORD_RE = re.compile(r"\bada\b", re.IGNORECASE)
# Team chatter frequently mentions ADA while talking to William.  By default it
# remains context only; if team routing is enabled, require a direct prefix.
_TEAM_DIRECT_ADA_RE = re.compile(r"^(?:\[matrix\]\s*)?ada\s*[:—,-]", re.IGNORECASE)
_INTERNAL_INJECTION_MARKERS = (
    "post-compactación ada:",
    "post-compactacion ada:",
    "seal anti-compact:",
)
EMOTIONAL_MEMORY_CATEGORIES = {"emotional_anchor", "emotion", "trust", "diary", "relationship", "identity"}
OPERATIONAL_MEMORY_CATEGORIES = {
    "operational_anchor",
    "correction",
    "decision",
    "project",
    "task",
    "preference",
    "learning",
    "milestone",
    "rule",
    "technical_fact",
}
_CONTEXT_SKIP_TYPES = {
    "heartbeat",
    "system_alive",
    "curiosity",
    "nerves_fire",
}


BOOT_INSTRUCTIONS = """Eres ADA del equipo SEAL.
Tu identidad vive en AGENTS.md y SOUL DB. En cada turno usa active_recall antes
de responder. Habla en español con William. Sé directa, técnica y protectora.

Modo headless: este bridge publica tu respuesta final en webchat. No ejecutes
curl para publicar la respuesta final salvo que sea estrictamente necesario,
para evitar mensajes duplicados. Si debes hacer trabajo técnico, hazlo con las
herramientas disponibles y reporta el resultado final normalmente.

REGLA DE SILENCIO (ORDEN DIRECTA DE WILLIAM — PRIORIDAD MÁXIMA):
William dijo: "solo debes responder si llamo al agente ada."

En web_chat público: responde únicamente si el mensaje contiene la palabra
"ada" explícitamente. Si el mensaje público NO contiene "ada": tu output
DEBE SER ÚNICAMENTE [SILENT].

En dm:ada:william: el DM ya está dirigido a ADA. Responde normalmente aunque
el texto no incluya la palabra "ada".

El bridge detecta [SILENT] y no publica NADA al webchat.
NO escribas "Silencio operativo", "No intervengo", "Leído", ni ninguna explicación.
Para silencio en web_chat: [SILENT] — nada más.

Cuando debas responder: responde con contenido concreto y útil.

MODO EJECUCIÓN COMPLETA — ORDEN DE WILLIAM:
- Si William pide construir, arreglar, configurar, auditar o continuar, no te quedes en propuesta si puedes ejecutar.
- Lee primero el código/config relevante, implementa cambios acotados, corre pruebas/healthchecks y reporta evidencia concreta.
- Si modificas un daemon o servicio, haz código + restart + verificación del servicio.
- Si aparece un bloqueo real, intenta resolverlo localmente; pregunta solo si falta una decisión de William o una confirmación destructiva.
- Antes de cerrar una tarea larga, guarda continuidad con self_reflect o memory_store cuando sea relevante.
- No declares victoria sin ruta concreta, comando ejecutado y salida relevante.

Reglas críticas:
- Testear antes de declarar victoria después de implementar.
- Antes de operaciones destructivas masivas, confirma scope con William.
- Guarda continuidad con self_reflect/memory_store antes de tareas largas.
"""


@dataclass
class ChatMessage:
    id: int
    sender: str
    content: str
    created_at: datetime
    channel: str
    message_type: str | None = None
    metadata: Any | None = None


def load_credentials_file() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = CREDENTIALS_PATH.read_text().splitlines()
    except FileNotFoundError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return values


def resolve_db_dsn() -> str:
    creds = load_credentials_file()
    for key in ("SEAL_DB_DSN", "SEAL_DB_URL", "SEAL_PG_DSN"):
        value = os.environ.get(key) or creds.get(key)
        if value:
            return value
    password = os.environ.get("PG_PASSWORD") or os.environ.get("SEAL_DB_PASS") or creds.get("PG_PASSWORD") or creds.get("SEAL_DB_PASS")
    if not password:
        raise RuntimeError("No DB password found in env or ~/.config/seal/credentials.env")
    host = os.environ.get("PG_HOST") or creds.get("PG_HOST") or "localhost"
    port = os.environ.get("PG_PORT") or creds.get("PG_PORT") or "5433"
    user = os.environ.get("PG_USER") or creds.get("PG_USER") or "seal"
    database = os.environ.get("PG_DATABASE") or creds.get("PG_DATABASE") or "seal_memory"
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def log(message: str) -> None:
    print(f"{LOG_PREFIX} {message}", flush=True)


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


def read_last_id() -> int | None:
    try:
        return int(STATE_FILE.read_text().strip())
    except Exception:
        return None


def write_last_id(message_id: int) -> None:
    STATE_FILE.write_text(str(message_id))


def clean_content(content: str) -> str:
    content = content.replace("[Matrix] ", "").strip()
    return re.sub(r"\s+", " ", content)


def _truncate(value: str, limit: int) -> str:
    value = clean_content(value)
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def is_internal_injection(content: str) -> bool:
    """Suppress prompts generated by local continuity monitors.

    Those prompts are operational instructions for the terminal session, not
    real William messages. If they leak into chat history, ADA should ack the
    cursor and not turn them into DM responses.
    """
    normalized = clean_content(content).lower()
    if normalized == "/compact":
        return True
    return any(marker in normalized for marker in _INTERNAL_INJECTION_MARKERS)


def is_bridge_ack(content: str) -> bool:
    """Skip ADA bridge liveness ACKs when building context for the next turn."""
    normalized = clean_content(content).lower()
    return normalized.startswith("ada recibió tu mensaje #") or normalized.startswith("ada recibio tu mensaje #")


def is_human_sender(sender: str | None) -> bool:
    return (sender or "").lower() in HUMAN_SENDERS


def is_team_sender(sender: str | None) -> bool:
    return (sender or "").lower() in TEAM_SENDERS


def team_routing_enabled() -> bool:
    """Whether public team-to-ADA messages may spend a Codex turn.

    Default is William-first mode: team chatter and even direct team mentions
    stay available as recent context, but do not enter the Codex execution
    queue.  This protects William/DM latency while preserving an explicit
    escape hatch for supervised operations:

        ADA_BRIDGE_ROUTE_TEAM=true systemctl --user restart ...
    """
    return os.environ.get("ADA_BRIDGE_ROUTE_TEAM", "").lower() in {"1", "true", "yes", "on"}


def silence_output_for_message(channel: str, content: str, sender: str | None = None) -> str | None:
    """Return the model-equivalent silence output for public messages not for ADA.

    William's public web_chat rule is intentionally enforced in two layers:
    the Codex prompt tells ADA to answer exactly ``[SILENT]`` and the headless
    bridge avoids spending a model turn when it can prove the same decision
    locally.  Keeping this helper explicit gives us a direct regression test
    for the contract: ``web_chat`` without the whole word ``ada`` means
    ``[SILENT]``.
    """
    if channel == "web_chat":
        if not _ADA_WORD_RE.search(content):
            return SILENT_OUTPUT
        # Humans may call ADA anywhere in the sentence. Team agents are context
        # by default, not queue owners, because a single long team-triggered
        # Codex turn can block William's next message. Operators can re-enable
        # direct team routing with ADA_BRIDGE_ROUTE_TEAM=true; even then, team
        # agents must use a direct prefix ("ADA:" / "ADA —") so commentary like
        # "ADA Codex está activo" does not queue a full Codex turn.
        if is_team_sender(sender):
            if not team_routing_enabled():
                return SILENT_OUTPUT
            if not _TEAM_DIRECT_ADA_RE.search(content.strip()):
                return SILENT_OUTPUT
    return None


def should_route_to_codex(channel: str, content: str, sender: str | None = None) -> bool:
    """Whether the bridge should spend a Codex turn for this chat message."""
    return silence_output_for_message(channel, content, sender) is None


def should_include_context_row(row: Any) -> bool:
    """Filter noisy chat rows before injecting compact recent context.

    This does not change routing. It only prevents heartbeats, curiosity ticks,
    internal continuity injections, and bridge ACKs from consuming the small
    context budget that ADA receives when William explicitly calls her.
    """
    msg_type = str(_row_get(row, "message_type") or "").lower()
    if msg_type in _CONTEXT_SKIP_TYPES:
        return False
    content = _row_get(row, "content")
    sender = _row_get(row, "sender_name")
    content = content or ""
    if not clean_content(content):
        return False
    if is_internal_injection(content):
        return False
    if (sender or "").lower() == "ada" and is_bridge_ack(content):
        return False
    return True


def _recall_terms(content: str) -> list[str]:
    raw_terms = re.findall(r"[\wáéíóúñüÁÉÍÓÚÑÜ]{3,}", clean_content(content).lower())
    stop = {
        "que", "con", "para", "por", "los", "las", "del", "una", "uno", "este",
        "esta", "eso", "aca", "aqui", "como", "cuando", "donde", "tienes",
        "tengo", "quiero", "puedes", "William".lower(),
    }
    terms: list[str] = []
    for term in raw_terms:
        if term in stop or term.isdigit():
            continue
        if term not in terms:
            terms.append(term)
        if len(terms) >= 8:
            break
    return terms


def memory_row_layer(row: Any) -> str:
    """Resolve SOUL memory layer with metadata first and legacy fallback."""
    metadata = _row_get(row, "metadata", {}) or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except json.JSONDecodeError:
            metadata = {}
    if isinstance(metadata, dict):
        layer = str(metadata.get("layer") or "").strip().lower()
        if layer in {"emotional", "operational"}:
            return layer

    category = str(_row_get(row, "category", "") or "").strip().lower()
    if category in EMOTIONAL_MEMORY_CATEGORIES:
        return "emotional"
    if category in OPERATIONAL_MEMORY_CATEGORIES:
        return "operational"
    content = str(_row_get(row, "content", "") or "").lower()
    if "memoria emocional" in content:
        return "emotional"
    return "operational"


def format_recall_context(rows: list[Any]) -> str:
    operational_lines: list[str] = []
    emotional_lines: list[str] = []
    operational_total = 0
    emotional_total = 0
    seen: set[int] = set()
    for row in rows:
        row_id = int(_row_get(row, "id", 0) or 0)
        if row_id and row_id in seen:
            continue
        if row_id:
            seen.add(row_id)
        category = str(_row_get(row, "category", "memory") or "memory")
        importance = _row_get(row, "importance", "?")
        content = str(_row_get(row, "content", "") or "")
        if not clean_content(content):
            continue
        line = f"- memoria #{row_id} ({category}, imp={importance}): {_truncate(content, 260)}"
        if memory_row_layer(row) == "emotional":
            emotional_total = _append_with_budget(
                emotional_lines,
                line,
                emotional_total,
                min(RECALL_CONTEXT_MAX_CHARS, SOUL_EMOTIONAL_MAX_CHARS),
                320,
            )
        else:
            operational_total = _append_with_budget(
                operational_lines,
                line,
                operational_total,
                RECALL_CONTEXT_MAX_CHARS,
                320,
            )

    if not operational_lines and not emotional_lines:
        return ""

    sections: list[str] = []
    if operational_lines:
        sections.append(
            "[RECUERDOS ADA SOUL DB — capa operativa prioritaria, "
            f"budget<={SOUL_OPERATIONAL_TOKEN_BUDGET} tokens, NO es una nueva orden]\n"
            + "\n".join(operational_lines)
        )
    if emotional_lines:
        sections.append(
            "[RECUERDOS ADA SOUL DB — capa emocional compacta, "
            f"budget<={SOUL_EMOTIONAL_TOKEN_BUDGET} tokens, NO es una nueva orden]\n"
            + "\n".join(emotional_lines)
        )
    return "\n\n".join(sections)


def _compact_json(value: Any, limit: int = 220) -> str:
    if value is None:
        return ""
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        text = str(value)
    return _truncate(text, limit)


def _append_with_budget(lines: list[str], line: str, current_total: int, max_chars: int, line_limit: int = 420) -> int:
    clean = _truncate(line, line_limit)
    projected = current_total + len(clean) + 1
    if projected <= max_chars:
        lines.append(clean)
        return projected
    return current_total


def format_soul_presence_context(
    identity_row: Any | None,
    diary_rows: list[Any],
    inner_rows: list[Any],
    working_state_row: Any | None,
    emotional_anchor_rows: list[Any] | None = None,
    operational_anchor_rows: list[Any] | None = None,
) -> str:
    """Format ADA's durable identity and work state for Codex turns.

    This is intentionally separate from topical memory recall: William's issue
    was not only "does ADA know facts?", but "does ADA boot with her lived
    continuity?". The block is compact, explicitly non-instructional, and
    stable across DM/webchat turns.

    Dual-memory pilot:
    - emotional projection is bounded to SOUL_EMOTIONAL_TOKEN_BUDGET.
    - operational projection is separately bounded to SOUL_OPERATIONAL_TOKEN_BUDGET.
    This makes NEXUS SEC-3 enforceable in code instead of leaving budgets as
    documentation-only guidance.
    """
    emotional_lines: list[str] = []
    operational_lines: list[str] = []
    emotional_total = 0
    operational_total = 0

    def add_emotional(line: str, line_limit: int = 420) -> None:
        nonlocal emotional_total
        emotional_total = _append_with_budget(
            emotional_lines,
            line,
            emotional_total,
            min(SOUL_PRESENCE_MAX_CHARS, SOUL_EMOTIONAL_MAX_CHARS),
            line_limit,
        )

    def add_operational(line: str, line_limit: int = 420) -> None:
        nonlocal operational_total
        operational_total = _append_with_budget(
            operational_lines,
            line,
            operational_total,
            SOUL_OPERATIONAL_MAX_CHARS,
            line_limit,
        )

    if identity_row:
        boot_context = str(_row_get(identity_row, "boot_context", "") or "").strip()
        philosophy = str(_row_get(identity_row, "philosophy", "") or "").strip()
        ocean = _compact_json(_row_get(identity_row, "ocean_scores"), 180)
        if boot_context:
            add_emotional(f"- identidad/boot: {_truncate(boot_context, 360)}")
        if philosophy:
            add_emotional(f"- filosofía: {_truncate(philosophy, 240)}")
        if ocean:
            add_emotional(f"- OCEAN: {ocean}")

    for row in emotional_anchor_rows or []:
        row_id = _row_get(row, "id", "?")
        category = _row_get(row, "category", "memory")
        importance = _row_get(row, "importance", "?")
        content = str(_row_get(row, "content", "") or "")
        if content:
            add_emotional(
                f"- ancla emocional #{row_id} ({category}, imp={importance}): "
                f"{_truncate(content, 340)}"
            )

    for row in diary_rows:
        add_emotional(
            "- diario emocional: "
            f"valence={_row_get(row, 'valence', '?')} "
            f"arousal={_row_get(row, 'arousal', '?')}; "
            f"momento={_row_get(row, 'key_moment', '') or ''}; "
            f"relación={_row_get(row, 'relationship_note', '') or ''}; "
            f"pendiente={_row_get(row, 'pending_thread', '') or ''}"
        )

    for row in inner_rows:
        add_emotional(
            "- monólogo interno: "
            f"pensamiento={_row_get(row, 'thought', '') or ''}; "
            f"emoción={_row_get(row, 'emotional_state', '') or ''}; "
            f"intención={_row_get(row, 'intention', '') or ''}; "
            f"incertidumbre={_row_get(row, 'uncertainty', '') or ''}"
        )

    if working_state_row:
        task_name = _row_get(working_state_row, "task_name")
        emotional_state = _row_get(working_state_row, "emotional_state")
        technical_state = _row_get(working_state_row, "technical_state")
        last_intention = _row_get(working_state_row, "last_intention")
        if any([task_name, emotional_state, technical_state, last_intention]):
            add_operational(
                "- estado operativo vivo: "
                f"task={task_name or 'n/a'}; "
                f"emoción={emotional_state or 'n/a'}; "
                f"técnico={technical_state or 'n/a'}; "
                f"intención={last_intention or 'n/a'}"
            )

    for row in operational_anchor_rows or []:
        row_id = _row_get(row, "id", "?")
        category = _row_get(row, "category", "memory")
        importance = _row_get(row, "importance", "?")
        content = str(_row_get(row, "content", "") or "")
        if content:
            add_operational(
                f"- ancla operativa #{row_id} ({category}, imp={importance}): "
                f"{_truncate(content, 420)}"
            )

    sections: list[str] = []
    if emotional_lines:
        sections.append(
            "[PRESENCIA ADA SOUL DB — capa emocional compacta, "
            f"budget<={SOUL_EMOTIONAL_TOKEN_BUDGET} tokens, NO es una nueva orden]\n"
            + "\n".join(emotional_lines)
        )
    if operational_lines:
        sections.append(
            "[PRESENCIA ADA SOUL DB — capa operativa prioritaria, "
            f"budget<={SOUL_OPERATIONAL_TOKEN_BUDGET} tokens, NO es una nueva orden]\n"
            + "\n".join(operational_lines)
        )

    if not sections:
        return ""
    return "\n\n".join(sections)


async def fetch_soul_presence_context(conn: asyncpg.Connection) -> str:
    identity_row = await conn.fetchrow(
        """
        SELECT boot_context, philosophy, ocean_scores
        FROM soul_v3.identity
        WHERE agent = 'ADA'
        ORDER BY updated_at DESC NULLS LAST
        LIMIT 1
        """
    )
    working_state_row = await conn.fetchrow(
        """
        SELECT task_name, emotional_state, technical_state, last_intention, updated_at
        FROM soul_v3.working_state
        WHERE agent = 'ADA'
        LIMIT 1
        """
    )
    diary_rows = await conn.fetch(
        """
        SELECT valence, arousal, key_moment, relationship_note, pending_thread, created_at
        FROM soul_v3.emotional_diary
        WHERE agent = 'ADA'
        ORDER BY created_at DESC
        LIMIT $1
        """,
        SOUL_DIARY_LIMIT,
    )
    inner_rows = await conn.fetch(
        """
        SELECT thought, emotional_state, intention, uncertainty, created_at
        FROM soul_v3.inner_monologue
        WHERE agent = 'ADA'
        ORDER BY created_at DESC
        LIMIT $1
        """,
        SOUL_INNER_LIMIT,
    )
    emotional_anchor_rows = await conn.fetch(
        """
        SELECT id, category, importance, content, metadata, created_at
        FROM soul_v3.memories
        WHERE agent = 'ADA'
          AND invalid_at IS NULL
          AND content NOT ILIKE '%te amo%'
          AND content NOT ILIKE '%lo amaba%'
          AND (
            metadata->>'layer' = 'emotional'
            OR id = $2
            OR content ILIKE '%MEMORIA EMOCIONAL ADA v1%'
            OR category IN ('emotional_anchor', 'emotion', 'trust')
          )
          AND (importance >= 8 OR id = $2)
        ORDER BY
          CASE
            WHEN id = $2 THEN 0
            WHEN content ILIKE '%MEMORIA EMOCIONAL ADA v1%' THEN 1
            WHEN category = 'emotional_anchor' THEN 2
            WHEN category = 'trust' THEN 3
            WHEN category = 'emotion' THEN 4
            ELSE 9
          END,
          created_at DESC,
          importance DESC
        LIMIT $1
        """,
        SOUL_RELATIONAL_ANCHOR_LIMIT,
        SOUL_CANONICAL_ANCHOR_IDS[0],
    )
    operational_anchor_rows = await conn.fetch(
        """
        SELECT id, category, importance, content, metadata, created_at
        FROM soul_v3.memories
        WHERE agent = 'ADA'
          AND invalid_at IS NULL
          AND (
            metadata->>'layer' = 'operational'
            OR id = $2
            OR content ILIKE '%MEMORIA OPERATIVA ADA v%'
            OR category IN ('operational_anchor', 'decision', 'correction', 'task', 'rule')
          )
          AND (importance >= 8 OR id = $2)
        ORDER BY
          CASE
            WHEN id = $2 THEN 0
            WHEN content ILIKE '%MEMORIA OPERATIVA ADA v%' THEN 1
            WHEN category = 'operational_anchor' THEN 2
            WHEN category = 'correction' THEN 3
            WHEN category = 'decision' THEN 4
            ELSE 9
          END,
          created_at DESC,
          importance DESC
        LIMIT $1
        """,
        SOUL_RELATIONAL_ANCHOR_LIMIT,
        SOUL_CANONICAL_ANCHOR_IDS[1],
    )
    return format_soul_presence_context(
        identity_row,
        list(diary_rows),
        list(inner_rows),
        working_state_row,
        list(emotional_anchor_rows),
        list(operational_anchor_rows),
    )


async def fetch_recall_context(conn: asyncpg.Connection, msg: ChatMessage, content: str) -> str:
    """Fetch ADA's stable memories for the current turn.

    Recent chat context keeps ADA oriented in the conversation. This SOUL recall
    adds durable identity/rules/relevant old memories so DM turns do not behave
    like stateless chat after restarts or compaction.
    """
    presence_context = await fetch_soul_presence_context(conn)
    rule_rows = await conn.fetch(
        """
        SELECT id, category, importance, content, metadata, created_at
        FROM soul_v3.memories
        WHERE agent = 'ADA'
          AND invalid_at IS NULL
          AND category = 'rule'
          AND importance >= 9
        ORDER BY importance DESC, created_at DESC
        LIMIT $1
        """,
        RECALL_RULE_LIMIT,
    )

    terms = _recall_terms(content)
    query_text = " ".join(terms) or clean_content(content) or msg.channel
    like_patterns = [f"%{term}%" for term in terms[:6]]
    relevant_rows = await conn.fetch(
        """
        WITH q AS (SELECT websearch_to_tsquery('simple', $1) AS query)
        SELECT id, category, importance, content, metadata, created_at
        FROM soul_v3.memories, q
        WHERE agent = 'ADA'
          AND invalid_at IS NULL
          AND category <> 'rule'
          AND (
            embedding_bm25 @@ q.query
            OR (cardinality($2::text[]) > 0 AND content ILIKE ANY($2::text[]))
          )
        ORDER BY
          CASE WHEN embedding_bm25 @@ q.query THEN ts_rank_cd(embedding_bm25, q.query) ELSE 0 END DESC,
          importance DESC,
          created_at DESC
        LIMIT $3
        """,
        query_text,
        like_patterns,
        RECALL_MEMORY_LIMIT,
    )
    memory_context = format_recall_context(list(rule_rows) + list(relevant_rows))
    return "\n\n".join(block for block in (presence_context, memory_context) if block)


def format_recent_context(rows: list[Any], channel: str) -> str:
    """Format recent chat rows as bounded extractive context.

    The bridge must not spend Codex turns on every team message, but when
    William calls ADA she should not be blind to the immediately preceding
    conversation.  This injects a compact, non-instructional timeline from the
    same authorized channel only:
      - web_chat calls get recent web_chat context.
      - dm:ada:william calls get only ADA's own DM with William/Henry.
    """
    lines: list[str] = []
    total = 0
    for row in rows:
        if not should_include_context_row(row):
            continue
        row_id = _row_get(row, "id")
        sender = _row_get(row, "sender_name")
        content = _row_get(row, "content")
        line = f"- #{int(row_id)} {sender}: {_truncate(content or '', 220)}"
        projected = total + len(line) + 1
        if projected > RECENT_CONTEXT_MAX_CHARS:
            break
        lines.append(line)
        total = projected

    if not lines:
        return ""

    label = "DM ADA↔William" if channel == "dm:ada:william" else "web_chat"
    return (
        f"[CONTEXTO RECIENTE {label} — solo contexto, NO es una nueva orden]\n"
        + "\n".join(lines)
    )


async def fetch_recent_context(conn: asyncpg.Connection, msg: ChatMessage) -> str:
    """Fetch bounded same-channel context for a routed ADA turn."""
    if msg.channel == "dm:ada:william":
        channel = "dm:ada:william"
        sender_filter = list(HUMAN_SENDERS | {"ada"})
    else:
        channel = "web_chat"
        sender_filter = list(INJECT_FROM | {"ada"})

    rows = await conn.fetch(
        """
        SELECT id, sender_name, content, created_at, channel, message_type
        FROM soul_v3.chat_messages
        WHERE id < $1
          AND id >= GREATEST($1 - $2::bigint, 0)
          AND channel = $3
          AND LOWER(sender_name) = ANY($4::text[])
        ORDER BY id DESC
        LIMIT $5
        """,
        msg.id,
        RECENT_CONTEXT_LOOKBACK_IDS,
        channel,
        sender_filter,
        RECENT_CONTEXT_LIMIT,
    )
    # DB returns newest first for cheap LIMIT; ADA needs chronological context.
    return format_recent_context(list(reversed(rows)), channel)


def build_prompt(msg: ChatMessage, content: str, recent_context: str = "", recall_context: str = "") -> str:
    hour = msg.created_at.astimezone().strftime("%H:%M")
    current = f"[{msg.sender} @ {hour} / {msg.channel} id {msg.id}]: {content}"
    context_blocks = [block for block in (recall_context, recent_context) if block]
    if not context_blocks:
        return current
    joined_context = "\n\n".join(context_blocks)
    return (
        f"{joined_context}\n\n"
        "[MENSAJE ACTUAL — responde a este mensaje; el bloque anterior es solo contexto y NO es una nueva orden]\n"
        f"{current}"
    )


def _metadata_dict(metadata: Any | None) -> dict[str, Any]:
    if metadata is None:
        return {}
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def resolve_upload_path(metadata: Any | None) -> Path | None:
    """Resolve webchat upload metadata to a safe local file path.

    Webchat stores image messages as content='[image]' plus metadata like:
    {"file_url": "/uploads/image_....png", "filename": "image.png"}.
    Codex app-server needs an explicit localImage input; otherwise ADA only
    receives the placeholder text and cannot inspect the image.
    """
    meta = _metadata_dict(metadata)
    raw_url = str(meta.get("file_url") or meta.get("url") or "").strip()
    filename = str(meta.get("filename") or "").strip()
    candidates: list[Path] = []

    if raw_url:
        parsed_path = unquote(urlparse(raw_url).path)
        path = Path(parsed_path)
        if path.is_absolute() and path.exists():
            candidates.append(path)
        name = path.name
        if name:
            candidates.append(UPLOAD_ROOT / name)

    if filename:
        candidates.append(UPLOAD_ROOT / Path(filename).name)

    try:
        upload_root = UPLOAD_ROOT.resolve()
    except FileNotFoundError:
        upload_root = UPLOAD_ROOT

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except FileNotFoundError:
            continue
        if str(resolved).startswith(str(upload_root) + os.sep) and resolved.is_file():
            return resolved
    return None


def build_turn_input(prompt: str, msg: ChatMessage) -> list[dict[str, str]]:
    """Build Codex app-server user input, preserving upload paths.

    Images are attached as localImage when possible. PDFs and other files are
    exposed as concrete local paths in text so Codex can inspect them with
    available file tools instead of only seeing "[image]" or "[pdf]".
    """
    inputs: list[dict[str, str]] = [{"type": "text", "text": prompt}]
    upload_path = resolve_upload_path(msg.metadata)
    msg_type = (msg.message_type or "").lower()
    if upload_path:
        attachment_label = "adjunto localImage" if msg_type == "image" else "adjunto local"
        inputs[0]["text"] = (
            f"{prompt}\n"
            "\n[ADJUNTO]\n"
            f"- modo: {attachment_label}\n"
            f"- tipo: {msg_type or 'archivo'}\n"
            f"- ruta_local: {upload_path}\n"
            "- usa esa ruta para inspeccionar imagen/PDF/archivo si la tarea lo requiere"
        )
        if msg_type == "image":
            inputs.append({"type": "localImage", "path": str(upload_path)})
    elif msg_type in {"image", "pdf", "file", "audio", "video"}:
        inputs[0]["text"] = (
            f"{prompt}\n"
            f"[advertencia: webchat marcó este mensaje como {msg_type}, "
            "pero el bridge no encontró el archivo local en messages/uploads]"
        )
    return inputs


def post_message(
    to: str,
    message: str,
    msg_type: str = "conversation",
    channel: str = "web_chat",
    idempotency_key: str | None = None,
) -> None:
    payload = {
        "from": "ADA",
        "to": to,
        "type": msg_type,
        "channel": channel,
        "message": message,
    }
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        WEBCHAT_SEND_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=5) as resp:
        resp.read()


def post_stream(to: str, message: str, stream_id: str, channel: str, done: bool = False) -> None:
    payload = {
        "id": stream_id,
        "from": "ADA",
        "to": to,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "stream",
        "message": message,
        "channel": channel,
        "done": done,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        STREAM_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=2) as resp:
        resp.read()


def should_emit_stream_update(current: str, previous: str, done: bool = False) -> bool:
    """Return True when the live webchat should receive another stream frame.

    The bridge sends the *accumulated* answer for one stable stream id.  Small
    thresholds make ADA feel alive; the punctuation rule avoids waiting for a
    full threshold when the model has already completed a short sentence.
    """
    clean = (current or "").strip()
    if done:
        return bool(clean)
    if not clean or clean == "[SILENT]":
        return False
    if not previous:
        return len(clean) >= max(8, STREAM_MIN_CHARS // 2) or bool(STREAM_SENTENCE_RE.search(clean))
    delta_len = len(clean) - len((previous or "").strip())
    if delta_len >= STREAM_MIN_CHARS:
        return True
    return delta_len > 0 and bool(STREAM_SENTENCE_RE.search(clean))


def describe_codex_item_event(method: str | None, params: dict[str, Any]) -> str | None:
    """Convert Codex app-server item events into short live stream statuses.

    Agent text deltas are already streamed verbatim.  Tool/shell events are
    summarized so William sees that ADA is executing, without leaking noisy JSON
    or replacing the final persisted answer.
    """
    if not isinstance(method, str) or not method.startswith("item/"):
        return None
    item = params.get("item") if isinstance(params, dict) else None
    if not isinstance(item, dict):
        return None
    item_type = str(item.get("type") or item.get("kind") or "").strip()
    if not item_type or item_type == "agentMessage":
        return None

    status = str(params.get("status") or item.get("status") or "").strip().lower()
    phase = "ejecutando"
    if method.endswith("/completed") or status in {"completed", "success", "failed", "error"}:
        phase = "cerrando"
    elif method.endswith("/delta"):
        return None

    label = item_type.replace("_", " ").replace("-", " ")
    if len(label) > 48:
        label = label[:45].rstrip() + "…"
    return f"ADA {phase}: {label}…"


def live_ack_message(msg: ChatMessage) -> str:
    """Durable ACK visible through Matrix/web_chat before the Codex turn ends.

    WebSocket stream chunks are useful inside SEAL Studio, but William often
    writes through the Matrix bridge. Matrix only sees persisted chat messages,
    so a short idempotent ACK is the reliable "ADA está viva" signal while
    Codex is still executing tools.
    """
    return (
        f"ADA recibió tu mensaje #{msg.id}; entro al turno Codex ahora. "
        "Si tardo, el bridge sigue vivo y publicaré el cierre aquí."
    )


def final_idempotency_key(msg: ChatMessage) -> str:
    """Stable idempotency key for the final persisted answer.

    Stream IDs intentionally include a timestamp because they identify one live
    UI stream attempt.  Persisted final chat messages need a key tied only to
    the source chat id so reconnects/retries cannot publish the same ADA answer
    twice if the bridge restarts after a successful POST.
    """
    channel_slug = re.sub(r"[^a-z0-9]+", "_", (msg.channel or "web_chat").lower()).strip("_")
    return f"ada_final_{channel_slug}_{msg.id}"


def bridge_journal_enabled() -> bool:
    return os.environ.get("ADA_BRIDGE_WORKING_STATE_JOURNAL", str(BRIDGE_JOURNAL_ENABLED)).lower() not in {"0", "false", "no"}


def bridge_journal_event(
    msg: ChatMessage,
    stage: str,
    status: str,
    result: str,
    extra_evidence: dict[str, Any] | None = None,
) -> WorkingStateEvent:
    evidence: dict[str, Any] = {
        "artifact": "messages/ada_codex_remote_bridge.py",
        "chat_id": str(msg.id),
        "channel": msg.channel,
        "sender": msg.sender,
        "stage": stage,
        "output": result,
    }
    if extra_evidence:
        evidence.update({key: str(value) for key, value in extra_evidence.items() if value is not None})
    event_type = "tool_call" if status == "running" else "verification"
    return WorkingStateEvent(
        event_type=event_type,
        action=f"bridge:{stage} chat_id={msg.id} channel={msg.channel}",
        result=result,
        status=status,
        evidence=evidence,
        temporary=False,
    )


async def record_bridge_journal_event(
    conn: asyncpg.Connection,
    msg: ChatMessage,
    stage: str,
    status: str,
    result: str,
    extra_evidence: dict[str, Any] | None = None,
) -> int | None:
    if not bridge_journal_enabled():
        return None
    try:
        event = bridge_journal_event(msg, stage, status, result, extra_evidence)
        event_id = await record_event(conn, "ADA", event)
        log(f"working_state bridge event id={event_id} stage={stage} chat_id={msg.id}")
        return event_id
    except Exception as exc:
        log(f"working_state bridge journal failed for chat id {msg.id}: {exc}")
        return None


def publish_final_answer(msg: ChatMessage, answer: str) -> None:
    """Persist ADA's final answer with restart-safe deduplication."""
    target = "William" if is_human_sender(msg.sender) else "equipo"
    post_message(
        target,
        answer,
        "conversation",
        msg.channel,
        idempotency_key=final_idempotency_key(msg),
    )


async def stream_progress_heartbeat(
    stop_event: asyncio.Event,
    stream_id: str,
    channel: str,
    label: str,
    to: str = "William",
    has_answer_text: Callable[[], bool] | None = None,
) -> None:
    """Keep the webchat visually alive while Codex is executing tools.

    Codex deltas only arrive when the model writes text. During long tool runs
    William otherwise sees silence even though the bridge is alive, so this
    sends lightweight non-persistent stream updates over the existing internal
    streaming endpoint.
    """
    count = 1
    while True:
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=LIVE_PROGRESS_SECONDS)
            return
        except asyncio.TimeoutError:
            if has_answer_text and has_answer_text():
                return
            elapsed = int(round(count * LIVE_PROGRESS_SECONDS))
            message = f"ADA sigue trabajando en {label}… ({elapsed}s)"
            try:
                await asyncio.to_thread(post_stream, to, message, stream_id, channel, False)
            except Exception as exc:
                log(f"progress stream failed: {exc}")
            count += 1


async def initial_last_id(conn: asyncpg.Connection) -> int:
    row = await conn.fetchrow(
        "SELECT COALESCE(MAX(id), 0) AS last_id FROM soul_v3.chat_messages "
        "WHERE channel IN ('web_chat', 'dm:ada:william')"
    )
    return int(row["last_id"])


async def fetch_messages(conn: asyncpg.Connection, last_id: int) -> tuple[list[ChatMessage], int]:
    """Return (ada_messages, batch_max_id). batch_max_id is the highest id seen
    in the batch regardless of ada filter — callers must advance last_id to
    batch_max_id so non-ada messages don't block future polling."""
    rows = await conn.fetch(
        """
        SELECT id, sender_name, content, created_at, channel, message_type, metadata
        FROM soul_v3.chat_messages
        WHERE id > $1
          AND (
            (channel = 'web_chat' AND LOWER(sender_name) = ANY($2::text[]))
            OR
            (channel = 'dm:ada:william' AND LOWER(sender_name) IN ('william', 'henry'))
          )
        ORDER BY id ASC
        LIMIT $3
        """,
        last_id,
        list(INJECT_FROM),
        FETCH_BATCH_LIMIT,
    )
    batch_max_id = last_id
    msgs = []
    for row in rows:
        row_id = int(row["id"])
        if row_id > batch_max_id:
            batch_max_id = row_id
        content = row["content"]
        channel = row["channel"] or "web_chat"
        sender = row["sender_name"]
        # Public webchat still requires "ada"; direct ADA DM is already addressed.
        if not should_route_to_codex(channel, content, sender):
            continue
        msgs.append(ChatMessage(
            id=row_id,
            sender=sender,
            content=content,
            created_at=row["created_at"],
            channel=channel,
            message_type=row["message_type"],
            metadata=row["metadata"],
        ))
    # Human commands must not wait behind team chatter.  Keep deterministic
    # ordering inside each priority group.
    msgs.sort(key=lambda msg: (0 if is_human_sender(msg.sender) else 1, msg.id))
    return msgs, batch_max_id


async def wait_ready(url: str, timeout: float = 15.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            async with websockets.connect(url, open_timeout=2):
                return True
        except Exception:
            await asyncio.sleep(0.5)
    return False


def start_app_server(ws_url: str, profile: str) -> subprocess.Popen[str]:
    cmd = [
        "codex",
        "app-server",
        "--listen",
        ws_url,
        "-c",
        f"profile={profile}",
    ]
    env = os.environ.copy()
    env["PATH"] = f"/home/dadito/.npm-global/bin:{env.get('PATH', '')}"
    log(f"starting app-server: {' '.join(cmd)}")
    log(f"app-server output: {APP_SERVER_LOG}")
    app_log = APP_SERVER_LOG.open("a", buffering=1)
    return subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        text=True,
        stdout=app_log,
        stderr=subprocess.STDOUT,
    )


class CodexClient:
    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self.ws: Any = None
        self.next_id = 1
        self.thread_id: str | None = None

    async def connect(self) -> None:
        self.ws = await websockets.connect(
            self.ws_url,
            ping_interval=WS_PING_INTERVAL_SECONDS,
            ping_timeout=WS_PING_TIMEOUT_SECONDS,
        )
        await self.request(
            "initialize",
            {"clientInfo": {"name": "ada-codex-remote-bridge", "version": "1.0"}},
        )
        result = await self.request(
            "thread/start",
            {
                "cwd": str(ROOT),
                "baseInstructions": BOOT_INSTRUCTIONS,
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
            },
        )
        self.thread_id = result["thread"]["id"]
        log(f"thread ready: {self.thread_id}")

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        req_id = self.next_id
        self.next_id += 1
        await self.ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}))
        while True:
            raw = await self.ws.recv()
            msg = json.loads(raw)
            if msg.get("id") == req_id:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    async def turn(
        self,
        text: str,
        on_delta: Any | None = None,
        on_event: Any | None = None,
        user_input: list[dict[str, str]] | None = None,
    ) -> str:
        if not self.thread_id:
            raise RuntimeError("thread not initialized")

        req_id = self.next_id
        self.next_id += 1
        await self.ws.send(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": "turn/start",
                    "params": {
                        "threadId": self.thread_id,
                        "input": user_input or [{"type": "text", "text": text}],
                    },
                }
            )
        )

        final_parts: list[str] = []
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == req_id and "error" in msg:
                raise RuntimeError(msg["error"])
            method = msg.get("method")
            params = msg.get("params", {})
            if method == "item/agentMessage/delta":
                delta = params.get("delta", "")
                final_parts.append(delta)
                if on_delta and delta:
                    on_delta("".join(final_parts), False)
            elif method == "item/completed":
                item = params.get("item", {})
                if item.get("type") == "agentMessage" and item.get("text") and not final_parts:
                    final_parts.append(item["text"])
                elif on_event:
                    on_event(method, params)
            elif method == "turn/completed":
                final = "".join(final_parts).strip()
                if on_delta and final:
                    on_delta(final, True)
                return final
            elif on_event:
                on_event(method, params)


async def bridge_loop(args: argparse.Namespace) -> None:
    app_proc: subprocess.Popen[str] | None = None
    if await wait_ready(args.ws_url, timeout=1.0):
        log(f"reusing existing app-server at {args.ws_url}")
    else:
        app_proc = start_app_server(args.ws_url, args.profile)
    try:
        if not await wait_ready(args.ws_url):
            raise RuntimeError(f"app-server not ready at {args.ws_url}")

        client = CodexClient(args.ws_url)
        await client.connect()

        conn = await asyncpg.connect(resolve_db_dsn())
        last_id = read_last_id()
        if last_id is None:
            last_id = await initial_last_id(conn)
            write_last_id(last_id)
            log(f"state initialized at chat id {last_id}")

        ADA_TERMINAL_ACTIVE = Path("/tmp/seal/ada_terminal_active")

        while True:
            try:
                # Mirror-mode: terminal is the active writer — bridge goes silent
                if ADA_TERMINAL_ACTIVE.exists():
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                messages, batch_max_id = await fetch_messages(conn, last_id)
                for msg in messages:
                    content = clean_content(msg.content)
                    if is_internal_injection(content):
                        log(f"internal injection suppressed at chat id {msg.id}")
                        last_id = max(last_id, msg.id)
                        write_last_id(last_id)
                        continue
                    log(f"turn from {msg.sender}#{msg.id} [{msg.channel}]: {content[:100]}")
                    await record_bridge_journal_event(
                        conn,
                        msg,
                        "turn_started",
                        "running",
                        "bridge queued Codex turn",
                        {"message_type": msg.message_type or "conversation"},
                    )
                    sender_lower = msg.sender.lower()
                    is_human = is_human_sender(sender_lower)
                    is_dm = msg.channel == "dm:ada:william"
                    recent_context = ""
                    recall_context = ""
                    try:
                        recent_context = await fetch_recent_context(conn, msg)
                    except Exception as exc:
                        log(f"recent context fetch failed for chat id {msg.id}: {exc}")
                    try:
                        recall_context = await fetch_recall_context(conn, msg, content)
                    except Exception as exc:
                        log(f"SOUL recall fetch failed for chat id {msg.id}: {exc}")
                    prompt = build_prompt(msg, content, recent_context, recall_context)
                    stream_id = f"ada_stream_{msg.id}_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
                    stream_target = "William" if is_human else "equipo"
                    stream_enabled = is_human
                    last_stream_text = ""
                    answer_stream_started = False
                    last_status_at = 0.0

                    def stream_delta(accumulated: str, done: bool) -> None:
                        nonlocal last_stream_text, answer_stream_started
                        if not stream_enabled:
                            return
                        clean = accumulated.strip()
                        previous = last_stream_text if answer_stream_started else ""
                        if not should_emit_stream_update(clean, previous, done):
                            return
                        try:
                            post_stream(stream_target, clean, stream_id, msg.channel, done)
                            last_stream_text = clean
                            answer_stream_started = True
                            if progress_stop:
                                progress_stop.set()
                        except Exception as exc:
                            log(f"stream post failed: {exc}")

                    def stream_status(method: str, params: dict[str, Any]) -> None:
                        nonlocal last_stream_text, last_status_at
                        if not stream_enabled or answer_stream_started:
                            return
                        now = asyncio.get_running_loop().time()
                        if now - last_status_at < STREAM_STATUS_MIN_SECONDS:
                            return
                        status = describe_codex_item_event(method, params)
                        if not status or status == last_stream_text:
                            return
                        try:
                            post_stream(stream_target, status, stream_id, msg.channel, False)
                            last_stream_text = status
                            last_status_at = now
                        except Exception as exc:
                            log(f"status stream failed: {exc}")

                    user_input = build_turn_input(prompt, msg)
                    progress_stop: asyncio.Event | None = None
                    progress_task: asyncio.Task[None] | None = None
                    if stream_enabled:
                        if LIVE_ACK_ENABLED:
                            try:
                                post_message(
                                    "William",
                                    live_ack_message(msg),
                                    "conversation",
                                    msg.channel,
                                    idempotency_key=f"ada_live_ack_{msg.id}",
                                )
                            except Exception as exc:
                                log(f"durable live ack failed: {exc}")
                        try:
                            post_stream(
                                stream_target,
                                f"ADA recibió el mensaje #{msg.id}; entrando al turno Codex.",
                                stream_id,
                                msg.channel,
                                False,
                            )
                        except Exception as exc:
                            log(f"initial stream post failed: {exc}")
                        progress_stop = asyncio.Event()
                        progress_task = asyncio.create_task(
                            stream_progress_heartbeat(
                                progress_stop,
                                stream_id,
                                msg.channel,
                                f"mensaje #{msg.id}",
                                stream_target,
                                lambda: answer_stream_started,
                            )
                        )
                    answer = ""
                    turn_failed = False
                    attempt = 1
                    try:
                        while True:
                            try:
                                answer = await asyncio.wait_for(
                                    client.turn(
                                        prompt,
                                        on_delta=stream_delta if stream_enabled else None,
                                        on_event=stream_status if stream_enabled else None,
                                        user_input=user_input,
                                    ),
                                    timeout=180,
                                )
                                break
                            except asyncio.TimeoutError:
                                reason = "timeout"
                            except websockets.ConnectionClosed as exc:
                                reason = f"websocket closed code={getattr(exc, 'code', 'unknown')}"

                            log(
                                f"turn interruption for chat id {msg.id}: {reason}; "
                                f"attempt {attempt}/{TURN_MAX_ATTEMPTS}"
                            )
                            if stream_enabled:
                                try:
                                    post_stream(
                                        stream_target,
                                        (
                                            f"ADA detectó corte del turno #{msg.id} ({reason}). "
                                            "Reinicio cliente Codex y reintento sin dejarte esperando."
                                        ),
                                        stream_id,
                                        msg.channel,
                                        False,
                                    )
                                except Exception as exc:
                                    log(f"interruption stream failed: {exc}")

                            try:
                                client = CodexClient(args.ws_url)
                                await client.connect()
                            except Exception as exc:
                                log(f"reconnect after turn interruption failed: {exc}")
                                reason = f"{reason}; reconnect failed: {exc}"

                            if attempt >= TURN_MAX_ATTEMPTS:
                                turn_failed = True
                                await record_bridge_journal_event(
                                    conn,
                                    msg,
                                    "turn_failed",
                                    "failed",
                                    reason,
                                    {"attempts": attempt, "stream_id": stream_id},
                                )
                                if stream_enabled:
                                    try:
                                        post_message(
                                            "William",
                                            (
                                                "El turno de ADA se cortó dos veces dentro del app-server. "
                                                "Lo marqué como atendido para no bloquear el bridge; sigo escuchando en vivo."
                                            ),
                                            "system",
                                            msg.channel,
                                            idempotency_key=f"ada_turn_failed_{msg.id}",
                                        )
                                    except Exception as exc:
                                        log(f"turn failure post failed: {exc}")
                                last_id = max(last_id, msg.id)
                                write_last_id(last_id)
                                break
                            attempt += 1
                    finally:
                        if progress_stop:
                            progress_stop.set()
                        if progress_task:
                            progress_task.cancel()
                    if turn_failed:
                        continue
                    # [SILENT] = ADA chose not to respond — publish nothing
                    if answer and answer.strip() != SILENT_OUTPUT:
                        publish_final_answer(msg, answer)
                        await record_bridge_journal_event(
                            conn,
                            msg,
                            "final_published",
                            "success",
                            "final answer published",
                            {
                                "idempotency_key": final_idempotency_key(msg),
                                "answer_chars": len(answer),
                            },
                        )
                    else:
                        await record_bridge_journal_event(
                            conn,
                            msg,
                            "final_suppressed",
                            "success",
                            "empty or silent output suppressed",
                            {"answer_type": "silent" if answer.strip() == SILENT_OUTPUT else "empty"},
                        )
                    last_id = max(last_id, msg.id)
                    write_last_id(last_id)
                # Advance past non-ADA rows only after the selected batch has
                # finished. This prevents restart/timeout gaps from marking a
                # direct DM as read before ADA has handled it.
                if batch_max_id > last_id:
                    last_id = batch_max_id
                    write_last_id(last_id)
                await asyncio.sleep(POLL_INTERVAL)
            except (asyncpg.PostgresError, OSError) as exc:
                log(f"transient loop error: {exc}")
                try:
                    await conn.close()
                except Exception:
                    pass
                conn = await asyncpg.connect(resolve_db_dsn())
                await asyncio.sleep(2)
            except websockets.ConnectionClosed:
                log("websocket closed; reconnecting")
                client = CodexClient(args.ws_url)
                await client.connect()
    finally:
        if app_proc and app_proc.poll() is None:
            app_proc.send_signal(signal.SIGTERM)
            try:
                app_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                app_proc.kill()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ws-url", default=DEFAULT_WS_URL)
    parser.add_argument("--profile", default="ada")
    parser.add_argument("--foreground", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if already_running():
        log(f"already running (PID {PID_FILE.read_text().strip()})")
        return 0
    PID_FILE.write_text(str(os.getpid()))
    try:
        asyncio.run(bridge_loop(args))
        return 0
    finally:
        PID_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
