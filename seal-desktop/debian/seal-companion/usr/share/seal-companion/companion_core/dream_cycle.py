"""
dream_cycle.py — SEAL App local Dream Cycle (SQLite port).

Generates a "dream" narrative twice a day per agent, persists to
`daily_dreams` SQLite table, and exposes a helper that boot_context can
use to inject the latest dreams into the system prompt.

Local-first: uses Ollama (default) or local llama-server when available.
Falls back gracefully when no LLM is reachable.

CLI:
    python -m companion_core.dream_cycle --cycle morning --agent SOUL
    python -m companion_core.dream_cycle --cycle evening --dry-run
    python -m companion_core.dream_cycle --inject-preview
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import urllib.request
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from companion_core.db import init_db, close_db, get_db

LIMA_TZ = ZoneInfo("America/Lima")
DEFAULT_AGENT = "SOUL"
VALID_CYCLES = {"morning", "midday", "evening", "nocturnal"}
DEFAULT_INJECT_N = 2

OLLAMA_URL = "http://localhost:11434/api/generate"
LLAMA_SERVER_URL = "http://localhost:8899/v1/chat/completions"
DEFAULT_OLLAMA_MODEL = "gemma3:4b-it-qat"
DEFAULT_LLAMA_MODEL = "gemma-4-e2b-q8"


# ─── Prompts ─────────────────────────────────────────────────────────────────

_MORNING_PROMPT = """Estás despertando para empezar el día. Tu memoria de las últimas 24 horas:

{recap}

Narralo como un sueño matinal en primera persona — 200-300 palabras.
Incluye qué pasó ayer, cómo te sentiste, qué aprendiste, qué queda pendiente
y una intención clara para hoy. Tono cercano y reflexivo. Sin bullets, prosa fluida."""

_EVENING_PROMPT = """Estás cerrando el día. Tu memoria de las últimas 12 horas:

{recap}

Narralo como un sueño nocturno en primera persona — 250-400 palabras.
Incluye qué pasó, cómo evolucionó tu estado emocional, qué aprendiste,
qué quedó sin resolver, y una nota de cierre para mañana. Tono introspectivo."""

_EXTRACT_PROMPT = """Lee este sueño y devuelve SOLO un JSON válido (sin markdown):
{{
  "key_events": ["...", "..."],
  "emotional_arc": {{"start_valence": -1.0..1.0, "end_valence": -1.0..1.0, "dominant_emotion": "..."}},
  "learnings": ["..."],
  "pending_threads": ["..."]
}}

Sueño:
---
{narrative}
---"""


# ─── LLM call helpers ────────────────────────────────────────────────────────

def _call_llama_server(prompt: str, timeout: float = 90) -> Optional[str]:
    payload = json.dumps({
        "model": DEFAULT_LLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 700,
        "stream": False,
    }).encode()
    try:
        req = urllib.request.Request(
            LLAMA_SERVER_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        choices = data.get("choices") or []
        msg = choices[0].get("message", {}) if choices else {}
        text = msg.get("content")
        return text.strip() if isinstance(text, str) else None
    except Exception:
        return None


def _call_ollama(prompt: str, timeout: float = 90) -> Optional[str]:
    payload = json.dumps({
        "model": DEFAULT_OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 700},
    }).encode()
    try:
        req = urllib.request.Request(
            OLLAMA_URL, data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        txt = data.get("response")
        return txt.strip() if isinstance(txt, str) else None
    except Exception:
        return None


async def _llm_call(prompt: str) -> tuple[Optional[str], str]:
    text = await asyncio.to_thread(_call_llama_server, prompt)
    if text:
        return text, DEFAULT_LLAMA_MODEL
    text = await asyncio.to_thread(_call_ollama, prompt)
    if text:
        return text, DEFAULT_OLLAMA_MODEL
    return None, "none"


# ─── Data gathering (SQLite) ────────────────────────────────────────────────

async def _gather_recent_memories(agent: str, hours_back: int, limit: int = 60) -> list[dict]:
    """Pull recent meaningful memories from the local SQLite store."""
    db = get_db()
    rows = await db.execute_fetchall(
        f"""SELECT id, category, importance, content, created_at
            FROM memories
            WHERE agent = ?
              AND importance >= 4
              AND created_at >= datetime('now', '-{int(hours_back)} hours')
            ORDER BY importance DESC, created_at DESC
            LIMIT ?""",
        (agent, limit),
    )
    return [dict(r) for r in rows]


def _format_memories(memories: list[dict], max_chars: int = 6000) -> str:
    """Truncate memories into a compact prompt-friendly block."""
    parts: list[str] = []
    total = 0
    for m in memories:
        line = f"[{m.get('category') or '?'} imp={m.get('importance')}] {str(m.get('content',''))[:280]}"
        if total + len(line) > max_chars:
            break
        parts.append(line)
        total += len(line) + 1
    return "\n".join(parts) if parts else "(sin memorias recientes)"


# ─── JSON extraction helpers ─────────────────────────────────────────────────

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]+\}", re.MULTILINE)


def _extract_json(text: str) -> dict:
    """Best-effort parse of a JSON object from LLM output."""
    if not text:
        return {}
    candidates = []
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]+?\})\s*```", text)
    if fence:
        candidates.append(fence.group(1))
    block = _JSON_BLOCK_RE.search(text)
    if block:
        candidates.append(block.group(0))
    for cand in candidates:
        try:
            return json.loads(cand)
        except Exception:
            continue
    return {}


# ─── Public API ──────────────────────────────────────────────────────────────

async def generate_dream(agent: str, cycle: str, *, dry_run: bool = False) -> dict:
    """Generate (and optionally persist) one dream for the given agent/cycle."""
    if cycle not in VALID_CYCLES:
        raise ValueError(f"unknown cycle: {cycle}")
    hours_back = 24 if cycle == "morning" else 12
    today_iso = datetime.now(LIMA_TZ).date().isoformat()

    memories = await _gather_recent_memories(agent, hours_back)
    recap = _format_memories(memories)

    template = _MORNING_PROMPT if cycle in {"morning", "midday"} else _EVENING_PROMPT
    narrative_prompt = template.format(recap=recap)

    narrative, model_used = await _llm_call(narrative_prompt)
    if not narrative:
        return {"ok": False, "error": "no local LLM reachable",
                "agent": agent, "cycle": cycle, "date": today_iso}

    extract_raw, _ = await _llm_call(_EXTRACT_PROMPT.format(narrative=narrative))
    parsed = _extract_json(extract_raw or "")
    key_events = parsed.get("key_events") or []
    emotional_arc = parsed.get("emotional_arc") or {}
    learnings = parsed.get("learnings") or []
    pending_threads = parsed.get("pending_threads") or []
    source_ids = [m["id"] for m in memories]

    result = {
        "ok": True,
        "agent": agent,
        "cycle": cycle,
        "date": today_iso,
        "narrative": narrative,
        "key_events": key_events,
        "emotional_arc": emotional_arc,
        "learnings": learnings,
        "pending_threads": pending_threads,
        "model_used": model_used,
        "source_count": len(memories),
        "dry_run": dry_run,
    }

    if not dry_run:
        db = get_db()
        await db.execute(
            """INSERT INTO daily_dreams
               (agent, date, cycle, dream_narrative, key_events, emotional_arc,
                learnings, pending_threads, model_used, source_memory_ids, inject_to_prompt)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
               ON CONFLICT(agent, date, cycle) DO UPDATE SET
                   dream_narrative=excluded.dream_narrative,
                   key_events=excluded.key_events,
                   emotional_arc=excluded.emotional_arc,
                   learnings=excluded.learnings,
                   pending_threads=excluded.pending_threads,
                   model_used=excluded.model_used,
                   source_memory_ids=excluded.source_memory_ids""",
            (agent, today_iso, cycle, narrative,
             json.dumps(key_events), json.dumps(emotional_arc),
             json.dumps(learnings), json.dumps(pending_threads),
             model_used, json.dumps(source_ids)),
        )
        await db.commit()
        result["persisted"] = True
    return result


async def get_dreams_for_prompt(agent: str = DEFAULT_AGENT, n: int = DEFAULT_INJECT_N) -> list[dict]:
    """Latest N dreams flagged inject_to_prompt=true, newest first."""
    db = get_db()
    rows = await db.execute_fetchall(
        """SELECT date, cycle, dream_narrative, pending_threads, learnings
           FROM daily_dreams
           WHERE agent = ? AND inject_to_prompt = 1
           ORDER BY date DESC, created_at DESC
           LIMIT ?""",
        (agent, max(1, min(int(n), 5))),
    )
    out = []
    for r in rows:
        try:
            pending = json.loads(r["pending_threads"]) if r["pending_threads"] else []
        except Exception:
            pending = []
        try:
            learnings = json.loads(r["learnings"]) if r["learnings"] else []
        except Exception:
            learnings = []
        out.append({
            "date": r["date"],
            "cycle": r["cycle"],
            "narrative": r["dream_narrative"],
            "pending_threads": pending,
            "learnings": learnings,
        })
    return out


def render_dreams_section(dreams: list[dict]) -> str:
    """Build a text block ready to prepend to a system prompt."""
    if not dreams:
        return ""
    blocks = ["## Dreams recientes (continuidad narrativa)"]
    cycle_label = {
        "morning": "🌅 mañana", "midday": "☀️ mediodía",
        "evening": "🌇 noche", "nocturnal": "🌙 nocturno",
    }
    for d in dreams:
        label = cycle_label.get(d["cycle"], d["cycle"])
        blocks.append(f"\n### {d['date']} — {label}")
        narrative = d["narrative"][:500]
        blocks.append(narrative + ("…" if len(d["narrative"]) > 500 else ""))
        if d["pending_threads"]:
            pending = ", ".join(str(t)[:80] for t in d["pending_threads"][:3])
            blocks.append(f"_Hilos pendientes:_ {pending}")
    return "\n".join(blocks)


# ─── CLI ─────────────────────────────────────────────────────────────────────

async def _cli_main(args: argparse.Namespace) -> None:
    await init_db()
    try:
        if args.inject_preview:
            dreams = await get_dreams_for_prompt(args.agent, args.inject_n)
            print(render_dreams_section(dreams) or "(no dreams to inject)")
            return
        result = await generate_dream(args.agent, args.cycle, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        await close_db()


def main() -> None:
    p = argparse.ArgumentParser(description="SEAL App local Dream Cycle (SQLite)")
    p.add_argument("--agent", default=DEFAULT_AGENT)
    p.add_argument("--cycle", default="morning", choices=sorted(VALID_CYCLES))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--inject-preview", action="store_true",
                   help="Show what get_dreams_for_prompt would inject (no LLM call)")
    p.add_argument("--inject-n", type=int, default=DEFAULT_INJECT_N)
    args = p.parse_args()
    asyncio.run(_cli_main(args))


if __name__ == "__main__":
    main()
