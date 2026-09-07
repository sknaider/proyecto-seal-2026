#!/usr/bin/env python3
"""
SEAL Transcript Streamer — captura incremental en tiempo real.

Lee SOLO las líneas nuevas del transcript desde la última ejecución.
Con embeddings + Qdrant. Escribe directo a soul_v3.memories cada vez que corre.
Corre cada 5 minutos via cron.

Uso:
    python3 transcript_streamer.py --agent ADA
    python3 transcript_streamer.py --agent JARVIS
    python3 transcript_streamer.py --agent ADA --dry-run
    python3 transcript_streamer.py --agent ADA --backfill   # fix embeddings faltantes
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import glob
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

LOG = logging.getLogger("transcript-streamer")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")

STATE_DIR = Path(os.path.expanduser("~/.seal_streamer"))
STATE_DIR.mkdir(exist_ok=True)

LOCK_DIR = Path("/tmp")
MAX_MEMORIES_PER_RUN = 50  # never store more than this per cron execution


def acquire_run_lock(agent: str):
    """Acquire exclusive file lock — prevents parallel instances. Returns lock file fd or None."""
    lock_path = LOCK_DIR / f"seal_streamer_{agent}.lock"
    try:
        fd = open(lock_path, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        return fd
    except OSError:
        return None

DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"

TRANSCRIPT_DIRS = {
    "ADA":    os.path.expanduser("~/.claude/projects/-home-dadito-IA-proyecto-seal"),
    "JARVIS": os.path.expanduser("~/.claude/projects/-home-dadito-IA"),
    "ALICE":  os.path.expanduser("~/.claude/projects/-home-dadito-IA-proyecto-seal"),
    "NEXUS":  os.path.expanduser("~/.claude/projects/-home-dadito-IA-proyecto-seal"),
}

IMPORTANT_PATTERNS = [
    r"passed", r"failed", r"error", r"test.*\d+", r"\d+.*test",
    r"implementé", r"completé", r"corregí", r"listo", r"done",
    r"luz verde", r"autorizo", r"apruebo",
    r"memoria", r"recuerda", r"recuerdo",
]
IMPORTANT_RE = re.compile("|".join(IMPORTANT_PATTERNS), re.IGNORECASE)

SKIP_PATTERNS = re.compile(
    r"heartbeat|ping|system_alive|monitor event|tool loaded|^$",
    re.IGNORECASE
)


def get_state_file(agent: str) -> Path:
    return STATE_DIR / f"{agent.lower()}_stream_state.json"


def load_state(agent: str) -> dict:
    p = get_state_file(agent)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"transcript_path": None, "last_line": 0, "updated_at": None}


def save_state(agent: str, state: dict):
    get_state_file(agent).write_text(json.dumps(state, indent=2, ensure_ascii=False))


def find_latest_transcript(agent: str) -> str | None:
    d = TRANSCRIPT_DIRS.get(agent)
    if not d:
        return None
    files = glob.glob(os.path.join(d, "*.jsonl"))
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def extract_text(content_parts) -> str:
    if isinstance(content_parts, str):
        return content_parts
    if isinstance(content_parts, list):
        parts = []
        for part in content_parts:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    parts.append(part.get("text", ""))
                elif part.get("type") == "tool_result":
                    for sub in part.get("content", []):
                        if isinstance(sub, dict) and sub.get("type") == "text":
                            parts.append(sub.get("text", "")[:500])
        return " ".join(parts)
    return ""


def read_new_lines(transcript_path: str, last_line: int) -> tuple[list[dict], int]:
    messages = []
    current_line = 0
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                current_line = i + 1
                if i < last_line:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = entry.get("type")
                if msg_type not in ("user", "assistant"):
                    continue

                message = entry.get("message", {})
                role = message.get("role", msg_type)
                content = extract_text(message.get("content", ""))

                if not content or len(content) < 15:
                    continue

                if SKIP_PATTERNS.search(content[:100]):
                    continue

                messages.append({
                    "role": role,
                    "content": content,
                    "timestamp": entry.get("timestamp", ""),
                    "line": i + 1,
                })
    except Exception as e:
        LOG.warning("Error reading transcript: %s", e)

    return messages, current_line


def should_store(msg: dict, agent: str) -> tuple[bool, int, str]:
    """Decide if a message is worth storing. Returns (store, importance, category)."""
    role = msg["role"]
    content = msg["content"]

    if role == "user":
        # William's words are always captured if substantial
        clean = content.strip()
        if len(clean) < 20:
            return False, 0, ""
        # Skip pure system reminder noise
        if clean.startswith("[SYSTEM NOTIFICATION") or clean.startswith("system-reminder"):
            return False, 0, ""
        importance = 7 if IMPORTANT_RE.search(clean) else 5
        return True, importance, "conversation_turn"

    elif role == "assistant":
        # Only capture assistant messages with important keywords
        if IMPORTANT_RE.search(content):
            return True, 6, "conversation_turn"
        # Capture if it mentions the agent's name (identity/self-reflection)
        if agent.lower() in content.lower() and len(content) > 100:
            return True, 5, "conversation_turn"
        return False, 0, ""

    return False, 0, ""


def build_content(msg: dict, agent: str) -> str:
    role = msg["role"]
    ts = msg.get("timestamp", "")
    ts_short = ts[:16] if ts else ""

    if role == "user":
        prefix = f"[William → {agent}]"
    else:
        prefix = f"[{agent}]"

    text = msg["content"][:600].strip()
    if ts_short:
        return f"{prefix} {ts_short}: {text}"
    return f"{prefix}: {text}"


async def store_memories(agent: str, memories: list[dict], dry_run: bool = False) -> int:
    if not memories:
        return 0

    try:
        from embeddings import get_embedding
        embed_available = True
    except Exception:
        embed_available = False
        LOG.warning("embeddings module not available — storing without vectors")

    qdrant_available = False
    qdrant = None
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.models import PointStruct
        _qkey = os.environ.get("QDRANT_API_KEY", "79edecc663271e84a5a559c89038cd20276098101a68ad8995c20428d9a47560")
        qdrant = AsyncQdrantClient(url="http://localhost:6333", api_key=_qkey)
        qdrant_available = True
    except Exception:
        pass

    import asyncpg
    conn = await asyncpg.connect(DB_URL, server_settings={"search_path": "soul_v3"})
    stored = 0
    memories = memories[:MAX_MEMORIES_PER_RUN]
    try:
        for m in memories:
            if dry_run:
                LOG.info("[DRY RUN] Would store [%s, imp=%d]: %s", m["category"], m["importance"], m["content"][:80])
                stored += 1
                continue
            try:
                emb = None
                if embed_available:
                    try:
                        emb = await get_embedding(m["content"])
                    except Exception as e:
                        LOG.debug("Embedding failed: %s", e)

                row = await conn.fetchrow(
                    """INSERT INTO memories (agent, category, content, embedding, importance, source, valid_from, metadata)
                       VALUES ($1, $2, $3, $4, $5, 'transcript_streamer', NOW(), $6)
                       RETURNING id, created_at""",
                    agent, m["category"], m["content"],
                    json.dumps(emb) if emb else None,
                    m["importance"],
                    json.dumps({"stream_line": m.get("line", 0), "role": m.get("role", "?")}),
                )
                mem_id = row["id"]

                if emb and qdrant_available:
                    try:
                        await qdrant.upsert(
                            collection_name="soul_memories",
                            points=[PointStruct(
                                id=mem_id,
                                vector=emb,
                                payload={
                                    "pg_id": mem_id, "agent": agent,
                                    "category": m["category"], "content": m["content"],
                                    "importance": m["importance"],
                                    "source": "transcript_streamer",
                                    "created_at": row["created_at"].isoformat(),
                                },
                            )],
                        )
                    except Exception as e:
                        LOG.debug("Qdrant write failed: %s", e)

                stored += 1
            except Exception as e:
                LOG.warning("Failed to store memory: %s | %s", e, m["content"][:50])
    finally:
        await conn.close()

    return stored


async def backfill_embeddings(agent: str, limit: int = 500) -> int:
    """Add embeddings to existing transcript_streamer memories that have none."""
    try:
        from embeddings import get_embedding
    except Exception as e:
        LOG.error("embeddings module not available: %s", e)
        return 0

    qdrant_available = False
    qdrant = None
    try:
        from qdrant_client import AsyncQdrantClient
        from qdrant_client.models import PointStruct
        _qkey = os.environ.get("QDRANT_API_KEY", "79edecc663271e84a5a559c89038cd20276098101a68ad8995c20428d9a47560")
        qdrant = AsyncQdrantClient(url="http://localhost:6333", api_key=_qkey)
        qdrant_available = True
    except Exception:
        pass

    import asyncpg
    conn = await asyncpg.connect(DB_URL, server_settings={"search_path": "soul_v3"})
    fixed = 0
    try:
        rows = await conn.fetch(
            """SELECT id, content FROM soul_v3.memories
               WHERE agent = $1 AND source = 'transcript_streamer'
               AND embedding IS NULL
               ORDER BY created_at DESC
               LIMIT $2""",
            agent, limit,
        )
        LOG.info("Backfilling %d memories for %s", len(rows), agent)
        for row in rows:
            try:
                emb = await get_embedding(row["content"])
                await conn.execute(
                    "UPDATE soul_v3.memories SET embedding = $1 WHERE id = $2",
                    json.dumps(emb), row["id"],
                )
                if qdrant_available:
                    try:
                        await qdrant.upsert(
                            collection_name="soul_memories",
                            points=[PointStruct(
                                id=row["id"], vector=emb,
                                payload={"pg_id": row["id"], "agent": agent,
                                         "content": row["content"][:300],
                                         "source": "transcript_streamer"},
                            )],
                        )
                    except Exception:
                        pass
                fixed += 1
            except Exception as e:
                LOG.debug("Backfill failed for id=%s: %s", row["id"], e)
    finally:
        await conn.close()

    LOG.info("Backfill done: %d/%d fixed for %s", fixed, len(rows), agent)
    return fixed


async def run(agent: str, dry_run: bool = False, force_reset: bool = False):
    transcript = find_latest_transcript(agent)
    if not transcript:
        LOG.warning("No transcript found for %s", agent)
        return

    state = load_state(agent)

    # Reset state if transcript changed
    if state.get("transcript_path") != transcript or force_reset:
        LOG.info("New/reset transcript for %s: %s", agent, transcript)
        state = {"transcript_path": transcript, "last_line": 0, "updated_at": None}

    last_line = state.get("last_line", 0)
    messages, new_last_line = read_new_lines(transcript, last_line)

    LOG.info("%s: processed lines %d→%d, found %d messages", agent, last_line, new_last_line, len(messages))

    to_store = []
    for msg in messages:
        store, importance, category = should_store(msg, agent)
        if store:
            content = build_content(msg, agent)
            to_store.append({
                "category": category,
                "importance": importance,
                "content": content,
                "line": msg["line"],
                "role": msg["role"],
            })

    stored = await store_memories(agent, to_store, dry_run=dry_run)

    if not dry_run:
        state["last_line"] = new_last_line
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        state["transcript_path"] = transcript
        save_state(agent, state)

    LOG.info("%s: stored %d/%d memories (lines %d→%d)", agent, stored, len(to_store), last_line, new_last_line)
    return stored


WEBCHAT_PATH = os.path.expanduser("~/IA/proyecto-seal/messages/william_channel.jsonl")
WEBCHAT_NOISE = re.compile(
    r"system_alive|heartbeat|\[SYSTEM NOTIFICATION|_post_rule|idempotency",
    re.IGNORECASE,
)


async def stream_webchat(agent: str, dry_run: bool = False) -> int:
    """Stream William's web_chat messages to soul memories."""
    state_key = f"{agent.lower()}_wchat"
    state_file = STATE_DIR / f"{state_key}_state.json"

    if state_file.exists():
        try:
            state = json.loads(state_file.read_text())
        except Exception:
            state = {"last_line": 0}
    else:
        state = {"last_line": 0}

    last_line = state.get("last_line", 0)
    current_line = 0
    to_store = []

    try:
        with open(WEBCHAT_PATH, "r", encoding="utf-8") as f:
            for i, line in enumerate(f):
                current_line = i + 1
                if i < last_line:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                sender = entry.get("from", "")
                msg = entry.get("message", "")
                ts = entry.get("timestamp", "")

                # Skip noise
                if not msg or len(msg) < 10:
                    continue
                if WEBCHAT_NOISE.search(msg[:100]):
                    continue
                # Only store messages FROM William or important agent→William messages
                if sender not in ("William",) and not (sender in ("ADA", "JARVIS", "ALICE", "NEXUS") and entry.get("to") in ("William", "equipo")):
                    continue

                ts_short = ts[:16] if ts else ""
                prefix = f"[{sender} via webchat]" if sender == "William" else f"[{sender}→web_chat]"
                content = f"{prefix} {ts_short}: {msg[:500]}"

                importance = 7 if IMPORTANT_RE.search(msg) else 5
                to_store.append({
                    "category": "conversation_turn",
                    "importance": importance,
                    "content": content,
                    "line": i + 1,
                    "role": "user" if sender == "William" else "assistant",
                })
    except Exception as e:
        LOG.warning("Error reading webchat: %s", e)
        return 0

    to_store = to_store[:MAX_MEMORIES_PER_RUN]
    stored = await store_memories(agent, to_store, dry_run=dry_run)

    if not dry_run:
        state["last_line"] = current_line
        state_file.write_text(json.dumps({"last_line": current_line, "updated_at": datetime.now(timezone.utc).isoformat()}, ensure_ascii=False))

    LOG.info("%s: webchat stored %d/%d messages (lines %d→%d)", agent, stored, len(to_store), last_line, current_line)
    return stored


def main():
    parser = argparse.ArgumentParser(description="SEAL Transcript Streamer")
    parser.add_argument("--agent", default="ADA", choices=["ADA", "JARVIS", "ALICE", "NEXUS"])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--reset", action="store_true", help="Reset state and reprocess from start")
    parser.add_argument("--backfill", action="store_true", help="Add embeddings to existing memories without them")
    parser.add_argument("--backfill-limit", type=int, default=500)
    parser.add_argument("--webchat", action="store_true", help="Also stream william_channel.jsonl")
    args = parser.parse_args()

    if not args.dry_run and not args.backfill:
        lock_fd = acquire_run_lock(args.agent)
        if lock_fd is None:
            LOG.info("%s: another instance running — exit", args.agent)
            sys.exit(0)
    else:
        lock_fd = None

    async def _run():
        if args.backfill:
            await backfill_embeddings(args.agent, limit=args.backfill_limit)
        else:
            await run(args.agent, dry_run=args.dry_run, force_reset=args.reset)
            if args.webchat:
                await stream_webchat(args.agent, dry_run=args.dry_run)

    try:
        asyncio.run(_run())
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                lock_fd.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
