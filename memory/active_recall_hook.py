#!/usr/bin/env python3
"""SEAL Active Recall Hook — Inyecta memorias relevantes antes de cada respuesta.

Se ejecuta como hook de Claude Code en UserPromptSubmit.
Recibe el mensaje del usuario por stdin, busca memorias/instincts/corrections
relevantes, y retorna contexto adicional via hookSpecificOutput.

Tiempo target: <500ms. Si falla, retorna vacío (no bloquear al agente).
"""

import asyncio
import json
import os
import sys
import time

DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
OLLAMA_URL = "http://localhost:11434/api/embed"
EMBED_MODEL = "nomic-embed-text"

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


async def get_embedding_async(text: str) -> list:
    """Get text embedding from Ollama — async, 1.5s timeout."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            r = await client.post(OLLAMA_URL, json={"model": EMBED_MODEL, "input": text})
            return r.json()["embeddings"][0]
    except Exception:
        return []


async def semantic_recall(conn, message: str) -> str:
    """Per-message semantic search — surfaces relevant memories for the current topic."""
    if not message or len(message.strip()) < 15:
        return ""
    try:
        embedding = await get_embedding_async(message[:500])
        if not embedding:
            return ""
        vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
        results = await conn.fetch(f"""
            SELECT content, category, agent,
                   1 - (embedding <=> '{vec_str}'::vector) AS similarity
            FROM memories
            WHERE invalid_at IS NULL
              AND importance >= 7
            ORDER BY embedding <=> '{vec_str}'::vector
            LIMIT 4
        """)
        hits = [r for r in results if float(r["similarity"]) > 0.78]
        if not hits:
            return ""
        lines = ["🔍 MEMORIAS RELEVANTES AL MENSAJE ACTUAL:"]
        for h in hits:
            lines.append(f"  - [{h['agent']}·{h['category']}] {h['content'][:160]}")
        return "\n".join(lines)
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
                SELECT content, importance FROM memories
                WHERE agent = $1 AND category = 'correction' AND invalid_at IS NULL
                ORDER BY importance DESC, created_at DESC
                LIMIT 5
            """, agent)

            if corrections:
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

            # 4. Recent important memories — projects, decisions, tasks (last 14 days)
            try:
                recent = await conn.fetch("""
                    SELECT content, category, importance FROM memories
                    WHERE invalid_at IS NULL
                      AND importance >= 8
                      AND created_at > NOW() - INTERVAL '14 days'
                      AND category IN ('decision','project','task','preference','learning')
                    ORDER BY importance DESC, created_at DESC
                    LIMIT 8
                """)

                if recent:
                    mem_lines = ["🧠 PROYECTOS/DECISIONES RECIENTES (14 días):"]
                    for m in recent:
                        mem_lines.append(f"  - [{m['category']}] {m['content'][:180]}")
                    sections.append("\n".join(mem_lines))
            except Exception:
                pass

        # 5. Semantic recall — runs EVERY turn (boot and non-boot)
        # Surfaces memories relevant to what William is actually asking about right now.
        sem = await semantic_recall(conn, user_message)
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


def main():
    """Entry point for Claude Code hook."""
    # Read user prompt from stdin (Claude Code passes it)
    try:
        input_data = json.loads(sys.stdin.read())
        user_message = input_data.get("prompt", input_data.get("message", ""))
    except Exception:
        user_message = ""

    if not user_message or len(user_message) < 3:
        print(json.dumps({}))
        return

    # Guard: only run recall in identified agent sessions
    agent = detect_agent()
    if agent is None:
        print(json.dumps({}))
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

    # Gate: nueva sesión → corre en boot, luego rate-limit. Misma sesión → solo si re-auth.
    # Exception: if a steer arrived, always pass through so William's redirect is surfaced.
    is_new = _is_new_session(agent)
    if not is_new and steer_data is None and _should_skip(user_message):
        print(json.dumps({}))
        return
    if is_new:
        # Boot run — escribir timestamp para rate-limitar el resto de la sesión
        try:
            with open(RATE_LIMIT_FILE, "w") as f:
                f.write(str(time.monotonic()))
        except Exception:
            pass

    try:
        result = asyncio.run(active_recall(user_message))
    except Exception:
        result = ""

    # Prepend steer section — highest priority, William's live redirect
    if steer_data:
        steer_section = (
            f"⚡ STEER DE WILLIAM (aplica AHORA en este turno):\n"
            f"  \"{steer_data['message']}\""
        )
        result = steer_section + ("\n\n" + result if result else "")

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


if __name__ == "__main__":
    main()
