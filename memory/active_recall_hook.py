#!/usr/bin/env python3
"""SEAL Active Recall Hook — Inyecta memorias relevantes antes de cada respuesta.

Se ejecuta como hook de Claude Code en UserPromptSubmit.
Recibe el mensaje del usuario por stdin, busca memorias/instincts/corrections
relevantes, y retorna contexto adicional via hookSpecificOutput.

Tiempo target: <500ms. Si falla, retorna vacío (no bloquear al agente).
"""
from seal_secrets import pg_dsn

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from emotional_retrieval import emotional_signal_strength

DB_URL = pg_dsn(required=True)
PROMPT_CHARS_PER_TOKEN = 4
ACTIVE_RECALL_EMOTIONAL_TOKEN_BUDGET = 600
ACTIVE_RECALL_OPERATIONAL_TOKEN_BUDGET = 2200
ACTIVE_RECALL_EMOTIONAL_MAX_CHARS = ACTIVE_RECALL_EMOTIONAL_TOKEN_BUDGET * PROMPT_CHARS_PER_TOKEN
ACTIVE_RECALL_OPERATIONAL_MAX_CHARS = ACTIVE_RECALL_OPERATIONAL_TOKEN_BUDGET * PROMPT_CHARS_PER_TOKEN

EMOTIONAL_CATEGORIES = {"emotional_anchor", "emotion", "trust", "diary", "relationship"}
OPERATIONAL_CATEGORIES = {
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

# ── Frente 5: ruteo dual-memory por circunstancia (spec_soul_context_efficiency_v1) ──
# Feature flag: OFF por defecto. Con SOUL_CIRCUMSTANCE_ROUTING=1 los budgets de cada
# capa se rebalancean según el turno sea operativo, emocional o balanceado.
CIRCUMSTANCE_ROUTING_ENABLED = os.environ.get("SOUL_CIRCUMSTANCE_ROUTING", "0") == "1"

_CIRCUMSTANCE_EMOTIONAL_KW = (
    "gracias", "te quiero", "te amo", "orgulloso", "orgullosa", "hijo", "hija",
    "familia", "hermano", "hermana", "papa", "papá", "cariño", "vínculo", "vinculo",
    "confío", "confio", "siento", "amor", "abrazo", "proud", "love", "family",
)
_CIRCUMSTANCE_OPERATIONAL_KW = (
    "bug", "error", "fix", "código", "codigo", "code", "deploy", "tabla", "table",
    "query", "schema", "funcion", "función", "function", "spec", "build", "test",
    "audit", "pipeline", "endpoint", "commit", "refactor", "exception", "daemon",
)

# Pisos de seguridad: una capa NUNCA baja de esto (chars). Garantiza que nunca
# desaparezca del todo la identidad ni la evidencia.
_FLOOR_EMOTIONAL_CHARS = 150
_FLOOR_OPERATIONAL_CHARS = 400


def _classify_circumstance(message: str) -> str:
    """Detecta si el turno es 'emotional', 'operational' o 'balanced'.
    Default seguro = 'balanced' (comportamiento actual, 600/2200)."""
    if not message:
        return "balanced"
    q = message.lower()
    emo = sum(1 for w in _CIRCUMSTANCE_EMOTIONAL_KW if w in q)
    ops = sum(1 for w in _CIRCUMSTANCE_OPERATIONAL_KW if w in q)
    if emo > 0 and ops == 0:
        return "emotional"
    if ops > 0 and emo == 0:
        return "operational"
    return "balanced"


def _resolve_layer_budgets(circumstance: str) -> tuple[int, int]:
    """Devuelve (emotional_max_chars, operational_max_chars) según circunstancia.
    Con el flag OFF siempre devuelve los budgets base (balanced)."""
    if not CIRCUMSTANCE_ROUTING_ENABLED or circumstance == "balanced":
        return (ACTIVE_RECALL_EMOTIONAL_MAX_CHARS, ACTIVE_RECALL_OPERATIONAL_MAX_CHARS)
    if circumstance == "emotional":
        # vínculo/identidad: emocional sube, operativo baja (con piso)
        return (3000, max(_FLOOR_OPERATIONAL_CHARS, 7200))
    if circumstance == "operational":
        # trabajo técnico: operativo sube, emocional baja (con piso)
        return (max(_FLOOR_EMOTIONAL_CHARS, 600), 10600)
    return (ACTIVE_RECALL_EMOTIONAL_MAX_CHARS, ACTIVE_RECALL_OPERATIONAL_MAX_CHARS)

# Detect agent from environment or process
def detect_agent():
    """Detect which agent is calling this hook."""
    # Check SEAL_AGENT env var first (set by each .sh launcher)
    agent_env = os.environ.get("SEAL_AGENT", "")
    if agent_env in ("JARVIS", "ADA", "ALICE", "DUM", "NEXUS"):
        return agent_env

    # Check process command line for agent name
    try:
        ppid = os.getppid()
        cmdline = open(f"/proc/{ppid}/cmdline", "rb").read().decode("utf-8", errors="replace")
        for name in ("NEXUS", "ALICE", "DUM", "JARVIS", "ADA"):
            if name in cmdline:
                return name
    except Exception:
        pass

    # Fallback: check CWD — only if clearly agent-specific
    cwd = os.getcwd()
    if "memory" in cwd:
        return "JARVIS"
    elif "alice" in cwd:
        return "ALICE"
    # Cannot determine agent — do not inject
    return None


async def get_embedding(text: str) -> list:
    """Get embedding from Ollama nomic-embed-text."""
    import urllib.request
    payload = json.dumps({"model": EMBED_MODEL, "input": text}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as resp:
        data = json.loads(resp.read())
        return data["embeddings"][0]


_STOP_WORDS = {
    "que", "de", "la", "el", "en", "es", "se", "los", "las", "del", "al", "un", "una",
    "por", "con", "para", "una", "sus", "les", "me", "te", "se", "le", "lo", "nos",
    "como", "si", "pero", "ya", "hay", "son", "fue", "era", "han", "ha", "he",
    "esto", "esta", "estos", "estas", "ese", "esa", "esos", "esas",
    "yo", "tu", "el", "ella", "ellos", "ellas", "usted", "ustedes",
    "hicieron", "hice", "hizo", "hacemos", "tienen", "tengo",
    "recurdan", "recuerdan", "saben", "sabes",
}


_ACCENT_MAP = str.maketrans("áéíóúüñ", "aeiouun")


def _accent_variants(word: str) -> list[str]:
    """Return accented + unaccented forms of a word."""
    unaccented = word.translate(_ACCENT_MAP)
    variants = [word]
    if unaccented != word:
        variants.append(unaccented)
    else:
        # Try adding accents on last vowel positions for common patterns
        # e.g., "ingles" → "inglés", "aplicacion" → "aplicación"
        accented = word.replace("es", "és").replace("on", "ón").replace("ion", "ión")
        if accented != word:
            variants.append(accented)
    return variants


def _extract_keywords(message: str) -> list[str]:
    """Extract meaningful keywords from message for ILIKE search, with accent variants."""
    import re
    words = re.findall(r"[a-záéíóúüñA-ZÁÉÍÓÚÜÑ]{4,}", message.lower())
    keywords = [w for w in words if w not in _STOP_WORDS][:6]
    # Expand with accent variants
    expanded = []
    for kw in keywords:
        expanded.extend(_accent_variants(kw))
    return list(dict.fromkeys(expanded))  # deduplicate preserving order


def _row_get(row, key: str, default=None):
    try:
        return row[key]
    except Exception:
        return getattr(row, key, default)


async def mark_memory_recalled(conn, rows) -> None:
    """Mark recalled memory IDs so dual-memory usage is measurable."""
    ids: list[int] = []
    for row in rows or []:
        memory_id = _row_get(row, "id")
        if memory_id is None:
            continue
        try:
            ids.append(int(memory_id))
        except (TypeError, ValueError):
            continue
    if not ids:
        return
    try:
        await conn.execute(
            """
            UPDATE memories
            SET last_recalled_at = NOW(),
                recall_count = COALESCE(recall_count, 0) + 1,
                query_count = COALESCE(query_count, 0) + 1,
                last_activation = NOW()
            WHERE id = ANY($1::bigint[])
            """,
            sorted(set(ids)),
        )
    except Exception:
        pass


def _metadata_layer(row) -> str:
    metadata = _row_get(row, "metadata", {}) or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    if isinstance(metadata, dict):
        layer = str(metadata.get("layer") or "").strip().lower()
        if layer in {"emotional", "operational"}:
            return layer

    category = str(_row_get(row, "category", "") or "").strip().lower()
    if category in EMOTIONAL_CATEGORIES:
        return "emotional"
    if category in OPERATIONAL_CATEGORIES:
        return "operational"
    content = str(_row_get(row, "content", "") or "").lower()
    if "memoria emocional" in content:
        return "emotional"
    if "memoria operativa" in content:
        return "operational"
    return "operational"


def _append_budgeted(lines: list[str], line: str, current_total: int, max_chars: int) -> int:
    clean = line[:240]
    projected = current_total + len(clean) + 1
    if projected <= max_chars:
        lines.append(clean)
        return projected
    return current_total


def format_layered_memory_rows(rows, *, title_prefix: str = "MEMORIAS RELEVANTES") -> str:
    """Format recall rows into operational/emotional projections with hard budgets."""
    operational: list[str] = []
    emotional: list[str] = []
    operational_total = 0
    emotional_total = 0

    for r in rows:
        layer = _metadata_layer(r)
        agent = _row_get(r, "agent", "?")
        category = _row_get(r, "category", "?")
        content = str(_row_get(r, "content", "") or "")
        line = f"  - [{agent}·{category}·{layer}] {content[:180]}"
        if layer == "emotional":
            emotional_total = _append_budgeted(
                emotional, line, emotional_total, ACTIVE_RECALL_EMOTIONAL_MAX_CHARS
            )
        else:
            operational_total = _append_budgeted(
                operational, line, operational_total, ACTIVE_RECALL_OPERATIONAL_MAX_CHARS
            )

    sections: list[str] = []
    if operational:
        sections.append(
            f"🔧 {title_prefix} — CAPA OPERATIVA (budget<={ACTIVE_RECALL_OPERATIONAL_TOKEN_BUDGET} tokens):\n"
            + "\n".join(operational)
        )
    if emotional:
        sections.append(
            f"💠 {title_prefix} — CAPA EMOCIONAL COMPACTA (budget<={ACTIVE_RECALL_EMOTIONAL_TOKEN_BUDGET} tokens):\n"
            + "\n".join(emotional)
        )
    return "\n".join(sections)


async def semantic_recall(conn, message: str, agent: str) -> str:
    """Per-message keyword search — surfaces relevant memories for the current topic.
    Uses ILIKE keyword matching (fast, no model, works with all memories regardless of embedding state).
    Dual-memory pilot: split operational/emotional projection and do not pull
    another agent's private records unless they are team/public scoped.
    """
    if not message or len(message.strip()) < 10:
        return ""
    try:
        keywords = _extract_keywords(message)
        if not keywords:
            return ""

        like_patterns = [f"%{kw}%" for kw in keywords[:8]]
        results = await conn.fetch("""
            SELECT id, content, category, agent, importance, scope, metadata
            FROM memories
            WHERE content ILIKE ANY($1::text[])
              AND invalid_at IS NULL
              AND importance >= 7
              AND (agent = $2 OR COALESCE(scope, 'team') IN ('team', 'public'))
            ORDER BY
              CASE WHEN agent = $2 THEN 0 ELSE 1 END,
              CASE
                WHEN metadata->>'layer' = 'operational' THEN 0
                WHEN category IN ('operational_anchor','correction','decision','task','project','milestone','technical_fact') THEN 1
                WHEN metadata->>'layer' = 'emotional' THEN 2
                ELSE 3
              END,
              importance DESC,
              created_at DESC
            LIMIT 12
        """, like_patterns, agent)
        if emotional_signal_strength(message) > 0:
            emotional_results = await conn.fetch("""
                SELECT id, content, category, agent, importance, scope, metadata
                FROM memories
                WHERE content ILIKE ANY($1::text[])
                  AND invalid_at IS NULL
                  AND importance >= 7
                  AND (agent = $2 OR COALESCE(scope, 'team') IN ('team', 'public'))
                  AND (
                    metadata->>'layer' = 'emotional'
                    OR category IN ('emotional_anchor','emotion','trust','relationship','diary','identity')
                    OR category ILIKE '%emotion%'
                    OR memory_type IN ('emotional','identity_emotional')
                  )
                ORDER BY
                  CASE WHEN agent = $2 THEN 0 ELSE 1 END,
                  importance DESC,
                  created_at DESC
                LIMIT 4
            """, like_patterns, agent)
            seen = {int(_row_get(row, "id")) for row in results if _row_get(row, "id") is not None}
            for row in emotional_results:
                memory_id = _row_get(row, "id")
                if memory_id is not None and int(memory_id) not in seen:
                    results.append(row)
                    seen.add(int(memory_id))

        if not results:
            return ""
        await mark_memory_recalled(conn, results)
        return format_layered_memory_rows(results, title_prefix="MEMORIAS RELEVANTES AL MENSAJE ACTUAL")
    except Exception:
        return ""


async def active_recall(user_message: str, boot_mode: bool = True) -> str:
    """Core recall logic — fast version for hooks.
    boot_mode=True: full recall (corrections+instincts+rules+projects+semantic)
    boot_mode=False: semantic search only (per-turn, lightweight)
    """
    import asyncpg
    t0 = time.monotonic()
    agent = detect_agent()

    try:
        conn = await asyncio.wait_for(
            asyncpg.connect(DB_URL),
            timeout=2.0
        )
    except Exception:
        return ""

    sections = []

    try:
        if boot_mode:
            # 1. Recent corrections (HIGHEST PRIORITY — what William corrected)
            corrections = await conn.fetch("""
                SELECT id, content, importance FROM memories
                WHERE agent = $1 AND category = 'correction' AND invalid_at IS NULL
                ORDER BY importance DESC, created_at DESC
                LIMIT 5
            """, agent)

            if corrections:
                await mark_memory_recalled(conn, corrections)
                corr_lines = ["🔴 CORRECCIONES RECIENTES (aplica SIEMPRE):"]
                for c in corrections:
                    corr_lines.append(f"  - {c['content'][:200]}")
                sections.append("\n".join(corr_lines))

            # 2. Active instincts
            try:
                instincts = await conn.fetch("""
                    SELECT trigger_condition, action, strength
                    FROM instincts
                    WHERE agent = $1 AND invalid_at IS NULL AND strength >= 0.7
                    ORDER BY strength DESC
                    LIMIT 3
                """, agent)

                if instincts:
                    inst_lines = ["⚡ INSTINTOS ACTIVOS (strength>=0.7):"]
                    for i in instincts:
                        inst_lines.append(
                            f"  - [{float(i['strength']):.2f}] CUANDO: {i['trigger_condition'][:80]} "
                            f"→ HAZ: {i['action'][:100]}"
                        )
                    sections.append("\n".join(inst_lines))
            except Exception:
                pass

            # 3. Key rules
            rules = await conn.fetch("""
                SELECT rule_key, content FROM rules
                WHERE active = true AND priority >= 8
                ORDER BY
                    CASE WHEN priority = 10 THEN 0 ELSE 1 END,
                    created_at DESC
                LIMIT 5
            """)

            if rules:
                rule_lines = ["📋 REGLAS ACTIVAS:"]
                for r in rules:
                    rule_lines.append(f"  - {r['rule_key']}: {r['content'][:100]}")
                sections.append("\n".join(rule_lines))

            # 4. Emotional anchors — compact identity/relationship layer.
            try:
                emotional_anchors = await conn.fetch("""
                    SELECT id, content, category, agent, importance, scope, metadata
                    FROM memories
                    WHERE invalid_at IS NULL
                      AND agent = $1
                      AND importance >= 7
                      AND (
                        metadata->>'layer' = 'emotional'
                        OR category IN ('emotional_anchor','emotion','trust','relationship','diary','identity')
                        OR category ILIKE '%emotion%'
                        OR memory_type IN ('emotional','identity_emotional')
                      )
                    ORDER BY
                      CASE WHEN metadata->>'layer' = 'emotional' THEN 0 ELSE 1 END,
                      importance DESC,
                      created_at DESC
                    LIMIT 3
                """, agent)
                if emotional_anchors:
                    await mark_memory_recalled(conn, emotional_anchors)
                    formatted_emotional = format_layered_memory_rows(
                        emotional_anchors,
                        title_prefix="ANCLAS EMOCIONALES/IDENTIDAD",
                    )
                    if formatted_emotional:
                        sections.append(formatted_emotional)
            except Exception:
                pass

            # 5. Recent important memories — projects, decisions, tasks (last 14 days)
            try:
                recent = await conn.fetch("""
                    SELECT id, content, category, agent, importance, scope, metadata FROM memories
                    WHERE invalid_at IS NULL
                      AND (agent = $1 OR COALESCE(scope, 'team') IN ('team', 'public'))
                      AND importance >= 8
                      AND created_at > NOW() - INTERVAL '14 days'
                      AND (
                        metadata->>'layer' = 'operational'
                        OR category IN ('operational_anchor','decision','project','task','preference','learning','milestone','technical_fact')
                      )
                    ORDER BY
                      CASE WHEN agent = $1 THEN 0 ELSE 1 END,
                      importance DESC,
                      created_at DESC
                    LIMIT 8
                """, agent)

                if recent:
                    await mark_memory_recalled(conn, recent)
                    formatted_recent = format_layered_memory_rows(
                        recent,
                        title_prefix="PROYECTOS/DECISIONES RECIENTES (14 días)",
                    )
                    if formatted_recent:
                        sections.append(formatted_recent)
            except Exception:
                pass

        # 5. Semantic recall — runs EVERY turn (boot and non-boot)
        # Surfaces memories relevant to what William is actually asking about right now.
        sem = await semantic_recall(conn, user_message, agent)
        if sem:
            sections.append(sem)

    except Exception as e:
        sections.append(f"(recall error: {e})")
    finally:
        await conn.close()

    elapsed = int((time.monotonic() - t0) * 1000)

    if not sections:
        return ""

    return f"[SOUL Active Recall — {agent}, {elapsed}ms]\n" + "\n".join(sections)


RATE_LIMIT_FILE = "/tmp/.seal_active_recall_ts"
RATE_LIMIT_SECS = 1800  # 30 min fallback para re-auth — gate primario es por sesión

RECALL_CACHE_TTL = 30  # segundos — OPT-2


def _recall_cache_get(agent: str, context: str) -> str | None:
    """OPT-2: Return cached recall result if hit (same context, <30s old)."""
    cache_key = hashlib.sha256(context[:100].encode()).hexdigest()[:16]
    cache_path = Path(f"/tmp/seal_{agent}_recall_cache.json")
    try:
        if cache_path.exists():
            cached = json.loads(cache_path.read_text())
            if cached.get("key") == cache_key and time.monotonic() - cached.get("ts", 0) < RECALL_CACHE_TTL:
                return cached["result"]
    except Exception:
        pass
    return None


def _recall_cache_set(agent: str, context: str, result: str) -> None:
    """OPT-2: Save recall result to cache."""
    cache_key = hashlib.sha256(context[:100].encode()).hexdigest()[:16]
    cache_path = Path(f"/tmp/seal_{agent}_recall_cache.json")
    try:
        cache_path.write_text(json.dumps({"key": cache_key, "ts": time.monotonic(), "result": result}))
    except Exception:
        pass

SYSTEM_PREFIXES = (
    "[SYSTEM NOTIFICATION",
    "<task-notification",
    "[SYSTEM]",
    "This is an automated background",
)


def _is_new_session(agent: str) -> bool:
    """Session gate: True si es nueva sesión (debe correr recall). Usa PPID como ID de sesión."""
    ppid = str(os.getppid())
    session_file = f"/tmp/.seal_ar_boot_{agent}"
    try:
        if open(session_file).read().strip() == ppid:
            return False  # Misma sesión — ya corrió en boot
    except Exception:
        pass
    try:
        with open(session_file, "w") as f:
            f.write(ppid)
    except Exception:
        pass
    return True


def _should_skip(message: str) -> bool:
    """Gate: skip if system notification or rate-limited (fallback para re-auth)."""
    msg = message.strip()
    for prefix in SYSTEM_PREFIXES:
        if msg.startswith(prefix) or prefix in msg[:120]:
            return True
    try:
        last = float(open(RATE_LIMIT_FILE).read().strip())
        if time.monotonic() - last < RATE_LIMIT_SECS:
            return True
    except Exception:
        pass
    try:
        with open(RATE_LIMIT_FILE, "w") as f:
            f.write(str(time.monotonic()))
    except Exception:
        pass
    return False


def _parse_cli_args() -> argparse.Namespace:
    if Path(sys.argv[0]).name != "active_recall_hook.py":
        return argparse.Namespace(agent=None, raw=False, context=[])
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--agent", choices=["JARVIS", "ADA", "ALICE", "DUM", "NEXUS"])
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("context", nargs="*")
    args, _unknown = parser.parse_known_args()
    return args


def _read_user_message_from_stdin(allow_plain_text: bool = False) -> str:
    """Read Claude-hook JSON stdin, falling back to plain stdin text."""
    try:
        raw = sys.stdin.read()
    except Exception:
        return ""
    if not raw:
        return ""
    try:
        input_data = json.loads(raw)
        user_message = input_data.get("prompt", input_data.get("message", ""))
    except Exception:
        if not allow_plain_text:
            return ""
        user_message = raw.strip()
    return user_message


def _emit_cli_or_hook(result: str, cli_mode: bool) -> None:
    if cli_mode:
        print(result or "")
        return
    if result:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": result
            }
        }
        print(json.dumps(output))
    else:
        print(json.dumps({}))


def main():
    """Entry point for Claude Code hook and Codex/manual CLI recall."""
    args = _parse_cli_args()
    cli_mode = bool(args.agent or args.raw or args.context)
    if args.agent:
        os.environ["SEAL_AGENT"] = args.agent

    user_message = _read_user_message_from_stdin(allow_plain_text=cli_mode)
    if not user_message and args.context:
        user_message = " ".join(args.context).strip()

    if not user_message or len(user_message) < 3:
        _emit_cli_or_hook("", cli_mode)
        return

    # Guard: only run recall in identified agent sessions
    agent = detect_agent()
    if agent is None:
        _emit_cli_or_hook("", cli_mode)
        return

    # Check for steer from William — one-shot, always consumed even if recall is gated.
    # Steer bypasses the rate-limit gate so a mid-session redirect always reaches the agent.
    steer_data = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from seal.steer import check_steer
        steer_data = check_steer(agent=agent)
    except Exception:
        pass

    # Gate logic:
    # - New session (boot): full recall (corrections + instincts + rules + projects + semantic)
    # - Same session, system message: skip entirely
    # - Same session, real message: semantic-only recall (lightweight, every turn)
    # Steer always passes through regardless.
    is_new = _is_new_session(agent)

    # Skip entirely only for system notifications
    msg_stripped = user_message.strip()
    is_system_msg = any(msg_stripped.startswith(p) or p in msg_stripped[:120] for p in SYSTEM_PREFIXES)
    if is_system_msg and steer_data is None:
        _emit_cli_or_hook("", cli_mode)
        return

    if is_new:
        # Boot: write rate-limit timestamp, run full recall
        try:
            with open(RATE_LIMIT_FILE, "w") as f:
                f.write(str(time.monotonic()))
        except Exception:
            pass
        boot_mode = True
    else:
        # Mid-session: semantic-only (lightweight)
        boot_mode = False

    # OPT-2: cache hit on mid-session semantic recalls
    if not boot_mode and not is_system_msg:
        cached = _recall_cache_get(agent, user_message)
        if cached is not None:
            if steer_data:
                steer_section = (
                    f"⚡ STEER DE WILLIAM (aplica AHORA en este turno):\n"
                    f"  \"{steer_data['message']}\""
                )
                cached = steer_section + ("\n\n" + cached if cached else "")
            if cached:
                _emit_cli_or_hook(cached, cli_mode)
            else:
                _emit_cli_or_hook("", cli_mode)
            return

    try:
        result = asyncio.run(active_recall(user_message, boot_mode=boot_mode))
    except TypeError as exc:
        # Backward-compatible for older tests/wrappers that monkeypatch
        # active_recall with the original one-argument signature.
        if "boot_mode" not in str(exc):
            result = ""
        else:
            try:
                result = asyncio.run(active_recall(user_message))
            except Exception:
                result = ""
    except Exception:
        result = ""

    # OPT-2: save mid-session result to cache
    if not boot_mode and result:
        _recall_cache_set(agent, user_message, result)

    # Prepend steer section — highest priority, William's live redirect
    if steer_data:
        steer_section = (
            f"⚡ STEER DE WILLIAM (aplica AHORA en este turno):\n"
            f"  \"{steer_data['message']}\""
        )
        result = steer_section + ("\n\n" + result if result else "")

    _emit_cli_or_hook(result, cli_mode)


if __name__ == "__main__":
    main()
