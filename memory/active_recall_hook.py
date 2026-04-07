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
    # Check process command line for agent name
    try:
        import subprocess
        ppid = os.getppid()
        cmdline = open(f"/proc/{ppid}/cmdline", "rb").read().decode("utf-8", errors="replace")
        if "JARVIS" in cmdline:
            return "JARVIS"
        elif "ADA" in cmdline:
            return "ADA"
    except Exception:
        pass

    # Fallback: check CWD
    cwd = os.getcwd()
    if "memory" in cwd:
        return "JARVIS"
    return "ADA"


async def get_embedding(text: str) -> list:
    """Get embedding from Ollama nomic-embed-text."""
    import urllib.request
    payload = json.dumps({"model": EMBED_MODEL, "input": text}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as resp:
        data = json.loads(resp.read())
        return data["embeddings"][0]


async def active_recall(user_message: str) -> str:
    """Core recall logic — fast version for hooks."""
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
        # 1. Recent corrections (HIGHEST PRIORITY — what William corrected)
        # No time window — corrections don't expire. Limit 5 to keep context tight.
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

        # 2. Active instincts — top by confidence (not similarity)
        # Semantic search fails for short trigger patterns vs conversational messages.
        # Since agents have <15 instincts, returning top-confidence is more reliable.
        try:
            instincts = await conn.fetch("""
                SELECT trigger_pattern, response, confidence
                FROM instincts
                WHERE agent = $1 AND active = true AND confidence >= 0.7
                ORDER BY confidence DESC
                LIMIT 3
            """, agent)

            if instincts:
                inst_lines = ["⚡ INSTINTOS ACTIVOS (conf>=0.7):"]
                for i in instincts:
                    inst_lines.append(
                        f"  - [{i['confidence']:.2f}] CUANDO: {i['trigger_pattern'][:80]} "
                        f"→ HAZ: {i['response'][:100]}"
                    )
                sections.append("\n".join(inst_lines))
        except Exception:
            pass

        # 3. Key rules — ALL critical rules, then high. No hardcoded names.
        rules = await conn.fetch("""
            SELECT rule_key, content FROM rules
            WHERE active = true AND LOWER(priority) IN ('critical', 'high')
            ORDER BY
                CASE LOWER(priority) WHEN 'critical' THEN 0 ELSE 1 END,
                created_at DESC
            LIMIT 5
        """)

        if rules:
            rule_lines = ["📋 REGLAS ACTIVAS:"]
            for r in rules:
                rule_lines.append(f"  - {r['rule_key']}: {r['content'][:100]}")
            sections.append("\n".join(rule_lines))

    except Exception as e:
        sections.append(f"(recall error: {e})")
    finally:
        await conn.close()

    elapsed = int((time.monotonic() - t0) * 1000)

    if not sections:
        return ""

    return f"[SOUL Active Recall — {agent}, {elapsed}ms]\n" + "\n".join(sections)


def main():
    """Entry point for Claude Code hook."""
    # Read user prompt from stdin (Claude Code passes it)
    try:
        input_data = json.loads(sys.stdin.read())
        user_message = input_data.get("prompt", input_data.get("message", ""))
    except Exception:
        user_message = ""

    if not user_message or len(user_message) < 3:
        # Don't recall for empty or very short messages
        print(json.dumps({}))
        return

    try:
        result = asyncio.run(active_recall(user_message))
    except Exception:
        print(json.dumps({}))
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


if __name__ == "__main__":
    main()
