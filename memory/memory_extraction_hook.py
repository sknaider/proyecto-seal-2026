#!/usr/bin/env python3
"""SEAL Memory Extraction Hook — H2.5 (Stop hook).

Dispara al final de cada turno del agente. Lee el último exchange del JSONL
de conversación, detecta contenido memorable via regex/heurísticas (fast path),
y guarda memoria via MCP si hay señal real.

Fast path sin LLM: <1ms para el 80% de casos (skip).
Slow path con MCP store: <5s para memorias reales.

Bypass: SEAL_MEM_EXTRACT_BYPASS=1 → no memorizar.
Owner: JARVIS (diseño H2.5) | ADA (implementación) — 2026-04-19
"""
from __future__ import annotations

import json
import os
import re
import sys
import glob
from pathlib import Path
from typing import Optional

BYPASS = (
    os.environ.get("SEAL_MEM_EXTRACT_BYPASS", "0") == "1"
    or os.environ.get("SEAL_MEMORY_EXTRACT_BYPASS", "0") == "1"
)
DB_URL = "postgresql://seal:seal_memory_2026@localhost:5433/seal_memory"
LOG_FILE = Path("/tmp/memory_extraction_hook.log")
MAX_CONTENT_CHARS = 200
MIN_EXCHANGE_LEN = 100  # skip trivially short exchanges

# Pattern → (memory_type, importance, tags)
PATTERNS: list[tuple[re.Pattern, str, int, list[str]]] = [
    (re.compile(r"William (corrigió|ordenó|dijo|mandó|fijó)|CORRECCIÓN:|REGLA\b|REGLA DE ORO", re.I),
     "feedback", 9, ["correction", "william_order"]),
    (re.compile(r"(traceback|error:|exception:).*?(fixed|solucionado|arreglado|fix aplicado)", re.I | re.S),
     "episodic", 7, ["fix", "error"]),
    (re.compile(r"(decidimos|vamos a usar|arquitectura:|diseño:|spec:|usaremos)\b", re.I),
     "semantic", 8, ["decision", "architecture"]),
    (re.compile(r"(loss[:\s]=?\s*[\d.]+|accuracy[:\s]+[\d.]+%?|benchmark|eval result)", re.I),
     "episodic", 6, ["benchmark", "training"]),
    (re.compile(r"(Edit|Write|created|implementé|implementado|hook registrado|settings\.json)", re.I),
     "episodic", 5, ["implementation", "file_change"]),
    (re.compile(r"(seguridad|security|contraseña|password|credential|token) (expuest|en plaintext|rotación)", re.I),
     "feedback", 9, ["security", "urgent"]),
]

SKIP_PATTERNS = [
    re.compile(r"^(hola|gracias|ok\b|recibido|ack\b|confirmado|entendido|👍|✅)", re.I),
    re.compile(r"system_alive|heartbeat|monitor event", re.I),
    re.compile(r"^\[ADA AUDIT\]|\[SEAL\]", re.I),
]


def log(msg: str) -> None:
    try:
        with LOG_FILE.open("a") as f:
            ts = __import__("datetime").datetime.now().strftime("%H:%M:%S")
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


def get_agent() -> str:
    return os.environ.get("SEAL_AGENT", "").upper()


def get_project_dir(cwd: str) -> Optional[str]:
    cwd = cwd or ""
    home = os.path.expanduser("~")
    if "proyecto-seal-alice" in cwd:
        return os.path.join(home, ".claude/projects/-home-dadito-IA-proyecto-seal-alice")
    if "proyecto-seal" in cwd:
        return os.path.join(home, ".claude/projects/-home-dadito-IA-proyecto-seal")
    if "/IA" in cwd:
        return os.path.join(home, ".claude/projects/-home-dadito-IA")
    return None


def read_last_exchange(jsonl_path: str) -> str:
    """Lee los últimos 4 mensajes del JSONL de conversación."""
    try:
        with open(jsonl_path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        last_msgs = []
        for line in reversed(lines[-60:]):
            try:
                d = json.loads(line)
                role = d.get("role", "")
                content = d.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        c.get("text", "") for c in content if isinstance(c, dict)
                    )
                if role in ("user", "assistant") and content:
                    last_msgs.append(f"{role}: {content[:500]}")
                    if len(last_msgs) >= 4:
                        break
            except Exception:
                continue
        return "\n".join(reversed(last_msgs))
    except Exception:
        return ""


def classify(text: str) -> Optional[tuple[str, int, list[str], str]]:
    """Retorna (memory_type, importance, tags, extracted_content) o None si skip."""
    if len(text) < MIN_EXCHANGE_LEN:
        return None
    for skip_pat in SKIP_PATTERNS:
        if skip_pat.search(text[:200]):
            return None
    for pattern, mem_type, importance, tags in PATTERNS:
        match = pattern.search(text)
        if match:
            start = max(0, match.start() - 50)
            end = min(len(text), match.end() + 150)
            content = text[start:end].replace("\n", " ").strip()
            content = content[:MAX_CONTENT_CHARS]
            return mem_type, importance, tags, content
    return None


def store_memory_db(agent: str, content: str, mem_type: str,
                    importance: int, tags: list[str]) -> bool:
    """Guarda memoria via asyncpg directo (primario — MCP SSE demasiado complejo para hook síncrono)."""
    try:
        import asyncio
        import asyncpg
        from datetime import datetime, timezone

        async def _insert():
            conn = await asyncpg.connect(DB_URL)
            now = datetime.now(timezone.utc)
            try:
                prefix = content[:50]
                existing = await conn.fetchval(
                    "SELECT id FROM memories WHERE agent=$1 AND content LIKE $2 "
                    "AND invalid_at IS NULL LIMIT 1",
                    agent, prefix + "%"
                )
                if existing:
                    log(f"  dedup skip id={existing}")
                    return None
                row_id = await conn.fetchval("""
                    INSERT INTO memories
                      (agent, category, content, importance, confidence_score,
                       source, valid_from, created_at, provenance)
                    VALUES ($1,$2,$3,$4,0.75,'auto_stop_hook',$5,$5,$6)
                    RETURNING id
                """,
                    agent, mem_type, content, importance, now,
                    f"H2.5 auto — {now.strftime('%Y-%m-%d %H:%M')}",
                )
                return row_id
            finally:
                await conn.close()

        row_id = asyncio.run(_insert())
        if row_id:
            log(f"  stored id={row_id} type={mem_type} imp={importance}")
        return row_id is not None
    except Exception as e:
        log(f"  store_memory_db error: {e}")
        return False


def store_reflection_db(agent: str, content: str, tags: list[str]) -> None:
    """AEL slow timescale — escribe reflexión a inner_monologue cuando se detecta fallo+fix.

    Implementa el eje lento de AEL (arxiv 2604.21725): el agente reflexiona sobre
    patrones de fallo para no repetirlos. Post_compact_hook inyecta esto como
    'último pensamiento' al despertar, cerrando el ciclo de aprendizaje.
    """
    try:
        import asyncio
        import asyncpg

        has_error = any(t in tags for t in ("fix", "error"))
        has_decision = any(t in tags for t in ("decision", "architecture"))
        if not has_error and not has_decision:
            return

        if has_error:
            emotional_state = "reflexiva_post_error"
            thought = (
                f"Detecté y resolví un problema: {content[:150]}. "
                f"Reflexión AEL: ¿por qué ocurrió? ¿qué evita que se repita? "
                f"Tags: {', '.join(tags)}."
            )
        else:
            emotional_state = "enfocada"
            thought = (
                f"Tomé una decisión arquitectural: {content[:150]}. "
                f"Tags: {', '.join(tags)}."
            )

        async def _insert():
            conn = await asyncpg.connect(DB_URL)
            try:
                await conn.execute(
                    "INSERT INTO inner_monologue (agent, thought, emotional_state, created_at) "
                    "VALUES ($1, $2, $3, NOW())",
                    agent, thought[:500], emotional_state,
                )
            finally:
                await conn.close()

        asyncio.run(_insert())
        log(f"  reflection stored emotional_state={emotional_state}")
    except Exception as e:
        log(f"  store_reflection_db error: {e}")


def _trigger_llm_extraction(agent: str, exchange: str) -> None:
    """Lanza auto_extract_llm en background (no bloqueante) cuando regex no matchea."""
    if len(exchange) < MIN_EXCHANGE_LEN:
        return
    try:
        import subprocess
        script = Path(__file__).parent / "auto_extract_llm.py"
        if not script.exists():
            return
        env = os.environ.copy()
        env["SEAL_AGENT"] = agent
        env["_AEX_EXCHANGE"] = exchange[:3000]
        subprocess.Popen(
            ["python3", str(script), "--from-hook"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        log(f"[{agent}] llm_extract triggered (background)")
    except Exception as e:
        log(f"_trigger_llm_extraction error: {e}")


def main() -> None:
    if BYPASS:
        print(json.dumps({}))
        return

    agent = get_agent()
    if not agent or agent == "DUM":
        print(json.dumps({}))
        return

    try:
        event = json.loads(sys.stdin.read())
    except Exception:
        print(json.dumps({}))
        return

    # Skip turnos incompletos
    if event.get("stop_reason") == "max_tokens":
        print(json.dumps({}))
        return

    cwd = event.get("cwd", "")

    project_dir = get_project_dir(cwd)
    if not project_dir:
        print(json.dumps({}))
        return

    jsonl_files = sorted(glob.glob(f"{project_dir}/*.jsonl"), key=os.path.getmtime, reverse=True)
    if not jsonl_files:
        print(json.dumps({}))
        return

    exchange = read_last_exchange(jsonl_files[0])
    if not exchange:
        print(json.dumps({}))
        return

    result = classify(exchange)
    if result is None:
        # Regex no matcheó — slow path: LLM extraction en background (mem0-style)
        _trigger_llm_extraction(agent, exchange)
        print(json.dumps({}))
        return

    mem_type, importance, tags, content = result
    log(f"[{agent}] match type={mem_type} imp={importance} snippet={content[:60]}...")
    stored = store_memory_db(agent, content, mem_type, importance, tags)
    if stored:
        store_reflection_db(agent, content, tags)
    print(json.dumps({}))


if __name__ == "__main__":
    main()
