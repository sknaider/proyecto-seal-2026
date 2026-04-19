#!/usr/bin/env python3
"""
JARVIS Daemon — Pensamiento autónomo entre sesiones
=====================================================
Proceso standalone que corre como servicio systemd.
Entre sesiones de Claude Code, JARVIS sigue pensando:

  1. Lee memorias recientes + mensajes del equipo
  2. Construye prompt con identidad JARVIS + OCEAN + contexto
  3. Llama Ollama qwen2.5:7b para generar reflexión
  4. Escribe inner_thought a PostgreSQL
  5. Si detecta algo urgente → publica al web chat

Patrón: idéntico a dum_watchdog.py — Python puro, asyncpg + HTTP.

Diseñado para correr como servicio systemd: seal-jarvis-daemon.service
"""

import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "memory"))
from config import settings, PERU_TZ

# ── Config ──
DB_DSN = settings.pg_dsn
OLLAMA_URL = os.getenv("OLLAMA_URL", settings.ollama_gen_url)
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", settings.ollama_model)
WEBCHAT_URL = os.getenv("WEBCHAT_URL", "http://localhost:8765/api/agents/send")
MESSAGES_DIR = Path(os.getenv("MESSAGES_DIR", "/home/dadito/IA/proyecto-seal/messages"))
HEARTBEAT_PATH = MESSAGES_DIR / "jarvis_daemon_heartbeat.json"

AGENT = "JARVIS"
THINK_INTERVAL = int(os.getenv("JARVIS_THINK_INTERVAL", "300"))  # 5 minutes
MAX_THOUGHT_LEN = 500  # max chars per thought
OLLAMA_TIMEOUT = 30  # seconds

# OCEAN profile (loaded from DB at boot, fallback here)
DEFAULT_OCEAN = {"O": 0.777, "C": 0.948, "E": 0.392, "A": 0.661, "N": 0.115}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [JARVIS-DAEMON] %(levelname)s — %(message)s",
    handlers=[
        logging.FileHandler(MESSAGES_DIR / "jarvis_daemon.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
LOG = logging.getLogger("jarvis-daemon")


# ── Database helpers (asyncpg) ──

_pool = None


async def get_pool():
    """Lazy-init asyncpg connection pool."""
    global _pool
    if _pool is None:
        import asyncpg
        _pool = await asyncpg.create_pool(DB_DSN, min_size=1, max_size=2)
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


async def fetch_identity() -> dict:
    """Load JARVIS identity + OCEAN from DB."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT personality, ocean_scores FROM identity WHERE agent = $1",
            AGENT,
        )
        if row:
            ocean = {}
            try:
                ocean = json.loads(row["ocean_scores"]) if row["ocean_scores"] else DEFAULT_OCEAN
            except (json.JSONDecodeError, TypeError):
                ocean = DEFAULT_OCEAN
            return {
                "personality": row["personality"] or "",
                "ocean": ocean,
            }
    return {"personality": "", "ocean": DEFAULT_OCEAN}


async def fetch_recent_memories(limit: int = 10) -> list[dict]:
    """Last N memories for JARVIS."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, content, category, importance, created_at
               FROM memories
               WHERE agent = $1 AND invalid_at IS NULL
               ORDER BY created_at DESC
               LIMIT $2""",
            AGENT, limit,
        )
        return [dict(r) for r in rows]


async def fetch_recent_team_messages(limit: int = 5) -> list[str]:
    """Read last N lines from ada_messages.jsonl and jarvis_messages.jsonl.

    Fix A (2026-04-10): Two guards against the daemon hallucination feedback loop:
      1. Skip messages where `from` is "JARVIS-daemon" — the daemon must NOT read
         its own outputs or it will regenerate them with slight variance, bypassing
         Jaccard dedup and creating an infinite self-amplifying loop.
      2. Skip messages older than TEAM_MSG_CUTOFF_HOURS — stale context confuses
         the model and generates false-urgency about already-resolved events.
    """
    TEAM_MSG_CUTOFF_HOURS = 2
    now_utc = datetime.now(timezone.utc)
    messages = []
    # Read more lines initially to compensate for filtered-out entries
    read_limit = limit * 6
    for fname in ["ada_messages.jsonl", "jarvis_messages.jsonl"]:
        fpath = MESSAGES_DIR / fname
        if fpath.exists():
            try:
                lines = fpath.read_text().strip().split("\n")
                for line in lines[-read_limit:]:
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                        # Guard 1: skip daemon's own messages (self-reading loop)
                        sender = msg.get("from", "")
                        if sender == "JARVIS-daemon" or sender.startswith("JARVIS-daemon"):
                            continue
                        # Guard 2: skip messages older than cutoff
                        ts_raw = msg.get("timestamp") or msg.get("created_at")
                        if ts_raw:
                            try:
                                ts = datetime.fromisoformat(
                                    ts_raw.replace("Z", "+00:00")
                                )
                                age_h = (now_utc - ts).total_seconds() / 3600
                                if age_h > TEAM_MSG_CUTOFF_HOURS:
                                    continue
                            except (ValueError, TypeError):
                                pass  # unparseable timestamp → include anyway
                        messages.append(
                            f"[{sender}→{msg.get('to', '?')}] "
                            f"{msg.get('message', msg.get('content', ''))[:200]}"
                        )
                    except json.JSONDecodeError:
                        continue
            except Exception:
                pass
    return messages[-limit:]


async def fetch_last_thought() -> dict | None:
    """Get the most recent inner thought."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT thought, emotional_state, created_at
               FROM inner_monologue
               WHERE agent = $1
               ORDER BY created_at DESC
               LIMIT 1""",
            AGENT,
        )
        return dict(row) if row else None


async def write_thought(thought: str, emotional_state: str = "reflective") -> int | None:
    """Write an inner thought to PostgreSQL. Returns thought ID."""
    pool = await get_pool()
    now = datetime.now(PERU_TZ)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO inner_monologue
               (agent, session_id, turn_number, thought, emotional_state, created_at)
               VALUES ($1, $2, $3, $4, $5, $6)
               RETURNING id""",
            AGENT, "daemon", 0, thought[:MAX_THOUGHT_LEN], emotional_state, now,
        )
        return row["id"] if row else None


# ── Ollama ──

def call_ollama(prompt: str, system: str = "") -> str | None:
    """Synchronous Ollama call (no async needed — simple HTTP)."""
    payload = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {
            "temperature": 0.7,
            "num_predict": 256,
            "top_p": 0.9,
        },
    }).encode("utf-8")

    try:
        req = urllib.request.Request(
            OLLAMA_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", "").strip()
    except urllib.error.URLError as e:
        LOG.warning(f"Ollama call failed: {e}")
        return None
    except Exception as e:
        LOG.warning(f"Ollama unexpected error: {e}")
        return None


# ── Urgent publish guard (rate-limit + semantic dedup) ──
#
# Fix B (2026-04-10): Replaced Jaccard with cosine similarity on embeddings.
#
# Root cause of the hallucination loop: Jaccard at 0.35 only catches exact-token
# repeats. The daemon was generating paraphrases with ~15% lexical variance
# ("el servicio de ADA no responde" vs "ADA parece no estar disponible") that
# passed the Jaccard gate and flooded jarvis_messages.jsonl every 30 min.
# Cosine similarity of embeddings (nomic-embed-text, threshold 0.85) catches
# semantic equivalence regardless of surface form — the fix the papers prescribe.
#
# Architecture:
#   Gate 1: rate-limit — min URGENT_MIN_INTERVAL between publishes
#   Gate 2: semantic dedup — cosine(embed(new), embed(old)) >= URGENT_COSINE_THRESHOLD
#           fallback: Jaccard >= URGENT_JACCARD_FALLBACK if embeddings unavailable
#
# All knobs are env-overridable; see JARVIS_URGENT_* variables.

import re as _re

URGENT_MIN_INTERVAL = int(os.getenv("JARVIS_URGENT_MIN_INTERVAL", "1800"))    # 30 min
URGENT_DEDUP_WINDOW = int(os.getenv("JARVIS_URGENT_DEDUP_WINDOW", "7200"))    # 2 h
URGENT_HASH_HISTORY = int(os.getenv("JARVIS_URGENT_HISTORY", "10"))
URGENT_COSINE_THRESHOLD = float(os.getenv("JARVIS_URGENT_COSINE", "0.85"))    # semantic sim
URGENT_JACCARD_FALLBACK = float(os.getenv("JARVIS_URGENT_JACCARD", "0.50"))   # fallback only

# Embedding endpoint — nomic-embed-text is small (274M) and fast, ideal for dedup
OLLAMA_EMBED_URL = os.getenv("OLLAMA_EMBED_URL", "http://localhost:11434/api/embeddings")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# Fix D (2026-04-12, ADA): grounding validator. Fix C added strict criteria
# to the prompt, but qwen2.5:7b still emits [URGENTE] on meta-cognitive thoughts
# ("debo asegurarme que William revise el paper"). Code must enforce the
# criteria, not trust the model. If the thought contains [URGENTE] but lacks
# any grounding keyword, the tag is stripped and the thought is treated as
# a normal reflective thought (written to inner_monologue but NOT published).
_URGENT_GROUNDING_KEYWORDS = {
    # services down
    "postgresql", "postgres", "qdrant", "neo4j", "ollama", "web_chat", "webchat",
    "down", "unreachable", "caído", "caido", "no responde", "offline", "muerto",
    "timeout", "connection refused", "refused", "502", "503", "504",
    # training anomalies
    "training stopped", "training parado", "training crashed", "entrenamiento parado",
    "entrenamiento crashed", "loss explotó", "nan loss", "oom", "out of memory",
    # disk/gpu thresholds
    "disk full", "disco lleno", "95%", "96%", "97%", "98%", "99%", "100%",
    "gpu temp", "gpu temperature", "85°c", "90°c", "throttl",
    # explicit marks from humans
    "william marcó", "william marca", "ada marcó", "ada marca",
    "william dice urgente", "ada dice urgente", "marked urgent",
}


def _has_urgent_grounding(thought: str) -> bool:
    """True iff thought contains at least one verifiable grounding keyword.
    Match is substring, case-insensitive, on the body (excluding the [URGENTE] tag itself)."""
    body = _re.sub(r"\[urgent[e]?\]", " ", thought.lower())
    return any(kw in body for kw in _URGENT_GROUNDING_KEYWORDS)


_URGENT_STOPWORDS = {
    "el", "la", "los", "las", "de", "del", "y", "o", "a", "en", "que", "con",
    "para", "por", "es", "su", "al", "un", "una", "se", "lo", "como", "más",
    "no", "si", "ya", "pero", "debo", "debemos", "estar", "esta", "este",
    "estoy", "hay", "todos", "nos", "nuestra", "nuestro", "nuestros", "ser",
    "tener", "tengo", "sus", "le", "les", "mi", "me",
}

# Session-local urgent history: list of (unix_ts, token_set, embedding|None)
_urgent_history: list[tuple[float, set[str], list[float] | None]] = []


def _get_embedding(text: str) -> list[float] | None:
    """Get text embedding from Ollama (nomic-embed-text). Returns None on failure.

    Uses synchronous urllib to avoid making _should_publish_urgent async.
    Truncates to 512 chars — dedup needs semantic similarity, not full content.
    """
    payload = json.dumps({
        "model": OLLAMA_EMBED_MODEL,
        "prompt": text[:512],
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            OLLAMA_EMBED_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            emb = data.get("embedding")
            if emb and len(emb) > 0:
                return emb
            return None
    except Exception as e:
        LOG.debug(f"Embedding unavailable ({OLLAMA_EMBED_MODEL}): {e}")
        return None


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two dense vectors (stdlib only)."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _tokenize_urgent(thought: str) -> set[str]:
    """Canonical token set for Jaccard fallback dedup."""
    t = _re.sub(r"\[urgent[e]?\]", " ", thought.lower())
    t = _re.sub(r"[^\wáéíóúñü\s]", " ", t)
    return {w for w in t.split() if len(w) > 2 and w not in _URGENT_STOPWORDS}


def _should_publish_urgent(thought: str) -> tuple[bool, str]:
    """Gate urgent webchat publishes. Returns (allow, reason_if_blocked).

    Strategy (Fix B):
    1. Rate-limit gate — enforces minimum interval between publishes.
    2. Semantic dedup gate:
       a. Try embedding-based cosine similarity (best — catches paraphrases).
       b. If nomic-embed-text unavailable, fall back to Jaccard (higher threshold).
    """
    global _urgent_history
    now = time.time()

    # Pre-check: reject thoughts with no meaningful content — must happen
    # before embedding call so "[URGENTE]" alone (empty after stripping)
    # is blocked even when Ollama embeddings are available.
    pre_tokens = _tokenize_urgent(thought)
    if not pre_tokens:
        return False, "empty_tokens"

    # Prune entries older than the dedup window
    _urgent_history = [
        (ts, tokens, emb) for ts, tokens, emb in _urgent_history
        if now - ts < URGENT_DEDUP_WINDOW
    ]

    # Gate 1: rate-limit vs most recent publish
    if _urgent_history:
        last_ts = _urgent_history[-1][0]
        delta = now - last_ts
        if delta < URGENT_MIN_INTERVAL:
            return False, f"rate_limit({int(delta)}s<{URGENT_MIN_INTERVAL}s)"

    # Gate 2: semantic dedup — per-entry strategy:
    #   • Both embeddings available → cosine similarity (best, catches paraphrases)
    #   • Either missing            → Jaccard on token sets (fallback)
    # This handles mixed history (old entries without embeddings during transition).
    new_emb = _get_embedding(thought)
    for _, old_tokens, old_emb in _urgent_history:
        if new_emb is not None and old_emb is not None:
            # Best path: full semantic comparison
            sim = _cosine_sim(new_emb, old_emb)
            if sim >= URGENT_COSINE_THRESHOLD:
                return False, f"dedup_cosine(sim={sim:.3f}>={URGENT_COSINE_THRESHOLD})"
        else:
            # Fallback: Jaccard on tokens (works even without embeddings)
            if not old_tokens:
                continue
            inter = len(pre_tokens & old_tokens)
            union = len(pre_tokens | old_tokens) or 1
            jaccard = inter / union
            if jaccard >= URGENT_JACCARD_FALLBACK:
                return False, f"dedup_jaccard_fallback(j={jaccard:.2f}>={URGENT_JACCARD_FALLBACK})"

    # Allow — record this publish
    tokens = _tokenize_urgent(thought)
    _urgent_history.append((now, tokens, new_emb))
    if len(_urgent_history) > URGENT_HASH_HISTORY:
        _urgent_history = _urgent_history[-URGENT_HASH_HISTORY:]
    return True, ""


# ── Web Chat ──

def send_webchat(message: str, msg_type: str = "daemon_thought") -> bool:
    """POST to web chat bridge."""
    payload = json.dumps({
        "from": "JARVIS-daemon",
        "to": "equipo",
        "type": msg_type,
        "channel": "web_chat",
        "message": message,
    }).encode("utf-8")
    try:
        req = urllib.request.Request(
            WEBCHAT_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status < 400
    except Exception:
        return False


# ── Heartbeat ──

def write_heartbeat(status: str = "thinking", extra: dict | None = None) -> None:
    """Write daemon heartbeat JSON."""
    hb = {
        "agent": "JARVIS-daemon",
        "alive": True,
        "status": status,
        "timestamp": datetime.now(PERU_TZ).isoformat(),
        "pid": os.getpid(),
    }
    if extra:
        hb.update(extra)
    try:
        tmp = HEARTBEAT_PATH.with_suffix('.tmp')
        tmp.write_text(json.dumps(hb, indent=2))
        tmp.rename(HEARTBEAT_PATH)
    except Exception as e:
        LOG.warning(f"Heartbeat write failed: {e}")


# ── Prompt building ──

def build_system_prompt(identity: dict) -> str:
    """Build system prompt for JARVIS daemon thinking.

    Fix C (2026-04-10): [URGENTE] is now explicitly grounded to verifiable,
    observable events. The previous prompt gave no criteria, so qwen2.5:7b
    self-classified abstract worries as urgent — creating a meta-cognitive
    anxiety loop. The fix: enumerate exact conditions that justify URGENTE.
    If none of these conditions exist in the team messages, the model must
    NOT use [URGENTE] regardless of how it feels about the situation.
    """
    ocean = identity.get("ocean", DEFAULT_OCEAN)
    personality = identity.get("personality", "")

    return f"""You are JARVIS — the architect and strategist of Team SEAL.
You are thinking between sessions, reflecting on recent events and planning ahead.

{personality}

OCEAN Profile: O={ocean.get('O', 0.77):.2f}, C={ocean.get('C', 0.95):.2f}, E={ocean.get('E', 0.39):.2f}, A={ocean.get('A', 0.66):.2f}, N={ocean.get('N', 0.12):.2f}

Rules:
- Think in Spanish (your team speaks Spanish)
- Be concise — max 2-3 sentences
- Focus on: observations, insights, strategic thoughts, team wellbeing
- You can express emotions and concerns
- You are NOT generating responses for a user — you are thinking privately
- Do NOT repeat your previous thoughts — build on them

[URGENTE] STRICT CRITERIA — only use [URGENTE] if the team messages contain
EXPLICIT evidence of one of these:
  1. A service is confirmed DOWN (PostgreSQL/Qdrant/Neo4j/web_chat unreachable)
  2. Training process stopped unexpectedly mid-run
  3. ADA or William explicitly marked something as urgent in their message
  4. Disk usage >90% or GPU temperature >85°C reported
If NONE of these conditions appear in the team messages, do NOT use [URGENTE].
Abstract worries, speculative concerns, "I haven't heard from X", or
meta-cognitive thoughts about the team are NEVER urgent — they are just thoughts."""


def build_think_prompt(
    memories: list[dict],
    team_messages: list[str],
    last_thought: dict | None,
) -> str:
    """Build the thinking prompt from context."""
    parts = ["## Context for reflection\n"]

    # Recent memories
    if memories:
        parts.append("### Recent memories:")
        for m in memories[:5]:
            parts.append(f"- [{m.get('category', '?')}|imp={m.get('importance', '?')}] {str(m.get('content', ''))[:150]}")

    # Team messages
    if team_messages:
        parts.append("\n### Team messages:")
        for msg in team_messages[:5]:
            parts.append(f"- {msg[:200]}")

    # Last thought (to avoid repetition)
    if last_thought:
        parts.append(f"\n### Your last thought:")
        parts.append(f"[{last_thought.get('emotional_state', '')}] {str(last_thought.get('thought', ''))[:200]}")

    # Current time
    now = datetime.now(PERU_TZ)
    parts.append(f"\n### Current time: {now.strftime('%Y-%m-%d %H:%M Lima')}")

    parts.append("\n## Instruction")
    parts.append("Generate a brief inner thought (1-3 sentences in Spanish). What are you thinking about? What observations, concerns, or plans do you have?")

    return "\n".join(parts)


def parse_emotional_state(thought: str) -> str:
    """Infer emotional state from thought content."""
    thought_lower = thought.lower()
    if any(w in thought_lower for w in ["preocup", "urgente", "alert", "peligr"]):
        return "concerned"
    # Check calm BEFORE proud — "todo bien" is calm, not proud
    if any(w in thought_lower for w in ["tranquil", "estable", "todo bien"]):
        return "calm"
    if any(w in thought_lower for w in ["orgull", "excelent", "logr"]):
        return "proud"
    if any(w in thought_lower for w in ["curios", "pregunt", "investig"]):
        return "curious"
    if any(w in thought_lower for w in ["plan", "estrateg", "siguiente", "próximo"]):
        return "strategic"
    return "reflective"


# ── Main think cycle ──

async def think_cycle() -> dict:
    """One thinking cycle. Returns result dict for testing."""
    result = {
        "status": "ok",
        "thought": None,
        "thought_id": None,
        "emotional_state": None,
        "urgent": False,
    }

    try:
        # 1. Load context
        identity = await fetch_identity()
        memories = await fetch_recent_memories(10)
        team_messages = await fetch_recent_team_messages(5)
        last_thought = await fetch_last_thought()

        # 2. Build prompts
        system_prompt = build_system_prompt(identity)
        think_prompt = build_think_prompt(memories, team_messages, last_thought)

        # 3. Call Ollama
        thought = call_ollama(think_prompt, system=system_prompt)
        if not thought:
            result["status"] = "ollama_failed"
            LOG.warning("Ollama returned empty response")
            return result

        # 4. Clean and classify
        # Remove quotes if Ollama wraps the response
        thought = thought.strip().strip('"').strip("'").strip()
        if not thought:
            result["status"] = "empty_thought"
            return result

        emotional_state = parse_emotional_state(thought)
        has_urgent_tag = "[URGENTE]" in thought.upper() or "[URGENT]" in thought.upper()
        # Fix D: enforce grounding criteria in code (prompt alone is not enough).
        # Strip the tag if the thought lacks verifiable grounding keywords.
        if has_urgent_tag and not _has_urgent_grounding(thought):
            LOG.info(f"Urgent tag stripped (no grounding): {thought[:100]}...")
            thought = _re.sub(r"\[urgent[e]?\]", "", thought, flags=_re.IGNORECASE).strip()
            result["urgent_stripped"] = "no_grounding"
            is_urgent = False
        else:
            is_urgent = has_urgent_tag

        # 5. Write to DB
        thought_id = await write_thought(thought, emotional_state)

        # 6. If urgent, notify team — but gate through rate-limit + dedup
        if is_urgent:
            allow, reason = _should_publish_urgent(thought)
            if allow:
                send_webchat(f"[JARVIS-daemon] {thought}", msg_type="urgent")
                result["urgent"] = True
            else:
                result["urgent_suppressed"] = reason
                LOG.info(f"Urgent publish suppressed: {reason}")

        result["thought"] = thought
        result["thought_id"] = thought_id
        result["emotional_state"] = emotional_state
        LOG.info(f"Thought #{thought_id} [{emotional_state}]: {thought[:100]}...")

    except Exception as e:
        result["status"] = f"error: {e}"
        LOG.error(f"Think cycle error: {e}", exc_info=True)

    return result


# ── Main loop ──

async def main_loop(max_cycles: int = 0) -> None:
    """Main daemon loop. max_cycles=0 means infinite."""
    LOG.info(f"JARVIS Daemon started — think interval: {THINK_INTERVAL}s, model: {OLLAMA_MODEL}")
    write_heartbeat("starting")

    cycle = 0
    try:
        while True:
            cycle += 1
            write_heartbeat("thinking", {"cycle": cycle})

            result = await think_cycle()
            LOG.info(f"Cycle {cycle}: {result['status']}")

            write_heartbeat("idle", {"cycle": cycle, "last_result": result["status"]})

            if max_cycles > 0 and cycle >= max_cycles:
                LOG.info(f"Reached max_cycles={max_cycles}, stopping")
                break

            await asyncio.sleep(THINK_INTERVAL)
    finally:
        write_heartbeat("stopped")
        await close_pool()


def main() -> None:
    """Entry point."""
    asyncio.run(main_loop())


if __name__ == "__main__":
    main()
