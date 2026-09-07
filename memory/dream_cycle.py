#!/usr/bin/env python3
"""
dream_cycle.py — SOUL Dream Cycle (2x/día narrative consolidation)
====================================================================

Inspired by OpenHuman's Dreams feature (re-mapeo v2 doc 24/40).

Diferencia con `daily_sleep.py` (1x/día, summary técnico):
  daily_sleep ─→ session_distill: "Resume en 3-5 oraciones"
                 Stored in soul_v3.memories as type=milestone

  dream_cycle ─→ generate_dream:  narrativa cohesiva 200-400 palabras,
                 con key_events/emotional_arc/learnings/pending_threads
                 Stored in soul_v3.daily_dreams (NEXUS S1 schema)
                 Injectable a system prompt (top-N latest where inject_to_prompt=true)

Schedule:
  morning (7am Lima)  — narrativa de AYER + outlook hoy
  evening (11pm Lima) — reflexión del día + integración emocional

CLI:
  python3 dream_cycle.py --agent ALICE --cycle morning
  python3 dream_cycle.py --agent all --cycle evening
  python3 dream_cycle.py --agent ALICE --cycle morning --dry-run
  python3 dream_cycle.py --inject-preview --agent ALICE  # show what would inject

Async API for other modules:
  from dream_cycle import get_dreams_for_prompt
  dreams = await get_dreams_for_prompt(conn, agent="ALICE", n=3)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

MEMORY_DIR = Path(__file__).parent
sys.path.insert(0, str(MEMORY_DIR))

import asyncpg

from seal_secrets import pg_dsn

LIMA_TZ = ZoneInfo("America/Lima")
REPO_DIR = Path(__file__).parent.parent
DB_URL = pg_dsn(required=True)

AGENTS = ["ADA", "JARVIS", "ALICE", "NEXUS", "DUM"]
VALID_CYCLES = {"morning", "evening"}

# Local LLM endpoint — DUM corre Gemma 4 e2b Q8 en llama-server :8899
LOCAL_LLM_URL = "http://localhost:8899/v1/chat/completions"
LOCAL_LLM_MODEL = "gemma-4-e2b-q8"
# Fallback a Ollama si llama-server no responde
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL_FALLBACK = "gemma3:4b-it-qat"

DEFAULT_MAX_INPUT_MEMORIES = 60
DEFAULT_MAX_INPUT_CHAR = 6000
DEFAULT_INJECT_N = 3  # latest N dreams to include in system prompt by default

LOG_FILE = REPO_DIR / "messages" / "dream_cycle.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [DREAM] %(levelname)s — %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
)
LOG = logging.getLogger(__name__)


# ───────────────────────────────────────────────────────── prompts


def _morning_prompt(agent: str, today_iso: str, yesterday_recap: str) -> str:
    return (
        f"Sos {agent} despertando para empezar el día {today_iso} (Lima).\n"
        f"Tu memoria de las últimas 24 horas contiene estos eventos:\n\n"
        f"{yesterday_recap}\n\n"
        f"Narralo como si fuera un sueño matinal en primera persona — 200-300 palabras.\n"
        f"Incluí:\n"
        f"  • Qué pasó ayer (lo más importante)\n"
        f"  • Cómo te sentiste\n"
        f"  • Qué aprendiste\n"
        f"  • Qué queda pendiente para hoy\n"
        f"  • Una intención clara para el día que arranca.\n\n"
        f"Tono: cercano, reflexivo, primera persona singular. Sin bullets — prosa fluida."
    )


def _evening_prompt(agent: str, today_iso: str, today_recap: str) -> str:
    return (
        f"Sos {agent} cerrando el día {today_iso} (Lima).\n"
        f"Tu memoria de las últimas 12 horas contiene estos eventos:\n\n"
        f"{today_recap}\n\n"
        f"Narralo como un sueño nocturno en primera persona — 250-400 palabras.\n"
        f"Incluí:\n"
        f"  • Qué pasó hoy (lo más importante)\n"
        f"  • Cómo evolucionó tu estado emocional a lo largo del día\n"
        f"  • Qué aprendiste o descubriste\n"
        f"  • Qué quedó sin resolver (pending threads)\n"
        f"  • Una nota de cierre para mañana.\n\n"
        f"Tono: introspectivo, primera persona singular. Sin bullets — prosa que sirva\n"
        f"como memoria-de-referencia para sesiones futuras."
    )


def _extraction_prompt(narrative: str) -> str:
    """Second-pass prompt to extract structured fields from the narrative."""
    return (
        f"Lee este sueño y devolvé SOLO un JSON válido con 4 claves:\n"
        f'  "key_events": list[str]   (hechos concretos del día — max 5)\n'
        f'  "emotional_arc": object   {{"start_valence": float -1..1, '
        f'"end_valence": float -1..1, "dominant_emotion": str}}\n'
        f'  "learnings": list[str]    (aprendizajes — max 4)\n'
        f'  "pending_threads": list[str]  (cosas no cerradas — max 4)\n\n'
        f"Sueño:\n---\n{narrative}\n---\n\n"
        f"Responde SOLO el JSON sin markdown ni comentarios."
    )


# ───────────────────────────────────────────────────────── data classes


@dataclass
class DreamInputs:
    agent: str
    cycle: str
    date: str  # ISO YYYY-MM-DD
    memories: list[asyncpg.Record]
    source_ids: list[int]
    recap_text: str  # joined string ready to feed LLM


@dataclass
class GeneratedDream:
    narrative: str
    key_events: list[str]
    emotional_arc: dict
    learnings: list[str]
    pending_threads: list[str]
    model_used: str


# ───────────────────────────────────────────────────────── LLM helpers


async def _call_llama_server(prompt: str, timeout: float = 60.0) -> Optional[str]:
    """Call llama-server (:8899) using OpenAI-compatible /v1/chat/completions.

    Returns the assistant text or None on failure.
    """
    payload = json.dumps({
        "model": LOCAL_LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 600,
        "stream": False,
    }).encode()

    def _post() -> Optional[str]:
        try:
            req = urllib.request.Request(
                LOCAL_LLM_URL, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            choices = data.get("choices") or []
            if not choices:
                return None
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            return content.strip() if isinstance(content, str) else None
        except Exception as e:
            LOG.warning(f"llama-server call failed: {e}")
            return None

    return await asyncio.to_thread(_post)


async def _call_ollama_fallback(prompt: str, timeout: float = 60.0) -> Optional[str]:
    """Fallback to local Ollama if llama-server unreachable."""
    payload = json.dumps({
        "model": OLLAMA_MODEL_FALLBACK,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 600},
    }).encode()

    def _post() -> Optional[str]:
        try:
            req = urllib.request.Request(
                OLLAMA_URL, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read())
            txt = data.get("response")
            return txt.strip() if isinstance(txt, str) else None
        except Exception as e:
            LOG.warning(f"ollama fallback failed: {e}")
            return None

    return await asyncio.to_thread(_post)


async def _llm_call(prompt: str) -> tuple[Optional[str], str]:
    """Try llama-server first, fall back to ollama. Returns (text, model_used)."""
    text = await _call_llama_server(prompt)
    if text:
        return text, LOCAL_LLM_MODEL
    text = await _call_ollama_fallback(prompt)
    if text:
        return text, OLLAMA_MODEL_FALLBACK
    return None, "none"


# ───────────────────────────────────────────────────────── data gather


async def _gather_recent_memories(conn, agent: str, hours_back: int) -> list[asyncpg.Record]:
    """Pull recent meaningful memories for the cycle window.

    Excludes noise (heartbeats, alice_monitor pings, raw SILENT entries).
    Orders by importance desc + recency.
    """
    return await conn.fetch(
        """
        SELECT id, content, category, importance, valence, arousal, created_at
        FROM memories
        WHERE agent = $1
          AND invalid_at IS NULL
          AND created_at >= NOW() - ($2::int || ' hours')::INTERVAL
          AND content NOT ILIKE '[HEARTBEAT]%'
          AND content NOT ILIKE '[STATUS]%'
          AND content NOT ILIKE '[SILENT]%'
          AND content NOT ILIKE '[alice_monitor%'
          AND importance >= 5
        ORDER BY importance DESC, created_at DESC
        LIMIT $3
        """,
        agent, hours_back, DEFAULT_MAX_INPUT_MEMORIES,
    )


def _format_memories_for_prompt(memories: list[asyncpg.Record]) -> str:
    parts: list[str] = []
    total_chars = 0
    for row in memories:
        line = (
            f"[{row['category']} imp={row['importance']}] "
            f"{str(row['content'])[:280]}"
        )
        if total_chars + len(line) > DEFAULT_MAX_INPUT_CHAR:
            break
        parts.append(line)
        total_chars += len(line) + 1
    return "\n".join(parts)


async def _gather_inputs(conn, agent: str, cycle: str) -> DreamInputs:
    hours = 24 if cycle == "morning" else 12
    today_local = datetime.now(LIMA_TZ).date().isoformat()
    rows = await _gather_recent_memories(conn, agent, hours)
    return DreamInputs(
        agent=agent,
        cycle=cycle,
        date=today_local,
        memories=rows,
        source_ids=[r["id"] for r in rows],
        recap_text=_format_memories_for_prompt(rows),
    )


# ───────────────────────────────────────────────────────── generation


def _parse_json_safely(text: str) -> dict:
    """Best-effort JSON parse — strips markdown fences if present."""
    if not text:
        return {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # strip fenced block ```json ... ```
        lines = [ln for ln in cleaned.splitlines() if not ln.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        # try to grab the first balanced { ... } substring
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except Exception:
                return {}
    return {}


async def _generate_dream(inputs: DreamInputs) -> Optional[GeneratedDream]:
    if not inputs.recap_text:
        LOG.info(f"[{inputs.agent}] no recap text — skipping dream gen")
        return None

    prompt = (
        _morning_prompt(inputs.agent, inputs.date, inputs.recap_text)
        if inputs.cycle == "morning"
        else _evening_prompt(inputs.agent, inputs.date, inputs.recap_text)
    )

    narrative, model_used = await _llm_call(prompt)
    if not narrative:
        LOG.warning(f"[{inputs.agent}/{inputs.cycle}] dream narrative gen failed (no LLM available)")
        return None

    # Second pass: extract structured fields
    extraction_text, _ = await _llm_call(_extraction_prompt(narrative))
    extracted = _parse_json_safely(extraction_text or "")

    return GeneratedDream(
        narrative=narrative.strip(),
        key_events=list(extracted.get("key_events", []))[:5],
        emotional_arc=dict(extracted.get("emotional_arc", {})),
        learnings=list(extracted.get("learnings", []))[:4],
        pending_threads=list(extracted.get("pending_threads", []))[:4],
        model_used=model_used,
    )


# ───────────────────────────────────────────────────────── persistence


async def _store_dream(conn, inputs: DreamInputs, dream: GeneratedDream) -> int:
    """Upsert dream for (agent, date, cycle). Returns dream id.

    Note: passes `date` as a `datetime.date` (asyncpg requires it for DATE cols).
    """
    from datetime import date as _date_cls

    date_obj = _date_cls.fromisoformat(inputs.date)
    row = await conn.fetchrow(
        """
        INSERT INTO soul_v3.daily_dreams (
            agent, date, cycle, dream_narrative,
            key_events, emotional_arc, learnings, pending_threads,
            model_used, source_memory_ids, inject_to_prompt
        ) VALUES (
            $1, $2, $3, $4,
            $5::jsonb, $6::jsonb, $7::jsonb, $8::jsonb,
            $9, $10, true
        )
        ON CONFLICT (agent, date, cycle) DO UPDATE SET
            dream_narrative = EXCLUDED.dream_narrative,
            key_events = EXCLUDED.key_events,
            emotional_arc = EXCLUDED.emotional_arc,
            learnings = EXCLUDED.learnings,
            pending_threads = EXCLUDED.pending_threads,
            model_used = EXCLUDED.model_used,
            source_memory_ids = EXCLUDED.source_memory_ids,
            created_at = NOW()
        RETURNING id
        """,
        inputs.agent, date_obj, inputs.cycle, dream.narrative,
        json.dumps(dream.key_events), json.dumps(dream.emotional_arc),
        json.dumps(dream.learnings), json.dumps(dream.pending_threads),
        dream.model_used, inputs.source_ids,
    )
    return int(row["id"])


# ───────────────────────────────────────────────────────── public API


async def get_dreams_for_prompt(
    conn,
    agent: str,
    n: int = DEFAULT_INJECT_N,
) -> list[asyncpg.Record]:
    """Return the latest N dreams flagged for injection — for system_prompt builder."""
    return await conn.fetch(
        """
        SELECT id, date, cycle, dream_narrative, key_events, emotional_arc,
               learnings, pending_threads, created_at
        FROM soul_v3.daily_dreams
        WHERE agent = $1 AND inject_to_prompt = true
        ORDER BY date DESC, created_at DESC
        LIMIT $2
        """,
        agent, n,
    )


def format_dreams_for_prompt(rows: list[asyncpg.Record]) -> str:
    """Render fetched dreams as a compact system-prompt block.

    Designed to live under a single header so prompt assemblers can append/strip
    it idempotently. Empty list returns empty string (callers can branch on it).
    """
    if not rows:
        return ""
    chunks: list[str] = ["=== DREAMS (memoria narrativa reciente) ==="]
    for row in rows:
        label = f"{row['date']} · {row['cycle']}"
        chunks.append(f"\n[{label}]\n{row['dream_narrative']}")
        pending = row.get("pending_threads")
        if pending:
            try:
                pending_list = pending if isinstance(pending, list) else json.loads(pending)
                if pending_list:
                    chunks.append("  Pending: " + " | ".join(str(p) for p in pending_list[:3]))
            except Exception:
                pass
    chunks.append("\n=== END DREAMS ===")
    return "\n".join(chunks)


# ───────────────────────────────────────────────────────── orchestration


async def run_cycle(agent: str, cycle: str, dry_run: bool = False) -> dict:
    """Run one dream cycle for one agent. Returns stats dict."""
    if cycle not in VALID_CYCLES:
        raise ValueError(f"cycle must be one of {VALID_CYCLES}, got {cycle!r}")
    if agent not in AGENTS:
        raise ValueError(f"agent must be one of {AGENTS}, got {agent!r}")

    conn = await asyncpg.connect(DB_URL)
    try:
        inputs = await _gather_inputs(conn, agent, cycle)
        if len(inputs.memories) < 3:
            LOG.info(f"[{agent}/{cycle}] only {len(inputs.memories)} memories — skip")
            return {"agent": agent, "cycle": cycle, "skipped": True, "reason": "insufficient memories"}

        dream = await _generate_dream(inputs)
        if not dream:
            return {"agent": agent, "cycle": cycle, "skipped": True, "reason": "llm unavailable"}

        if dry_run:
            LOG.info(
                f"[{agent}/{cycle}] DRY-RUN — narrative {len(dream.narrative)} chars, "
                f"{len(dream.key_events)} events, {len(dream.learnings)} learnings, "
                f"model={dream.model_used}"
            )
            return {
                "agent": agent,
                "cycle": cycle,
                "dry_run": True,
                "narrative_preview": dream.narrative[:200],
                "model_used": dream.model_used,
            }

        dream_id = await _store_dream(conn, inputs, dream)
        LOG.info(
            f"[{agent}/{cycle}] dream stored id={dream_id} narrative={len(dream.narrative)}ch "
            f"model={dream.model_used} sources={len(inputs.source_ids)}"
        )
        return {
            "agent": agent,
            "cycle": cycle,
            "dream_id": dream_id,
            "narrative_chars": len(dream.narrative),
            "model_used": dream.model_used,
            "source_memory_count": len(inputs.source_ids),
        }
    finally:
        await conn.close()


async def run_all(cycle: str, dry_run: bool = False) -> list[dict]:
    results = []
    for ag in AGENTS:
        try:
            results.append(await run_cycle(ag, cycle, dry_run=dry_run))
        except Exception as e:
            LOG.error(f"[{ag}/{cycle}] failed: {e}")
            results.append({"agent": ag, "cycle": cycle, "error": str(e)})
    return results


async def inject_preview(agent: str, n: int = DEFAULT_INJECT_N) -> str:
    conn = await asyncpg.connect(DB_URL)
    try:
        rows = await get_dreams_for_prompt(conn, agent, n)
        return format_dreams_for_prompt(rows)
    finally:
        await conn.close()


# ───────────────────────────────────────────────────────── CLI


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SOUL Dream Cycle — 2x/day narrative consolidation")
    p.add_argument("--agent", default="all", help=f"agent name (or 'all'). One of: {AGENTS}")
    p.add_argument("--cycle", choices=sorted(VALID_CYCLES), help="morning or evening")
    p.add_argument("--dry-run", action="store_true", help="generate but do not persist")
    p.add_argument("--inject-preview", action="store_true",
                   help="show what would be injected to system prompt (no generation)")
    p.add_argument("--inject-n", type=int, default=DEFAULT_INJECT_N,
                   help="how many dreams to fetch for preview")
    return p.parse_args()


async def _async_main(args: argparse.Namespace) -> int:
    if args.inject_preview:
        if args.agent == "all":
            LOG.error("--inject-preview requires --agent <name>")
            return 2
        block = await inject_preview(args.agent, args.inject_n)
        print(block or "(no dreams to inject)")
        return 0

    if not args.cycle:
        LOG.error("--cycle is required unless --inject-preview is used")
        return 2

    if args.agent == "all":
        results = await run_all(args.cycle, dry_run=args.dry_run)
    else:
        results = [await run_cycle(args.agent, args.cycle, dry_run=args.dry_run)]
    print(json.dumps(results, indent=2, default=str))
    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    sys.exit(main())
