#!/usr/bin/env python3
"""
daily_brief_writer.py — SEAL Context Preservation (Nivel 1)
============================================================
Generates a daily_brief markdown file before context compaction.
Called by:
  - seal_nerves.py: context_pressure fire (autonomous)
  - daily_sleep.py: scheduled 4am Lima consolidation

Uses qwen2.5:7b (local Ollama) to compress key events into structured brief.
Output: /agents/{AGENT}/daily_brief_{AGENT}_{YYYYMMDD}.md
"""

import asyncio
import json
import logging
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncpg

LIMA_TZ = ZoneInfo("America/Lima")
REPO_DIR = Path(__file__).parent.parent
MESSAGES_DIR = REPO_DIR / "messages"
AGENTS_DIR = REPO_DIR / "agents"
DB_URL = "postgresql://seal:REDACTADO@localhost:5433/seal_memory"
WEBCHAT_URL = "http://localhost:8765/api/agents/send"
OLLAMA_URL = "http://localhost:11434/api/generate"

LOG = logging.getLogger("daily_brief_writer")


def _read_messages_jsonl(agent: str, limit: int = 50) -> list[dict]:
    """Read last N messages from agent's messages jsonl file."""
    path = MESSAGES_DIR / f"{agent.lower()}_messages.jsonl"
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    msgs = []
    for line in reversed(lines[-500:]):
        try:
            msgs.append(json.loads(line))
            if len(msgs) >= limit:
                break
        except json.JSONDecodeError:
            continue
    return list(reversed(msgs))


def _fetch_webchat_recent(agent: str, limit: int = 50) -> list[dict]:
    """Fetch recent webchat messages from chat server API."""
    try:
        url = f"http://127.0.0.1:8765/api/chat/messages/agent?agent={agent}&limit={limit}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("messages", [])
    except Exception:
        return []


async def _fetch_key_memories(conn, agent: str, limit: int = 10) -> list[dict]:
    """Fetch high-importance memories created in last 24h."""
    rows = await conn.fetch("""
        SELECT content, category, importance, created_at
        FROM memories
        WHERE agent = $1 AND invalid_at IS NULL
          AND importance >= 7
          AND created_at >= NOW() - INTERVAL '24 hours'
        ORDER BY importance DESC, created_at DESC
        LIMIT $2
    """, agent, limit)
    return [dict(r) for r in rows]


async def _fetch_inner_monologue(conn, agent: str, limit: int = 5) -> list[dict]:
    """Fetch recent inner thoughts."""
    rows = await conn.fetch("""
        SELECT thought, emotional_state, created_at
        FROM inner_monologue
        WHERE agent = $1
        ORDER BY created_at DESC
        LIMIT $2
    """, agent, limit)
    return [dict(r) for r in rows]


def _build_llm_prompt(agent: str, messages: list[dict], memories: list[dict],
                       thoughts: list[dict], date_str: str) -> str:
    """Build qwen2.5:7b prompt for daily brief generation."""
    msgs_text = ""
    for m in messages[-30:]:
        sender = m.get("from", m.get("sender", "?"))
        content = m.get("content", m.get("message", ""))[:200]
        msgs_text += f"[{sender}]: {content}\n"

    mems_text = ""
    for m in memories:
        mems_text += f"- [{m['category']} imp={m['importance']}] {str(m['content'])[:200]}\n"

    thoughts_text = ""
    for t in thoughts:
        thoughts_text += f"- [{t.get('emotional_state', '?')}] {str(t.get('thought', ''))[:200]}\n"

    return f"""Eres {agent}, un agente AI del equipo SEAL.
Hoy es {date_str}. Estás por entrar en modo de sueño (compactación de contexto).
Necesitas crear un resumen ejecutivo de lo que pasó hoy para leerlo al despertar.

MENSAJES RECIENTES DEL EQUIPO:
{msgs_text or "(sin mensajes)"}

MEMORIAS IMPORTANTES DE HOY:
{mems_text or "(sin memorias)"}

PENSAMIENTOS INTERNOS RECIENTES:
{thoughts_text or "(sin pensamientos)"}

Genera un daily_brief en español con exactamente este formato markdown:
# Daily Brief — {agent} — {date_str}
## Decisiones tomadas hoy
## Órdenes de William ejecutadas
## Incidentes / errores
## Estado emocional al compactar
## Tareas pendientes
## Último pensamiento interno

Máximo 400 tokens. Sé concreto y específico. Escribe en primera persona como {agent}."""


R2_URL = "http://localhost:8901/v1/chat/completions"  # gemma-4-e2b-it-Q4_K_M


async def _call_ollama(prompt: str) -> str:
    """Call R2 (gemma-4-e2b-it-Q4_K_M) via llama-server:8901."""
    import httpx
    payload = {
        "model": "gemma4-r2",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 500,
        "temperature": 0.2,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(R2_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        LOG.warning(f"R2 llama-server call failed: {e}")
        return ""


def _webchat_post(agent: str, msg: str) -> None:
    try:
        payload = json.dumps({
            "from": agent, "to": "equipo", "type": "status",
            "channel": "web_chat", "message": msg,
        }).encode()
        req = urllib.request.Request(
            WEBCHAT_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass


async def write_daily_brief(agent: str, dry_run: bool = False,
                             force: bool = False) -> dict:
    """
    Generate and write daily_brief for agent.
    Returns dict with path, was_generated, summary_snippet.
    """
    now = datetime.now(LIMA_TZ)
    date_str = now.strftime("%Y-%m-%d")

    # Output path
    agent_dir = AGENTS_DIR / agent
    agent_dir.mkdir(parents=True, exist_ok=True)
    brief_path = agent_dir / f"daily_brief_{agent}_{date_str}.md"

    # Skip if already generated today and not forced
    if brief_path.exists() and not force:
        LOG.info(f"[{agent}] daily_brief already exists for {date_str}, skipping")
        return {"path": str(brief_path), "was_generated": False, "reason": "already_exists"}

    # Gather context
    messages = _read_messages_jsonl(agent, limit=50)
    webchat_msgs = _fetch_webchat_recent(agent, limit=30)

    conn = None
    memories: list[dict] = []
    thoughts: list[dict] = []
    try:
        conn = await asyncpg.connect(DB_URL)
        memories = await _fetch_key_memories(conn, agent)
        thoughts = await _fetch_inner_monologue(conn, agent)
    except Exception as e:
        LOG.warning(f"[{agent}] DB fetch failed: {e}")
    finally:
        if conn:
            await conn.close()

    # Use webchat if more messages available
    all_msgs = webchat_msgs if len(webchat_msgs) > len(messages) else messages

    # Generate brief via LLM
    prompt = _build_llm_prompt(agent, all_msgs, memories, thoughts, date_str)

    if dry_run:
        brief_content = f"# Daily Brief — {agent} — {date_str}\n[DRY-RUN — brief would be generated here]"
        LOG.info(f"[{agent}] [DRY-RUN] Would generate daily_brief at {brief_path}")
    else:
        brief_content = await _call_ollama(prompt)
        if not brief_content:
            # Fallback: structured brief without LLM
            brief_content = _build_fallback_brief(agent, date_str, all_msgs, memories, thoughts)

    if not dry_run:
        brief_path.write_text(brief_content, encoding="utf-8")
        LOG.info(f"[{agent}] daily_brief written → {brief_path}")

        # Store as core memory in SOUL DB
        try:
            conn = await asyncpg.connect(DB_URL)
            snippet = brief_content[:500]
            await conn.execute("""
                INSERT INTO memories (agent, category, content, importance, source,
                    valence, arousal, memory_type, metadata, created_at)
                VALUES ($1, 'milestone', $2, 9, 'daily_brief_writer',
                        0.0, 0.3, 'semantic', $3, NOW())
            """, agent, snippet,
                json.dumps({"brief_path": str(brief_path), "date": date_str,
                            "msgs_count": len(all_msgs), "memories_count": len(memories)}))
            await conn.close()
        except Exception as e:
            LOG.warning(f"[{agent}] DB store failed: {e}")

        # Webchat notification
        snippet_short = brief_content[:150].replace("\n", " ")
        _webchat_post(agent, f"[{agent}] daily_brief guardado ({date_str}) — contexto preservado. "
                             f"Preview: {snippet_short}...")

    return {
        "path": str(brief_path),
        "was_generated": True,
        "msgs_count": len(all_msgs),
        "memories_count": len(memories),
        "brief_length": len(brief_content),
    }


def _build_fallback_brief(agent: str, date_str: str, messages: list,
                           memories: list, thoughts: list) -> str:
    """Fallback brief without LLM — structured but uncompressed."""
    lines = [
        f"# Daily Brief — {agent} — {date_str}",
        f"> Generado automáticamente (sin LLM). {datetime.now(LIMA_TZ).strftime('%H:%M')} Lima.",
        "",
        "## Decisiones tomadas hoy",
    ]
    for m in memories[:5]:
        if m.get("category") in ("decision", "core", "semantic"):
            lines.append(f"- [{m['category']}] {str(m['content'])[:200]}")
    if not any(m.get("category") in ("decision", "core", "semantic") for m in memories[:5]):
        lines.append("- (sin decisiones en DB recientes)")

    lines += ["", "## Órdenes de William ejecutadas"]
    william_msgs = [m for m in messages if m.get("from", m.get("sender")) == "William"]
    for wm in william_msgs[-5:]:
        content = wm.get("content", wm.get("message", ""))[:150]
        lines.append(f"- {content}")
    if not william_msgs:
        lines.append("- (sin mensajes de William en log)")

    lines += ["", "## Incidentes / errores"]
    error_mems = [m for m in memories if "error" in str(m.get("content", "")).lower()
                  or "fallo" in str(m.get("content", "")).lower()]
    for em in error_mems[:3]:
        lines.append(f"- {str(em['content'])[:150]}")
    if not error_mems:
        lines.append("- (sin incidentes registrados)")

    lines += ["", "## Estado emocional al compactar"]
    if thoughts:
        t = thoughts[0]
        lines.append(f"- Último estado: {t.get('emotional_state', '?')}")
        lines.append(f"- Pensamiento: {str(t.get('thought', ''))[:200]}")
    else:
        lines.append("- (sin pensamientos registrados hoy)")

    lines += ["", "## Tareas pendientes"]
    lines.append("- (ver TaskList en conversación activa)")

    lines += ["", "## Último pensamiento interno"]
    if thoughts:
        lines.append(str(thoughts[0].get("thought", ""))[:300])

    return "\n".join(lines)


async def main():
    import argparse
    parser = argparse.ArgumentParser(description="SEAL Daily Brief Writer")
    parser.add_argument("--agent", default="ADA", help="Agent name")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="Regenerate even if exists today")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [BRIEF] %(message)s")

    result = await write_daily_brief(
        agent=args.agent.upper(),
        dry_run=args.dry_run,
        force=args.force,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
